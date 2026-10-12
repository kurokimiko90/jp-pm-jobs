"""面接シアター対話台本生成 — QA md + 会社 brief + JD → 9 幕インタラクティブ VNScript。

prep.py {id} interview の script stage 本体。tts/theater.py の平板台本（一問一答
読み上げ）を、手書き SCRIPT_MAIN と同じ 9 幕骨架＋面接官との往復（追問・JD 確認・
逆質問チェーン）に編み直す。

品質保証は全て機械ゲート（LLM 審稿なし）:
  Gate A 構造 — JSON 形状 / speaker・emotion 合法 / quoteFrom が直前の面接官発言の
                部分文字列（換話題式の偽追問を機械的に拒否）/ 指定幕はチェーン必須
  Gate B 事実 — 候補者台詞の数字 ⊆ 素材（QA md 等）に出現する数字（列挙用 0〜10 許容）
                → 素材にない数字の捏造は幕ごと拒否・再生成
  Gate C 口語 — です・ます調 / 1 句 ≤90 字 / 質問文はクッション言葉必須 /
                markdown・{{}} 残留禁止 / 貴社→御社等の書面語は自動修正
  Gate D 網羅 — 桶に入った QA が実際に台詞へ反映されているか（キーワード/n-gram 照合）。
                LLM は「全て消化」指示を無視して QA を黙って落とすことがあり、桶サイズ
                だけを見る review_report の "QA N問" では検出できないため機械チェック

LLM 呼び出しは最小化：キャッシュに無い幕だけを **1 回の合併呼び出し** で一括生成し、
ゲート不合格の幕のみ違反フィードバック付きで個別修復（幕あたり最大 2 回）。
新規 pack の最良ケース = 1 call、素材未変更 = 0 call、1 問修正 = 1 call。
なお不合格なら平板版（QA md そのまま）に降級し review_report.md に明記 —
未検証内容の無言出荷はしない。面接官の「素材で裏取りできない回答」は simulated と
して note に明示。

用法:
    python3 -m interview.theater_script --job-id 123 [--force]
    python3 -m interview.theater_script --pack 123_サンプル社
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.pii_gate import scrub_for_external  # noqa: E402
from tts.theater import (  # noqa: E402
    PREP_DIR, QAItem, _source_path, parse_standard_qa, theater_dir,
)
from .qa_quality import parse_drills  # noqa: E402

PROMPT_VERSION = 1
MAX_REGEN = 2          # 幕ごとの再生成上限（超えたら平板版に降級）
MAX_LINE_CHARS = 90    # 口頭で一息で言える・TTS の息継ぎに収まる上限
MAX_ACT_LINES = 40

VALID_EMOTIONS = {"neutral", "smile", "think", "serious", "nervous", "relieved"}

# 質問文に最低ひとつ必要な丁寧マーカー（裸の「〜ですか。」を機械的に拒否）
POLITE_MARKERS = (
    "いただけ", "いただい", "いただき", "伺", "よろしいでしょうか", "幸いです",
    "ください", "願え", "差し支えなければ", "恐れ入り", "よろしければ",
)
# 書面語 → 口頭語の自動修正（拒否ではなく決定論的に置換）
AUTOFIX = (("貴社", "御社"), ("了解しました", "承知しました"), ("了解いたしました", "承知いたしました"))
CASUAL_RE = re.compile(r"(だ。|である。|だね。|だよ。|じゃん|っすね)")
MARKDOWN_RE = re.compile(r"(\*\*|##|\]\(|\{\{|\}\})")
ENUM_PREFIX_RE = re.compile(r"^\d+[\.、]\s*")
DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")

SIMULATED_NOTE = {
    "ja": "この面接官の回答は素材で裏取りできない練習用の模擬回答です。実際の面接官の回答を優先してください。",
    "zh": "這句面試官回答是練習用的模擬內容（素材無法佐證），實際請以真人面試官所說為準。",
}


# ---------------------------------------------------------------- 9 幕骨架

@dataclass(frozen=True)
class ActSpec:
    no: int
    title_ja: str
    title_zh: str
    goal_ja: str
    goal_zh: str
    keywords: tuple[str, ...] = ()   # QA 質問文とのマッチ（分桶）
    chain_required: bool = False     # quoteFrom 追問チェーン必須
    framework: tuple[tuple[str, str], ...] = ()   # (ja, zh) 学習チェックリスト（全社共通・静的）


# 学習チェックリスト = 汎用メソッド論（手書き SCRIPT_MAIN と同じ粒度）。会社固有の事実は
# 含まない静的コンテンツなので LLM 不要 — 全パック共通で復習頁の幕ヘッダーに出す。
SKELETON: tuple[ActSpec, ...] = (
    ActSpec(1, "第一幕　ご挨拶", "第一幕　開場",
            "面接は「見極め合う場」と設定する", "把面試定位成互相確認的場合"),
    ActSpec(2, "第二幕　自己紹介", "第二幕　自我介紹",
            "強みを一本の線で語る", "把強項連成一條線",
            keywords=("自己紹介", "経歴", "強み"),
            framework=(("現在の役割（一言で）", "現在的角色（一句話）"),
                      ("定量実績（数字で）", "量化實績（用數字）"),
                      ("志望への接続", "接到志望動機"))),
    ActSpec(3, "第三幕　転職理由・志望動機", "第三幕　轉職理由・志望動機",
            "募集背景・裁量範囲を聞き出す", "問出招募背景與裁量範圍",
            keywords=("転職理由", "志望", "なぜオープン", "なぜこの", "キャリア", "5年後"),
            chain_required=True,
            framework=(("前向きな転職として語る（不満は言わない）", "當作正向轉職來講（不抱怨前公司）"),
                      ("会社の需要に合わせて接続する", "對準公司需求收尾"),
                      ("募集背景を聞き出す", "問出招募背景"))),
    ActSpec(4, "第四幕　一番難しかった意思決定", "第四幕　最艱難的決策",
            "STAR で実績を語り、仕事の進め方を確認する", "用 STAR 講實績＋確認工作方式",
            keywords=("意思決定", "失敗", "対立", "プレッシャー", "課題を発見", "リードした経験"),
            framework=(("Situation — 状況", "Situation — 情境"),
                      ("Task — 課題", "Task — 課題"),
                      ("Action — 行動", "Action — 行動"),
                      ("Result — 成果（数字）", "Result — 成果（數字）"))),
    ActSpec(5, "第五幕　ケース・優先順位付け", "第五幕　案例・優先順序",
            "前提を確認してから答える", "先確認前提再作答",
            keywords=("優先順位", "ロードマップ", "改善提案", "KPI", "リサーチ"),
            framework=(("前提を確認（KPI・フェーズ）", "先確認前提（KPI・階段）"),
                      ("2軸で整理", "兩軸整理"),
                      ("小さく検証してから開発", "先小驗證再開發"))),
    ActSpec(6, "第六幕　深掘り", "第六幕　深挖",
            "リスク項目への復元＋期待水準の確認", "風險項復原＋確認期待水準",
            keywords=("未経験", "生成AI", "AIを業務", "ステークホルダー", "キャッチアップ",
                      "Plug and Play", "ペアプロダクト", "適応"),
            chain_required=True,
            framework=(("具体的な成果物", "具體產出"),
                      ("定量的な成果", "量化成果"),
                      ("再現可能なプロセス", "可複製的流程"))),
    ActSpec(7, "第七幕　逆質問", "第七幕　反向提問",
            "求める人物像・評価基準を聞き出し、回答に食い込む", "問出人物像/評價基準並追問",
            chain_required=True,
            framework=(("活躍している人の共通点", "表現出色者的共通點"),
                      ("最初の3ヶ月の期待値", "頭三個月的期待值"),
                      ("評価の基準", "評價的基準"))),
    ActSpec(8, "第八幕　希望年収", "第八幕　期望年收",
            "提供価値を基準に説明する", "以提供價值為基準說明",
            keywords=("年収",),
            framework=(("事業価値を創出する", "創造商業價值"),
                      ("市場投入を加速する", "加速產品上市"),
                      ("組織全体の生産性を高める", "提升組織整體效率"))),
    ActSpec(9, "第九幕　結び", "第九幕　收尾",
            "面接中に得た情報を引用して締める", "引用面試中獲得的資訊收尾",
            framework=(("面接で得た情報を引用", "引用面試中獲得的資訊"),
                      ("意欲を一言で", "一句話表達意願"),
                      ("次の工程を確認", "確認下一步流程"))),
)


def bucket_qa(items: list[QAItem]) -> dict[int, list[QAItem]]:
    """QA 項目を幕に分桶。未マッチは第六幕（深掘り）へ — 全問カバレッジを落とさない。"""
    buckets: dict[int, list[QAItem]] = {spec.no: [] for spec in SKELETON}
    for item in items:
        placed = False
        for spec in SKELETON:
            if spec.keywords and any(k in item.question for k in spec.keywords):
                buckets[spec.no].append(item)
                placed = True
                break
        if not placed:
            buckets[6].append(item)
    return buckets


# ---------------------------------------------------------------- 素材

@dataclass
class Materials:
    company: str
    title: str
    qa_md: str
    shibou: str
    brief_intro: str
    brief_jd: str        # brief §9 求人・面接対策
    brief_gyaku: str     # brief §11 逆質問リスト
    unconfirmed: list[str]  # {{要確認}} を含む行（模擬回答が必要な質問素材）
    raw_jd: str
    numbers: str
    corpus_digits: set[str] = field(default_factory=set)


def _brief_section(text: str, needle: str, limit: int) -> str:
    """`## 見出し` に needle を含むセクション本文を抽出。"""
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts:
        if part and needle in part.splitlines()[0]:
            return ("## " + part)[:limit]
    return ""


def _digits_of(text: str) -> set[str]:
    return set(DIGIT_RE.findall(text.replace(",", "")))


def collect_materials(pack_dir: Path, job: dict | None) -> Materials:
    """pack 内素材 + job.raw_jd を読み込み PII scrub。数字コーパスも構築。"""
    def read(name: str) -> str:
        p = pack_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    src = _source_path(pack_dir)
    qa_md = src.read_text(encoding="utf-8") if src else ""
    brief = read("01_company_brief.md")
    unconfirmed = [ln.strip() for ln in brief.splitlines() if "{{要確認}}" in ln][:8]

    job = job or {}
    dirname = pack_dir.name
    mat = Materials(
        company=job.get("company") or (dirname.split("_", 1)[1] if "_" in dirname else dirname),
        title=job.get("title") or "",
        qa_md=qa_md,
        shibou=read("02_shibou_doki.md"),
        brief_intro=brief[:1200],
        brief_jd=_brief_section(brief, "求人・面接対策", 2200),
        brief_gyaku=_brief_section(brief, "逆質問", 1600),
        unconfirmed=unconfirmed,
        raw_jd=(job.get("raw_jd") or "")[:2600],
        numbers=read("06_numbers_card.md")[:2200],
    )
    # PII scrub（外部 LLM へ出る前に全素材を通す）
    for f in ("qa_md", "shibou", "brief_intro", "brief_jd", "brief_gyaku", "raw_jd", "numbers"):
        scrubbed, _ = scrub_for_external(getattr(mat, f))
        setattr(mat, f, scrubbed)
    mat.corpus_digits = _digits_of(
        "\n".join([mat.qa_md, mat.shibou, mat.numbers, mat.brief_jd, mat.brief_gyaku,
                  mat.brief_intro, mat.raw_jd]))
    return mat


# ---------------------------------------------------------------- Gates（機械検収）

def _autofix_line(line: dict) -> dict:
    if line.get("type") == "talk":
        ja = line.get("ja", "")
        for src_w, dst_w in AUTOFIX:
            ja = ja.replace(src_w, dst_w)
        line["ja"] = ja
    if line.get("emotion") not in VALID_EMOTIONS:
        line["emotion"] = "neutral"
    return line


def _is_question(ja: str) -> bool:
    return ja.rstrip().endswith(("か。", "か？", "か?"))


def gate_structure(lines: list[dict], spec: ActSpec) -> list[str]:
    """Gate A: 形状 / quoteFrom 錨定 / チェーン必須。"""
    v: list[str] = []
    if not lines:
        return ["幕が空"]
    if len(lines) > MAX_ACT_LINES:
        v.append(f"台詞 {len(lines)} 句 > 上限 {MAX_ACT_LINES}")
    chain_ok = False
    for i, ln in enumerate(lines):
        if ln.get("speaker") not in ("interviewer", "candidate", "inner"):
            v.append(f"line{i}: speaker 不正 {ln.get('speaker')!r}")
            continue
        if ln.get("type") not in ("talk", "inner"):
            v.append(f"line{i}: type 不正")
        if not (ln.get("ja") or "").strip():
            v.append(f"line{i}: ja 空")
        quote = ln.get("quoteFrom")
        if quote:
            prev_iv = next((p.get("ja", "") for p in reversed(lines[:i])
                            if p.get("speaker") == "interviewer" and p.get("type") == "talk"), "")
            if len(quote.strip()) < 4 or quote.strip() not in prev_iv:
                v.append(f"line{i}: quoteFrom が直前の面接官発言の部分文字列でない: {quote!r}")
            elif ln.get("speaker") == "candidate":
                chain_ok = True
    if spec.chain_required and not chain_ok:
        v.append("quoteFrom 付き追問チェーンが必須なのに存在しない")
    return v


def gate_facts(lines: list[dict], corpus_digits: set[str]) -> list[str]:
    """Gate B: 候補者台詞の数字は素材に出現するものだけ（列挙用 0〜10 は許容）。"""
    allowed = corpus_digits | {str(n) for n in range(11)}
    v: list[str] = []
    for i, ln in enumerate(lines):
        if ln.get("speaker") != "candidate" or ln.get("type") != "talk":
            continue
        for num in _digits_of(ln.get("ja", "")):
            if num not in allowed:
                v.append(f"line{i}: 素材にない数字 {num!r}（捏造禁止）")
    return v


def gate_speech(lines: list[dict]) -> list[str]:
    """Gate C: です・ます調 / 句長 / クッション言葉 / markdown 残留。inner は文体免除。"""
    v: list[str] = []
    for i, ln in enumerate(lines):
        ja = ln.get("ja", "")
        if MARKDOWN_RE.search(ja) or ENUM_PREFIX_RE.match(ja):
            v.append(f"line{i}: markdown/番号残留")
        if ln.get("type") == "inner":
            continue
        if len(ja) > MAX_LINE_CHARS:
            v.append(f"line{i}: {len(ja)} 字 > {MAX_LINE_CHARS} 字（分割せよ）")
        if CASUAL_RE.search(ja):
            v.append(f"line{i}: 常体/カジュアル表現（です・ます調で）")
        if (ln.get("speaker") == "candidate" and _is_question(ja)
                and not any(m in ja for m in POLITE_MARKERS)):
            v.append(f"line{i}: 質問にクッション言葉/依頼表現がない")
    return v


# 質問文からトピック語を抜くための定型ノイズ（除去後の残りをカバレッジ判定に使う）
_Q_STOPWORDS = (
    "を教えてください", "について教えてください", "を教えてください。", "はありますか",
    "でしょうか", "ですか", "ください", "について", "具体的な", "教えてください",
)


def _ngrams(s: str, n: int = 4) -> list[str]:
    return [s[i:i + n] for i in range(len(s) - n + 1)]


def _qa_topic_terms(item: QAItem, spec: ActSpec) -> list[str]:
    """QA が幕へ配置された根拠語（spec.keywords 命中語。無ければ質問文から抽出した
    4字 n-gram）。台詞にこの語が一つも出ない QA は「桶には入ったが対話に反映されて
    いない（=見た目は消化済みだが実質欠落）」とみなす。"""
    hits = [k for k in spec.keywords if k in item.question]
    if hits:
        return hits
    q = item.question
    for sw in _Q_STOPWORDS:
        q = q.replace(sw, "")
    q = re.sub(r"[、。？?　\s]", "", q)
    return _ngrams(q, 4) if len(q) >= 4 else []


def gate_qa_coverage(lines: list[dict], items: list[QAItem], spec: ActSpec) -> list[str]:
    """Gate D: 桶に入った QA が実際に台詞へ反映されているか。LLM は「全て消化」指示を
    無視して一部の QA を黙って落とすことがあり、幕別の QA 件数だけでは検出できない
    （review_report の "QA N問" は桶サイズであって被引用数ではない）。"""
    if not items:
        return []
    text = "".join(ln.get("ja", "") for ln in lines if ln.get("type") == "talk")
    v: list[str] = []
    for item in items:
        terms = _qa_topic_terms(item, spec)
        if terms and not any(t in text for t in terms):
            v.append(f"QA『{item.question[:24]}』の論点が台詞に反映されていない — 触れること")
    return v


def run_gates(lines: list[dict], spec: ActSpec, mat: Materials, items: list[QAItem]) -> list[str]:
    lines[:] = [_autofix_line(dict(ln)) for ln in lines]
    return (gate_structure(lines, spec)
            + gate_facts(lines, mat.corpus_digits)
            + gate_speech(lines)
            + gate_qa_coverage(lines, items, spec))


# ---------------------------------------------------------------- LLM 生成

def _llm_call(prompt: str) -> str:
    """テスト時に monkeypatch する薄い層。"""
    from interview._llm import call
    return call(prompt, timeout=420)


def _act_material_block(spec: ActSpec, items: list[QAItem], mat: Materials) -> str:
    parts = [f"# 会社: {mat.company} / 職位: {mat.title}"]
    if items:
        qa_lines = []
        for it in items:
            qa_lines.append(f"### Q. {it.question}\n{it.conclusion}")
            qa_lines += [f"- {p}" for p in it.points]
        parts.append("# この幕で使う想定問答（候補者の事実はここからのみ）\n" + "\n".join(qa_lines))
    if spec.no == 1:
        parts.append("# 会社情報（抜粋）\n" + mat.brief_intro)
    if spec.no == 3:
        parts.append("# 志望動機素材\n" + mat.shibou[:1800])
        parts.append("# JD 分析（募集背景・未確認事項）\n" + mat.brief_jd)
    if spec.no == 5:
        parts.append("# JD 原文（ケース設定・JD 確認質問の素材）\n" + mat.raw_jd)
    if spec.no == 6:
        parts.append("# JD 分析（リスク項目）\n" + mat.brief_jd)
    if spec.no == 7:
        parts.append("# 逆質問リスト（この幕の主素材）\n" + mat.brief_gyaku)
        if mat.unconfirmed:
            parts.append("# 未確認事項（{{要確認}} — 面接官の回答は simulated 扱い）\n"
                         + "\n".join(mat.unconfirmed))
    if spec.no == 9:
        parts.append("# 会社情報（引用して締める）\n" + mat.brief_intro[:600])
    return "\n\n".join(parts)


def _act_prompt(spec: ActSpec, items: list[QAItem], mat: Materials,
                feedback: list[str] | None = None) -> str:
    fb = ""
    if feedback:
        fb = ("\n# 前回出力の違反（全て修正すること）\n"
              + "\n".join(f"- {x}" for x in feedback[:15]) + "\n")
    return f"""\
あなたは日本語の面接シミュレーション台本作家です。
PM 中途面接の一幕「{spec.title_ja}」（学習目標: {spec.goal_ja}）の台本を JSON で出力してください。

# 絶対ルール
1. 候補者（candidate）の発言の事実・数字・実績・固有名詞は、下の素材に **出現するものだけ**。
   素材にない数字・経験の捏造は不合格。列挙（2点あります等）の 0〜10 のみ例外。
2. 面接官（interviewer）の発言は JD・会社情報に基づく。素材で裏取りできない内容を
   答えさせる場合は、その行に "simulated": true を付ける。
3. 対話にすること。一問一答の朗読ではなく、面接官の相づち・追問、候補者からの確認・
   反問を入れる。候補者が面接官の回答を受けて更に追問する行には
   "quoteFrom": "<直前の面接官発言からそのまま抜き出した 4 字以上の部分文字列>" を必ず付ける
   （話題を変えた質問に quoteFrom を付けてはいけない）。
   {"この幕は quoteFrom 付き追問チェーン（候補者が質問→面接官が回答→候補者がその回答の言葉を拾って反問→面接官が短く締める、の 4 往復）を最低 1 セット含めること。" if spec.chain_required else ""}
4. 口語: 全て話し言葉のです・ます調。1 行 {MAX_LINE_CHARS} 字以内（長い内容は複数行に分割）。
   候補者の質問文にはクッション言葉（差し支えなければ / 恐れ入りますが 等）か
   依頼表現（〜いただけますか 等）を必ず含める。書面語（貴社等）は使わない。
   markdown 記号・番号書式・{{{{}}}} は出力しない。
5. "inner" は候補者の心の声（斜体表示・常体可）。幕の学習ポイントを 1〜2 行入れてよい。
6. 行数は {MAX_ACT_LINES} 行以内。素材の想定問答が複数ある場合も全て消化すること
   （主軸の 1 問を対話化し、残りはテンポよく Q→A で流してよい）。

# 出力形式（JSON のみ。前後に文章を書かない）
{{"lines": [{{"speaker": "interviewer|candidate|inner", "type": "talk|inner",
  "ja": "台詞", "emotion": "neutral|smile|think|serious|nervous|relieved",
  "quoteFrom": "（追問行のみ）", "simulated": true }}], "done": true}}
{fb}
{_act_material_block(spec, items, mat)}
"""


def _parse_llm_json(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("JSON が見つからない")
    data = json.loads(m.group(0))
    if data.get("done") is not True:
        raise ValueError("done フラグ欠落（切断の疑い）")
    lines = data.get("lines")
    if not isinstance(lines, list):
        raise ValueError("lines が配列でない")
    return lines


def _combined_prompt(specs: list[ActSpec], buckets: dict[int, list[QAItem]],
                     mat: Materials, feedback: str | None = None) -> str:
    """欠けている幕だけを 1 回で一括生成する合併 prompt（規則・素材は 1 度だけ送る）。"""
    act_blocks = []
    for spec in specs:
        chain = ("【この幕は quoteFrom 付き追問チェーン（候補者が質問→面接官が回答→"
                 "候補者がその回答の言葉を拾って反問→面接官が短く締める）を最低 1 セット必須】"
                 if spec.chain_required else "")
        act_blocks.append(f"## 幕{spec.no}「{spec.title_ja}」（学習目標: {spec.goal_ja}）{chain}\n"
                          + _act_material_block(spec, buckets[spec.no], mat))
    fb = f"\n# 前回出力の問題（修正すること）\n{feedback}\n" if feedback else ""
    return f"""\
あなたは日本語の面接シミュレーション台本作家です。
PM 中途面接の台本を、下記の各幕についてまとめて JSON で出力してください。

# 絶対ルール（全幕共通）
1. 候補者（candidate）の発言の事実・数字・実績・固有名詞は、各幕の素材に **出現するものだけ**。
   素材にない数字・経験の捏造は不合格。列挙（2点あります等）の 0〜10 のみ例外。
2. 面接官（interviewer）の発言は JD・会社情報に基づく。素材で裏取りできない内容を
   答えさせる場合は、その行に "simulated": true を付ける。
3. 対話にすること。面接官の相づち・追問、候補者からの確認・反問を入れる。候補者が
   面接官の回答を受けて更に追問する行には "quoteFrom": "<直前の面接官発言からそのまま
   抜き出した 4 字以上の部分文字列>" を必ず付ける（話題を変えた質問には付けない）。
4. 口語: 話し言葉のです・ます調。1 行 {MAX_LINE_CHARS} 字以内（長い内容は複数行に分割）。
   候補者の質問文にはクッション言葉（差し支えなければ 等）か依頼表現（〜いただけますか 等）
   を必ず含める。書面語（貴社等）・markdown 記号・番号書式・{{{{}}}} は出力しない。
5. "inner" は候補者の心の声（常体可）。各幕 1〜2 行入れてよい。
6. 各幕 {MAX_ACT_LINES} 行以内。幕に想定問答が複数ある場合も全て消化する
   （主軸の 1 問を対話化し、残りはテンポよく Q→A で流してよい）。

# 出力形式（JSON のみ。前後に文章を書かない。指定された全幕のキーを必ず含める）
{{"acts": {{"{specs[0].no}": [{{"speaker": "interviewer|candidate|inner", "type": "talk|inner",
  "ja": "台詞", "emotion": "neutral|smile|think|serious|nervous|relieved",
  "quoteFrom": "（追問行のみ）", "simulated": true }}], ...}}, "done": true}}
{fb}
# 各幕の仕様と素材
{chr(10).join(act_blocks)}
"""


def _parse_combined(text: str, expected: set[int]) -> dict[int, list[dict]]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("JSON が見つからない")
    data = json.loads(m.group(0))
    if data.get("done") is not True:
        raise ValueError("done フラグ欠落（切断の疑い）")
    acts = data.get("acts")
    if not isinstance(acts, dict):
        raise ValueError("acts が dict でない")
    out: dict[int, list[dict]] = {}
    for key, lines in acts.items():
        try:
            no = int(key)
        except (TypeError, ValueError):
            continue
        if no in expected and isinstance(lines, list):
            out[no] = lines
    if not out:
        raise ValueError("期待した幕がひとつも含まれない")
    return out


def fallback_act(spec: ActSpec, items: list[QAItem], mat: Materials | None = None) -> list[dict]:
    """降級: QA md をそのまま Q→A で並べた平板版（tts.theater と同形）。

    幕1（ご挨拶）/ 幕9（結び）は QA が分桶されない = items が常に空なので、
    そのままだと降級時に幕ごと無音消失する。最小限の定型文で埋めて必ず可視化する。
    """
    lines: list[dict] = []
    for it in items:
        lines.append({"speaker": "interviewer", "type": "talk", "ja": it.question,
                      "emotion": "neutral"})
        for part in ([it.conclusion] if it.conclusion else []) + it.points:
            lines.append({"speaker": "candidate", "type": "talk", "ja": part,
                          "emotion": "neutral"})
    if lines:
        return lines
    if spec.no == 1:
        company = (mat.company if mat else "") or "本日"
        return [
            {"speaker": "interviewer", "type": "talk", "emotion": "smile",
             "ja": f"本日はお越しいただきありがとうございます。{company}の面接を担当いたします。"},
            {"speaker": "candidate", "type": "talk", "emotion": "neutral",
             "ja": "本日は貴重なお時間をいただき、ありがとうございます。よろしくお願いいたします。"},
        ]
    if spec.no == 9:
        return [
            {"speaker": "candidate", "type": "talk", "emotion": "smile",
             "ja": "本日はお話を伺えて、大変参考になりました。お時間をいただき、ありがとうございました。"},
            {"speaker": "interviewer", "type": "talk", "emotion": "smile",
             "ja": "こちらこそ、ありがとうございました。結果につきましては追ってご連絡いたします。"},
        ]
    return lines


# ---------------------------------------------------------------- 組裝

def _act_hash(spec: ActSpec, items: list[QAItem], mat: Materials) -> str:
    payload = json.dumps({
        "v": PROMPT_VERSION, "act": spec.no,
        "items": [(i.question, i.conclusion, i.points) for i in items],
        "mat": [mat.brief_jd, mat.brief_gyaku, mat.raw_jd, mat.shibou[:1800]],
    }, ensure_ascii=False, sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _load_cache(pack_dir: Path) -> dict:
    p = theater_dir(pack_dir) / "script_gen_cache.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(pack_dir: Path, cache: dict) -> None:
    tdir = theater_dir(pack_dir)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "script_gen_cache.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_audited_drills(pack_dir: Path) -> dict[int, list[dict[str, str]]]:
    """qa_upgrade 済みなら 03_drilldown_qa.md（審稿・事実監査済み）を qid 別に読む。

    無ければ空 dict — 幕6 は従来どおり LLM 生成にフォールバックする。

    採用条件は 04_audit.md の結論が PASS であること。REVIEW（hard findings 残り）の
    深掘りを取り込むと、幕6 だけゲート A/B/C を素通りして未確認事実が台本と音声に
    載る（JD にない事業内容を LLM が前提化するケースが実際に起きた）。
    """
    path = pack_dir / "qa_upgrade" / "03_drilldown_qa.md"
    audit = pack_dir / "qa_upgrade" / "04_audit.md"
    if not path.exists():
        return {}
    if not audit.exists() or "結論: **PASS**" not in audit.read_text(encoding="utf-8"):
        return {}
    return parse_drills(path.read_text(encoding="utf-8"))


def _finalize_lines(spec: ActSpec, lines: list[dict]) -> list[dict]:
    """id 付与（LLM に任せない）・zh ミラー・simulated note 注入。"""
    out = []
    for i, ln in enumerate(lines, start=1):
        entry = {
            "id": f"x{spec.no}-{i:02d}",
            "speaker": ln["speaker"],
            "type": ln.get("type", "talk"),
            "ja": ln["ja"].strip(),
            "zh": ln["ja"].strip(),
            "emotion": ln.get("emotion", "neutral"),
        }
        if ln.get("simulated"):
            entry["simulated"] = True
            entry["note"] = dict(SIMULATED_NOTE)
        out.append(entry)
    return out


def generate(pack_dir: Path, job: dict | None = None, *,
             force: bool = False, log=print) -> dict:
    """9 幕インタラクティブ script.json + review_report.md を生成。

    回傳 {acts, degraded, regens, cached, simulated} 統計。
    LLM 全滅時も例外にせず全幕降級で完走（stage を壊さない）。
    """
    src = _source_path(pack_dir)
    if src is None:
        raise FileNotFoundError(f"{pack_dir.name}: 想定問答 md が見つかりません（先に qa stage）")
    md_text = src.read_text(encoding="utf-8")
    items = parse_standard_qa(md_text)
    if not items:
        raise ValueError(f"{src.name} は標準一問一答形式ではありません — qa stage を再実行してください")

    mat = collect_materials(pack_dir, job)
    buckets = bucket_qa(items)
    item_qid = {id(item): idx + 1 for idx, item in enumerate(items)}
    audited_drills = _load_audited_drills(pack_dir)
    # 幕6（深掘り）は qa_upgrade で審稿・事実監査済みの深掘り回答があれば、それを
    # そのまま台詞化する — LLM に作り直させると、審査済みの厳密な表現を崩しかねない。
    act6_drilled = any(item_qid[id(it)] in audited_drills for it in buckets[6])
    cache = {} if force else _load_cache(pack_dir)
    new_cache: dict = {}
    stats = {"acts": 0, "degraded": [], "regens": 0, "cached": 0,
             "simulated": 0, "llm_calls": 0}
    report: list[str] = []
    acts_out: list[dict] = []
    llm_dead = False

    # 1) キャッシュ命中幕を回収し、欠けている幕を列挙（幕6が審査済み深掘りで
    #    賄える場合は、そもそもキャッシュ/LLM経路に乗せない）
    act_lines: dict[int, list[dict]] = {}
    status_map: dict[int, str] = {}
    needed: list[ActSpec] = []
    for spec in SKELETON:
        if spec.no == 6 and act6_drilled:
            continue
        h = _act_hash(spec, buckets[spec.no], mat)
        cached = cache.get(str(spec.no))
        if cached and cached.get("hash") == h:
            act_lines[spec.no] = cached["lines"]
            status_map[spec.no] = "cache"
            stats["cached"] += 1
        else:
            needed.append(spec)

    # 2) 欠けている幕だけを 1 回の合併呼び出しで一括生成（パース失敗のみ 1 回リトライ）
    if needed:
        feedback: str | None = None
        for _ in range(2):
            try:
                stats["llm_calls"] += 1
                raw = _llm_call(_combined_prompt(needed, buckets, mat, feedback))
                for no, lines in _parse_combined(raw, {s.no for s in needed}).items():
                    act_lines[no] = lines
                    status_map[no] = "gen"
                break
            except RuntimeError as exc:      # 全 provider 失敗 → 全幕降級
                log(f"  ✗ LLM 不可 — {exc}")
                llm_dead = True
                break
            except (ValueError, json.JSONDecodeError) as exc:
                feedback = f"出力パース失敗: {exc}"
                stats["regens"] += 1

    # 3) ゲート検収 — 不合格/欠落幕のみ個別修復（幕あたり ≤MAX_REGEN 回）
    for spec in needed:
        if llm_dead:
            break
        lines = act_lines.get(spec.no)
        violations = run_gates(lines, spec, mat, buckets[spec.no]) if lines is not None else ["合併出力に幕が欠落"]
        tries = 0
        while violations and tries < MAX_REGEN:
            tries += 1
            stats["regens"] += 1
            log(f"  ⟳ 幕{spec.no}: ゲート違反 {len(violations)} 件 → 個別修復 {tries}")
            try:
                stats["llm_calls"] += 1
                lines = _parse_llm_json(_llm_call(_act_prompt(spec, buckets[spec.no], mat, violations)))
                violations = run_gates(lines, spec, mat, buckets[spec.no])
            except RuntimeError as exc:
                log(f"  ✗ 幕{spec.no}: LLM 不可 — {exc}")
                llm_dead = True
                break
            except (ValueError, json.JSONDecodeError) as exc:
                violations = [f"出力パース失敗: {exc}"]
        if lines is not None and not violations:
            act_lines[spec.no] = lines
            if tries:
                status_map[spec.no] = f"repair({tries})"
        else:
            act_lines.pop(spec.no, None)   # → 降級へ

    # 3.5) 幕6（深掘り） — 審査済み深掘り回答をそのまま台詞化（LLM 呼び出しなし、
    #      ゲート不要 = qa_upgrade の事実監査を再度信用する）
    if act6_drilled:
        lines: list[dict] = []
        for it in buckets[6]:
            for drill in audited_drills.get(item_qid[id(it)], []):
                lines.append({"speaker": "interviewer", "type": "talk", "emotion": "serious",
                              "ja": drill["question"]})
                lines.append({"speaker": "candidate", "type": "talk", "emotion": "neutral",
                              "ja": drill["answer"]})
        act_lines[6] = lines
        status_map[6] = "audited"

    # 4) 組裝（降級幕は平板版 — md 由来なのでゲート不要）
    for spec in SKELETON:
        act_items = buckets[spec.no]
        lines = act_lines.get(spec.no)
        status = status_map.get(spec.no, "")
        if lines is None:
            lines = fallback_act(spec, act_items, mat)
            stats["degraded"].append(spec.no)
            status = "degraded"
        if not lines:      # 素材ゼロの幕が降級した場合はスキップ
            report.append(f"- 幕{spec.no} {spec.title_ja}: 素材なし・スキップ")
            continue

        final = _finalize_lines(spec, lines)
        stats["simulated"] += sum(1 for ln in final if ln.get("simulated"))
        # 降級幕はキャッシュしない — 平板版を固定すると次回以降ずっと再生成されず、
        # しかも status が "cache" になって report 上は降級が消える（誤報）
        if status != "degraded":
            new_cache[str(spec.no)] = {"hash": _act_hash(spec, act_items, mat), "lines": lines}
        act_out = {
            "id": spec.no,
            "titleJa": spec.title_ja, "titleZh": spec.title_zh,
            "goalJa": spec.goal_ja, "goalZh": spec.goal_zh,
            "lines": final,
        }
        if spec.framework:
            act_out["framework"] = {
                "titleJa": "チェックポイント", "titleZh": "檢查點",
                "items": [{"ja": ja, "zh": zh} for ja, zh in spec.framework],
            }
        acts_out.append(act_out)
        stats["acts"] += 1
        report.append(f"- 幕{spec.no} {spec.title_ja}: {status}, {len(final)} 句, "
                      f"QA {len(act_items)} 問")

    _save_cache(pack_dir, new_cache)

    overall = (f"{mat.company} 面接シミュレーション（9 幕・全{len(items)}問消化）。"
               f"候補者の事実は面接パック素材のみ（機械検証済み）。"
               f"面接官の simulated 行は練習用の模擬回答です。")
    script = {
        "id": f"pack-{pack_dir.name}",
        "titleJa": f"{mat.company} 模擬面接（対話版）",
        "titleZh": f"{mat.company} 模擬面試（對話版）",
        "interviewerJa": "面接官", "interviewerZh": "面試官",
        "sample": False,
        "noFavor": True,
        "acts": acts_out,
        "report": {"aspects": [], "overallJa": overall, "overallZh": overall},
        "_meta": {
            "version": 1,
            "interactive": True,
            "dirname": pack_dir.name,
            "company": mat.company,
            "jobTitle": mat.title,
            "questions": len(items),
            "sourceHash": sha256(md_text.encode("utf-8")).hexdigest(),
            "sourceFile": src.name,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "degradedActs": stats["degraded"],
        },
    }
    tdir = theater_dir(pack_dir)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")

    sim_lines = [f"  - {ln['id']}: {ln['ja'][:60]}"
                 for a in acts_out for ln in a["lines"] if ln.get("simulated")]
    report_md = "\n".join([
        f"# 対話台本 生成レポート — {mat.company}",
        f"生成: {datetime.now().isoformat(timespec='seconds')} / prompt v{PROMPT_VERSION}",
        "",
        "## 幕別結果", *report, "",
        f"## 降級幕（平板版のまま）: {stats['degraded'] or 'なし'}",
        f"LLM 呼び出し: {stats['llm_calls']} 回 / 修復: {stats['regens']} / "
        f"キャッシュ命中: {stats['cached']}",
        "",
        f"## 模擬回答（{len(sim_lines)} 行 — 実際の面接官の回答を優先）",
        *(sim_lines or ["  なし"]),
        "",
        "voice stage の前にこのレポートを一読してください。",
    ])
    (tdir / "review_report.md").write_text(report_md, encoding="utf-8")
    log(f"  ✓ theater/script.json（対話版 {stats['acts']} 幕"
        + (f"、降級 {stats['degraded']}" if stats["degraded"] else "")
        + f"）+ review_report.md")
    return stats


# ---------------------------------------------------------------- CLI

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="面接シアター対話台本生成（9 幕 + 機械ゲート）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--job-id", type=int)
    g.add_argument("--pack")
    p.add_argument("--force", action="store_true", help="キャッシュ無視で全幕再生成")
    args = p.parse_args()

    if args.pack:
        pack_dir = PREP_DIR / args.pack
        job = None
    else:
        from tts.theater import find_pack_dir
        pack_dir = find_pack_dir(args.job_id)
        from tracker.db import connect
        with connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
        job = dict(row) if row else None
    if not pack_dir.is_dir():
        sys.exit(f"{pack_dir} が存在しません")
    stats = generate(pack_dir, job, force=args.force)
    if stats["degraded"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
