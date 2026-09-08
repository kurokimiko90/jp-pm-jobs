"""共通回答底稿生成 — Will/Can フレームワーク + STAR/CAR 構造（career coach プロンプト）。

question-bank/common_qa.md（手動維護の底稿）とは別に、日本 転職面接の career coach /
HR / hiring manager 視点で「そのまま暗記して話せる回答」だけを生成するツール。
common_qa.md は置き換えない。出力ファイルには設問と回答のみを書く（解説・採点・
人設核心・一貫性チェックは内部処理には使うが本文には含めない）。

生成は 3 段階（1 回の巨大呼び出しは gateway で timeout するため分割）:
  Phase 1 [LLM x1]  人設核心（Will / To Be / To Do / Can / Portable Skills / 価値観 / ストーリー）
                    → 設問間の一貫性を保つための内部素材。.persona.md に保存、出力本文には含めない
  Phase 2 [LLM xN]  設問ごとに人設核心を注入して回答のみ生成（並行実行・失敗リトライ・都度書き出し）
  Phase 3 [LLM x1]  3問以上あるとき、一貫性セルフチェック（HR視点）→ コンソールにのみ表示

用法:
    python3 -m interview.common_qa_generate                     # 会社非依存の 3 問のみ（弱み/年収/入社時期。LLM 最大 5 回）
    python3 -m interview.common_qa_generate --job-id 123        # 全 16 問（経歴/転職理由/志望動機/強み/
                                                                #   代表プロジェクト/失敗経験等、13 問が JD・企業情報を踏まえて生成される）
    python3 -m interview.common_qa_generate --job-id 123 --questions 1,5   # 指定設問のみ再生成 → 既存出力にマージ
                                                                # （人設核心は .persona.md サイドカーから再利用）
    python3 -m interview.common_qa_generate --persona-file interview/common_qa_generated.persona.md
                                                                # 手直しした人設核心を再利用（Phase 1 スキップ）
    python3 -m interview.common_qa_generate --brief recruit     # interview/companies/recruit.md を企業情報に追加
    python3 -m interview.common_qa_generate --parallel 1        # 並行なし（デフォルト 3）
    python3 -m interview.common_qa_generate --no-llm            # prompt 一式だけ落として手動確認
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tools.deid import build_deid_profile, load_profile
from tools.locale import text as locale_text
from tracker.db import connect

from . import _llm

ROOT = Path(__file__).parent.parent
OUT_DIR = Path(__file__).parent
GEN_DIR = OUT_DIR / "generated"  # 生成物一律寫這裡，不與手寫 source 混放
REFERENCE_QA_PATH = OUT_DIR / "question-bank" / "common_qa.md"
COMPANIES_DIR = OUT_DIR / "companies"
MAX_JD_CHARS = 3000
MAX_BRIEF_CHARS = 4000
MAX_REFERENCE_CHARS = 6000
MAX_GAP_ITEM_CHARS = 300
MAX_GAP_MATCHED = 5

# 出力フォーマット（デフォルトは全設問共通の「結論先行+箇条書き」。Q3/Q16 は形が違うので個別上書き）
_DEFAULT_OUTPUT_FORMAT = (
    "形式: 結論先行 — 1 行目に結論を 1 文。続けて要点を「1. 2. 3.」の番号付きで最大 3 点、\n"
    "各要点は 1〜2 文の話し言葉（そのまま口に出せる平易な表現）で、実績・数字・プロジェクト名の証拠を添える。\n"
    "見出し・解説・前置き・後書きは一切不要。"
)
_PER_COMPANY_OUTPUT_FORMAT = (
    "形式: 見出し・箇条書き記号は使わず、候選人資料の experience にある過去の転職ごとに\n"
    "短い段落を時系列で並べる（各社 2〜3 文）。最後に「共通のキャリア軸」として 1 段落（1〜2 文）でまとめる。\n"
    "解説・前置き・後書きは不要。"
)
_REVERSE_QUESTION_OUTPUT_FORMAT = (
    "形式: 質問文のみを「1. 2. 3.」の番号付きで箇条書きにする。狙いの説明・前置き・後書きは書かない。"
)

# 各設問の構成ルール（中途採用面接 升級版フレームワークの「輸出格式建議」を設問単位に分解）
# id は求職者提供のフレームワーク通し番号（1-15）+ 逆質問（16）。
# BASE=企業情報不要（純粋に候選人自身の事実）、COMPANY=--job-id 必須（JD・企業情報を踏まえて内容を作る）。
# 経歴・強み・代表プロジェクト・失敗経験・過去の転職理由も「今回の応募先にどう繋がるか」まで
# 語る設問のため COMPANY 側。年収・入社時期・弱みの改善策だけは会社に依存しない固定事実。
BASE_QUESTIONS = [
    dict(id=10, title="弱み・課題と改善", spec="4〜6文",
         structure="弱みを明確に → どんな場面で影響が出るか → どう影響を抑えているか → "
                    "現在取っている改善行動 → 既に出ている改善効果 → 今後どう伸ばすか",
         extra="「克服の努力」ではなく「仕組みで解決した」構造にする（日本の面接官はそちらを評価する）。"),
    dict(id=11, title="希望年収とその理由", spec="4〜6文",
         structure="希望年収レンジ → 現年収・市場水準に対する認識 → 経験年数と専門性の裏付け → "
                    "今回のポジションの責任範囲 → 総合的に協議する姿勢",
         extra="最低希望額は自分から出さない。候選人資料の match_summary.negotiation_anchor にある"
               "水準感のみを根拠にし、具体的な現年収の数字は書かない（資料に含まれていない）。"),
    dict(id=12, title="入社可能時期", spec="3〜4文",
         structure="現在の在籍状況 → 退職予告・引き継ぎに必要な期間 → 内定後の最短入社可能時期 → 協議の余地",
         extra="候選人資料に確定した退職予告期間の情報はないため、具体的な月数を断定しない。"
               "「規定を確認のうえ」など幅を持たせた言い方にする。"),
]

COMPANY_QUESTIONS = [
    dict(id=1, title="職歴とキャリア概要", spec="4〜6文",
         structure="現在の職業的な立ち位置 → 主な業界・プロダクト経験 → 中核の担当範囲 → "
                    "代表的な成果 → 直近伸ばしている新しい力 → 応募職種との接続",
         extra="面接官が 2 分で「何をしてきた人か」を理解できることを最優先にする。"
               "⑥の接続部分は、今回の求人票・企業情報で重視されていそうな観点に合わせる。"),
    dict(id=2, title="今回の転職理由", spec="4〜6文",
         structure="現職で得た経験 → 現在の環境・担当範囲の限界 → 次に担いたい仕事 → "
                    "なぜ今が転職の適切なタイミングか → 応募先企業がなぜこの方向性に合うか",
         extra="前職・上司・給与・激務への不満は書かない。ポジティブな語り口のみ。"),
    dict(id=3, title="過去の転職理由（各社ごと）", spec="各社 3〜4文＋総括 1〜2文",
         structure="候選人資料の experience にある過去の転職それぞれについて: "
                    "①入社時に得たかったもの → ②実際に積んだ経験 → ③次に生まれたキャリア上のニーズ → "
                    "④なぜ次の会社がそれにより適していたか → ⑤その選択がもたらした成長。"
                    "最後に、すべての転職を貫く共通のキャリア軸を 1〜2 文でまとめる。",
         extra="場当たり的な転職ではなく一貫した方向性の連続であることを示す。前職・上司・待遇への不満は理由にしない。"
               "総括の一文は、この流れの先に今回の応募先が位置づけられることまで一言で示す。",
         output_format=_PER_COMPANY_OUTPUT_FORMAT),
    dict(id=4, title="志望業界の理由", spec="4〜5文",
         structure="その業界で今起きている変化 → 市場・顧客が抱える課題 → その課題への自分の理解 → "
                    "自分の経験がどう活きるか → 長期的に積み上げたい価値",
         extra="業界を漠然と選んだのではなく、市場・顧客理解に基づくことを示す。"),
    dict(id=5, title="志望企業の理由", spec="4〜5文",
         structure="事業・プロダクトへの理解 → 同社が解決しようとしている課題 → 他社との具体的な違い → "
                    "惹かれた具体的な理由 → 自分の経験で貢献できる点",
         extra="他社にも言える一般論を避け、企業研究に基づく固有の理由にする。"
               "求人票・企業調査ノートにない情報は憶測で作らない。"),
    dict(id=6, title="志望職種・ポジションの理由", spec="4〜5文",
         structure="ポジションの主な責任範囲への理解 → 解決すべき中核課題 → これまでの関連経験 → "
                    "すぐに貢献できる部分 → 今後伸ばしたい能力",
         extra="肩書きや「AI」というキーワードだけに惹かれたと取られないよう、責任の実態への理解を示す。"),
    dict(id=7, title="入社後にやりたいこと（最初の3ヶ月〜1年）", spec="5〜6文",
         structure="入社初期に理解すべきこと → プロダクト・顧客・業務・組織課題の確認 → 優先順位の整理 → "
                    "検証可能な改善を 1 つ選ぶ → 継続的な改善の仕組み化 → 1 年後に見込む成果",
         extra="会社を理解しないうちからの大規模な改革提案は避け、現実的な入社計画を示す。"),
    dict(id=8, title="経験をどう活かして貢献するか", spec="5〜6文",
         structure="会社・ポジションが今必要としている能力 → 自分が持つ関連経験 → "
                    "過去にその能力をどう使ったか → 生まれた結果 → 入社後どの業務に応用できるか → もたらせる価値",
         extra="Q6（志望職種の理由）と重複させず、具体的な業務での活かし方に焦点を当てる。"),
    dict(id=9, title="自分の強み（エピソード付き）", spec="5〜6文",
         structure="強みを一言で → 直面した具体的な課題 → 自分の役割 → とった判断と行動 → "
                    "生まれた成果 → 新しい職位でどう活かすか",
         extra="「真面目・責任感がある」のような抽象語で終わらせない。候選人資料にない数字・経験は使わない。"
               "「新しい職位でどう活かすか」は今回の求人票の要件に対応させる。"),
    dict(id=13, title="なぜあなたを採用すべきか", spec="5〜6文",
         structure="ポジションが最も必要とする中核能力 → 差別化ポイント① → 差別化ポイント② → "
                    "差別化ポイント③ → それらの組み合わせによる強み → 会社にもたらせる具体的な価値",
         extra="他候補者を貶めず、自分固有の組み合わせの強みで語る。"),
    dict(id=14, title="最も成果を出したプロジェクト", spec="6〜8文",
         structure="背景 → 解決すべき課題 → 自分の役割と目標 → 課題をどう分析したか → "
                    "下した重要な判断 → 関係者をどう推進したか → 最終成果 → 学びとその後の応用",
         extra="候選人資料の proof_projects / experience にある実プロジェクトのみ使用し、"
               "JD で重視されていそうな観点を優先的に選ぶ。"),
    dict(id=15, title="失敗・期待した成果が出なかった経験", spec="5〜7文",
         structure="当時の目標と判断 → 実際に起きた問題 → 期待未達の原因 → 自分が負うべき責任の部分 → "
                    "当時の補教措置 → その後構築した改善の仕組み → 再発防止できているか",
         extra="他責にしない。「仕組み化した学び」で締める。学びが今回の応募先でどう再発防止に活きるかも一言添える。"),
    dict(id=16, title="面接官への逆質問", spec="4〜5問",
         structure="Will（長期就業意欲）と Can（価値創出）を補強する質問のみ",
         extra="待遇・福利厚生の質問は入れない。",
         output_format=_REVERSE_QUESTION_OUTPUT_FORMAT),
]

PERSONA_PROMPT = """\
あなたは日本の中途採用（転職）面接に精通した career coach です。
以下の候選人資料（去識別化済み）から、面接回答全体の土台になる「人設核心」を作ってください。
これ以降のすべての設問回答はこの人設と一致させます。

出力（Markdown、各項目 1〜3 行、簡潔に）:
- **Will**（実現したいキャリアの目標）
- **To Be**（将来なりたい姿）
- **To Do**（今回の転職で積みたい能力）
- **Can**（専門スキル）
- **Portable Skills**（持ち運べる能力）
- **核心的価値観**
- **キャリアストーリー**（3〜5 行、一本線で）

捏造禁止。候選人資料にある事実のみ使うこと。前置き・後書きなしで Markdown 本文のみ出力。

# 候選人資料
```yaml
{candidate_data}
```
{company_section}"""

QUESTION_PROMPT = """\
あなたは日本の中途採用（転職）面接に精通した career coach であり、大手企業の
HR・Hiring Manager・Recruiter でもあります。仕事は質問に答えることではなく、
候補者が「受かりやすい」回答を作ることです。

# 大原則
面接官が確認したいのは 2 点だけ:
1. Will — 長くこの会社で働き続けるか
2. Can — 価値を生み出せるか
回答は必ずこの 2 軸に収斂させること。

# 人設核心（この人設と完全に一致させること。矛盾禁止）
{persona}

# スタイル
自然な転職面接の話し方。暗記感・官僚的な言い回し・誇張・AIっぽい作文は禁止。
自然な文章、説得力、ストーリー性、誠実さを重視。
候選人資料にない数字・経験の捏造は絶対禁止。
{reference_note}{company_section}
# 候選人資料（去識別化済み — 姓名/聯絡/生年/住所は除去済み。還原・捏造禁止）
```yaml
{candidate_data}
```

# 設問
Q{qid}. {title}（{spec}）
構成: {structure}
{extra}

# 出力
面接でそのまま話せる回答の本文だけを出力すること（{spec}以內）。
{output_format}
{lang_note}
"""

FINAL_CHECK_PROMPT = """\
あなたは日本の大手企業 HR です。以下は同一候補者の面接回答一式（人設核心つき）です。
次の 4 点をチェックし、{lang_note}
1. すべての回答が同じ Will に戻っているか
2. すべての回答が同じ Can を証明しているか
3. すべてのストーリーが同じキャリア本線を支えているか
4. 「長く働いて価値を出す人」という余韻を残せているか
矛盾があれば、どの設問のどの文が矛盾しているか具体的に指摘すること。前置きなし。

# 回答一式
{answers}
"""

QUESTION_ACCEPT = {
    "minChars": 80,
    "notIncludes": ["狙い", "回答骨子", "回答例", "NG回答", "NG例"],
}
PERSONA_ACCEPT = {"minChars": 300, "includesAll": ["Will", "To Be", "To Do", "Can"]}

_TOP_HEADING = re.compile(r"(?m)^## ")
_Q_HEADING = re.compile(r"^## Q(\d+)\.")


def _cut_at_heading(text: str, cap: int) -> str:
    """cap 超過時は直前の見出し境界で切る（文中でぶつ切りにしない）。"""
    if len(text) <= cap:
        return text
    cut = text.rfind("\n## ", 0, cap)
    return text[: cut if cut > 0 else cap]


def _reference_note() -> str:
    if not REFERENCE_QA_PATH.exists():
        return ""
    existing = _cut_at_heading(REFERENCE_QA_PATH.read_text(encoding="utf-8"), MAX_REFERENCE_CHARS)
    return (
        "\n# 既存の共通回答底稿（事実・語気の参照 — 矛盾させないこと、丸写し不要）\n"
        "```markdown\n" + existing + "\n```\n"
    )


def _gap_note(row) -> str:
    """jobs.gap_analysis（あれば）を企業別設問（COMPANY_QUESTIONS）用の事前評価として要約注入。"""
    raw = row["gap_analysis"] if "gap_analysis" in row.keys() else None
    if not raw:
        return ""
    try:
        gap = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    lines = ["\n# Gap 分析（事前評価 — 回答設計に反映し、リスクは追問対策で先手を打つこと）"]
    score, verdict = gap.get("recommend_score"), gap.get("verdict")
    if score is not None:
        lines.append(f"推薦度: {score}/100（{verdict or '判定なし'}）")
    if gap.get("recommend_reason"):
        lines.append(f"理由: {gap['recommend_reason'][:MAX_GAP_ITEM_CHARS]}")
    matched = gap.get("matched") or []
    if matched:
        lines.append("合致点:")
        lines += [f"- {m[:MAX_GAP_ITEM_CHARS]}" for m in matched[:MAX_GAP_MATCHED]]
    gaps = gap.get("gaps") or []
    if gaps:
        lines.append("ギャップ/リスク（面接で突かれうる点）:")
        lines += [f"- {g[:MAX_GAP_ITEM_CHARS]}" for g in gaps]
    return "\n".join(lines) + "\n"


def _company_section(job_id: int | None, brief: str | None) -> str:
    parts: list[str] = []
    if job_id is not None:
        with connect() as conn:
            row = conn.execute(
                "SELECT company, title, raw_jd, salary_min, salary_max, tier, gap_analysis "
                "FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if not row:
            raise SystemExit(f"job_id {job_id} 不存在")
        raw_jd = (row["raw_jd"] or "").strip()[:MAX_JD_CHARS]
        salary = ""
        if row["salary_min"] or row["salary_max"]:
            salary = f"提示年収レンジ: {row['salary_min'] or '?'}〜{row['salary_max'] or '?'} 万円\n"
        parts.append(
            "\n# 応募先企業情報（志望動機 / 入社後の貢献 / 逆質問で使うこと）\n"
            f"会社名: {row['company'] or '不明'}\n"
            f"求人タイトル: {row['title'] or '不明'}\n"
            + salary
            + (f"企業分類: {row['tier']}\n" if row["tier"] else "")
            + f"求人票（原文抜粋）:\n{raw_jd or '（求人票なし — 一般的な内容に留めること）'}\n"
        )
        parts.append(_gap_note(row))
    if brief:
        brief_path = COMPANIES_DIR / f"{brief}.md"
        if not brief_path.exists():
            raise SystemExit(f"brief が見つかりません: {brief_path}")
        parts.append(
            "\n# 企業調査ノート（抜粋）\n"
            + _cut_at_heading(brief_path.read_text(encoding="utf-8"), MAX_BRIEF_CHARS) + "\n"
        )
    return "".join(parts)


def _parse_question_ids(arg: str | None, has_company: bool) -> list[int]:
    known = {q["id"] for q in BASE_QUESTIONS + COMPANY_QUESTIONS}
    company_ids = {q["id"] for q in COMPANY_QUESTIONS}
    if arg:
        ids = sorted({int(x) for x in arg.split(",") if x.strip()})
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise SystemExit(f"未知の設問番号: {unknown}（1-{max(known)}）")
        needs_company = sorted(i for i in ids if i in company_ids)
        if not has_company and needs_company:
            raise SystemExit(f"設問 {needs_company} は企業別の情報が必要なため --job-id が必要です")
        return ids
    return [q["id"] for q in BASE_QUESTIONS] + (
        [q["id"] for q in COMPANY_QUESTIONS] if has_company else [])


def _parse_existing(path: Path) -> dict[int, str]:
    """既存出力を '## Qn.' 見出しで分割 → {qid: block}。

    旧フォーマット（人設核心/一貫性チェック節を含む）を読んでも、Q ブロック以外は破棄する
    （出力は設問と回答のみ、という現行方針への移行）。
    """
    text = path.read_text(encoding="utf-8")
    marks = list(_TOP_HEADING.finditer(text))
    q_blocks: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        block = re.sub(r"\s*\n---\s*$", "", text[m.start():end].strip())
        qm = _Q_HEADING.match(block)
        if qm:
            q_blocks[int(qm.group(1))] = block
    return q_blocks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, default=None,
                         help="指定すると経歴/転職理由/志望動機/強み/代表プロジェクト/失敗経験等 13 問"
                              "（JD・企業情報を踏まえて生成）を追加。未指定だと弱み/年収/入社時期の 3 問のみ")
    parser.add_argument("--questions", default=None, help="生成する設問番号（例: 1,4,5）。既存出力にマージ")
    parser.add_argument("--persona-file", type=Path, default=None, help="既存の人設核心 md を使う（Phase 1 スキップ）")
    parser.add_argument("--brief", default=None, help="interview/companies/{name}.md を企業情報として同梱")
    parser.add_argument("--lang", choices=["ja", "en"], default="ja", help="建議回答欄の言語")
    parser.add_argument("--timeout", type=int, default=300, help="LLM 1 回あたりの timeout 秒")
    parser.add_argument("--parallel", type=int, default=3, help="Phase 2 の並行数（1 で逐次）")
    parser.add_argument("--retries", type=int, default=1, help="設問ごとの失敗リトライ回数")
    parser.add_argument("--no-llm", action="store_true", help="prompt 一式だけ書き出して LLM を呼ばない")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    question_ids = _parse_question_ids(args.questions, args.job_id is not None)
    all_questions = {q["id"]: q for q in BASE_QUESTIONS + COMPANY_QUESTIONS}
    candidate_data = build_deid_profile(load_profile())
    company_section = _company_section(args.job_id, args.brief)
    reference_note = _reference_note()
    lang_note = "" if args.lang == "ja" else "回答は英語で書くこと。"

    GEN_DIR.mkdir(exist_ok=True)
    out_path = args.out or (
        GEN_DIR / (f"common_qa_generated_{args.job_id}.md" if args.job_id
                   else "common_qa_generated.md"))
    persona_out = out_path.with_suffix(".persona.md")

    def question_prompt(qid: int, persona: str) -> str:
        q = all_questions[qid]
        return QUESTION_PROMPT.format(
            persona=persona, lang_note=lang_note, reference_note=reference_note,
            company_section=company_section, candidate_data=candidate_data,
            qid=q["id"], title=q["title"], spec=q["spec"],
            structure=q["structure"], extra=q["extra"],
            output_format=q.get("output_format", _DEFAULT_OUTPUT_FORMAT))

    persona_prompt = PERSONA_PROMPT.format(
        candidate_data=candidate_data, company_section=company_section)

    if args.no_llm:
        dump = ["===== Phase 1: 人設核心 =====", persona_prompt]
        for qid in question_ids:
            dump += [f"===== Phase 2: Q{qid} =====",
                     question_prompt(qid, "（Phase 1 の出力をここに注入）")]
        prompt_path = out_path.with_suffix(".prompt.txt")
        prompt_path.write_text("\n\n".join(dump), encoding="utf-8")
        print(f"✓ prompt 出力: {prompt_path}")
        return 0

    # --questions 指定 + 既存出力あり → マージモード（他の設問を消さない、出力は設問と回答のみ）
    merge_mode = args.questions is not None and out_path.exists()
    q_blocks: dict[int, str] = {}
    if merge_mode:
        q_blocks = _parse_existing(out_path)
        print(f"✓ マージモード: {out_path}（既存 {len(q_blocks)} 問を保持）")

    # Phase 1: 人設核心（出力本文には含めない内部素材。設問間の一貫性維持だけに使う）
    # 優先度: --persona-file > マージ元の .persona.md サイドカー > 新規生成
    if args.persona_file:
        persona = args.persona_file.read_text(encoding="utf-8").strip()
        print(f"✓ 人設核心を再利用: {args.persona_file}")
    elif merge_mode and persona_out.exists():
        persona = persona_out.read_text(encoding="utf-8").strip()
        print(f"✓ 人設核心を再利用: {persona_out}（一貫性維持のため。作り直すなら --persona-file）")
    else:
        print("Phase 1: 人設核心を生成中…")
        try:
            persona = _llm.call(persona_prompt, timeout=args.timeout, accept=PERSONA_ACCEPT)
        except RuntimeError as e:
            print(f"人設核心の生成失敗: {e}", file=sys.stderr)
            return 2
        persona_out.write_text(persona, encoding="utf-8")
        print(f"✓ 人設核心: {persona_out}（出力本文には含まれない。手直し後 --persona-file で再利用可）")

    write_lock = threading.Lock()

    def flush() -> None:
        blocks = [q_blocks[k] for k in sorted(q_blocks)]
        out_path.write_text("\n\n---\n\n".join(blocks) + "\n", encoding="utf-8")

    def generate_one(qid: int) -> tuple[int, str | None, str]:
        """返回 (qid, 回答 or None, 錯誤訊息)。"""
        last_err = ""
        for attempt in range(args.retries + 1):
            try:
                ans = _llm.call(question_prompt(qid, persona), timeout=args.timeout,
                                accept=QUESTION_ACCEPT)
                return qid, ans, ""
            except RuntimeError as e:
                last_err = str(e)
                if attempt < args.retries:
                    print(f"  … Q{qid} 失敗、リトライ {attempt + 1}/{args.retries}", file=sys.stderr)
        return qid, None, last_err

    # Phase 2: 設問ごとに生成（並行、都度書き出し — 途中失敗しても成果は残る）
    failed: list[int] = []
    generated = 0
    print(f"Phase 2: {len(question_ids)} 問を生成（並行 {max(1, args.parallel)}）…")
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {pool.submit(generate_one, qid): qid for qid in question_ids}
        for fut in as_completed(futures):
            qid, ans, err = fut.result()
            q = all_questions[qid]
            with write_lock:
                if ans is not None:
                    q_blocks[qid] = f"## Q{qid}. {q['title']}（{q['spec']}）\n\n{ans}"
                    generated += 1
                    print(f"  ✓ Q{qid} {q['title']}")
                else:
                    failed.append(qid)
                    print(f"  ✗ Q{qid} 失敗: {err}", file=sys.stderr)
                    if qid not in q_blocks:  # マージ時は旧回答を残す
                        q_blocks[qid] = (f"## Q{qid}. {q['title']}（{q['spec']}）\n\n"
                                         f"（生成失敗 — `--questions {qid}` で再実行）")
                flush()

    # Phase 3: 一貫性チェック（マージ後の全回答で。新規生成が 1 問以上 & 計 3 問以上のとき）
    # 出力ファイルには書かない — コンソールで確認するだけの内部 QA。
    valid_blocks = [q_blocks[k] for k in sorted(q_blocks) if "（生成失敗" not in q_blocks[k]]
    if generated >= 1 and len(valid_blocks) >= 3:
        print("Phase 3: 一貫性セルフチェック…")
        try:
            check = _llm.call(
                FINAL_CHECK_PROMPT.format(
                    answers="## 人設核心\n" + persona + "\n\n" + "\n\n".join(valid_blocks),
                    lang_note=locale_text("qa_check_lang")),
                timeout=args.timeout, accept={"minChars": 100})
            print("\n----- 一貫性セルフチェック（HR視点、出力ファイルには含まれません） -----")
            print(check)
            print("-----------------------------------------------------------\n")
        except RuntimeError as e:
            print(f"  ✗ 一貫性チェック失敗（回答本体は保存済み）: {e}", file=sys.stderr)

    print(f"\n✓ 出力: {out_path}")
    if failed:
        failed.sort()
        print(f"✗ 失敗した設問: {failed} — `--questions {','.join(map(str, failed))}` で再実行",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
