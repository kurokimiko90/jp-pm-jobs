"""素材入口 — prompt に入るものは全部ここを通す（PII / 取引先遮蔽の単一関門）。

growth/context.py と同じ二段構え:
  tools.deid.build_deid_profile()  白名單去識別化
  tools.redact.redact()            取引先ブランド遮蔽

加えて proposal 固有の `evidence_corpus()` — 「本人が語ってよい数字」の権威素材。
Gate B（数字錨定）はこの文字列に無い数字を捏造とみなす。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.deid import build_deid_profile
from tools.gap_facts import match_evidence
from tools.redact import redact

ROOT = Path(__file__).parent.parent
CORE_DIR = ROOT / "interview" / "question-bank" / "core"
JD_MAX_CHARS = 6000
FACTS_MAX_CHARS = 4000

# raw_jd は勤務地/給与/社会保険など、提案には要らない項目が大半を占める
# （実測: 全体の 7〜8 割）。prompt に入れるのは「何をする仕事か」「何を
# 求めているか」の 2 節だけに絞る。見出しはソースにより表記揺れがあるので
# 別名を吸収する。マッチしなければ（未知の書式）安全側で全文へ fallback。
_JD_HEADER_RE = re.compile(r"^(?:##\s+(.+?)|\*\*([^*]+)\*\*)\s*$")
_JD_SECTION_ALIASES: dict[str, str] = {
    "職務内容": "職務内容", "仕事内容": "職務内容",
    "仕事の内容": "職務内容", "業務内容": "職務内容",
    "職務内容詳細": "職務内容", "主な職務内容": "職務内容",
    "業務詳細": "職務内容", "募集要項": "職務内容", "募集背景": "職務内容",
    "求める能力・経験": "求める能力・経験", "必要な経験・能力等": "求める能力・経験",
    "求める人材": "求める能力・経験", "求める人物像": "求める能力・経験",
    "応募資格": "求める能力・経験", "必須": "求める能力・経験",
    "歓迎条件": "求める能力・経験", "応募要件・スキル": "求める能力・経験",
    # CareerCross / LinkedIn 系の英語求人
    "job description": "職務内容", "job description summary": "職務内容",
    "key responsibilities": "職務内容", "responsibilities": "職務内容",
    "about the role": "職務内容", "position overview": "職務内容",
    "the opportunity": "職務内容",
    "qualifications": "求める能力・経験", "required qualifications": "求める能力・経験",
    "preferred qualifications": "求める能力・経験",
    "minimum qualifications": "求める能力・経験", "requirements": "求める能力・経験",
    "your background": "求める能力・経験",
}
_JD_SECTION_ORDER = ["職務内容", "求める能力・経験"]

# 対象節を終わらせる「本物の」見出し。JAC 系ソースは要件を 1 行ずつ
# `**要件**` で太字強調するため、太字＝見出しと決め打ちすると対象節の
# 本文が全滅する（実測: 求める能力・経験が空になった）。ここに無い太字行は
# 対象節の中にいる間は本文として扱う。`## ` は常に真の見出し（indeed_jp
# 由来の規約）。
_JD_STOP_HEADERS = {
    "勤務地", "勤務時間", "労働時間", "勤務形態", "勤務地所在地", "勤務地備考",
    "給与", "年収", "想定年収", "通勤手当", "試用期間", "社会保険", "受動喫煙対策",
    "応募後の流れ", "企業情報", "個人情報の取扱いについて", "企業名", "求人名",
    "募集職種", "学歴・資格", "学歴", "企業・求人の特色", "就業時間", "休日",
    "休日休暇", "選考・企業概要", "勤務条件", "雇用形態", "雇用期間の定め",
    "職業紹介事業者", "更新日", "代表電話番号", "賃金形態", "その他制度",
    "配属先情報", "本社所在地", "業種", "代表者名", "アクセス", "その他",
    "勤務時間・曜日", "アピールポイント",
}


def _extract_jd_sections(raw: str) -> str:
    """見出し行（`## x` / `**x**`）で分割し、対象見出しの本文だけ拾う。"""
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if current and text:
            bodies.setdefault(current, []).append(text)
        buf.clear()

    for line in raw.splitlines():
        m = _JD_HEADER_RE.match(line.strip())
        if m:
            is_md = m.group(1) is not None
            label = (m.group(1) or m.group(2) or "").strip()
            canonical = (_JD_SECTION_ALIASES.get(label)
                         or _JD_SECTION_ALIASES.get(label.lower()))
            if canonical:
                flush()
                current = canonical
                continue
            if is_md or label in _JD_STOP_HEADERS:
                flush()
                current = None
                continue
            # 未知の太字行: 対象節の中にいれば強調された条件文として本文に含める
            if current:
                buf.append(line)
            continue
        if current:
            buf.append(line)
    flush()

    if not bodies:
        return ""
    return "\n\n".join(
        f"## {key}\n" + "\n\n".join(bodies[key])
        for key in _JD_SECTION_ORDER if key in bodies
    )


def jd_text(job: dict) -> str:
    raw = job.get("raw_jd") or ""
    text = _extract_jd_sections(raw) or raw
    cleaned, _ = redact(text[:JD_MAX_CHARS])
    return cleaned


def job_header(job: dict) -> str:
    salary = ""
    if job.get("salary_min"):
        salary = f"{job['salary_min']}万〜{job.get('salary_max') or '?'}万円"
    return (
        f"- 会社: {job.get('company') or '{{要確認}}'}\n"
        f"- 職位: {job.get('title')}\n"
        f"- 想定年収: {salary or '不明'}\n"
        f"- 企業タイプ: {job.get('tier') or 'unknown'} / "
        f"従業員数: {job.get('employee_count') or '不明'}\n"
        f"- 職種分類: {job.get('job_type') or '不明'}"
    )


def profile_text(compact: bool = True) -> str:
    return build_deid_profile(compact=compact)


def gap_requirements(job: dict) -> list[str]:
    """gap 分析が抽出した要件リスト（能力カードの網羅対象）。"""
    raw = job.get("gap_analysis")
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    reqs = data.get("requirements") or []
    out: list[str] = []
    for r in reqs:
        if isinstance(r, dict):
            r = r.get("requirement") or r.get("name") or r.get("item") or ""
        if isinstance(r, str) and r.strip():
            cleaned, _ = redact(r.strip())
            out.append(cleaned)
    return out[:12]


def gap_block(job: dict) -> str:
    try:
        return match_evidence(job) or ""
    except Exception:
        return ""


def core_answers() -> str:
    """正典 49 問の既存回答 — 本人が実際に語っている事実の集積。

    Gate B の権威素材にも、提案が引く実績エピソードの供給源にもなる。
    """
    if not CORE_DIR.exists():
        return ""
    chunks = []
    for path in sorted(CORE_DIR.glob("*.md")):
        chunks.append(path.read_text(encoding="utf-8"))
    text = "\n\n".join(chunks)
    cleaned, _ = redact(text)
    return cleaned


def evidence_corpus(job: dict, facts: str = "", research: str = "") -> str:
    """数字錨定（Gate B）の権威素材。ここに無い数字は捏造とみなす。

    候補者側の実績数値（profile / 正典回答 / gap 素材）と、会社側の調査済み事実。
    JD 本文も含める — JD に書かれた数字を引用するのは捏造ではない。
    研究層が取ってきた官網の原文も同じ扱い（公開されている数字の引用は捏造ではない）。
    """
    parts = [profile_text(compact=False), core_answers(), gap_block(job),
             jd_text(job), facts or "", research or ""]
    return "\n".join(p for p in parts if p)


def facts_text(facts: str) -> str:
    if not facts:
        return ""
    cleaned, _ = redact(facts[:FACTS_MAX_CHARS])
    return cleaned


def build(job: dict, facts: str = "", *, with_profile: bool = True) -> str:
    """全 stage 共用の基礎コンテキスト。

    素材は `<素材 …>` タグで囲む。Markdown の見出しで区切っていた頃は、
    JD 原文（`## 職務内容` 等の見出しを含む）と prompt 側の `# 出力` が同じ
    階層に並び、**どこまでが読む対象でどこからが指示か**が曖昧だった。
    JD も取得原文も外部から来た文字列なので、ここは境界であると同時に
    指示注入の防波堤でもある（`prompts.SOURCE_ISOLATION_RULE` が対で効く）。
    """
    parts = [
        "<素材 種別=\"求人票の基本情報\">",
        job_header(job),
        "</素材>",
        "",
        "<素材 種別=\"JD 抜粋\" 注記=\"職務内容・求める能力/経験。"
        "この範囲外の求人内容を引用しない\">",
        jd_text(job),
        "</素材>",
    ]
    f = facts_text(facts)
    if f:
        parts += ["", "<素材 種別=\"確認済みの会社事実\" "
                  "注記=\"調査済み・製品への言及はここからのみ断定可\">", f, "</素材>"]
    else:
        parts += ["", "<素材 種別=\"確認済みの会社事実\">",
                  "（未提供 — 製品の内部事情はすべて仮説として書くこと）", "</素材>"]
    if with_profile:
        parts += ["", "<素材 種別=\"応募者プロフィール\" "
                  "注記=\"去識別化済み・職業情報のみ\">",
                  "```yaml", profile_text(), "```", "</素材>"]
    return "\n".join(parts)
