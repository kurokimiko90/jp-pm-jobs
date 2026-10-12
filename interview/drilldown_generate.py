"""深掘り想定問答生成 — 基底回答への鋭い追問を設問 1 問 1 呼び出しで作る。

common_qa_generated.md（基底回答）の各設問に対し、1 回の LLM 呼び出しの中で
面接官パネル（攻撃質問）→ career coach（応答）の両役割を演じさせる。
手書きの common_qa_drilldown.md は事実・語気の参照として設問単位で注入する（置き換えない）。

生成は設問ごとに 1 回（同一呼び出し内で出題→応答、ラベル分けせず自然文で出力）:
  [LLM xN]  設問ごとに攻撃質問 + 応答を生成（並行実行・失敗リトライ・都度書き出し）
  [LLM x1]  一貫性・捏造チェック（HR視点）→ .check.md サイドカーに保存
            ＋ 純 Python の数字出典照合（LLM 不要）

用法:
    python3 -m interview.drilldown_generate                     # 共通 Q1-6（LLM 7 回）
    python3 -m interview.drilldown_generate --job-id 123       # 共通 + Q7-9（JD/gap を攻撃材料に、LLM 10 回）
    python3 -m interview.drilldown_generate --questions 1,4     # 指定設問のみ再生成 → 既存にマージ
    python3 -m interview.drilldown_generate --per-q 5           # 設問あたりの攻撃質問数
    python3 -m interview.drilldown_generate --no-llm            # prompt 一式だけ落として手動確認
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from tools.deid import build_deid_profile, load_profile
from tools.locale import text as locale_text

from . import _llm
from .common_qa_generate import _company_section, _cut_at_heading, _parse_existing

OUT_DIR = Path(__file__).parent
GEN_DIR = OUT_DIR / "generated"  # 生成物一律寫這裡，不與手寫 source 混放
BASE_QA_PATH = GEN_DIR / "common_qa_generated.md"
HAND_DRILLDOWN_PATH = OUT_DIR / "question-bank" / "common_qa_drilldown.md"
MAX_REF_CHARS = 4000
MAX_BASE_BLOCK_CHARS = 5000

# 攻撃カテゴリ（red team への必須視点。job モードでは gap_analysis の gaps も追加注入）
ATTACK_CATEGORIES = """\
- 数字・実績の検証（計測方法は？出典は？過大主張ではないか？）
- 権限と役割の範囲（「チームでやった」の中で、あなた自身は何を決めたのか）
- 再現性（それはうちの環境・規模でも通用するのか）
- 失敗・反例（うまくいかなかったケースは？そこから何を変えたか）
- 個人開発 vs 業務実績のギャップ（企業スケールで未検証ではないか）
- 経歴リスク（ドメイン転々・転職回数・年収・空白期間・昇格実績）
- 日本語・文化フィット（外国籍候補者への定番の突っ込み）"""

# 手書き深掘りの応答型（参照セクションがない設問にはこれを様式見本として注入）
STYLE_EXEMPLAR = """\
- 誠実承認 → 代替提示型: 「指摘はもっともです。ただし〜を補足させてください。（事実）。
  一方、〜の実績はまだありません。正直にそう言います。だからこそ〜が転職の動機です」
- 事実ベース限定型: 「正直に言うと、定量的な％は正式に計測していません。
  構造的な変化として言えるのは（検証可能な事実）です。数字の過大主張はしません」"""

COMBINED_PROMPT = """\
あなたは日本の中途採用面接のプロで、この一回の応答の中で二つの役割を両方演じます。
役割の境目は出力に書かないこと（ラベル分けせず、最終形式だけ出す）。

# 役割 1: 面接官パネル（HR / Hiring Manager / 技術系役員）として出題
以下の候補者の回答を読み、最も鋭い深掘り質問を {per_q} 問だけ厳選する。
仕事は候補者を落とすことではなく、「本物かどうか」を見抜くこと。
- 基底回答の中に既に「面試追問」「追問」として浅い想定問答が書かれている場合、
  それと重複する質問は禁止。その一段深い第二層（回答の回答を突く）を狙うこと。
- 甘い質問・答えやすい質問は不要。実際の面接で候補者が詰まる質問だけ。
- 各質問に視角タグ（HR / PdM / 技術）を付け、厳しさ順（最も危険な質問が D1）に並べる。
- 攻撃視点は必ず複数カテゴリに分散させる:
{attack_categories}
{gap_section}
# 役割 2: career coach として、役割 1 の質問それぞれに応答を作る
1. 捏造絶対禁止 — 数字・経験は「候選人資料」「基底回答」「手書き参照」にあるものだけ。
   答えられない事実は誠実に認め、代替の証拠や姿勢で返す（様式見本参照）。
2. 人設核心と完全一致。基底回答と矛盾する新事実を作らない。
3. 負面質問への型: 認める → 構造・事実で切り返す → Will / Can に着地。逃げ・ごまかしは逆効果。
4. スタイル: 話し言葉。1 文 60 字以内、各応答は音読 30〜45 秒（3〜6 文）。口頭では「御社」。
   暗記感・官僚的言い回し・「シナジー」等のカタカナビジネス語の乱用禁止。

# 様式見本（語気・構造の参照）
{style_ref}

# 人設核心（矛盾禁止。この主張の弱点を役割 1 で突く）
{persona}

# 候選人資料（去識別化済み — 面接官は履歴書として把握済み。還原・捏造禁止）
```yaml
{candidate_data}
```
{company_section}
# 基底回答（この回答への深掘り。役割 2 はこれと矛盾しない）
{base_block}

# 出力（この形式のみ、一問一答。前置き・後書き・役割ラベル・解説なし）
### D1. （質問文）（HR / PdM / 技術 のいずれか）
> （応答本文。結論先行 — 1 文目に結論、続けて要点を「1. 2. 3.」の番号付きで最大 3 点。
>   各要点は 1〜2 文の話し言葉＋証拠（実績・数字・プロジェクト名）。全体で音読 30〜45 秒）

### D2. …
{lang_note}"""

CHECK_PROMPT = """\
あなたは日本の大手企業 HR です。以下は同一候補者の基底回答と深掘り応答一式です。
次の 4 点をチェックしてください。{lang_note}
1. 深掘り応答が基底回答・人設核心と矛盾していないか（矛盾は設問と文を特定）
2. 基底回答・候選人資料にない新しい数字・実績が深掘り応答に出ていないか（捏造疑いを列挙）
3. 負面質問への応答が誠実か（ごまかし・話術で逃げている箇所の指摘）
4. 最も弱い応答はどれか（人間が手直しすべき優先順）

# 人設核心
{persona}

# 深掘り応答一式（基底回答つき）
{answers}
"""

_HAND_SECTION = re.compile(r"(?m)^## (\d+)\s*$")
_D_HEADING = re.compile(r"(?m)^### D\d+\.")
_NUM = re.compile(r"\d[\d,.]*")


def _parse_hand_drilldown() -> dict[int, str]:
    """手書き common_qa_drilldown.md を '## n' 見出しで設問番号ごとに分割。"""
    if not HAND_DRILLDOWN_PATH.exists():
        return {}
    text = HAND_DRILLDOWN_PATH.read_text(encoding="utf-8")
    marks = list(_HAND_SECTION.finditer(text))
    sections: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        sections[int(m.group(1))] = _cut_at_heading(text[m.start():end].strip(), MAX_REF_CHARS)
    return sections


def _load_base_blocks(job_id: int | None) -> dict[int, str]:
    """基底回答を読み込む。job モードは共通 Q1-6 + job 版 Q7-9 をマージ（job 版優先）。"""
    blocks: dict[int, str] = {}
    if BASE_QA_PATH.exists():
        blocks.update(_parse_existing(BASE_QA_PATH))
    if job_id is not None:
        job_path = GEN_DIR / f"common_qa_generated_{job_id}.md"
        if not job_path.exists():
            raise SystemExit(f"基底回答がありません: {job_path}（先に common_qa_generate --job-id を実行）")
        blocks.update(_parse_existing(job_path))
    if not blocks:
        raise SystemExit(f"基底回答がありません: {BASE_QA_PATH}（先に common_qa_generate を実行）")
    return {qid: _cut_at_heading(b, MAX_BASE_BLOCK_CHARS) for qid, b in blocks.items()}


def _load_persona(job_id: int | None, override: Path | None) -> tuple[str, str]:
    """人設核心を探す。優先: --persona-file > job 版サイドカー > 共通サイドカー。"""
    candidates = [override] if override else [
        GEN_DIR / f"common_qa_generated_{job_id}.persona.md" if job_id else None,
        BASE_QA_PATH.with_suffix(".persona.md"),
    ]
    for p in candidates:
        if p and p.exists():
            return p.read_text(encoding="utf-8").strip(), str(p)
    return "（人設核心なし — 基底回答と候選人資料のみで一貫性を保つこと）", "なし"


def _gap_attack_section(job_id: int | None) -> str:
    """job モード時、gap_analysis の gaps + 年収の唯一の正解を必須攻撃ベクトルとして追加。

    年収は複数箇所（希望/下限/現職）で数字がブレやすいので、ここで一度だけ確定値を渡し、
    9 問すべてがこの一節だけを参照するようにする（各設問が個別に推測しない）。
    """
    if job_id is None:
        return ""
    parts: list[str] = []

    comp = (load_profile() or {}).get("compensation") or {}
    desired = comp.get("desired_annual")
    floor = comp.get("desired_min")
    if desired or floor:
        parts.append(
            "\n# 年収の確定値（この数字だけを使うこと。他の万円数字を年収として作らない）\n"
            + (f"希望年収: {desired}万円\n" if desired else "")
            + (f"許容下限: {floor}万円\n" if floor else "")
            + "現年収は非公開（具体的な現年収の万円数字を応答に出さない）。\n")

    from tracker.db import connect
    with connect() as conn:
        row = conn.execute("SELECT gap_analysis FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row and row["gap_analysis"]:
        import json
        try:
            gaps = json.loads(row["gap_analysis"]).get("gaps") or []
        except (ValueError, TypeError):
            gaps = []
        if gaps:
            parts.append("\n# 事前 gap 分析で判明した弱点（必ず 1 問以上ここから出題すること）\n"
                          + "\n".join(f"- {g[:300]}" for g in gaps) + "\n")
    return "".join(parts)


def _audit_numbers(generated: str, sources: str) -> list[str]:
    """生成文中の数字で、出典（profile/基底回答/参照/JD）に存在しないものを列挙。"""
    def norm(s: str) -> str:
        return s.translate(str.maketrans("０１２３４５６７８９，", "0123456789,")).replace(",", "").rstrip(".")
    known = {norm(m.group()) for m in _NUM.finditer(sources)}
    return sorted({m.group() for m in _NUM.finditer(generated) if norm(m.group()) not in known})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, default=None, help="job 版 Q7-9 を含め、JD/gap を攻撃材料に注入")
    parser.add_argument("--questions", default=None, help="生成する設問番号（例: 1,4）。既存出力にマージ")
    parser.add_argument("--persona-file", type=Path, default=None, help="人設核心 md を明示指定")
    parser.add_argument("--per-q", type=int, default=5, help="設問あたりの攻撃質問数（デフォルト 5）")
    parser.add_argument("--lang", choices=["ja", "en"], default="ja")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--no-llm", action="store_true", help="prompt 一式だけ書き出して LLM を呼ばない")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    base_blocks = _load_base_blocks(args.job_id)
    if args.questions:
        wanted = sorted({int(x) for x in args.questions.split(",") if x.strip()})
        missing = [q for q in wanted if q not in base_blocks]
        if missing:
            raise SystemExit(f"基底回答に存在しない設問: {missing}（あるのは {sorted(base_blocks)}）")
        question_ids = wanted
    else:
        question_ids = sorted(base_blocks)

    candidate_data = build_deid_profile(load_profile())
    company_section = _company_section(args.job_id, None)
    persona, persona_src = _load_persona(args.job_id, args.persona_file)
    gap_section = _gap_attack_section(args.job_id)
    hand_refs = _parse_hand_drilldown()
    lang_note = "" if args.lang == "ja" else "\n応答は英語で書くこと（質問文は原文のまま）。"

    GEN_DIR.mkdir(exist_ok=True)
    out_path = args.out or (
        GEN_DIR / (f"common_qa_drilldown_generated_{args.job_id}.md" if args.job_id
                   else "common_qa_drilldown_generated.md"))
    check_path = out_path.with_suffix(".check.md")

    def style_ref(qid: int) -> str:
        ref = hand_refs.get(qid)
        if ref:
            return ("以下は同じ設問への手書き深掘り（事実・語気の参照。矛盾禁止、丸写し不要）:\n"
                    "```markdown\n" + ref + "\n```")
        return STYLE_EXEMPLAR

    def combined_prompt(qid: int) -> str:
        return COMBINED_PROMPT.format(
            per_q=args.per_q, attack_categories=ATTACK_CATEGORIES, gap_section=gap_section,
            style_ref=style_ref(qid), persona=persona, candidate_data=candidate_data,
            company_section=company_section, base_block=base_blocks[qid], lang_note=lang_note)

    if args.no_llm:
        dump: list[str] = []
        for qid in question_ids:
            dump += [f"===== Q{qid} 攻撃質問+応答 =====", combined_prompt(qid)]
        prompt_path = out_path.with_suffix(".prompt.txt")
        prompt_path.write_text("\n\n".join(dump), encoding="utf-8")
        print(f"✓ prompt 出力: {prompt_path}")
        return 0

    merge_mode = args.questions is not None and out_path.exists()
    q_blocks: dict[int, str] = _parse_existing(out_path) if merge_mode else {}
    if merge_mode:
        print(f"✓ マージモード: {out_path}（既存 {len(q_blocks)} 問を保持）")
    print(f"✓ 人設核心: {persona_src}")

    accept_combined = {
        "minChars": 600,
        "includesAll": [f"### D{i}" for i in range(1, args.per_q + 1)],
        "notIncludes": ["狙い", "回答骨子", "回答例", "NG回答", "NG例"],
    }

    write_lock = threading.Lock()

    def flush() -> None:
        header = (
            "# 深掘り想定問答（生成版）"
            + (f" — job {args.job_id}" if args.job_id else "") + "\n\n"
            f"生成: {date.today().isoformat()} / interview/drilldown_generate.py\n"
            f"基底: common_qa_generated{'_' + str(args.job_id) if args.job_id else ''}.md"
            f" / 人設: {persona_src}\n"
            "事實來源: candidate_profile.yaml（去識別化）+ 基底回答 + 手書き深掘り。"
            "出典のない数字は使用しない（.check.md の稽核結果を必ず確認）。\n\n---\n\n")
        blocks = [q_blocks[k] for k in sorted(q_blocks)]
        out_path.write_text(header + "\n\n---\n\n".join(blocks) + "\n", encoding="utf-8")

    def generate_one(qid: int) -> tuple[int, str | None, str]:
        """出題+応答を 1 回の LLM 呼び出しで。返回 (qid, block or None, 錯誤)。"""
        last_err = ""
        for attempt in range(args.retries + 1):
            try:
                answers = _llm.call(combined_prompt(qid), timeout=args.timeout,
                                    accept=accept_combined)
                title_line = base_blocks[qid].splitlines()[0].lstrip("# ").strip()
                return qid, f"## Q{qid}. {title_line.split('. ', 1)[-1]} — 深掘り\n\n{answers}", ""
            except RuntimeError as e:
                last_err = str(e)
                if attempt < args.retries:
                    print(f"  … Q{qid} 失敗、リトライ {attempt + 1}/{args.retries}", file=sys.stderr)
        return qid, None, last_err

    failed: list[int] = []
    generated = 0
    print(f"生成: {len(question_ids)} 問（並行 {max(1, args.parallel)}）…")
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {pool.submit(generate_one, qid): qid for qid in question_ids}
        for fut in as_completed(futures):
            qid, block, err = fut.result()
            with write_lock:
                if block is not None:
                    q_blocks[qid] = block
                    generated += 1
                    print(f"  ✓ Q{qid}（{len(_D_HEADING.findall(block))} 問）")
                else:
                    failed.append(qid)
                    print(f"  ✗ Q{qid} 失敗: {err}", file=sys.stderr)
                    if qid not in q_blocks:
                        q_blocks[qid] = (f"## Q{qid}. — 深掘り\n\n"
                                         f"（生成失敗 — `--questions {qid}` で再実行）")
                flush()

    # Phase C: 数字出典照合（純 Python）+ 一貫性・捏造チェック（LLM）→ .check.md
    valid = [q_blocks[k] for k in sorted(q_blocks) if "（生成失敗" not in q_blocks[k]]
    if generated >= 1 and valid:
        sources = "\n".join([candidate_data, persona, company_section,
                             *base_blocks.values(), *hand_refs.values()])
        suspects = _audit_numbers("\n".join(valid), sources)
        check_parts = ["# 深掘り想定問答 — 稽核結果\n",
                       f"生成: {date.today().isoformat()}\n",
                       "## 数字出典照合（純 Python — 出典が見つからない数字）\n",
                       ("- " + "\n- ".join(suspects) + "\n\n以上は捏造とは限らない（30秒等の一般表現含む）。"
                        "手動確認すること。") if suspects else "（すべての数字に出典あり）"]
        print("Phase C: 一貫性・捏造チェック…")
        try:
            check = _llm.call(
                CHECK_PROMPT.format(
                    persona=persona,
                    answers="\n\n".join([*base_blocks.values()][:3] + valid),
                    lang_note=locale_text("qa_check_lang")),
                timeout=args.timeout, accept={"minChars": 100})
            check_parts += ["\n## HR 視点チェック（LLM）\n", check]
        except RuntimeError as e:
            check_parts += ["\n## HR 視点チェック（LLM）\n", f"（失敗: {e}）"]
            print(f"  ✗ チェック失敗（応答本体は保存済み）: {e}", file=sys.stderr)
        check_path.write_text("\n".join(check_parts) + "\n", encoding="utf-8")
        print(f"✓ 稽核: {check_path}" + (f"（要確認の数字 {len(suspects)} 件）" if suspects else ""))

    print(f"\n✓ 出力: {out_path}")
    if failed:
        failed.sort()
        print(f"✗ 失敗した設問: {failed} — `--questions {','.join(map(str, failed))}` で再実行",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
