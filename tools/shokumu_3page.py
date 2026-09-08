"""3 頁 A4 定製職務経歴書：基底 HTML 設計 → LLM 選擇+reframe → Jinja2 渲染。

與既有流程的關係：
  - shokumu_tailor.py: 舊 2 頁版，LLM 全量 reframe
  - shokumu_select.py: 舊 2 頁版，規則預選 + LLM reframe
  - shokumu_3page.py (本檔): 新 3 頁版，固定 A4 容器 + 新設計系統

本檔不動 prep.py / shokumu_tailor.py / shokumu_select.py。

用法:
    python3 -m tools.shokumu_3page --job-id 123
    python3 -m tools.shokumu_3page --job-id 123 --no-llm       # 只落 prompt
    python3 -m tools.shokumu_3page --job-id 123 --from-json c.json  # 從 JSON 渲染
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
from tools.deid import build_deid_profile, load_profile

ROOT = Path(__file__).parent.parent
TEMPLATE_DIR = ROOT / "resume" / "jp"
TEMPLATE_NAME = "shokumu-3page.j2.html"
RESUME_DATA_PATH = ROOT / "resume" / "jp" / "data.yaml"

MAX_JD_CHARS = 5000


# ================================================================
# Master 資料載入（從 master.yaml，源自桌面 HTML）
# ================================================================

MASTER_PATH = ROOT / "resume" / "jp" / "master.yaml"
_MASTER_CACHE: dict | None = None


def _load_master() -> dict:
    global _MASTER_CACHE
    if _MASTER_CACHE is None:
        _MASTER_CACHE = yaml.safe_load(MASTER_PATH.read_text(encoding="utf-8")) or {}
    return _MASTER_CACHE


def _exp_by_slug() -> dict[str, dict]:
    return {e["slug"]: e for e in _load_master().get("experiences", []) if "slug" in e}


def _pj_by_id() -> dict[str, dict]:
    return {p["id"]: p for p in _load_master().get("personal_projects", []) if "id" in p}


def _pr_by_id() -> dict[int, dict]:
    return {p["id"]: p for p in _load_master().get("self_pr", [])}


# ================================================================
# Jinja2 環境
# ================================================================

def _md_bold(text: str) -> Markup:
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
        _ENV.filters["md_bold"] = _md_bold
    return _ENV


# ================================================================
# LLM Prompt
# ================================================================

_SCHEMA = """{
  "title_line": "2行以内の定位句（例: 決済・ポイント統合 PdM × AI-Native 開発実践\\n8+ ブランド E2E・Fintech 9 年）",
  "fit_points": [
    {"text": "**強調語**を含む適合性ポイント（50字以内）"}
  ],
  "summary": "キャリア全体のポジショニング。\\nで行を区切り **最大4行**（5行目以降は書かない）。150-200字。fit_points の個別実績を繰り返さず、強み・スタンス・差別化を語る。**で強調可。",
  "competencies": [
    {"title": "カテゴリ名", "chips": ["キーワード", "..."]}
  ],
  "p1_experience": {
    "bullets_select": [0, 1, 2, 3],
    "metrics_select": [0, 1, 2],
    "role_reframe": "役割を JD に対位させた言い換え（省略時はデフォルト使用）"
  },
  "p2_experiences": ["grad_school", "dodonew", "sunlike", "freelance", "ipanel"],
  "p2_dodonew_bullets": [0, 1, 2],
  "project_ids": ["fintech_agent", "finops"],
  "pr_bodies": {"1": "圧縮した body（30〜40字、JD に響く核心のみ）", "2": "...", "3": "...", "4": "...", "5": "..."},
  "skills_applicable": ["JD に対位させた活かせる経験 6 項目"]
}"""

_RULES = """# 厳守ルール
- **profile に無い数字・実績・経験を絶対に作らない。**
- fit_points は 3 項目。各項目は text のみ（sub フィールドは不要）。完結した 1 文・1 行に収める。summary と内容を重複させない。
- summary は **最大4行**（\n で区切る、5行目以降は書かない）。fit_points で挙げた個別実績を繰り返さず、候補者のキャリア全体の強み・スタンス・差別化を語る。具体的実績の列挙ではなくポジショニングの文章にすること。
- competencies は **最大 3 カテゴリ**。各カテゴリ **3 chips のみ**（JD に最も関連するものを厳選）。
- p1_experience: ネットスターズの bullets から最大 5 個を選択（index 指定）。metrics は最大 3 個。
- p2_experiences: slug で表示順を指定（デフォルト: grad_school, dodonew, sunlike, freelance, ipanel）。
- p2_dodonew_bullets: dodonew の bullets から最大 3 個を選択。他の p2 企業は bullet なし（role 行のみ）。
- project_ids: 個人プロジェクトから 2-3 個選択。JD に最も関連するもの。
- pr_bodies: 自己 PR は **id 1〜5 の 5 項目を必ず全て掲載する（絶対に削らない・順序固定）**。title は変更しない。pr_bodies で id ごとに body を JD に響く核心へ 30〜40 字に圧縮すること（5 件とも必須。冗長な修飾語を削り核心だけ残す）。
- skills_applicable: 6 項目。各 30 字以内。「この JD に出せる具体的な経験」を短く箇条書き。冗長な説明を避け、キーワード＋実績の形で書く。
- 全文日本語。用語は日本の IT 業界で通じる地道な表現を使うこと（中国語の直訳を避ける。例: ×「収銀端末」→○「決済端末」、×「対接」→○「連携」）。"""


def build_prompt(deid_profile: str, job: dict) -> str:
    jd = (job.get("raw_jd") or "")[:MAX_JD_CHARS]
    company = job.get("company") or "（会社名不明）"
    salary = ""
    if job.get("salary_min"):
        salary = f"年収レンジ: {job['salary_min']}万〜{job.get('salary_max') or '?'}万円"

    master = _load_master()

    projects_desc = "\n".join(
        f"  - id={p.get('id', '?')}, tags={p.get('tags', [])}, title={p.get('name', '?')}"
        for p in master.get("personal_projects", [])
    )
    pr_desc = "\n".join(
        f"  - id={p['id']}, tags={p.get('tags', [])}, title={p['title']}"
        for p in master.get("self_pr", [])
    )

    exps = _exp_by_slug()
    ns = exps.get("netstars", {})
    ns_bullets = "\n".join(
        f"  {i}: {b[:80]}" for i, b in enumerate(ns.get("bullets", []))
    )
    ns_metrics = "\n".join(
        f"  {i}: {m['val']} {m['lbl']}" for i, m in enumerate(ns.get("metrics", []))
    )
    dd = exps.get("dodonew", {})
    dd_bullets = "\n".join(
        f"  {i}: {b[:80]}" for i, b in enumerate(dd.get("bullets", []))
    )

    exp_slugs = "\n".join(
        f"  - slug={e.get('slug', '?')}, company={e.get('company', '?')[:30]}"
        for e in master.get("experiences", []) if e.get("slug") != "netstars"
    )

    return f"""あなたは日本のハイクラス転職に精通した職務経歴書コンサルタントです。
以下の候補者プロフィールと求人情報を元に、3 頁 A4 定製職務経歴書のコンテンツ選択・reframe を JSON で出力してください。

{_RULES}

# 対象求人
- 会社: {company}
- 職位: {job.get('title')}
- 企業タイプ: {job.get('tier') or 'unknown'} / {salary}

## JD 全文（抜粋）
{jd or '(本文なし)'}

# 候補者プロフィール（YAML・職業情報のみ）
```yaml
{deid_profile}
```

# 選択可能な個人プロジェクト（id で指定）
{projects_desc}

# 選択可能な自己 PR（id で指定）
{pr_desc}

# ネットスターズ bullets（index で指定、0始まり）
{ns_bullets}

# ネットスターズ metrics（index で指定、0始まり）
{ns_metrics}

# p2 経歴（slug で指定）
{exp_slugs}

# dodonew bullets（index で指定、0始まり）
{dd_bullets}

# 出力（下記スキーマの JSON のみ。コードフェンス内に。）
{_SCHEMA}"""


# ================================================================
# JSON 抽出 + 驗證
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
    raise ValueError(f"LLM 出力の JSON 抽出失敗：\n{text[:300]}")


def validate_grounding(content: dict, source_text: str) -> list[str]:
    src_nums = set(re.findall(r"\d+", source_text))
    warnings: list[str] = []

    def check(s, where: str) -> None:
        for n in re.findall(r"\d+", str(s or "")):
            if len(n) >= 2 and n not in src_nums:
                warnings.append(f"{where}: 数字 '{n}' が profile に無い")

    check(content.get("summary"), "summary")
    for fp in content.get("fit_points", []):
        check(fp.get("text"), "fit_point")
    for sk in content.get("skills_applicable", []):
        check(sk, "skills_applicable")
    return warnings


# ================================================================
# 組裝渲染 context
# ================================================================

def build_render_context(content: dict) -> dict:
    name_ja, kana = _load_resume_identity()
    master = _load_master()
    exps = _exp_by_slug()

    ns = exps.get("netstars", {})
    p1_exp = content.get("p1_experience", {})
    bullet_indices = p1_exp.get("bullets_select", [0, 1, 2, 3])
    metric_indices = p1_exp.get("metrics_select", [0])
    role_reframe = p1_exp.get("role_reframe") or ns.get("role", "")
    ns_bullets = ns.get("bullets", [])
    ns_metrics = ns.get("metrics", [])

    p1_experiences = [{
        "period": ns.get("period", ""),
        "info_fields": ns.get("info_fields", []),
        "company": ns.get("company", ""),
        "role": role_reframe,
        "bullets": [ns_bullets[i] for i in bullet_indices if i < len(ns_bullets)],
        "metrics": [ns_metrics[i] for i in metric_indices if i < len(ns_metrics)],
    }]
    gs = exps.get("grad_school")
    if gs:
        p1_experiences.append({
            "period": gs.get("period", ""),
            "info_fields": gs.get("info_fields", []),
            "company": gs.get("company", ""),
            "role": gs.get("role", ""),
        })

    p2_order = [s for s in content.get("p2_experiences", ["grad_school", "dodonew", "sunlike", "freelance", "ipanel"]) if s != "grad_school"]
    dodonew_bullets_idx = content.get("p2_dodonew_bullets", [0, 1, 2])
    _EARLY_CAREER = {"sunlike", "freelance", "ipanel"}
    p2_experiences = []
    early_inserted = False
    for key in p2_order:
        if key in _EARLY_CAREER:
            if not early_inserted:
                p2_experiences.append({
                    "period": "2010.04 - 2018.05",
                    "info_fields": [
                        {"label": "事業", "value": "Fintech"},
                        {"label": "体制", "value": "フリーランス／PdM"},
                        {"label": "技術", "value": "Java開発"},
                    ],
                    "company": "Sunlike / フリーランス / iPanel",
                    "role": "PdM / 受託PM / Javaエンジニア",
                    "bullets": ["**0→1金融PMと開発**：P2P金融にて10万ユーザー獲得、受託案件PM、Javaでのシステム開発・市場分析を経験。"],
                    "metrics": [{"val": "10万", "lbl": "獲得ユーザー数"}],
                })
                early_inserted = True
            continue
        exp = exps.get(key)
        if not exp:
            continue
        entry = {
            "period": exp.get("period", ""),
            "info_fields": exp.get("info_fields", []),
            "company": exp.get("company", ""),
            "role": exp.get("role", ""),
        }
        if key == "dodonew":
            dd_bullets = exp.get("bullets", [])
            entry["bullets"] = [dd_bullets[i] for i in dodonew_bullets_idx if i < len(dd_bullets)]
            entry["metrics"] = exp.get("metrics", [])
        p2_experiences.append(entry)

    project_ids = content.get("project_ids", ["fintech_agent", "finops"])
    project_overrides = content.get("project_overrides", {})
    pj_map = _pj_by_id()
    projects = []
    for pid in project_ids:
        pj = pj_map.get(pid)
        if pj:
            stack = pj.get("stack", "")
            if isinstance(stack, str):
                stack = [s.strip() for s in stack.split(",") if s.strip()]
            ov = project_overrides.get(pid, {})
            raw_bullets = pj.get("bullets", [])
            if "bullets_select" in ov:
                raw_bullets = [raw_bullets[i] for i in ov["bullets_select"] if i < len(raw_bullets)]
            projects.append({
                "title": pj.get("name", ""),
                "role": pj.get("role", ""),
                "stack": stack,
                "desc": ov.get("desc", pj.get("oneline", "")),
                "bullets": ov.get("bullets", raw_bullets),
            })

    # 自己 PR は master.yaml の 5 項目を id 順に必ず全件掲載（LLM に選別させない）。
    # title は master 原文のまま、body のみ pr_bodies があれば JD 向け圧縮版に差し替え。
    pr_bodies = content.get("pr_bodies", {})
    pr_map = _pr_by_id()
    self_pr = []
    for pid in sorted(pr_map.keys()):
        pr = dict(pr_map[pid])
        override = pr_bodies.get(str(pid))
        if override:
            pr["body"] = override
        self_pr.append(pr)

    return {
        "name_ja": name_ja,
        "kana": kana,
        "today": date.today().strftime("%Y 年 %-m 月現在"),
        "title_line": content.get("title_line", ""),
        "fit_points": content.get("fit_points", []),
        "summary": "\n".join(content.get("summary", "").split("\n")[:4]),
        "competencies": content.get("competencies", []),
        "p1_experiences": p1_experiences,
        "p2_experiences": p2_experiences,
        "projects": projects,
        "self_pr": self_pr,
        "skills_applicable": content.get("skills_applicable", []),
        "education": master.get("education", []),
        "certifications": master.get("certifications", []),
        "languages": master.get("languages", []),
    }


def _load_resume_identity() -> tuple[str, str]:
    data = yaml.safe_load(RESUME_DATA_PATH.read_text(encoding="utf-8")) or {}
    p = data.get("profile", {})
    name = f"{p.get('family_name', '')} {p.get('given_name', '')}".strip() or "本人"
    kana = f"{p.get('family_kana', '')} {p.get('given_kana', '')}".strip()
    return name, kana


# ================================================================
# 渲染
# ================================================================

def render_html(content: dict) -> str:
    ctx = build_render_context(content)
    return _env().get_template(TEMPLATE_NAME).render(**ctx)


# ================================================================
# Entry point
# ================================================================

def _load_job(job_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise SystemExit(f"job_id {job_id} が DB に見つかりません")
    return dict(row)


def generate(job: dict, out_path: Path, *, no_llm: bool = False,
             from_json: Path | None = None,
             prompts_dir: Path | None = None) -> bool:
    if from_json:
        content = json.loads(from_json.read_text(encoding="utf-8"))
        html = render_html(content)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"  ✓ {out_path.name}（3 頁定製・from JSON）")
        return True

    profile = load_profile()
    deid = build_deid_profile(profile)
    prompt = build_prompt(deid, job)

    def _drop_prompt(reason: str) -> bool:
        if prompts_dir is not None:
            prompts_dir.mkdir(parents=True, exist_ok=True)
            (prompts_dir / "shokumu-3page.prompt.md").write_text(prompt, encoding="utf-8")
            print(f"  → prompt 落地: _prompts/shokumu-3page.prompt.md（{reason}）")
        return False

    if no_llm:
        return _drop_prompt("--no-llm")

    from tools import miko_llm
    if not miko_llm.is_available():
        return _drop_prompt("指揮中心不可用")

    try:
        raw = miko_llm.text(prompt, timeout=300, opts={
            "accept": {"minChars": 300, "includesAny": ['"fit_points"', '"pr_bodies"']}
        })
    except Exception as e:
        return _drop_prompt(f"指揮中心失敗: {e}")
    if not raw:
        return _drop_prompt("指揮中心回空")

    try:
        content = _extract_json(raw)
    except ValueError as e:
        print(f"  ✗ JSON 抽出失敗: {e}")
        return _drop_prompt("JSON 抽出失敗")

    # 保存 content JSON 供後續微調
    out_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    warnings = validate_grounding(content, deid)
    html = render_html(content)
    out_path.write_text(html, encoding="utf-8")
    print(f"  ✓ {out_path.name}（3 頁定製職務経歴書）")
    if warnings:
        print(f"  ⚠️ 接地驗證 {len(warnings)} 件:")
        for w in warnings[:10]:
            print(f"     - {w}")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="3 頁 A4 定製職務経歴書")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--from-json", default=None, help="從既有 JSON 渲染（跳過 LLM）")
    args = p.parse_args()

    job = _load_job(args.job_id)
    slug = re.sub(r"[^\w]", "_", (job.get("company") or "unknown"))[:20]
    out = Path(args.out) if args.out else ROOT / "output" / "apply" / f"{args.job_id}_{slug}" / "shokumu_tailored.html"

    from_json = Path(args.from_json) if args.from_json else None
    ok = generate(job, out, no_llm=args.no_llm, from_json=from_json,
                  prompts_dir=out.parent / "_prompts")
    if ok:
        print(f"\n✓ {out}")


if __name__ == "__main__":
    main()
