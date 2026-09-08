"""依 JD 生成定制職務経歴書 HTML。

流程（LLM 內容 / 渲染分離 — 沿用專案哲學）:
  1. 載入 data/candidate_profile.yaml（與 gap 分析同源）
  2. 去識別化：白名單挑「職業內容」，姓名/聯絡方式絕不外送
  3. [LLM x1, Sonnet] 依 JD reframe 出履歷內容 JSON（reframe-only, 接地紀律）
  4. 本地注入姓名/kana/聯絡 → Jinja2 render → HTML
  5. 接地驗證：HTML 內所有數字回查 profile，編造則警告

用法:
    python3 -m tools.shokumu_tailor --job-id 123
    python3 -m tools.shokumu_tailor --job-id 123 --no-llm   # 只落 prompt
    python3 -m tools.shokumu_tailor --job-id 123 --out path/to.html
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

from interview._llm import call as llm_call_fallback
from tracker.db import connect

ROOT = Path(__file__).parent.parent
PROFILE_PATH = ROOT / "data" / "candidate_profile.yaml"
TEMPLATE_DIR = ROOT / "resume" / "jp"
TEMPLATE_NAME = "shokumu-tailored.j2.html"
GLOBAL_SHOKUMU = ROOT / "resume" / "jp" / "output" / "shokumu-keirekisho.html"

MODEL = "claude-sonnet-4-6"
MAX_JD_CHARS = 5000

# candidate_profile.yaml 無 kana — header 用，本地注入不外送
RESUME_DATA_PATH = ROOT / "resume" / "jp" / "data.yaml"


def _load_resume_identity() -> tuple[str, str]:
    """從 resume/jp/data.yaml 取名字和假名，避免在代碼裡硬編碼 PII。"""
    data = yaml.safe_load(RESUME_DATA_PATH.read_text(encoding="utf-8")) or {}
    p = data.get("profile", {})
    name = f"{p.get('family_name', '')} {p.get('given_name', '')}".strip() or "本人"
    kana = f"{p.get('family_kana', '')} {p.get('given_kana', '')}".strip()
    return name, kana

# 去識別化白名單：只有這些 key 會送進 LLM（identity / contact 絕不外送）
DEID_KEYS = [
    "positioning", "domains", "skills", "experience",
    "proof_projects", "ai_engineering", "differentiators",
    "match_summary", "education", "certifications", "languages",
    "sier_experience", "developer_tool_design",
]


# ---------------------------------------------------------------- jinja env

def md_bold(text: str) -> Markup:
    """`**xxx**` → <strong>xxx</strong>、其餘 HTML escape。"""
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


# ---------------------------------------------------------------- de-identify

def _load_profile() -> dict:
    if not PROFILE_PATH.exists():
        raise SystemExit(f"找不到 {PROFILE_PATH}")
    return yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}


def build_deid_subset(profile: dict) -> str:
    """白名單挑職業內容 + 防禦性洗掉任何洩漏的姓名/聯絡方式。"""
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


# ---------------------------------------------------------------- LLM prompt

_SCHEMA_HINT = """{
  "headline_title": "英日混在の職位定位句（会社の事業領域に対位させる。例: Senior PM — Enterprise Intelligence × Fintech B2B）",
  "summary": "職務要約 120-170字。会社の事業領域と本人の実績を対位。**で強調可。",
  "competencies": [
    {"title": "カテゴリ名(短)", "chips": ["キーワード", "..."]}
  ],
  "experiences": [
    {
      "period": "YYYY-MM 〜 YYYY-MM（または 現在）",
      "tags": ["上場 等", "規模", "業種"],
      "company": "会社名(profileのまま)",
      "role": "職位・一言役割（会社対位の角度可）",
      "bullets": ["**見出し**：本文。profileのhighlightsを会社向けに言い換え", "..."],
      "metrics": [{"val": "数値", "lbl": "ラベル"}]
    }
  ],
  "self_pr": [{"title": "見出し", "body": "本文。**で強調可"}],
  "education": [{"date": "YYYY-MM", "entry": "..."}],
  "languages": ["中国語（ネイティブ）", "日本語（N1）", "英語（...）"]
}"""

_RULES = """# 厳守ルール（最重要）
- **数字・指標・固有名詞は profile に存在するものだけ使う。新しい数字を絶対に作らない。**
- profile に無い実績・経験・資格を捏造しない。不確実なものは書かない（省く）。
- 会社の事業領域と本人の実績を「対位」させてよい（例: 会社の Enterprise Graph ←→ 本人の multi-LLM 評価ゲート）。これは reframe であり捏造ではない。
- competencies は必ず 3 カテゴリ。各カテゴリ 4-6 chips。
- experiences は profile.experience + proof_projects から、新しい順に 4-5 件。
  各 2-4 bullets。metrics は profile に数値がある経歴のみ 0-3 個。
- self_pr は 3 項目。profile.differentiators / match_summary を会社向けに reframe。
- 全文日本語。そのまま提出できる完成度で書く。"""


def build_prompt(deid_profile: str, job: dict) -> str:
    jd = (job.get("raw_jd") or "")[:MAX_JD_CHARS]
    company = job.get("company") or "（会社名不明）"
    salary = ""
    if job.get("salary_min"):
        salary = f"年収レンジ: {job['salary_min']}万〜{job.get('salary_max') or '?'}万円"
    return f"""あなたは日本のハイクラス転職に精通した職務経歴書ライターです。
以下の応募者プロフィール（職業情報のみ・個人特定情報は除外済み）を、
対象求人に最適化した「定制職務経歴書」の構造化コンテンツ(JSON)に reframe してください。

{_RULES}

# 対象求人
- 会社: {company}
- 職位: {job.get('title')}
- 企業タイプ: {job.get('tier') or 'unknown'} / {salary}

## JD 全文（抜粋）
{jd or '(本文なし)'}

# 応募者プロフィール（YAML・職業情報のみ）
```yaml
{deid_profile}
```

# 出力（下記スキーマの JSON のみ。前後に説明・コードフェンス以外を書かない）
{_SCHEMA_HINT}"""


# ---------------------------------------------------------------- json + validate

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
    """HTML に出る数字が profile に存在するか回查。2 桁以上のみ（序数1,2,3は無視）。"""
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


# ---------------------------------------------------------------- render

def render_html(content: dict, profile: dict, company: str | None) -> str:
    ident = profile.get("identity", {}) or {}
    nat = ident.get("nationality", "")
    restrict = ident.get("work_restrictions", "なし")
    visa = ident.get("visa_status", "永住者")
    visa_line = f"{visa}（{nat}籍・就労制限{restrict}）" if nat else f"{visa}（就労制限{restrict}）"

    name_ja, kana = _load_resume_identity()
    exps = content.get("experiences", [])
    ctx = dict(content)
    ctx.update({
        "name_ja": name_ja,
        "kana": kana,
        "today": date.today().strftime("%Y 年 %-m 月現在"),
        "company": company,
        "visa": visa_line,
        "page1_exp_count": min(2, len(exps)),
        "total_pages": 2,
    })
    return _env().get_template(TEMPLATE_NAME).render(**ctx)


# ---------------------------------------------------------------- entry

def _load_job(job_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise SystemExit(f"job_id {job_id} が DB に見つかりません")
    return dict(row)


def generate(job: dict, out_path: Path, *, no_llm: bool = False,
             prompts_dir: Path | None = None, timeout: int = 300) -> bool:
    """定制職務経歴書 HTML を out_path に生成。成功=True / prompt落地・失敗=False。"""
    profile = _load_profile()
    deid = build_deid_subset(profile)
    prompt = build_prompt(deid, job)

    def _drop_prompt(reason: str) -> bool:
        if prompts_dir is not None:
            prompts_dir.mkdir(parents=True, exist_ok=True)
            (prompts_dir / "shokumu.prompt.md").write_text(prompt, encoding="utf-8")
            print(f"  → prompt 落地: _prompts/shokumu.prompt.md（{reason}）")
        return False

    if no_llm:
        return _drop_prompt("--no-llm")

    from tools import miko_llm
    if not miko_llm.is_available():
        return _drop_prompt("指揮中心不可用")
    try:
        raw = miko_llm.text(prompt, timeout=timeout, opts={
            "accept": {"minChars": 500, "includesAny": ['"experiences"', '"summary"']}
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

    warnings = validate_grounding(content, deid)
    html = render_html(content, profile, job.get("company"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"  ✓ {out_path.name}（定制職務経歴書）")
    if warnings:
        print(f"  ⚠️ 接地驗證 {len(warnings)} 件（要人手確認）:")
        for w in warnings[:10]:
            print(f"     - {w}")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="依 JD 生成定制職務経歴書 HTML")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--out", default=None, help="輸出路徑（預設 output/shokumu-2page-{id}.html）")
    p.add_argument("--no-llm", action="store_true", help="只落 prompt 不呼叫 LLM")
    args = p.parse_args()

    job = _load_job(args.job_id)
    out = Path(args.out) if args.out else ROOT / "output" / f"shokumu-2page-{args.job_id}.html"
    ok = generate(job, out, no_llm=args.no_llm,
                  prompts_dir=out.parent / "_prompts")
    if ok:
        print(f"\n✓ {out}")


if __name__ == "__main__":
    main()
