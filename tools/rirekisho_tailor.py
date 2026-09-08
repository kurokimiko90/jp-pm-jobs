"""依 JD 客製履歴書の「志望の動機など」欄 — quiet 版基底 HTML の最小侵入置換。

投遞用の履歴書は手作りの完成版（resume/jp/output/rirekisho-*-quiet.html）を基底とし、
公司特化が必要な 3 欄（志望領域 / 志望動機 / アピール）だけを差し替える。
学歴・職歴・資格・本人希望・写真は一切触らない（= 最新の完成版そのまま）。

PII 紀律（重要）:
  - LLM prompt に入るのは JD + 公司事実（brief）+ 去識別化 profile のみ
  - 基底 HTML（実名・生年月日・住所・写真）は **prompt に一切入らない**
  - 送信直前に tools.pii_gate で最終スクラブ（多層防御）

用法:
    python3 -m tools.rirekisho_tailor --job-id 123
    python3 -m tools.rirekisho_tailor --job-id 123 --no-llm       # prompt 落地のみ
    python3 -m tools.rirekisho_tailor --job-id 123 --out path.html
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import date
from pathlib import Path

from markupsafe import Markup, escape

from tools import resume_assets
from tools.deid import build_deid_profile

ROOT = Path(__file__).parent.parent

MAX_JD_CHARS = 5000
MAX_FACTS_CHARS = 2500

# 行の並び。KEEP_LABELS は基底の原文を保持（固定事実）、他は LLM 生成
ROW_ORDER = ["志望領域", "経験", "志望動機", "アピール"]
KEEP_LABELS = ("経験",)
LLM_LABELS = tuple(l for l in ROW_ORDER if l not in KEEP_LABELS)

# 2 頁目に収める字数上限（超過分は切らずに警告 — 人手で調整する方が安全）
MAX_CHARS = {"志望領域": 80, "志望動機": 190, "アピール": 165}


def base_html_path() -> Path:
    """基底となる完成版履歴書 HTML（config/resume.yaml で差し替え可）。"""
    return resume_assets.rirekisho_html()


# ---------------------------------------------------------------- prompt

_RULES = """# 厳守ルール
- **profile に無い数字・実績・経験・資格を絶対に作らない。** 不確実なものは書かない。
- **同じ欄に固定表示される行（後述）と矛盾しないこと。特に経験年数は固定行の表記をそのまま使う**
  （固定行が「約9年間」なら他の欄でも「約9年間」。別の年数を書かない）。
- 会社の事業領域と本人の実績を「対位」させる reframe は可（捏造ではない）。
- 履歴書の書面語。「貴社」を使う（「御社」は口頭語なので使わない）。
- 応募者の氏名・連絡先・住所には一切言及しない（システムが基底書式で保持する）。
- **勤務先の取引先・接続ブランド・協業キャリアの固有名は書かない**（守秘義務）。
  「大手共通ポイント」「通信キャリア」等の一般名詞で表す。自社プロダクト名は可。
- 誇張・スローガン・抽象語（「多様な経験」等）を避け、事実と固有名詞で書く。"""

_SCHEMA = """{
  "志望領域": "応募職種と担いたい領域を1文（80字以内）。JDの職種名に寄せる。",
  "志望動機": "この会社を志望する理由（190字以内・2〜3文）。会社のプロダクト/事業/技術/フェーズに具体的に言及し、本人の経験との接点で締める。一般論禁止。",
  "アピール": "JDの主要要件2〜3点に本人の事実で対位（165字以内）。固有名詞と実績を使う。"
}"""


def build_prompt(job: dict, facts: str, deid_profile: str,
                 keep_context: str = "") -> str:
    """LLM prompt。基底 HTML・実名・連絡先は含めない（PII 非外送）。

    keep_context は同じ欄に残る固定行の素テキスト（職業事実のみ）。
    年数などの数字が生成欄と食い違うのを防ぐために渡す。
    """
    from tools.gap_facts import match_evidence

    jd = (job.get("raw_jd") or "")[:MAX_JD_CHARS]
    salary = ""
    if job.get("salary_min"):
        salary = f"年収レンジ: {job['salary_min']}万〜{job.get('salary_max') or '?'}万円"
    gap = match_evidence(job)
    return f"""\
あなたは日本のハイクラス中途採用に精通した応募書類ライターです。
**履歴書の「志望の動機など」欄**に入れる 3 項目を、対象求人に特化して書いてください。

{_RULES}

# 対象求人
- 会社: {job.get('company') or '{{要確認}}'}
- 職位: {job.get('title')}
- 勤務地: {job.get('location') or '?'} / {salary}
- 企業タイプ(tier): {job.get('tier') or 'unknown'}

## JD 全文（抜粋）
{jd or '(本文なし)'}

# 確認済みの会社事実（Web 調査済み・優先的に使う）
{(facts or '')[:MAX_FACTS_CHARS] or '（未提供 — 会社固有の数字には言及しない）'}

{gap}
# 同じ欄に固定表示される行（変更不可・これと矛盾しない書き方にする）
{keep_context or '（なし）'}

# 応募者プロフィール（去識別化済み・職業情報のみ）
```yaml
{deid_profile}
```

# 出力（下記スキーマの JSON のみ。説明・コードフェンス以外を書かない）
{_SCHEMA}"""


# ---------------------------------------------------------------- LLM 出力処理

def extract_fields(raw: str) -> dict[str, str]:
    """LLM 出力 → {label: text}。JSON 抽出失敗は ValueError。"""
    t = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip())
    t = re.sub(r"\s*```$", "", t)
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            raise ValueError(f"JSON 抽出失敗: {(raw or '')[:200]}")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON 頂層が mapping でない")
    fields = {}
    for label in LLM_LABELS:
        val = str(data.get(label) or "").strip()
        if val:
            fields[label] = val
    if not fields:
        raise ValueError(f"生成欄が空（期待キー: {', '.join(LLM_LABELS)}）")
    return fields


def redact_fields(fields: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """生成欄に取引先ブランド名が残っていれば一般語に置換（守秘の最終関門）。"""
    from tools.redact import redact
    out, hits = {}, []
    for label, text in fields.items():
        clean, found = redact(text)
        out[label] = clean
        hits += [h for h in found if h not in hits]
    return out, hits


def check_fields(fields: dict[str, str], deid_profile: str) -> list[str]:
    """字数超過 + 数字の接地（profile に無い 2 桁以上の数字）を警告として返す。"""
    warnings: list[str] = []
    src_nums = set(re.findall(r"\d+", deid_profile))
    for label, text in fields.items():
        limit = MAX_CHARS.get(label)
        if limit and len(text) > limit:
            warnings.append(f"{label}: {len(text)}字（上限 {limit}）— 2頁目からの溢れに注意")
        for n in re.findall(r"\d+", text):
            if len(n) >= 2 and n not in src_nums:
                warnings.append(f"{label}: 数字 '{n}' が profile に無い → 要確認")
    return warnings


# ---------------------------------------------------------------- HTML 注入

_BLOCK_RE = re.compile(
    r'(<div class="freeform-block">)(.*?)(</div>\s*</section>)', re.DOTALL)
_IMG_RE = re.compile(r'(<img[^>]+src=")([^"]+)(")')
_AS_OF_RE = re.compile(r'(<div class="as-of">)([^<]*)(</div>)')

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".gif": "image/gif"}


def md_bold(text: str) -> Markup:
    """`**xxx**` → <strong>、その他は HTML escape。"""
    parts = re.split(r"\*\*(.+?)\*\*", str(text or ""))
    out = [str(escape(p)) if i % 2 == 0 else f"<strong>{escape(p)}</strong>"
           for i, p in enumerate(parts)]
    return Markup("".join(out))


def extract_row_text(base_html: str, label: str) -> str | None:
    """基底から指定ラベル行の <div class="freeform-text">…</div> を原文で抜く。"""
    m = re.search(
        r'<div class="freeform-label">' + re.escape(label) +
        r'</div>\s*(<div class="freeform-text">.*?</div>)',
        base_html, re.DOTALL)
    return m.group(1) if m else None


def keep_context(base_html: str) -> str:
    """固定行の素テキスト（職業事実のみ・PII なし）。prompt の矛盾防止に使う。"""
    lines = []
    for label in KEEP_LABELS:
        div = extract_row_text(base_html, label)
        if div:
            text = re.sub(r"<[^>]+>", "", div).strip()
            if text:
                lines.append(f"- {label}: {text}")
    return "\n".join(lines)


def build_block(fields: dict[str, str], base_html: str) -> str:
    """freeform-block の中身を組み立てる（KEEP_LABELS は基底原文を再利用）。"""
    rows: list[str] = []
    for label in ROW_ORDER:
        if label in KEEP_LABELS:
            text_div = extract_row_text(base_html, label)
        else:
            val = fields.get(label)
            text_div = (f'<div class="freeform-text">{md_bold(val)}</div>'
                        if val else None)
        if not text_div:
            continue
        rows.append(
            '      <div class="freeform-row">\n'
            f'        <div class="freeform-label">{label}</div>\n'
            f'        {text_div}\n'
            '      </div>')
    return "\n" + "\n".join(rows) + "\n    "


def embed_assets(html: str, base_dir: Path) -> str:
    """相対パスの画像を base64 に内嵌 — HTML/PDF を自己完結にする。"""
    def repl(m: re.Match) -> str:
        src = m.group(2)
        if src.startswith(("data:", "http://", "https://")):
            return m.group(0)
        p = (base_dir / src).resolve()
        if not p.exists():
            return m.group(0)
        mime = _MIME.get(p.suffix.lower(), "application/octet-stream")
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f"{m.group(1)}data:{mime};base64,{b64}{m.group(3)}"
    return _IMG_RE.sub(repl, html)


def inject(base_html: str, fields: dict[str, str], *,
           base_dir: Path | None = None, today: str | None = None) -> str:
    """基底 HTML の志望動機ブロックを差し替え、日付を更新し、画像を内嵌。"""
    if not _BLOCK_RE.search(base_html):
        raise ValueError("基底 HTML に freeform-block（志望の動機など）が見つからない")
    block = build_block(fields, base_html)
    # sub に関数を渡すので、置換文字列内の \ や \1 は展開されない（そのまま入る）
    html = _BLOCK_RE.sub(lambda m: m.group(1) + block + m.group(3), base_html, count=1)
    stamp = today or date.today().strftime("%Y年%-m月%-d日")
    html = _AS_OF_RE.sub(lambda m: f"{m.group(1)}{stamp} 現在{m.group(3)}", html, count=1)
    if base_dir is not None:
        html = embed_assets(html, base_dir)
    return html


# ---------------------------------------------------------------- entry

def _load_job(job_id: int) -> dict:
    from tracker.db import connect
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise SystemExit(f"job_id {job_id} が DB に見つかりません")
    return dict(row)


def generate(job: dict, out_path: Path, *, facts: str = "", no_llm: bool = False,
             prompts_dir: Path | None = None, timeout: int = 180,
             pdf: bool = True) -> bool:
    """志望動機を JD 特化した履歴書 HTML（+PDF）を out_path に生成。

    LLM 不可用 / 失敗時は prompt を落として False（呼び出し側は全局 PDF に退避）。
    """
    base = base_html_path()
    if not base.exists():
        print(f"  ✗ 基底履歴書が無い: {base}", file=sys.stderr)
        return False

    base_html = base.read_text(encoding="utf-8")
    deid = build_deid_profile(compact=True)
    prompt = build_prompt(job, facts, deid, keep_context(base_html))
    # 多層防御：万一 profile 由来以外の経路で実名等が混ざっても外に出さない
    from tools.pii_gate import scrub_for_external
    prompt, findings = scrub_for_external(prompt)
    if findings:
        print(f"  ⚠ prompt から PII {len(findings)} 件をスクラブ（送信前）", file=sys.stderr)

    def _drop_prompt(reason: str) -> bool:
        if prompts_dir is not None:
            prompts_dir.mkdir(parents=True, exist_ok=True)
            (prompts_dir / "rirekisho.prompt.md").write_text(prompt, encoding="utf-8")
            print(f"  → prompt 落地: _prompts/rirekisho.prompt.md（{reason}）")
        return False

    if no_llm:
        return _drop_prompt("--no-llm")

    from tools import miko_llm
    if not miko_llm.is_available():
        return _drop_prompt("指揮中心不可用")
    try:
        raw = miko_llm.text(prompt, timeout=timeout, opts={
            "accept": {"minChars": 120, "includesAll": ['"志望動機"']}})
    except Exception as e:
        return _drop_prompt(f"指揮中心失敗: {e}")
    if not raw:
        return _drop_prompt("指揮中心回空")

    try:
        fields = extract_fields(raw)
    except ValueError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return _drop_prompt("JSON 抽出失敗")

    fields, redacted = redact_fields(fields)
    if redacted:
        print(f"  ⚠ 取引先ブランド名 {len(redacted)} 件を一般語に置換: {', '.join(redacted)}",
              file=sys.stderr)
    warnings = check_fields(fields, deid + "\n" + keep_context(base_html))
    html = inject(base_html, fields, base_dir=base.parent)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"  ✓ {out_path.name}（志望動機 JD 特化）")
    for label in LLM_LABELS:
        if label in fields:
            print(f"     {label}: {fields[label][:60]}…")
    if warnings:
        print(f"  ⚠️ 検証 {len(warnings)} 件（人手確認）:")
        for w in warnings[:8]:
            print(f"     - {w}")

    if pdf:
        try:
            from resume.jp.render import export_pdf
            print(f"  ✓ {export_pdf(out_path).name}")
        except Exception as e:
            print(f"  ⚠ PDF 変換失敗（{e}）— HTML は生成済み", file=sys.stderr)
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="JD 特化の志望動機付き履歴書を生成")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--facts-file", help="公司 brief（01_company_brief.md）のパス")
    p.add_argument("--out", help="出力 HTML パス（既定 output/rirekisho-{id}.html）")
    p.add_argument("--no-llm", action="store_true", help="prompt 落地のみ")
    p.add_argument("--no-pdf", action="store_true", help="PDF を出さない")
    args = p.parse_args()

    job = _load_job(args.job_id)
    facts = ""
    if args.facts_file and Path(args.facts_file).exists():
        facts = Path(args.facts_file).read_text(encoding="utf-8")
    out = Path(args.out) if args.out else ROOT / "output" / f"rirekisho-{args.job_id}.html"
    ok = generate(job, out, facts=facts, no_llm=args.no_llm,
                  prompts_dir=out.parent / "_prompts", pdf=not args.no_pdf)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
