"""面向 転職エージェント（獵頭）的通用職務経歴書 HTML 生成。

定制版（shokumu_tailor）鎖定單一 JD、固定 2 頁；本版面向獵頭：
  - JD 非依存 — 候補者の全体像を網羅（職歴全件 + 個人 PJ 全件）
  - 頁数無制限 — 流式多頁テンプレート、裁剪しない
  - 希望条件を明示 — 役職 / 年収レンジ / 勤務地 / ビザ をエージェントの matching 用に冒頭提示

PII 分離（CLAUDE.md 準拠）:
  - LLM へ送るのは職業内容のみ（build_deid_subset の白名単、姓名/連絡先は絶対外送しない）
  - 姓名 / ビザ / 希望条件 / 学歴 / 資格 / 語学 → profile から本地直接注入（LLM を通さない）

LLM 内容 / 渲染分離（専案哲学を踏襲）:
  1. data/candidate_profile.yaml を読込（gap 分析と同源）
  2. 去識別化サブセットを LLM へ → reframe JSON（headline/summary/competencies/experiences/projects/self_pr）
  3. 希望条件等を本地注入 → Jinja2 render → HTML
  4. 接地検証：HTML 内の数字を profile に回查、捏造なら警告

用法:
    python3 -m tools.shokumu_recruiter                       # LLM 呼出 → HTML
    python3 -m tools.shokumu_recruiter --no-llm              # prompt のみ落地
    python3 -m tools.shokumu_recruiter --from-json c.json    # 既存 JSON から渲染（LLM スキップ）
    python3 -m tools.shokumu_recruiter --out path/to.html
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# 共用関数は定制版から再利用（DRY）
from tools.shokumu_tailor import (
    _extract_json,
    _load_profile,
    _load_resume_identity,
    build_deid_subset,
    md_bold,
    validate_grounding,
)

ROOT = Path(__file__).parent.parent
TEMPLATE_DIR = ROOT / "resume" / "jp"
TEMPLATE_NAME = "shokumu-recruiter.j2.html"
DEFAULT_OUT = ROOT / "resume" / "jp" / "output" / "shokumu-recruiter.html"


# ---------------------------------------------------------------- jinja env

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


# ---------------------------------------------------------------- LLM prompt

_SCHEMA_HINT = """{
  "headline_title": "中性の職位定位句（特定企業に寄せない。例: Senior PM — AI Agent / LLM Orchestration × Fintech）",
  "summary": "職務要約 150-220字。候補者の全体像と最強の売りを凝縮。**で強調可。",
  "competencies": [
    {"title": "カテゴリ名(短)", "chips": ["キーワード", "..."]}
  ],
  "experiences": [
    {
      "period": "YYYY-MM 〜 YYYY-MM（または 現在）",
      "tags": ["上場 等", "規模", "業種"],
      "company": "会社名(profileのまま)",
      "role": "職位・一言役割",
      "bullets": ["**見出し**：本文。profileのhighlightsを整理（裁剪せず網羅）", "..."],
      "metrics": [{"val": "数値", "lbl": "ラベル"}]
    }
  ],
  "projects": [
    {"name": "PJ名", "role": "役割", "stack": "技術スタック", "bullets": ["**見出し**：本文", "..."]}
  ],
  "self_pr": [{"title": "見出し", "body": "本文。**で強調可"}]
}"""

_RULES = """# 厳守ルール（最重要）
- **数字・指標・固有名詞は profile に存在するものだけ使う。新しい数字を絶対に作らない。**
- profile に無い実績・経験・資格を捏造しない。不確実なものは省く。
- これはエージェント（人材紹介）向けの汎用職務経歴書。特定企業に寄せず、候補者の全体像を網羅する。
- competencies は 4-5 カテゴリ。各 4-7 chips。
- experiences は profile.experience 全件（新しい順）。各 2-5 bullets。裁剪しない。
  metrics は profile に数値がある経歴のみ 0-3 個。
- projects は profile.proof_projects から代表的な 6-7 件。各 2-4 bullets。
- self_pr は 3-4 項目。profile.differentiators / match_summary から汎用的に reframe。
- 全文日本語。そのまま提出できる完成度で書く。"""


def build_prompt(deid_profile: str) -> str:
    return f"""あなたは日本のハイクラス転職に精通した職務経歴書ライターです。
以下の応募者プロフィール（職業情報のみ・個人特定情報は除外済み）を、
人材紹介エージェント向けの「汎用職務経歴書」の構造化コンテンツ(JSON)に reframe してください。

{_RULES}

# 応募者プロフィール（YAML・職業情報のみ）
```yaml
{deid_profile}
```

# 出力（下記スキーマの JSON のみ。前後に説明・コードフェンス以外を書かない）
{_SCHEMA_HINT}"""


# ---------------------------------------------------------------- local inject

def _desired_conditions(profile: dict) -> dict:
    """希望条件・属性を profile から本地抽出（LLM を通さない）。"""
    pos = profile.get("positioning", {}) or {}
    comp = profile.get("compensation", {}) or {}
    cond = profile.get("conditions", {}) or {}
    ident = profile.get("identity", {}) or {}
    langs = profile.get("languages", {}) or {}

    target_min = comp.get("scorer_target_min")
    desired = comp.get("desired_annual")
    salary = ""
    if desired:
        salary = f"希望 {desired} 万円〜（{comp.get('desired_min', '')}万〜応相談）"
    elif target_min:
        salary = f"{target_min} 万〜{comp.get('scorer_target_max', '')}万円"

    nat = ident.get("nationality", "")
    visa = ident.get("visa_status", "")
    restrict = ident.get("work_restrictions", "なし")
    visa_line = f"{visa}（{nat}籍・就労制限{restrict}）" if nat else visa

    return {
        "headline_fallback": pos.get("title", ""),
        "tagline": pos.get("tagline", ""),
        "one_liner": (pos.get("one_liner", "") or "").strip(),
        "role_fit": pos.get("role_fit", []),
        "seniority_fit": pos.get("seniority_fit", []),
        "salary": salary,
        "location": "・".join(cond.get("location_preference", []) or []),
        "employment": "・".join(cond.get("employment_type", []) or []),
        "travel": cond.get("travel", ""),
        "visa": visa_line,
        "visa_short": visa or "永住者",
        "years_product": ident.get("years_in_product"),
        "years_total": ident.get("years_total_career"),
        "years_japan": ident.get("years_in_japan"),
        "languages": [v for v in (
            f"日本語：{langs.get('ja','')}" if langs.get("ja") else "",
            f"中国語：{langs.get('zh','')}" if langs.get("zh") else "",
            f"英語：{langs.get('en','')}" if langs.get("en") else "",
        ) if v],
        "education": profile.get("education", []) or [],
        "certifications": profile.get("certifications", []) or [],
    }


def render_html(content: dict, profile: dict) -> str:
    name_ja, kana = _load_resume_identity()
    ctx = dict(content)
    ctx.update({
        "name_ja": name_ja,
        "kana": kana,
        "today": date.today().strftime("%Y 年 %-m 月現在"),
        "cond": _desired_conditions(profile),
    })
    return _env().get_template(TEMPLATE_NAME).render(**ctx)


# ---------------------------------------------------------------- entry

def generate(out_path: Path, *, no_llm: bool = False,
             from_json: Path | None = None) -> bool:
    """獵頭版職務経歴書 HTML を out_path に生成。"""
    profile = _load_profile()

    # 既存 JSON から渲染（LLM スキップ）
    if from_json is not None:
        content = json.loads(from_json.read_text(encoding="utf-8"))
        _render_and_write(content, profile, out_path)
        return True

    deid = build_deid_subset(profile)
    prompt = build_prompt(deid)

    if no_llm:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        (out_path.parent / "shokumu-recruiter.prompt.md").write_text(prompt, encoding="utf-8")
        print(f"  → prompt 落地: {out_path.parent / 'shokumu-recruiter.prompt.md'}")
        return False

    from tools import miko_llm
    if not miko_llm.is_available():
        print("  ✗ 指揮中心不可用 — --no-llm で prompt 落地後、手動生成 JSON → --from-json を推奨")
        return False
    raw = miko_llm.text(prompt, timeout=300, opts={
        "accept": {"minChars": 800, "includesAny": ['"experiences"', '"summary"']}
    })
    if not raw:
        print("  ✗ 指揮中心回空")
        return False
    content = _extract_json(raw)
    _render_and_write(content, profile, out_path)
    return True


def _render_and_write(content: dict, profile: dict, out_path: Path) -> None:
    deid = build_deid_subset(profile)
    warnings = validate_grounding(content, deid)
    # projects の bullets も接地検証
    for p in content.get("projects", []):
        for b in p.get("bullets", []):
            for n in __import__("re").findall(r"\d+", str(b)):
                if len(n) >= 2 and n not in set(__import__("re").findall(r"\d+", deid)):
                    warnings.append(f"project/{p.get('name','?')}: 数字 '{n}' が profile に無い → 要確認")
    html = render_html(content, profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"  ✓ {out_path}（獵頭版・汎用職務経歴書）")
    if warnings:
        print(f"  ⚠️ 接地検証 {len(warnings)} 件（要人手確認）:")
        for w in warnings[:15]:
            print(f"     - {w}")


def main() -> None:
    p = argparse.ArgumentParser(description="面向獵頭的汎用職務経歴書 HTML 生成")
    p.add_argument("--out", default=None, help=f"輸出路徑（預設 {DEFAULT_OUT}）")
    p.add_argument("--no-llm", action="store_true", help="只落 prompt 不呼叫 LLM")
    p.add_argument("--from-json", default=None, help="既存 content JSON から渲染")
    args = p.parse_args()

    out = Path(args.out) if args.out else DEFAULT_OUT
    fj = Path(args.from_json) if args.from_json else None
    generate(out, no_llm=args.no_llm, from_json=fj)
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
