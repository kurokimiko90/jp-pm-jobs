"""3 層定製職務経歴書：規則預選 → LLM reframe → 接地驗證 → HTML。

與 shokumu_tailor.py 的差異：
  - tailor: 全量 profile 丟給 LLM，LLM 同時做「選擇」和「reframe」
  - select: 先用關鍵字匹配從母版 15 PR / 7 bullet / 7 PJ 中選出最相關子集，
            LLM 只負責 reframe 語氣（更省 token、更可控）

用法:
    python3 -m tools.shokumu_select --job-id 123                # 全流程
    python3 -m tools.shokumu_select --job-id 123 --no-llm       # 只落 prompt
    python3 -m tools.shokumu_select --job-id 123 --from-json c.json  # 從 JSON 渲染
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from tracker.db import connect

ROOT = Path(__file__).parent.parent
PROFILE_PATH = ROOT / "data" / "candidate_profile.yaml"
MASTER_PATH = ROOT / "resume" / "jp" / "master.yaml"
TEMPLATE_DIR = ROOT / "resume" / "jp"
TEMPLATE_NAME = "shokumu-tailored.j2.html"
RESUME_DATA_PATH = ROOT / "resume" / "jp" / "data.yaml"

DEID_KEYS = [
    "positioning", "domains", "skills", "experience",
    "proof_projects", "ai_engineering", "differentiators",
    "match_summary", "education", "certifications", "languages",
    "sier_experience", "developer_tool_design",
]


# JD 關鍵字 → tag 映射（用於自動偵測 JD 類型）
KEYWORD_TAG_MAP = {
    "ai": ["AI", "LLM", "機械学習", "エージェント", "agent", "GPT", "生成AI", "GenAI"],
    "engineering": ["実装", "開発", "エンジニア", "コード", "技術", "プログラミング", "SDK"],
    "fintech": ["決済", "金融", "Fintech", "FinTech", "銀行", "保険", "クレジット", "ペイメント"],
    "saas": ["SaaS", "プラットフォーム", "platform", "B2B", "BtoB", "クラウド"],
    "quality": ["品質", "テスト", "QA", "検証", "バグ"],
    "evals": ["評価", "evals", "品質管理", "ゲート", "gate"],
    "compliance": ["コンプライアンス", "法規", "セキュリティ", "GDPR", "個人情報"],
    "security": ["セキュリティ", "脅威", "脆弱性", "暗号"],
    "data_driven": ["データ", "分析", "KPI", "指標", "メトリクス", "data"],
    "strategy": ["戦略", "ポジショニング", "競合", "市場", "GTM"],
    "management": ["マネジメント", "チーム", "組織", "リーダー", "横断", "ステークホルダー"],
    "zero_to_one": ["0→1", "新規", "立ち上げ", "グロース", "growth"],
    "cross_cultural": ["グローバル", "海外", "英語", "バイリンガル", "多国籍"],
    "i18n": ["国際化", "i18n", "ローカライズ", "多言語"],
    "automation": ["自動化", "オートメーション", "効率化", "DX"],
    "prd": ["PRD", "要件定義", "仕様", "ロードマップ", "roadmap"],
    "multi_tenant": ["マルチテナント", "複数事業", "業態"],
    "sier": ["SIer", "受託", "ウォーターフォール", "検収", "納品"],
    "finops": ["コスト", "FinOps", "予算", "費用", "ROI"],
    "cost": ["コスト", "削減", "最適化", "効率"],
}


# ================================================================
# Master 資料載入（從 master.yaml，源自桌面 HTML）
# ================================================================

_MASTER_CACHE: dict | None = None

def _load_master() -> dict:
    global _MASTER_CACHE
    if _MASTER_CACHE is None:
        _MASTER_CACHE = yaml.safe_load(MASTER_PATH.read_text(encoding="utf-8")) or {}
    return _MASTER_CACHE


# ================================================================
# 素材選擇邏輯
# ================================================================

def detect_jd_tags(jd_text: str) -> dict[str, float]:
    """JD 文本 → tag 權重 dict（出現次數加權）。"""
    tag_scores: dict[str, float] = {}
    jd_lower = jd_text.lower()
    for tag, keywords in KEYWORD_TAG_MAP.items():
        hits = sum(1 for kw in keywords if kw.lower() in jd_lower)
        if hits:
            tag_scores[tag] = hits
    return tag_scores


def select_prs(jd_tags: dict[str, float], n: int = 4) -> list[dict]:
    """從 master.yaml 的 self_pr 中選出與 JD 最相關的 n 條。"""
    master = _load_master()
    scored = []
    for pr in master.get("self_pr", []):
        relevance = sum(jd_tags.get(t, 0) for t in pr.get("tags", []))
        scored.append((relevance, pr))
    scored.sort(key=lambda x: (-x[0], x[1].get("id", 99)))
    return [pr for _, pr in scored[:n]]


def select_projects(jd_tags: dict[str, float], n: int = 4) -> list[dict]:
    """從 master.yaml 的 personal_projects 中選出最相關的 n 個。"""
    master = _load_master()
    scored = []
    for pj in master.get("personal_projects", []):
        relevance = sum(jd_tags.get(t, 0) for t in pj.get("tags", []))
        scored.append((relevance, pj))
    scored.sort(key=lambda x: -x[0])
    return [pj for _, pj in scored[:n]]


# ================================================================
# Profile / 去識別化
# ================================================================

def _load_profile() -> dict:
    return yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}


def build_deid_subset(profile: dict) -> str:
    subset = {k: profile[k] for k in DEID_KEYS if k in profile}
    text = yaml.safe_dump(subset, allow_unicode=True, sort_keys=False)
    ident = profile.get("identity", {}) or {}
    contact = profile.get("contact", {}) or {}
    for v in (ident.get("name_ja"), ident.get("name_romaji")):
        if v:
            text = text.replace(str(v), "本人")
    for v in contact.values():
        if isinstance(v, str) and v:
            text = text.replace(v, "***")
    return text


def _load_resume_identity() -> tuple[str, str]:
    data = yaml.safe_load(RESUME_DATA_PATH.read_text(encoding="utf-8")) or {}
    p = data.get("profile", {})
    name = f"{p.get('family_name', '')} {p.get('given_name', '')}".strip() or "本人"
    kana = f"{p.get('family_kana', '')} {p.get('given_kana', '')}".strip()
    return name, kana


# ================================================================
# Prompt 構築（預選済みの素材のみ渡す）
# ================================================================

_SCHEMA_HINT = """{
  "headline_title": "英日混在の職位定位句（会社の事業領域に対位。例: Senior PM — AI Agent Platform × SaaS）",
  "summary": "職務要約 80-100字。簡潔に。**で強調可。",
  "competencies": [
    {"title": "カテゴリ名(短)", "chips": ["キーワード", "..."]}
  ],
  "experiences": [
    {
      "period": "YYYY-MM 〜 YYYY-MM（または 現在）",
      "tags": ["上場 等", "規模", "業種"],
      "company": "会社名(profileのまま)",
      "role": "職位・一言役割（会社対位の角度可）",
      "bullets": ["**見出し**：本文", "..."],
      "metrics": [{"val": "数値", "lbl": "ラベル"}]
    }
  ],
  "self_pr": [{"title": "見出し", "body": "本文。**で強調可"}],
  "education": [{"date": "YYYY-MM", "entry": "..."}],
  "languages": ["中国語（ネイティブ）", "日本語（N1）", "英語（...）"]
}"""


def build_prompt(deid_profile: str, job: dict,
                 selected_prs: list[dict],
                 selected_projects: list[dict]) -> str:
    jd = (job.get("raw_jd") or "")[:5000]
    company = job.get("company") or "（会社名不明）"
    salary = ""
    if job.get("salary_min"):
        salary = f"年収レンジ: {job['salary_min']}万〜{job.get('salary_max') or '?'}万円"

    pr_text = "\n".join(
        f"  PR{pr['id']}. {pr['title']}\n    {pr['body']}"
        for pr in selected_prs
    )
    pj_text = "\n".join(
        f"  - {pj.get('name', '?')}: {pj.get('oneline', '')}"
        for pj in selected_projects
    )

    master = _load_master()
    exp_text = ""
    for exp in master.get("experiences", []):
        exp_text += f"\n### {exp['company']}（{exp['period']}）\n役割: {exp['role']}\n"
        for b in exp.get("bullets", []):
            exp_text += f"  - {b}\n"

    return f"""あなたは日本のハイクラス転職に精通した職務経歴書ライターです。
以下の応募者プロフィールと【事前選定済みの PR・プロジェクト】を、
対象求人に最適化した「定制職務経歴書」の構造化コンテンツ(JSON)に reframe してください。

# 厳守ルール（最重要）
- **数字・指標・固有名詞は profile に存在するものだけ使う。新しい数字を絶対に作らない。**
- profile に無い実績・経験・資格を捏造しない。不確実なものは書かない。
- 会社の事業領域と本人の実績を「対位」させてよい（reframe であり捏造ではない）。
- competencies は必ず 3 カテゴリ。各カテゴリ 4-5 chips。
- experiences は新しい順に 4-5 件。**主要職歴は各 2 bullets（最大 3）、短期・進学は 1 bullet**。
  各 bullet は 1 行 60 字以内。metrics は主要職歴のみ 1-2 個。
  全キャリア期間をカバーし、ギャップを作らない（短い期間も 1 行で入れる）。
- experiences の最後に「個人プロジェクト（AI 実装力の証拠）」を 1 件追加。
  【事前選定プロジェクト】から最も JD に関連する 1-2 件を 1 bullet で簡潔に記載。
- self_pr は【事前選定 PR】から 3 項目を選び、各 body 50 字以内に reframe。追加しない。
- 「収銀端末」ではなく「決済端末」を使う（日本語）。
- **ネットスターズ（現職）の bullets はそのまま使用する。統合・分割・言い換えをしない。**
- summary や headline で会社のビジョン・戦略キーワードに 1 フレーズ対位させる。
- 複数ステークホルダーとの調整・横断連携の経験を experiences に明記する。
- 全文日本語。そのまま提出できる完成度で書く。

# 対象求人
- 会社: {company}
- 職位: {job.get('title')}
- 企業タイプ: {job.get('tier') or 'unknown'} / {salary}

## JD 全文（抜粋）
{jd or '(本文なし)'}

# 【事前選定 PR】（この中から 3-4 項目を会社向けに reframe）
{pr_text}

# 【事前選定 プロジェクト】（experiences の最後に 1 件「個人プロジェクト」として追加）
{pj_text}

# 応募者の職務経歴（母版・reframe 元テキスト）
{exp_text}

# 応募者プロフィール（YAML・職業情報のみ）
```yaml
{deid_profile}
```

# 出力（下記スキーマの JSON のみ。前後に説明・コードフェンス以外を書かない）
{_SCHEMA_HINT}"""


# ================================================================
# JSON 抽出 / 接地驗證 / 渲染
# ================================================================

def _extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"JSON 抽出失敗：\n{text[:300]}")


def validate_grounding(content: dict, source_text: str) -> list[str]:
    src_nums = set(re.findall(r"\d+", source_text))
    warnings: list[str] = []

    def check(s, where: str) -> None:
        for n in re.findall(r"\d+", str(s or "")):
            if len(n) >= 2 and n not in src_nums:
                warnings.append(f"{where}: 数字 '{n}' が profile に無い → 要確認")

    check(content.get("summary"), "summary")
    for e in content.get("experiences", []):
        tag = e.get("company", "?")
        for b in e.get("bullets", []):
            check(b, f"exp/{tag}")
        for m in e.get("metrics", []):
            check(m.get("val"), f"metric/{tag}")
    for p in content.get("self_pr", []):
        check(p.get("body"), "self_pr")
    return warnings


def md_bold(text: str) -> Markup:
    if not text:
        return Markup("")
    parts = re.split(r"\*\*(.+?)\*\*", str(text))
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(str(escape(part)))
        else:
            out.append(f"<strong>{escape(part)}</strong>")
    return Markup("".join(out))


_ENV: Environment | None = None

def _env() -> Environment:
    global _ENV
    if _ENV is None:
        _ENV = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        _ENV.filters["md_bold"] = md_bold
    return _ENV


def render_html(content: dict, profile: dict, company: str | None) -> str:
    ident = profile.get("identity", {}) or {}
    nat = ident.get("nationality", "")
    restrict = ident.get("work_restrictions", "なし")
    visa = ident.get("visa_status", "永住者")
    visa_line = f"{visa}（{nat}籍）" if nat else visa

    name_ja, kana = _load_resume_identity()
    exps = content.get("experiences", [])

    # page1 に PM 実務を優先（大学院・短期は page2 に回す）
    non_pm_keywords = ["修士", "大学院", "進学", "学校"]
    p1_count = 0
    for e in exps:
        combined = (e.get("company", "") + e.get("role", "")).lower()
        if any(kw in combined for kw in non_pm_keywords):
            break
        p1_count += 1
        if p1_count >= 3:
            break
    page1_exp_count = max(1, min(p1_count, 3))

    ctx = dict(content)
    ctx.update({
        "name_ja": name_ja,
        "kana": kana,
        "today": date.today().strftime("%Y 年 %-m 月現在"),
        "company": company,
        "visa": visa_line,
        "page1_exp_count": page1_exp_count,
        "total_pages": 2,
    })
    return _env().get_template(TEMPLATE_NAME).render(**ctx)


# ================================================================
# Entry
# ================================================================

def _load_job(job_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise SystemExit(f"job_id {job_id} が DB に見つかりません")
    return dict(row)


def generate(job: dict, out_path: Path, *, no_llm: bool = False,
             from_json: Path | None = None) -> bool:
    profile = _load_profile()
    jd_text = job.get("raw_jd") or ""

    # Layer 1: 自動偵測 JD tag
    jd_tags = detect_jd_tags(jd_text)
    print(f"  JD tags: {dict(sorted(jd_tags.items(), key=lambda x: -x[1])[:8])}")

    # Layer 2: 預選素材
    selected_prs = select_prs(jd_tags, n=4)
    selected_pjs = select_projects(jd_tags, n=4)
    print(f"  選定 PR: {[pr['id'] for pr in selected_prs]}")
    print(f"  選定 PJ: {[pj.get('name', '?')[:20] for pj in selected_pjs]}")

    # 從 JSON 渲染（跳過 LLM）
    if from_json is not None:
        content = json.loads(from_json.read_text(encoding="utf-8"))
        _render_and_write(content, profile, job, out_path)
        return True

    # 構築 prompt
    deid = build_deid_subset(profile)
    prompt = build_prompt(deid, job, selected_prs, selected_pjs)

    if no_llm:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path = out_path.parent / f"select-{job.get('id', 0)}.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        print(f"  → prompt 落地: {prompt_path}")
        print(f"  → 下一步: 用此 prompt 生成 JSON 後執行 --from-json")
        return False

    # LLM 呼叫
    from tools import miko_llm
    if not miko_llm.is_available():
        print("  ✗ 指揮中心不可用 — 用 --no-llm 落 prompt 後手動生成")
        return False
    raw = miko_llm.text(prompt, timeout=300, opts={
        "accept": {"minChars": 500, "includesAny": ['"experiences"', '"summary"']}
    })
    if not raw:
        print("  ✗ 指揮中心回空")
        return False

    content = _extract_json(raw)
    _enforce_netstars_bullets(content)
    _render_and_write(content, profile, job, out_path)
    return True


def _enforce_netstars_bullets(content: dict) -> None:
    """LLM が現職 bullets を改寫しないよう、master.yaml の原文で上書き。"""
    master = _load_master()
    for m_exp in master.get("experiences", []):
        if m_exp.get("slug") == "netstars":
            for c_exp in content.get("experiences", []):
                if "ネットスターズ" in c_exp.get("company", ""):
                    c_exp["bullets"] = list(m_exp["bullets"])
            break


def _render_and_write(content: dict, profile: dict, job: dict, out_path: Path) -> None:
    deid = build_deid_subset(profile)
    warnings = validate_grounding(content, deid)
    html = render_html(content, profile, job.get("company"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    # 同時落地 content JSON 方便調試
    json_path = out_path.with_suffix(".content.json")
    json_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  ✓ {out_path.name}（定制職務経歴書）")
    print(f"  ✓ {json_path.name}（content JSON）")
    if warnings:
        print(f"  ⚠️ 接地驗證 {len(warnings)} 件:")
        for w in warnings[:10]:
            print(f"     - {w}")


def main() -> None:
    p = argparse.ArgumentParser(description="3 層定製職務経歴書（規則預選 + LLM reframe）")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--from-json", default=None)
    p.add_argument("--pr-count", type=int, default=4, help="選定 PR 数")
    args = p.parse_args()

    job = _load_job(args.job_id)
    slug = re.sub(r"[^\w]", "_", (job.get("company") or "unknown")[:15]).strip("_")
    out = Path(args.out) if args.out else ROOT / "output" / "apply" / f"{args.job_id}_{slug}" / "shokumu-select.html"
    fj = Path(args.from_json) if args.from_json else None

    print(f"=== 定制職務経歴書（3 層選定）===")
    print(f"  Job #{args.job_id}: {job.get('company')} — {job.get('title')}")

    ok = generate(job, out, no_llm=args.no_llm, from_json=fj)
    if ok:
        print(f"\n✓ {out}")


if __name__ == "__main__":
    main()
