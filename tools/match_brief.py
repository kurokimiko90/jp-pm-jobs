"""JD 適配分析 — 零 LLM token，純規則引擎。

gap_analysis + raw_jd + 定制履歷 → 05_match_brief.md
blocker/mitigable 判斷 + 關鍵詞覆蓋率 + Go/No-Go verdict。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.locale import text as locale_text

BLOCKER_SIGNALS = [
    (r"(\d+)\+?\s*years?\s.*(?:engineer|development|data science|platform)", "years_eng"),
    (r"native.*(english|日本語|japanese)", "native_lang"),
    (r"(?:relocat|移住|渡航|Bangkok|海外赴任)", "relocation"),
    (r"(?:PhD|博士|Ph\.D)", "phd"),
    (r"(?:security clearance|機密)", "clearance"),
]

MITIGABLE_PATTERNS = [
    r"(?:ML|machine learning|deep learning)",
    r"(?:data science|data engineer)",
    r"(?:kubernetes|k8s|docker|terraform)",
    r"(?:english|英語|TOEIC)",
]

TECH_KEYWORDS_RE = re.compile(
    r"\b(?:Python|TypeScript|JavaScript|Java|Go|Rust|C\+\+|Kotlin|Swift|Ruby|Scala|"
    r"React|Vue|Angular|Next\.js|Node\.js|Express|FastAPI|Django|Flask|Spring|"
    r"AWS|GCP|Azure|Docker|Kubernetes|Terraform|"
    r"PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|"
    r"ML|machine learning|deep learning|NLP|LLM|RAG|fine-?tuning|"
    r"model serving|inference|training|evaluation|evals|"
    r"A/B test|CI/CD|API|REST|GraphQL|gRPC|"
    r"Playwright|Selenium|Spark|Kafka|Airflow|dbt|"
    r"Figma|Jira|Confluence|Notion|Linear|"
    r"PRD|OKR|KPI|SLO|SLA|"
    r"prompt|agent|orchestrat|pipeline|workflow|"
    r"Fintech|POS|決済|ポイント|リコンサイル|reconcil)\b",
    re.IGNORECASE,
)


def _extract_keywords(text: str) -> set[str]:
    return {m.lower() for m in TECH_KEYWORDS_RE.findall(text)}


def _classify_gap(gap_text: str) -> str:
    """blocker / mitigable を判定。"""
    low = gap_text.lower()
    for pattern, _ in BLOCKER_SIGNALS:
        if re.search(pattern, low):
            if "pm" in low or "product" in low:
                return "mitigable"
            return "blocker"
    return "mitigable"


def generate(job: dict, pack_dir: Path) -> str | None:
    """05_match_brief.md を生成して返す。gap_analysis がなければ None。"""
    raw_gap = job.get("gap_analysis")
    if not raw_gap:
        return None
    try:
        gap = json.loads(raw_gap) if isinstance(raw_gap, str) else raw_gap
    except Exception:
        return None

    rec = gap.get("recommend_score", 0)
    matched = gap.get("matched", [])
    gaps = gap.get("gaps", [])
    requirements = gap.get("requirements", [])
    reason = gap.get("recommend_reason", "")

    # 定制版があればそれ、無ければ投遞に実際使う完成版でキーワード照合する
    from tools import resume_assets
    resume_path = pack_dir / "04_shokumu.html"
    if not resume_path.exists():
        resume_path = resume_assets.shokumu_html()
    resume_text = ""
    if resume_path.exists():
        resume_text = re.sub(r"<[^>]+>", " ", resume_path.read_text(encoding="utf-8"))

    jd_text = job.get("raw_jd", "")

    jd_kw = _extract_keywords(jd_text)
    resume_kw = _extract_keywords(resume_text)
    covered = jd_kw & resume_kw
    missed = jd_kw - resume_kw
    coverage_pct = round(len(covered) / len(jd_kw) * 100) if jd_kw else 0

    classified_gaps: list[dict] = []
    n_blockers = 0
    for g in gaps:
        severity = _classify_gap(g)
        if severity == "blocker":
            n_blockers += 1
        classified_gaps.append({"text": g, "severity": severity})

    if n_blockers >= 2:
        verdict = "No-Go"
    elif n_blockers == 1 and rec < 50:
        verdict = "No-Go"
    elif n_blockers == 1 or rec < 60:
        verdict = "Conditional Go"
    elif rec >= 75:
        verdict = "Go"
    else:
        verdict = "Conditional Go"

    verdict_icon = {"Go": "🟢", "Conditional Go": "🟡", "No-Go": "🔴"}[verdict]

    lines: list[str] = []
    company = job.get("company", "?")
    title = (job.get("title") or "")[:50]
    lines.append(locale_text("match_brief_title", company=company, title=title))
    lines.append(f"\n{locale_text('match_brief_meta', job_id=job['id'], score=job.get('score'), rec=rec)}")
    lines.append(f"\n{locale_text('match_brief_verdict', icon=verdict_icon, verdict=verdict)}\n")
    if reason:
        lines.append(f"> {reason[:200]}")

    lines.append(f"\n{locale_text('match_brief_section')}\n")
    for m in matched:
        lines.append(f"- ✅ {m[:140]}")
    for g in classified_gaps:
        icon = "❌" if g["severity"] == "blocker" else "⚠️"
        tag = f"**{g['severity'].upper()}**"
        lines.append(f"- {icon} [{tag}] {g['text'][:140]}")

    lines.append(f"\n{locale_text('match_brief_coverage', coverage=coverage_pct)}\n")
    if covered:
        lines.append(locale_text("match_brief_covered", count=len(covered), items=', '.join(sorted(covered))))
    if missed:
        lines.append(f"\n{locale_text('match_brief_uncovered', count=len(missed), items=', '.join(sorted(missed)))}")

    lines.append("")

    content = "\n".join(lines)
    out = pack_dir / "05_match_brief.md"
    out.write_text(content, encoding="utf-8")
    return content
