"""JD 評分器 — 6 維加權打分，對標 2026 日本 AI PM 市場。

評分維度（權重）:
  market_keywords (30)  : 2026 市場關鍵字命中率（LLM/Multi-tenant/FinOps/Hallucination 等）
  tier_preference (20)  : 企業 tier 偏好（ai_startup=100 / mega_venture=80 / sier=50）
  tech_overlap    (20)  : JD 技術詞與候選人 38 技術實體重合
  salary_fit      (15)  : 薪資 vs 目標帶
  remote_visa     (10)  : 遠端 +5 / Visa sponsor +5
  domain          ( 5)  : Fintech / SaaS / AI 領域加分

最終 0-100 寫入 jobs.score；6 維度明細以 JSON 寫入 jobs.score_breakdown。

用法:
    python3 -m analyzer.jd_scorer --all
    python3 -m analyzer.jd_scorer --job-id 123
    python3 -m analyzer.jd_scorer --report          # 只重新生成 Markdown 報告
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import yaml

from analyzer.role_filter import is_engineering_only, is_pm_by_content
from tools.app_config import get as _cfg
from tools.salary_parser import parse_salary
from tracker.db import all_jobs, connect, top_scored, update_score

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"
PROFILE_PATH = ROOT / "data" / "cognitive_profile.yaml"
TECH_PATH = ROOT / "data" / "tech_footprint.yaml"

# 所有評分參數可由 config/scoring.yaml 覆蓋（缺檔 = 用以下預設值，行為不變）。
# 範本與說明見 config/scoring.yaml.example。

# ── 日本軌權重（合 100）─ indeed 等本土來源 ──────────
# 年収優先（2026-06）：salary_fit 15→60，主軸壓到年収契合度。
# 其餘 5 維壓到 40（market 12 / role_fit 12 / tier 6 / tech 6 / domain 4）。
# remote_visa 歸 0（不再獨立計分），分量併入年収主軸。
WEIGHTS = _cfg("scoring", "weights", {
    "market_keywords": 22,
    "tier_preference": 6,
    "tech_overlap": 8,
    "salary_fit": 35,
    "remote_visa": 0,
    "domain": 5,
    "role_fit": 24,
})

# 純工程職（無 PM 字樣）懲罰係數：PM 求職 pipeline 不該把工程職當精選。
ENG_ONLY_PENALTY = _cfg("scoring", "eng_only_penalty", 0.6)
# JD 內容閘：標題非 PM 且 JD 無足夠 PM 訊號（如 Bizreach qk 全文搜尋撈回的
# 営業/コンサル/法務/人事 等）→ 重壓分數使其沉底，不污染 PM 精選。
PM_GATE_PENALTY = _cfg("scoring", "pm_gate_penalty", 0.15)

# ── 海外軌權重（合 100）─ career-ops ATS 來源（%-api）──────────────
# 去 jp 化：移除 salary_fit（日本萬円帶無意義）、remote_visa（對日簽證無意義），
# 把權重轉給 role_fit（真 PM 職銜契合）與 domain。tier 由公司名自動推斷而非分類器。
# 年収優先（2026-06）：海外無薪資欄位，60% 由 role_fit 代位（職銜契合度當主軸）。
OVERSEAS_WEIGHTS = _cfg("scoring", "overseas_weights", {
    "market_keywords": 20,
    "tier_preference": 10,
    "tech_overlap": 6,
    "role_fit": 60,
    "domain": 4,
})

# 海外職 tier 推斷：career-ops portals.yml 全是精選 AI 公司，預設 mega_venture(80)；
# 前沿 AI lab / 明星新創拉到 ai_startup(100)。company 名小寫子字串比對。
FRONTIER_AI_COMPANIES = set(_cfg("scoring", "frontier_ai_companies", [
    "anthropic", "openai", "mistral", "cohere", "hugging face", "perplexity",
    "runway", "stability", "elevenlabs", "deepl", "aleph alpha", "black forest",
    "glean", "sierra", "decagon", "lovable", "langchain", "pinecone", "synthesia",
    "isomorphic", "wayve", "helsing", "physicsx", "cradle", "causaly", "lakera",
    "hightouch", "supabase", "vercel", "clay", "amplemarket", "legora",
]))

# role_fit：title 是否真 PM 核心職（而非 PMM / 分析 / 設計 / 工程）
ROLE_CORE_PM = _cfg("scoring", "role_core_pm", [
    "product manager", "product owner", "product lead", "head of product",
    "director of product", "director, product", "vp product", "vp of product",
    "group product manager", "principal product", "lead product manager",
    "staff product manager", "technical product manager", "ai product manager",
    "platform product manager", "growth product manager", "chief product",
    "プロダクトマネージャー", "プロダクトマネジャー", "プロダクトオーナー", "プロダクト責任者",
])

# Project / Program Manager / TPM / PMO：候選人定位是 Product Manager，
# 這類偏專案/工程協調職銜契合度較低，降分至鄰接職水平（非剔除）。
ROLE_DEMOTE = _cfg("scoring", "role_demote", [
    "technical program manager", "program manager", "project manager",
    "プロジェクトマネージャー", "プロジェクトマネジャー", "プロジェクトマネジメント",
])
ROLE_ADJACENT = _cfg("scoring", "role_adjacent", [
    "product marketing", "product analyst", "product designer", "product operations",
    "コンサルタント", "コンサル", "consultant", "consulting", "アドバイザー", "advisor",
])

# 2026 市場關鍵字（命中率 → 0-100）
MARKET_KEYWORDS = _cfg("scoring", "market_keywords", [
    "llm", "large language model", "生成 ai", "生成ai", "genai",
    "multi-tenant", "マルチテナント",
    "finops", "コスト最適化", "token コスト", "token cost",
    "hallucination", "ハルシネーション",
    "agentic", "ai エージェント", "ai agent",
    "rag", "retrieval augmented",
    "fine-tuning", "ファインチューニング",
    "prompt", "プロンプト",
    "claude", "gemini", "gpt-4", "gpt-5", "anthropic", "openai",
    "mcp", "model context protocol",
    "product architect",
    "開発生産性",
    "dx推進", "dx", "デジタルトランスフォーメーション",
    "要件定義", "上流工程", "システム企画",
    "erp", "基幹システム", "si", "sier",
])

TIER_PREFERENCE = _cfg("scoring", "tier_preference", {
    "ai_startup": 100,
    "mega_venture": 80,
    "traditional_sier": 60,
    "unknown": 40,
})

DOMAIN_KEYWORDS = _cfg("scoring", "domain_keywords", {
    "fintech": ["fintech", "決済", "payment", "金融", "銀行", "保険"],
    "saas": ["saas", "b2b", "enterprise"],
    "ai": ["ai", "llm", "ml", "機械学習", "生成 ai"],
})

# 候選人目標薪資帶（萬円）— cognitive_profile.yaml 優先，
# 缺檔時退 config/scoring.yaml 的 target_salary，再退 900-1800。
def _load_salary_target() -> tuple[int, int]:
    t = _cfg("scoring", "target_salary", {}) or {}
    dmin, dmax = int(t.get("min", 900)), int(t.get("max", 1800))
    if not PROFILE_PATH.exists():
        return dmin, dmax
    data = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}
    return (
        int(data.get("target_salary_min", dmin)),
        int(data.get("target_salary_max", dmax)),
    )


TARGET_SALARY_MIN, TARGET_SALARY_MAX = _load_salary_target()


@dataclass
class ScoreBreakdown:
    market_keywords: float = 0.0
    tier_preference: float = 0.0
    tech_overlap: float = 0.0
    salary_fit: float = 0.0
    remote_visa: float = 0.0
    domain: float = 0.0
    role_fit: float = 0.0
    salary_imputed: bool = False   # True = DB 缺薪資，靠 raw_jd 二次抽取
    salary_missing: bool = False   # True = 二次抽取仍無，給低分 30
    matched_keywords: list[str] = field(default_factory=list)
    matched_tech: list[str] = field(default_factory=list)
    matched_domain: list[str] = field(default_factory=list)


def load_candidate_tech() -> set[str]:
    """從 tech_footprint.yaml 抽出全部技術實體名稱。"""
    if not TECH_PATH.exists():
        return set()
    data = yaml.safe_load(TECH_PATH.read_text(encoding="utf-8"))
    tech: set[str] = set()
    by_cat = data.get("by_category", {}) if isinstance(data, dict) else {}
    for cat in by_cat.values():
        if isinstance(cat, dict):
            for name in (cat.get("top_5") or {}).keys():
                tech.add(name.lower().rstrip("?").strip())
    return tech


CANDIDATE_TECH = load_candidate_tech()


def _normalize(text: str) -> str:
    """全角英数字・記号を半角に正規化（リクルートエージェント等の全角変換対策）。"""
    result = []
    for ch in text:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:  # 全角 ! ~ → 半角
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def _blob(row) -> str:
    parts = [row["title"] or "", row["company"] or "", row["raw_jd"] or ""]
    return _normalize(" ".join(parts)).lower()


def score_market_keywords(blob: str, breakdown: ScoreBreakdown) -> float:
    hits = [kw for kw in MARKET_KEYWORDS if kw in blob]
    breakdown.matched_keywords = hits
    # 命中 4 個關鍵字以上拿滿分（緩和：1 命中 = 25%）
    return min(len(hits) / 4.0, 1.0) * 100


def score_tier_preference(tier: str | None, breakdown: ScoreBreakdown) -> float:
    return float(TIER_PREFERENCE.get(tier or "unknown", 40))


def score_tech_overlap(blob: str, breakdown: ScoreBreakdown) -> float:
    if not CANDIDATE_TECH:
        return 50.0  # 無資料時給中位數，不懲罰
    matched = [t for t in CANDIDATE_TECH if t and t in blob]
    breakdown.matched_tech = matched[:10]
    # 命中 5 項以上拿滿分
    return min(len(matched) / 5.0, 1.0) * 100


def score_salary_fit(row, breakdown: ScoreBreakdown) -> float:
    smin = row["salary_min"]
    smax = row["salary_max"]
    # 年収 60% 權重下「缺薪資=假中性分」會主導結果 →
    # 先用 salary_parser 從 raw_jd 二次抽取，仍缺則給低分 30（不再給中性 50）。
    if smin is None and smax is None:
        smin, smax = parse_salary(row["raw_jd"] or "")
        if smin is None and smax is None:
            breakdown.salary_missing = True
            return 50.0
        breakdown.salary_imputed = True
    s = smax or smin or 0
    if s >= TARGET_SALARY_MAX:
        base = 100.0
    elif s >= TARGET_SALARY_MIN:
        base = 55.0 + 45.0 * (s - TARGET_SALARY_MIN) / (TARGET_SALARY_MAX - TARGET_SALARY_MIN)
    else:
        soft_floor = TARGET_SALARY_MIN * 0.8  # 720万
        if s <= soft_floor:
            base = 25.0
        else:
            base = 25.0 + 30.0 * (s - soft_floor) / (TARGET_SALARY_MIN - soft_floor)

    # smin ペナルティ：最低年収がターゲット下限を下回るほど減点
    # 650万 → ×0.72、720万 → ×0.80、900万以上 → ×1.0
    if smin is not None and smin < TARGET_SALARY_MIN:
        smin_factor = max(0.5, smin / TARGET_SALARY_MIN)
        base *= smin_factor

    return base


def score_remote_visa(row, breakdown: ScoreBreakdown) -> float:
    base = 50.0
    if (row["remote"] or "").lower() in {"full", "remote", "リモート"}:
        base += 25.0
    elif (row["remote"] or "").lower() in {"hybrid", "ハイブリッド"}:
        base += 10.0
    if row["sponsor_visa"]:
        base += 25.0
    return min(base, 100.0)


def score_domain(blob: str, breakdown: ScoreBreakdown) -> float:
    matched: list[str] = []
    for dom, kws in DOMAIN_KEYWORDS.items():
        if any(k in blob for k in kws):
            matched.append(dom)
    breakdown.matched_domain = matched
    return min(len(matched) / 2.0, 1.0) * 100


_ROLE_PM_RE = re.compile(
    r"(?<![a-z])pd?m(?![a-z])"  # pm / pdm（ASCII境界）
    # プロダクトシニアマネージャー 等中間有修飾詞的職銜；マネージャ／マネジャー 等長音變體
    r"|プロダクト.{0,10}マネー?ジャー?",
    re.IGNORECASE,
)

# TPM / PjM / PMO / プロジェクトマネージャー（含修飾詞）—— 降分而非剔除
_ROLE_DEMOTE_RE = re.compile(
    r"(?<![a-z])tpm(?![a-z])"
    r"|(?<![a-z])pjm(?![a-z])"
    r"|(?<![a-z])pmo(?![a-z])"
    r"|(?:プロジェクト|プログラム).{0,10}マネー?ジャー?",
    re.IGNORECASE,
)
ROLE_DEMOTE_SCORE = float(_cfg("scoring", "role_demote_score", 50.0))


def score_role_fit(title: str, breakdown: ScoreBreakdown) -> float:
    """真 PM 核心職銜 → 100；Project/Program/TPM/PMO → 50；鄰接職（PMM / 分析 / 設計）→ 50；含 product 其他 → 70；皆無 → 30。
    PM + コンサル 混合職銜 → 70（PM 主軸但非純 PM）。"""
    t = _normalize(title or "").lower()
    # Project / Program Manager / TPM / PMO 先攔截降分（避免被 product manager 等核心職誤判為 100）
    if any(k in t for k in ROLE_DEMOTE) or bool(_ROLE_DEMOTE_RE.search(t)):
        return ROLE_DEMOTE_SCORE
    if any(k in t for k in ROLE_CORE_PM) or bool(_ROLE_PM_RE.search(t)):
        # PM 職銜命中，但同時含コンサル詞 → 混合職，降為 70
        if any(k in t for k in ROLE_ADJACENT):
            return 70.0
        return 100.0
    if any(k in t for k in ROLE_ADJACENT):
        return 50.0
    if "product" in t or "プロダクト" in t:
        return 70.0
    return 30.0


# 職位類型分類（jobs.job_type 用）—— 諮詢詞是 ROLE_ADJACENT 的子集，
# 單獨列出讓 consulting 分類不受 PMM/分析/設計 等其他鄰接詞影響
ROLE_CONSULTING = _cfg("scoring", "role_consulting", [
    "コンサルタント", "コンサル", "consultant", "consulting", "アドバイザー", "advisor",
])


def classify_role(title: str) -> str:
    """職位類型：pdm / pjm / consulting / other（寫入 jobs.job_type）。

    優先序與 score_role_fit 一致：Project/Program/TPM/PMO 詞先攔截 → pjm；
    PM 核心職銜 → pdm（PM+コンサル混合職銜以 PM 為主軸，仍歸 pdm）；
    純コンサル/顧問 → consulting；皆無 → other。
    """
    t = _normalize(title or "").lower()
    if any(k in t for k in ROLE_DEMOTE) or bool(_ROLE_DEMOTE_RE.search(t)):
        return "pjm"
    if any(k in t for k in ROLE_CORE_PM) or bool(_ROLE_PM_RE.search(t)):
        return "pdm"
    if any(k in t for k in ROLE_CONSULTING):
        return "consulting"
    return "other"


def _overseas_tier(company: str | None) -> str:
    name = (company or "").lower()
    if any(c in name for c in FRONTIER_AI_COMPANIES):
        return "ai_startup"
    return "mega_venture"


def _is_overseas(row) -> bool:
    return (row["source"] or "").endswith("-api")


def score_one(row) -> tuple[int, ScoreBreakdown]:
    blob = _blob(row)
    bd = ScoreBreakdown()

    if _is_overseas(row):
        # 海外軌：去 jp 化框架（無 salary/visa；加 role_fit；tier 由公司名推斷）
        bd.market_keywords = score_market_keywords(blob, bd)
        bd.tier_preference = score_tier_preference(_overseas_tier(row["company"]), bd)
        bd.tech_overlap = score_tech_overlap(blob, bd)
        bd.role_fit = score_role_fit(row["title"], bd)
        bd.domain = score_domain(blob, bd)
        weighted = (
            bd.market_keywords * OVERSEAS_WEIGHTS["market_keywords"]
            + bd.tier_preference * OVERSEAS_WEIGHTS["tier_preference"]
            + bd.tech_overlap * OVERSEAS_WEIGHTS["tech_overlap"]
            + bd.role_fit * OVERSEAS_WEIGHTS["role_fit"]
            + bd.domain * OVERSEAS_WEIGHTS["domain"]
        ) / 100.0
        if is_engineering_only(row["title"]):
            weighted *= ENG_ONLY_PENALTY
        if not is_pm_by_content(row["title"], row["raw_jd"]):
            weighted *= PM_GATE_PENALTY
        return int(round(weighted)), bd

    # 日本軌：7 維（含 role_fit）
    bd.market_keywords = score_market_keywords(blob, bd)
    bd.tier_preference = score_tier_preference(row["tier"], bd)
    bd.tech_overlap = score_tech_overlap(blob, bd)
    bd.salary_fit = score_salary_fit(row, bd)
    bd.remote_visa = score_remote_visa(row, bd)
    bd.domain = score_domain(blob, bd)
    bd.role_fit = score_role_fit(row["title"], bd)
    weighted = (
        bd.market_keywords * WEIGHTS["market_keywords"]
        + bd.tier_preference * WEIGHTS["tier_preference"]
        + bd.tech_overlap * WEIGHTS["tech_overlap"]
        + bd.salary_fit * WEIGHTS["salary_fit"]
        + bd.remote_visa * WEIGHTS["remote_visa"]
        + bd.domain * WEIGHTS["domain"]
        + bd.role_fit * WEIGHTS["role_fit"]
    ) / 100.0
    # 職能閘：純工程職（無 PM 字樣）降權，避免 PM 精選被工程職佔滿
    if is_engineering_only(row["title"]):
        weighted *= ENG_ONLY_PENALTY
    # JD 內容閘：非 PM 職（Bizreach 雜訊等）重壓沉底
    if not is_pm_by_content(row["title"], row["raw_jd"]):
        weighted *= PM_GATE_PENALTY
    return int(round(weighted)), bd


def score_all() -> int:
    rows = all_jobs()
    for row in rows:
        score, bd = score_one(row)
        update_score(row["id"], score, json.dumps(asdict(bd), ensure_ascii=False))
    print(f"[jd_scorer] 已評分 {len(rows)} 筆")
    return len(rows)


def score_job(job_id: int) -> int:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise SystemExit(f"job_id {job_id} 不存在")
    score, bd = score_one(row)
    update_score(job_id, score, json.dumps(asdict(bd), ensure_ascii=False))
    print(f"job_id={job_id} → score={score}")
    print(json.dumps(asdict(bd), ensure_ascii=False, indent=2))
    return score


def write_report() -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    report = OUTPUT_DIR / f"scored-{today}.md"

    rows = all_jobs()
    rows_sorted = sorted(rows, key=lambda r: -(r["score"] or 0))

    lines = [
        f"# JD Scoring Report — {today}",
        "",
        f"**對 {len(rows)} 筆 JD 評分（2026 AI PM 市場 6 維度加權）**",
        "",
        f"權重：market_keywords({WEIGHTS['market_keywords']}) / "
        f"tier_preference({WEIGHTS['tier_preference']}) / "
        f"tech_overlap({WEIGHTS['tech_overlap']}) / "
        f"salary_fit({WEIGHTS['salary_fit']}) / "
        f"remote_visa({WEIGHTS['remote_visa']}) / "
        f"domain({WEIGHTS['domain']})",
        "",
        "---",
        "",
        "## TOP 10",
        "",
        "| Score | Tier | Company | Title | Source | URL |",
        "|-------|------|---------|-------|--------|-----|",
    ]
    for r in rows_sorted[:10]:
        company = (r["company"] or "—")[:30]
        title = (r["title"] or "—")[:50]
        lines.append(
            f"| {r['score'] or 0} | {r['tier'] or '—'} | {company} | {title} | "
            f"{r['source']} | [link]({r['url']}) |"
        )

    lines.extend(["", "## BOTTOM 5（檢查是否設定有誤）", ""])
    lines.append("| Score | Tier | Company | Title |")
    lines.append("|-------|------|---------|-------|")
    for r in rows_sorted[-5:]:
        lines.append(
            f"| {r['score'] or 0} | {r['tier'] or '—'} | "
            f"{(r['company'] or '—')[:30]} | {(r['title'] or '—')[:60]} |"
        )

    lines.extend(["", "## 分數構成範例（TOP 3 明細）", ""])
    for r in rows_sorted[:3]:
        bd = json.loads(r["score_breakdown"] or "{}")
        lines.append(f"### {r['company']} — {r['title']}")
        lines.append(f"- **Total**: {r['score']}")
        lines.append(f"- market_keywords: {bd.get('market_keywords', 0):.0f}  matched: `{bd.get('matched_keywords', [])}`")
        lines.append(f"- tier_preference: {bd.get('tier_preference', 0):.0f}")
        lines.append(f"- tech_overlap: {bd.get('tech_overlap', 0):.0f}  matched: `{bd.get('matched_tech', [])}`")
        lines.append(f"- salary_fit: {bd.get('salary_fit', 0):.0f}")
        lines.append(f"- remote_visa: {bd.get('remote_visa', 0):.0f}")
        lines.append(f"- domain: {bd.get('domain', 0):.0f}  matched: `{bd.get('matched_domain', [])}`")
        lines.append("")

    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"[jd_scorer] Markdown 報告：{report}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="JD 6 維評分器（2026 AI PM 市場）")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--job-id", type=int)
    g.add_argument("--report", action="store_true", help="只重新生成 Markdown 報告")
    args = parser.parse_args()

    if args.all:
        score_all()
        write_report()
    elif args.job_id is not None:
        score_job(args.job_id)
    elif args.report:
        write_report()


if __name__ == "__main__":
    main()
