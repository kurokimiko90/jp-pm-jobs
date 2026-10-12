#!/usr/bin/env python3
"""求職準備 orchestrator — 投遞包（apply）と面接包（interview）の 2 モード。

投遞包（apply）: 公司調研 → cover note + 職務経歴書 + 志望動機 → 投遞判斷
面試包（interview）: 收到面試邀請後 → 想定問答 + 自己紹介 + checklist + 面接スライド

用法:
    python3 prep.py 123 apply                        # 投遞包（公司調研+履歷+志望動機）
    python3 prep.py 123 interview                    # 面試包（問答+checklist）
    python3 prep.py 123 apply --no-llm               # 只跑 zero-token stage + 產 prompt
    python3 prep.py 123 apply --facts research/x.md  # 注入已驗證的公司事實
    python3 prep.py 123 interview --stage qa          # 只跑面試包的某個 stage
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from tracker.db import connect
from tools import resume_assets
from tools.deid import build_deid_profile, load_resume_contact
from tools.gap_facts import match_evidence
from tools.locale import lang_directive, text as locale_text, tpl
from tools.resume_tailor import tailor_for_row

ROOT = Path(__file__).parent
APPLY_DIR = ROOT / "output" / "apply"
INTERVIEW_DIR = ROOT / "output" / "prep"
BULLETS_PATH = ROOT / "data" / "cognitive_bullets.md"
CHECKLIST_SRC = ROOT / "interview" / "checklists" / "japan.md"
SLIDES_TEMPLATE = ROOT / "interview" / "slides_template.html"
COMPANY_TEMPLATE = ROOT / "interview" / "companies" / "_template.md"

JD_MAX_CHARS = 3500

_CJK_RE = re.compile(r'[　-鿿＀-￯]')
_EN_THRESHOLD = 0.20  # CJK < 20% → English-first JD


def _detect_lang(job: dict) -> str:
    """投遞包語言自動偵測：CJK 字元比例決定生成英文包還是日文包。

    判斷邏輯（同步記載於 CLAUDE.md「投遞包語言自動偵測」）：
    - JD 的 CJK 比例 < 20% → "en"（外資 JD 通常 0% CJK）
    - JD 為空時 fallback：URL 含英文 ATS 網域 → "en"
    - 其他 → "jp"（日企 JD 通常 80%+ CJK；混合日企（如 25% CJK）仍判 jp）
    """
    jd = job.get("raw_jd") or ""
    if not jd:
        # Fallback: well-known EN ATS domains
        url = job.get("url") or ""
        if any(d in url for d in ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com")):
            return "en"
        return "jp"
    cjk_ratio = len(_CJK_RE.findall(jd)) / len(jd)
    return "en" if cjk_ratio < _EN_THRESHOLD else "jp"

GROUNDING_RULES = """\
厳守事項：
- 提供された素材（JD・事実リスト・本人プロフィール）以外の数字・実績を捏造しない
- 不明な事実は {{要確認}} と書く
- 本人の強みはプロフィール記載の「認知データ」と「実プロジェクト」のみを根拠にする
- 全文日本語、面接でそのまま使える具体性で書く
"""

BRIEF_GROUNDING_RULES = """\
厳守事項：
- 提供された素材（JD・事実リスト・本人プロフィール）以外の数字・実績を捏造しない
- 不明な事実は {{要確認}} と書く
- 本人の強みはプロフィール記載の「認知データ」と「実プロジェクト」のみを根拠にする
"""


# ---------------------------------------------------------------- helpers

def _load_job(job_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        sys.exit(f"job_id {job_id} が DB に見つかりません")
    return dict(row)


def _slug(text: str) -> str:
    s = re.sub(r"株式会社|有限会社|\s+", "", text or "company")
    s = re.sub(r"[^\w一-鿿぀-ヿa-zA-Z0-9]", "", s)
    return s[:20] or "company"


def _bullets_jp() -> str:
    if not BULLETS_PATH.exists():
        return ""
    lines = [l for l in BULLETS_PATH.read_text(encoding="utf-8").splitlines()
             if l.strip().startswith("- **JP:**")]
    return "\n".join(l.replace("- **JP:**", "-").strip() for l in lines)


def _llm_or_prompt(prompt: str, out_path: Path, pack_dir: Path,
                   no_llm: bool, timeout: int = 300,
                   accept: dict | None = None) -> bool:
    prompts_dir = pack_dir / "_prompts"
    if no_llm:
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / f"{out_path.stem}.prompt.md").write_text(prompt, encoding="utf-8")
        print(f"  → prompt 落地（--no-llm）: _prompts/{out_path.stem}.prompt.md")
        return False

    from tools import miko_llm
    if not miko_llm.is_available():
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / f"{out_path.stem}.prompt.md").write_text(prompt, encoding="utf-8")
        print(f"  ✗ 指揮中心不可用 → prompt 落地: _prompts/{out_path.stem}.prompt.md")
        return False
    try:
        opts = {"accept": accept} if accept else None
        result = miko_llm.text(prompt, timeout=timeout, opts=opts)
    except Exception as e:
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / f"{out_path.stem}.prompt.md").write_text(prompt, encoding="utf-8")
        print(f"  ✗ 指揮中心失敗（{e}）→ prompt 落地: _prompts/{out_path.stem}.prompt.md")
        return False
    if not result:
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / f"{out_path.stem}.prompt.md").write_text(prompt, encoding="utf-8")
        print(f"  ✗ 指揮中心回空 → prompt 落地: _prompts/{out_path.stem}.prompt.md")
        return False

    out_path.write_text(result, encoding="utf-8")
    print(f"  ✓ {out_path.relative_to(ROOT)}")
    return True


def _deid_profile_text() -> str:
    """去識別化 candidate profile — 送 LLM 安全。"""
    return build_deid_profile()


def _submitted_docs_facts() -> str:
    """添付する履歴書に固定表示される記載（経験年数など）。

    メール本文と履歴書で年数が食い違うと採用側に矛盾として映るため、
    実際に添付する完成版の記載を prompt に渡して合わせさせる。
    """
    try:
        from tools.rirekisho_tailor import base_html_path, keep_context
        base = base_html_path()
        if not base.exists():
            return ""
        ctx = keep_context(base.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not ctx:
        return ""
    from tools.redact import redact
    ctx, _ = redact(ctx)
    return ("# 添付する履歴書の固定記載（**数字はこれに合わせる**・別の年数を書かない）\n"
            f"{ctx}\n")


def _context_block(job: dict, facts: str, *, with_gap: bool = False) -> str:
    """求人 + 会社事実 + 去識別化プロフィール。with_gap で要件対位メモも足す。"""
    jd = (job.get("raw_jd") or "")[:JD_MAX_CHARS]
    salary = ""
    if job.get("salary_min"):
        salary = f"年収レンジ: {job['salary_min']}万〜{job.get('salary_max') or '?'}万円"
    deid = _deid_profile_text()
    gap = match_evidence(job) if with_gap else ""
    docs = _submitted_docs_facts() if with_gap else ""
    return f"""\
# 対象求人
- 会社: {job.get('company') or '{{要確認}}'}
- 職位: {job['title']}
- 勤務地: {job.get('location') or '?'} / {salary}
- 企業タイプ(tier): {job.get('tier') or 'unknown'}

# JD 全文（抜粋）
{jd}

# 確認済みの会社事実（Web 調査済み・これを優先的に使う）
{facts or '（事実リスト未提供 — 会社固有の数字は {{要確認}} とすること）'}

{gap}
{docs}
# 応募者プロフィール（去識別化済み・職業情報のみ）
```yaml
{deid}
```
"""


# ================================================================
#  投遞包 stages（apply）
# ================================================================

def apply_brief(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """公司調研 — 決定是否值得投遞"""
    print("[apply 1/3] 会社調研")
    template = COMPANY_TEMPLATE.read_text(encoding="utf-8") if COMPANY_TEMPLATE.exists() else ""
    prompt = f"""\
あなたは日本の IT 業界に詳しいプロダクトリサーチャーです。
以下の素材から、応募判断のための 1 ページの会社 brief を作成してください。

{BRIEF_GROUNDING_RULES}
- 「予想される質問」5 つと「逆質問」(HR/HM/Exec 各3つ) は JD の内容に即して具体的に
- 「私の applying angle」は応募者プロフィールと JD の重なりから 2-3 段落で書く
- {lang_directive("brief")}

{_context_block(job, facts)}

# 出力テンプレート（この構造で出力）
{template}
"""
    _llm_or_prompt(prompt, pack_dir / "01_company_brief.md", pack_dir, no_llm,
                   accept={"minChars": 800, "minLines": 15,
                           "includesAny": ["投遞", "applying angle", "Go", "逆質問", "逆提問"]})


def apply_resume(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """cover note + 定制職務経歴書"""
    print("[apply 2/3] 提出書類（cover note + 定制職務経歴書）")
    try:
        cover_path = tailor_for_row(job)
        print(f"  ✓ cover note: {cover_path}")
    except Exception as e:
        print(f"  ✗ cover note 失敗: {e}", file=sys.stderr)
        cover_path = None

    # 定制職務経歴書（去識別化 → LLM reframe → 本地注入姓名 → render）
    shokumu_out = pack_dir / "04_shokumu.html"
    tailored_ok = False
    try:
        from tools.shokumu_tailor import generate as gen_shokumu
        tailored_ok = gen_shokumu(job, shokumu_out, no_llm=no_llm,
                                  prompts_dir=pack_dir / "_prompts")
    except Exception as e:
        print(f"  ✗ 定制職務経歴書 失敗: {e}", file=sys.stderr)

    shokumu_html = resume_assets.shokumu_html()
    if not tailored_ok and shokumu_html.exists():
        shutil.copy(shokumu_html, shokumu_out)
        print("  → fallback：全局 shokumu を 04_shokumu.html にコピー")
    shokumu_pdf = resume_assets.shokumu_pdf()
    rirekisho_pdf = resume_assets.rirekisho_pdf()

    lines = ["# 提出書類\n", "## 投遞用（そのまま送る完成版）"]
    if shokumu_pdf.exists():
        lines.append(f"- 職務経歴書 PDF: `{shokumu_pdf.relative_to(ROOT)}`")
    if rirekisho_pdf.exists():
        lines.append(f"- 履歴書 PDF: `{rirekisho_pdf.relative_to(ROOT)}`"
                     "（志望動機を JD 特化するなら `python3 -m tools.rirekisho_tailor`）")
    lines.append("\n## 参考（LLM reframe・そのまま送らない）")
    if cover_path:
        lines.append(f"- cover note: `{cover_path}`")
    if shokumu_out.exists():
        kind = "定制" if tailored_ok else "全局(fallback)"
        lines.append(f"- 職務経歴書 HTML（{kind}・数字と固有名詞を要検証）: `04_shokumu.html`")
    lines.append(f"- 完成版 職務経歴書 HTML: `{shokumu_html.relative_to(ROOT)}`")
    lines.append(f"\n定制版を再生成: `python3 -m tools.shokumu_tailor --job-id {job['id']}`")
    lines.append(f"全局版を更新: `python3 -m resume.jp.render`")

    (pack_dir / "02_documents.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ 02_documents.md")


def _shibou_prompt_direct(job: dict, facts: str) -> str:
    """直接応募向け — 会社への志望動機を中心に。"""
    return f"""\
日本の中途採用に詳しいキャリアアドバイザーとして、**企業に直接応募する**書類を作成してください。
相手は企業の採用担当者・現場マネージャーです。

{GROUNDING_RULES}
追加ルール:
- JD の要件 2〜3 点に本人の具体的事実（プロダクト名・役割・規模）で対位させる
- 抽象語だけの自己評価を書かない。未充足の要件には触れない（埋め合わせを捏造しない）

{_context_block(job, facts, with_gap=True)}

# 出力（Markdown）
1. **志望動機**（300字・3段構成：取り組みたい領域→**この会社ならではの独自性**→貢献内容）
   - 会社のプロダクト・事業・技術に具体的に言及すること
2. **自己PR**（300字・認知データの強みを1つ軸に、具体エピソード型）
3. **転職理由**（200字・志望動機と一貫し、前職批判なし）
4. 上記3つの「面接口頭版」（それぞれ45秒で話せる話し言葉版）
"""


def _shibou_prompt_agent(job: dict, facts: str) -> str:
    """紹介会社・派遣会社向け — 職位適合性と転職動機を中心に。"""
    posting_label = "人材紹介会社" if job.get("posting_type") == "shokai" else "人材派遣会社"
    company = job.get("company") or "{{紹介先企業}}"
    return f"""\
日本の中途採用に詳しいキャリアアドバイザーとして、**{posting_label}経由で応募する**書類を作成してください。
書類の読み手は{posting_label}の担当コンサルタントです（企業の採用担当ではありません）。
紹介先企業: {company}

{GROUNDING_RULES}
追加ルール:
- 「貴社」は使わない（読み手は紹介会社であり紹介先企業ではない）
- 紹介先企業名は「{company}様」または社名で呼ぶ
- コンサルタントが紹介先に推薦しやすい表現にする（推薦理由を言語化しやすく）
- JD の要件 2〜3 点に本人の具体的事実（プロダクト名・役割・規模）で対位させる
- 未充足の要件には触れない（埋め合わせを捏造しない）

{_context_block(job, facts, with_gap=True)}

# 出力（Markdown）
1. **志望動機**（300字・3段構成：取り組みたい領域→**紹介先企業のどこに魅力を感じるか**→貢献内容）
   - 「貴社」ではなく企業名で言及
2. **自己PR**（300字・認知データの強みを1つ軸に、具体エピソード型）
   - コンサルタントが推薦文を書きやすい、客観的で転用可能な表現にする
3. **転職理由**（200字・志望動機と一貫し、前職批判なし）
4. **希望条件**（3行：希望年収レンジ・勤務地・転職時期）
5. 1〜3 の「面接口頭版」（それぞれ45秒で話せる話し言葉版）
"""


def apply_shibou(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """志望動機・自己PR・転職理由（posting_type で prompt 切替）"""
    pt = job.get("posting_type") or "direct"
    is_agent = pt in ("shokai", "haken", "agent")
    label = "紹介/派遣向け" if is_agent else "直接応募向け"
    print(f"[apply 3/3] 志望動機・自己PR・転職理由（{label}）")

    prompt = _shibou_prompt_agent(job, facts) if is_agent else _shibou_prompt_direct(job, facts)

    required = ["志望動機", "自己PR", "転職理由"]
    if is_agent:
        required.append("希望条件")

    _llm_or_prompt(prompt, pack_dir / "03_shibou_doki.md", pack_dir, no_llm,
                   accept={"minChars": 600, "minLines": 10,
                           "includesAll": required})


def apply_mail(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """応募メール（官網直投用 cover letter）— 件名 + 敬語定式本文。

    LLM は {{NAME}} / {{EMAIL}} / {{PHONE}} プレースホルダで生成し、
    実名・連絡先はローカルで置換（PII を LLM に渡さない）。
    """
    print("[apply mail] 応募メール（官網直投）")
    prompt = f"""\
日本の中途採用マナーに詳しいキャリアアドバイザーとして、企業の採用窓口へ
**直接応募するメール**を作成してください。読み手は企業の採用担当者です。

{GROUNDING_RULES}
追加ルール:
- 応募者の氏名は {{{{NAME}}}}、メールは {{{{EMAIL}}}}、電話は {{{{PHONE}}}} のまま出力（システムが置換する）
- ビジネス敬語。過度な自慢や誇張なし。全体 450〜650 字
- 添付書類（職務経歴書・履歴書）に言及する

# 説得力の作り方（最重要・ここで差がつく）
- **JD の要件のうち 2〜3 点を選び、各点に本人の具体的事実で応える**
  （プロダクト名・役割・技術・規模のいずれかを必ず含める）。要件対位メモを起点にする。
- 「多様な経験」「幅広く貢献」等の抽象語だけの自己評価は書かない。事実で語る。
- 会社側の事情（事業フェーズ・プロダクト・技術選択）に 1 箇所は具体的に触れる。
  会社事実が未確認なら固有の数字は書かず、JD に書かれた内容だけを使う。
- プロフィールに無い数字・実績・資格は一切書かない。未充足の要件には触れない
  （言い訳も埋め合わせも書かない）。
- **経験年数は添付履歴書の固定記載に合わせる**（別の年数を書くと書類間で矛盾する）。
- **勤務先の取引先・接続ブランド・協業キャリアの固有名は書かない**（守秘義務）。
  「大手共通ポイント」「通信キャリア」等の一般名詞で表す。自社プロダクト名は可。

{_context_block(job, facts, with_gap=True)}

# 出力フォーマット（この構造のまま・Markdown 見出しなし）

件名: 【応募】{job['title'][:30]}／{{{{NAME}}}}

{job.get('company') or '{{要確認}}'} 採用ご担当者様

（①応募の経緯 1-2 文：求人情報を拝見し応募する旨＋なぜこの職位かを一言）

（②要件対位 4-6 文：現職・経験年数を 1 文で述べた後、JD の主要要件 2〜3 点それぞれに
　本人の具体的事実で応える。1 要件 = 1〜2 文、固有名詞と役割を必ず入れる）

（③添付案内 1 文：職務経歴書・履歴書を添付、ご査収ください）

（④結び 1-2 文：面接機会への希望、御礼）

----------------
{{{{NAME}}}}
Email: {{{{EMAIL}}}}
Tel: {{{{PHONE}}}}
{{{{LINKEDIN_LINE}}}}{{{{GITHUB_LINE}}}}----------------
"""
    out = pack_dir / "06_oubo_mail.md"
    ok = _llm_or_prompt(prompt, out, pack_dir, no_llm,
                        accept={"minChars": 400, "includesAll": ["件名", "応募"]})
    if ok:
        rc = load_resume_contact()
        text = out.read_text(encoding="utf-8")
        # 守秘の最終関門：取引先ブランド名が残っていれば一般語に置換して警告
        from tools.redact import redact
        text, redacted = redact(text)
        if redacted:
            print(f"  ⚠ 取引先ブランド名 {len(redacted)} 件を一般語に置換: "
                  f"{', '.join(redacted)}", file=sys.stderr)
        text = text.replace("{{NAME}}", rc.get("name_ja") or "")
        text = text.replace("{{EMAIL}}", rc.get("email") or "")
        text = text.replace("{{PHONE}}", str(rc.get("phone") or ""))
        linkedin = rc.get("linkedin") or ""
        github = rc.get("github") or ""
        text = text.replace("{{LINKEDIN_LINE}}", f"LinkedIn: {linkedin}\n" if linkedin else "")
        text = text.replace("{{GITHUB_LINE}}", f"GitHub: {github}\n" if github else "")
        out.write_text(text, encoding="utf-8")


APPLY_STAGES = {
    "brief": apply_brief,
    "resume": apply_resume,
    "shibou": apply_shibou,
    "mail": apply_mail,  # 官網直投用・prep.py apply のデフォルトには含めない
}


# ================================================================
#  外資投遞包 stages（apply --lang en）
#  最適化: 2 LLM calls per job（brief_cover 合併 1 call + resume 1 call）
# ================================================================

EN_MASTER_PATH = ROOT / "resume" / "en" / "master.md"

_EN_PII_PATTERNS: list[tuple[str, str]] = []


def _deid_en_master() -> str:
    """去識別化英文 master resume — 移除姓名/聯絡方式後送 LLM。"""
    if not EN_MASTER_PATH.exists():
        return ""
    text = EN_MASTER_PATH.read_text(encoding="utf-8")
    if not _EN_PII_PATTERNS:
        rc = load_resume_contact()
        for v in (rc.get("name_ja"), rc.get("name_romaji")):
            if v:
                _EN_PII_PATTERNS.append((str(v), "本人"))
        for key in ("email", "phone", "github", "linkedin"):
            v = rc.get(key)
            if isinstance(v, str) and v:
                _EN_PII_PATTERNS.append((v, "***"))
    for old, new in _EN_PII_PATTERNS:
        text = text.replace(old, new)
    import re as _re
    text = _re.sub(r"\+\d{1,3}[-\d]{6,15}", "***", text)
    text = _re.sub(r"\[Portfolio\]\([^)]*\)", "[Portfolio](***)", text)
    text = _re.sub(r"\[LinkedIn\]\([^)]*\)", "[LinkedIn](***)", text)
    return text


EN_GROUNDING_RULES = """\
STRICT RULES (zero fabrication):
- Use ONLY numbers, metrics, and facts from the provided profile and JD.
- If a fact is unknown, write {{to_verify}}.
- Strengths must be grounded in: cognitive data from the profile, real projects, and verified work history.
- All experience bullets must reflect actual projects from the profile — no invented outcomes.
"""


def _en_context_block(job: dict, facts: str) -> str:
    jd = (job.get("raw_jd") or "")[:JD_MAX_CHARS]
    salary = ""
    if job.get("salary_min"):
        salary = f"Salary range: {job['salary_min']}–{job.get('salary_max') or '?'} 万円"
    deid = _deid_profile_text()
    master = _deid_en_master()
    return f"""\
# Target Role
- Company: {job.get('company') or '{{to_verify}}'}
- Title: {job['title']}
- Location: {job.get('location') or '?'} / {salary}
- Tier: {job.get('tier') or 'unknown'}

# JD (excerpt)
{jd}

# Verified Company Facts (web research — use these over JD marketing copy)
{facts or '(no facts provided — mark company-specific claims as {{to_verify}})'}

# Candidate Profile (de-identified, career info only)
```yaml
{deid}
```

# Full English Resume (de-identified, source of truth for bullet reframing)
{master[:4000]}
"""


def apply_en_brief_cover(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """1 LLM call → 01_company_brief.md (reader_lang) + 03_cover_letter.md (English)"""
    print("[apply-en 1/2] 公司 brief + cover letter (English)")
    prompt = f"""\
You are a senior PM career coach. Using the materials below, produce TWO outputs in one response.

{EN_GROUNDING_RULES}

{_en_context_block(job, facts)}

---

## OUTPUT 1 — Company Brief (internal use only)

{lang_directive("brief")}

Format:
### 公司概況
（2-3 句：業務・規模・融資階段・特色）

### 產品方向
（這個 role 要做什麼，1-2 段）

### 投遞角度（我的 applying angle）
（candidate 與 JD 的重疊點，2-3 段，基於 profile）

### 預期問題（5 題）
（面試官最可能問的，JD 特化）

### 逆提問（HR / HM / Exec 各 2 題）
（基於 JD 和公司調研）

### 投遞判斷
- Go / No-Go + 1 sentence reason

---

## OUTPUT 2 — Cover Letter (in English, ~280 words, ready to send)

Format:
Dear Hiring Team,

[Opening: 1 sentence hook — what specifically draws you to THIS company/role, not generic]

[Body paragraph 1: Strongest relevant experience, concrete, 2-3 sentences. Ground in profile data only.]

[Body paragraph 2: Specific connection between your LLM/AI engineering experience and this role's technical needs. 2-3 sentences.]

[Closing: 1-2 sentences, forward-looking, no clichés]

Best regards,
{{CANDIDATE_NAME}}
{{CANDIDATE_EMAIL}} | Tokyo, Japan (Permanent Resident)
"""
    out = pack_dir / "01_company_brief.md"
    if _llm_or_prompt(prompt, out, pack_dir, no_llm, timeout=360,
                      accept={"minChars": 1200, "minLines": 20,
                              "includesAll": ["OUTPUT 2", "Dear Hiring"]}):
        raw = out.read_text(encoding="utf-8")
        # split on the OUTPUT 2 marker and save separately
        marker = "## OUTPUT 2"
        if marker in raw:
            brief_part, cover_part = raw.split(marker, 1)
            out.write_text(brief_part.strip(), encoding="utf-8")
            rc = load_resume_contact()
            cover_text = cover_part.strip()
            cover_text = cover_text.replace("{{CANDIDATE_NAME}}", rc.get("name_romaji", ""))
            cover_text = cover_text.replace("{{CANDIDATE_EMAIL}}", rc.get("email", ""))
            (pack_dir / "03_cover_letter.md").write_text(
                "## Cover Letter\n\n" + cover_text, encoding="utf-8"
            )
            print(f"  ✓ 03_cover_letter.md (split from combined output)")
        else:
            # fallback: write cover letter as separate file with note
            (pack_dir / "03_cover_letter.md").write_text(
                "<!-- cover letter not split — check 01_company_brief.md -->", encoding="utf-8"
            )


def apply_en_resume(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """1 LLM call → 04_resume_tailored.md (English, JD-optimised)"""
    print("[apply-en 2/2] 定制英文履歷")
    master = _deid_en_master()
    jd = (job.get("raw_jd") or "")[:JD_MAX_CHARS]
    prompt = f"""\
You are an expert PM resume writer. Produce a tailored English resume for the role below.

{EN_GROUNDING_RULES}

# Target Role
Company: {job.get('company')} | Title: {job['title']}

# JD (excerpt)
{jd}

# Base Resume (de-identified, source of truth — reframe bullets, do NOT invent new facts)
{master}

---

## Task
Produce a complete 1-2 page English resume in Markdown.

Rules:
1. Keep the same structure as the base resume (Summary → Key Achievements → Experience → Tech Stack → Working Style → Skills → Education → Projects).
2. In Summary: Add 1 sentence that directly references what this company builds.
3. In Key Achievements: Reorder to put the most JD-relevant achievements first. Do not add new bullets.
4. In Experience bullets: Rewrite the top 2-3 bullets per role to emphasise skills the JD explicitly asks for. Keep facts from the base resume — no new numbers.
5. In the AI/LLM Tech Stack table: Surface the capabilities most relevant to this JD first.
6. Do NOT change Working Style section (it's data-driven, leave as-is).
7. Mark any uncertainty with {{to_verify}}.

Output: complete Markdown resume only, no explanation.
"""
    _llm_or_prompt(prompt, pack_dir / "04_resume_tailored.md", pack_dir, no_llm, timeout=360,
                   accept={"minChars": 1500, "minLines": 30,
                           "includesAny": ["Experience", "Summary", "Education"]})


APPLY_EN_STAGES = {
    "brief_cover": apply_en_brief_cover,
    "resume": apply_en_resume,
}


def _gap_block(job: dict) -> str:
    """gap_analysis から recommend / matched / gaps を README に埋める。"""
    raw = job.get("gap_analysis")
    if not raw:
        return ""
    try:
        g = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return ""
    rec = g.get("recommend_score", "—")
    matched = g.get("matched", [])
    gaps = g.get("gaps", [])
    lines = [f"\n## Gap Analysis ({rec})\n"]
    if matched:
        lines.append(locale_text("gap_block_matched"))
        for m in matched:
            lines.append(f"- ✅ {m[:120]}")
    if gaps:
        lines.append(f"\n{locale_text('gap_block_gaps')}")
        for gap in gaps:
            lines.append(f"- ⚠️ {gap[:120]}")
    return "\n".join(lines) + "\n"


def _write_en_apply_readme(job: dict, pack_dir: Path) -> None:
    gap = _gap_block(job)
    (pack_dir / "00_README.md").write_text(
        tpl(
            "apply_readme_en",
            company=job.get("company"),
            title=job["title"][:40],
            date=date.today().isoformat(),
            job_id=job["id"],
            score=job.get("score"),
            url=job.get("url"),
            gap=gap,
        ),
        encoding="utf-8",
    )


def _write_apply_readme(job: dict, pack_dir: Path) -> None:
    gap = _gap_block(job)
    (pack_dir / "00_README.md").write_text(
        tpl(
            "apply_readme_jp",
            company=job.get("company"),
            title=job["title"][:40],
            date=date.today().isoformat(),
            job_id=job["id"],
            score=job.get("score"),
            url=job.get("url"),
            gap=gap,
        ),
        encoding="utf-8",
    )


# ================================================================
#  面試包 stages（interview）— 收到面試邀請後才跑
# ================================================================

def interview_qa(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """想定問答 — 正典題庫 + JD 特化 + 深掘りを 1 枚に組む（interview/qa/）。"""
    print("[interview 1/6] 想定問答")
    if no_llm:
        print("  → no-llm: スキップ（qa stage は指揮中心が必要）")
        return
    # build 関数を直接取る。`from interview.qa import build` だと、パッケージの
    # __getattr__ が副作用で親 __dict__ に submodule を差し込むため、モジュールが返る。
    from interview.qa.build import AUDIT_FILENAME, QA_FILENAME, build as qa_build
    stats = qa_build(job, facts, pack_dir)
    print(f"  ✓ {QA_FILENAME}（全 {stats['questions']} 問 / 定番 {stats['core']}"
          f" / JD 特化 {stats['jd']} / 深掘り {stats['drilldown']}"
          f" / 定番と重複で除外 {stats['dropped']}）")
    print(f"  要件覆蓋 {stats['coverage']} / 修復 {stats['repair_rounds']} 巡"
          f" / 未解決の閘門 {stats['blocking']} 件"
          f" / 使い回しの多い実績 {stats['overused']} 種")
    if stats.get("score") is None:
        print(f"  ⚠ 面接官の目での採点は取れませんでした（機械閘門のみ） — 詳細は {AUDIT_FILENAME}",
              file=sys.stderr)
    else:
        mark = "合格線 80 到達" if stats["score"] >= 80 else "合格線 80 未満"
        print(f"  面接官採点 {stats['score']}/100（{mark}） / 批評 {stats['critique_rounds']} 巡"
              f" / 残る指摘 {stats['remaining']} 件 — 詳細は {AUDIT_FILENAME}")


JIKOSHOUKAI_GROUNDING_RULES = """\
厳守事項（零編造）：
- 提供された素材（JD・事実リスト・要件対位メモ・応募者プロフィール）以外の数字・実績を捏造しない
- 不明な事実は {{要確認}} と書く
- 「履歴書以外の情報」（働き方・思考の型・強みの形成過程・大事にしていること）は
  プロフィールの differentiators（cognitive_data / narrative / career_vision / self_pr_jp）
  のみを根拠にし、そこに無い性格描写を作らない
- 志望動機・転職理由は①〜⑤の中で軽く触れる程度に留め、完結させない
  （面接で「志望動機を教えてください」と別途聞かれたときの持ち駒を残す）
"""


def interview_jikoshoukai(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """自己紹介（オープニング）— JD 適合度分析 → 1分版台本 → 中文訳 → 根拠 → 想定印象 → 強み接続。

    面接コーチ手法（ユーザー提供のプロンプト設計）をこのプロジェクトの素材供給に接続したもの:
    JD・要件対位メモ（gap 分析）・去識別化プロフィール（differentiators に「履歴書に出ない
    情報」が入っている）を渡し、A〜F の構造で 1 本の Markdown を作らせる。
    """
    print("[interview 2/6] 自己紹介")
    from tools.locale import lang_directive, reader_lang

    lang = reader_lang()
    translation_heading = {"zh": "C. 中文翻譯", "en": "C. English Translation", "ja": None}[lang]
    c_instruction = (
        f"## {translation_heading}\n（B をそのまま訳す。意訳しすぎない）"
        if translation_heading else
        "（読み手は日本語話者のため、C. 翻訳セクションは省略してよい）"
    )
    required_sections = ["A.", "B.", "D.", "E.", "F."]
    if translation_heading:
        required_sections.append("C.")

    prompt = f"""\
あなたは日本の転職市場に精通した面接コーチです。以下の素材から、
面接冒頭で使う「自己紹介」を設計してください。

# 設計原則
1. **JD への高い適合度** — 提供素材から JD が最も重視する能力・経験・人物特質 3〜5 個を
   まず特定し、自己紹介はそこに最も関連する経験を優先する（経歴を平均的に紹介しない）。
2. **職務経歴書の朗読にしない** — 経歴の再列挙ではなく、「履歴書に書かれていない情報」
   （働き方・物事の考え方・強みがどう形成されたか・仕事で大事にしていること・
   なぜこの職位に向いているか）を必ず加える。この情報の根拠は
   プロフィールの differentiators（cognitive_data / narrative / career_vision / self_pr_jp）
   のみとする。
3. **日本の面接慣習に合う話し言葉** — 自然で落ち着いたビジネス日本語。誇張・広告的表現
   なし。過度な謙遜もしない。「結論として」等の前置き語で始めない。「貴社」ではなく「御社」。
4. **1 分版** — 250〜300 字程度で、面接で一息に話せる長さ。
5. **構成**：①簡単な経歴紹介 → ②JD に最も関連する中核能力 → ③その働き方を示す具体的な
   一場面（状況→判断→結果、簡潔に）→ ④その能力がこの職位にどう効くか
   （志望動機に深入りしすぎない）→ ⑤自然な締め。
6. **志望動機・転職理由は完結させない** — 軽く触れる程度に留め、後で単独に聞かれたときの
   ネタを残す。

{JIKOSHOUKAI_GROUNDING_RULES}
{lang_directive("jikoshoukai")}

{_context_block(job, facts, with_gap=True)}

# 出力（この構造・見出しどおりに Markdown で）
## A. 分析結果
- JD が最も重視する能力 Top 3〜5
- 本人の経歴の中で JD と最も合致する部分
- 職務経歴書で十分書けているため自己紹介で繰り返さない部分
- 自己紹介で補うべき「履歴書以外の情報」
- 面接官に持ち帰ってほしい 3 つの印象

## B. 1分鐘日文自己紹介
（① 〜 ⑤ の構成で書いた、そのまま声に出せる日本語。見出し・番号は付けず地の文で）

{c_instruction}

## D. 為什麼這樣寫
①〜⑤ の各段落が、A で特定した JD 要件のどれに対応するかを 1 行ずつ

## E. 面試官聽完後最可能形成的3個印象

## F. 「あなたの強みは何ですか？」と聞かれた場合の回答
自己紹介と重複しない切り口で、自然に続く回答（日本語・150字前後）
"""
    out = pack_dir / "06_jikoshoukai.md"
    # notIncludes 等の語調チェックは accept に入れない — 禁止語 1 つで gateway が
    # brain 総当たりの末に timeout/500 を返すことがある（interview/CLAUDE.md 参照）。
    # 「貴社」は下で機械置換するので、accept は形式（分量・見出し）だけに絞る。
    ok = _llm_or_prompt(prompt, out, pack_dir, no_llm, timeout=420,
                        accept={"minChars": 800, "includesAll": required_sections})
    if not ok:
        return

    text = out.read_text(encoding="utf-8")
    if "貴社" in text:
        text = text.replace("貴社", "御社")
        print("  → 「貴社」を「御社」へ機械置換")
    from tools.redact import redact
    text, redacted = redact(text)
    if redacted:
        print(f"  ⚠ 取引先ブランド名 {len(redacted)} 件を一般語に置換: {', '.join(redacted)}",
              file=sys.stderr)

    # 見出しを "## B. " で出すとは限らない（LLM が地の文の "B. " だけで書くこともある）
    # ため、行頭の "A."〜"F." そのものを区切りにする — accept の includesAll と同じ緩さで揃える。
    markers = list(re.finditer(r"(?m)^#{0,3}\s*([A-F])\.\s*.*$", text))
    sections = {mo.group(1): text[mo.end():markers[i + 1].start() if i + 1 < len(markers) else len(text)]
                for i, mo in enumerate(markers)}
    b_body = sections.get("B", "")
    if b_body:
        jp_chars = len(re.sub(r"\s", "", b_body))
        if not (220 <= jp_chars <= 340):
            print(f"  ⚠ 自己紹介本文が {jp_chars} 字（目安 250〜300 字から外れている）",
                  file=sys.stderr)

    out.write_text(text, encoding="utf-8")
    print(f"  ✓ {out.name}")


QA_SUPPLEMENT_FILENAME = "07_qa_supplement.md"

# エージェント面談・一次面接で定番の確認事項 18 問。正典 49 問
# （interview/question-bank/core/）と重なる設問（転職理由・キャリアプラン・希望年収・
# 逆質問など）も含めてすべて生成する — ユーザー判断で重複を許容（去重すると同じ設問が
# 別の角度で聞かれたときに手持ちの答えが単調になるため）。
QA_SUPPLEMENT_GROUPS: list[tuple[str, list[str]]] = [
    ("現職・経験の棚卸し", [
        "現職・前職で何をしているか",
        "現職・前職で一番頑張ったこととエピソード",
        "大変な時に頑張った経験",
        "長期的に継続できたこととその詳細",
    ]),
    ("自己認識・他者評価", [
        "仕事で大切にしていること・価値観",
        "周囲からどんな人と言われますか？",
    ]),
    ("退職・転職理由とキャリアプラン", [
        "退職理由",
        "転職しようと思ったキッカケ",
        "転職理由",
        "3・5・10年後の将来のキャリアプランは？",
    ]),
    ("志望動機と当社への熱意", [
        "志望動機（なぜこの業界・業種なのかも有）",
        "入社後挑戦したいこと",
        "競合他社ではなく、なぜ当社なのか？",
        "当社への志望度とどこに魅力を感じておりますか？",
        "当社への不安はあるか",
    ]),
    ("条件・進捗確認と逆質問", [
        "希望入社日や希望年収",
        "併願先の状況とフェーズ",
        "逆質問",
    ]),
]

QA_SUPPLEMENT_GROUNDING_RULES = """\
厳守事項（零編造）：
- 提供された素材（JD・事実リスト・要件対位メモ・応募者プロフィール・既存の想定問答）以外の
  数字・実績を捏造しない
- 不明な事実は {{要確認}} と書く
- 「履歴書以外の情報」（働き方・思考の型・価値観・他己評価）は
  プロフィールの differentiators（cognitive_data / narrative / career_vision / self_pr_jp）
  のみを根拠にする
- 01_interview_qa.md が既に生成されている場合、そこでの回答内容・数字・エピソードと矛盾しない
  （同じ経験を指す設問は同じ事実で答える。言い回しは変えてよい）
- 「退職理由」「転職しようと思ったキッカケ」「転職理由」は同じ経験を別角度から聞く3問なので、
  事実は同一に保ちつつ切り口（きっかけ／プロセス／結論）を変えて重複感を減らす
- 「志望動機」「入社後挑戦したいこと」「競合他社ではなく、なぜ当社なのか」
  「当社への志望度」「当社への不安はあるか」は同じ企業への熱意を扱う5問なので、
  事実の重なりは許容しつつ角度（Why this industry／Why now／Why not competitor／
  How much do you want it／リスクの正直な言語化）を明確に分ける
- 「希望入社日や希望年収」「併願先の状況とフェーズ」は転職エージェント面談で典型的に聞かれる
  条件確認。誇張・虚偽の条件を書かない
- 「逆質問」の B は自己紹介の話し言葉ではなく、面接官に聞く質問そのものを 3〜5 個、箇条書きで書く
"""


def _qa_supplement_question_block(n: int, question: str, c_instruction: str) -> str:
    return f"""\
## Q{n}. {question}
### A. 分析結果
（この設問が確認したい能力・懸念点／JD 要件との関連／回答で持ち帰ってほしい印象）
### B. 回答本文（日本語）
（実際に声に出せる日本語。200〜300字程度。逆質問と希望条件系の設問は質問・条件そのものを箇条書きで）
{c_instruction}
### D. 為什麼這樣寫
（A で特定した意図にどう対応しているか 1〜2 行）
### E. 面試官聽完後最可能形成的印象
### F. 備用延伸回答
（深掘りされた場合の追加の切り口。B と重複しない）
"""


def interview_qa_supplement(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """補充想定問答（エージェント面談定番 18 問）— jikoshoukai と同じ A〜F 構造で設問ごとに分析。

    デフォルトの実行対象には含めない（`--stage qa_supplement` 明示時のみ）。
    正典 49 問と重なる設問も含めて全問生成するため LLM 呼び出しがバッチ 5 回に及ぶ。
    01_interview_qa.md が既にあれば読み込み、経験の中身が矛盾しないようにする
    （回答形式は A〜F 構造で異なるが、事実は同じでなければならない）。
    """
    print(f"[interview] 補充想定問答（{sum(len(qs) for _, qs in QA_SUPPLEMENT_GROUPS)} 問・{len(QA_SUPPLEMENT_GROUPS)} バッチ）")
    from tools.locale import lang_directive, reader_lang
    from tools.redact import redact

    lang = reader_lang()
    translation_heading = {"zh": "C. 中文翻譯", "en": "C. English Translation", "ja": None}[lang]
    c_instruction = (
        f"### {translation_heading}\n（B をそのまま訳す。意訳しすぎない。質問・箇条書きの場合も同様に訳す）"
        if translation_heading else
        "（読み手は日本語話者のため、C. 翻訳セクションは省略してよい）"
    )

    existing_qa = ""
    qa_path = pack_dir / "01_interview_qa.md"
    if qa_path.exists():
        existing_qa = qa_path.read_text(encoding="utf-8")[:6000]
    existing_block = (
        f"# 既存の想定問答（01_interview_qa.md・事実の矛盾を避けるため参照。形式は真似しなくてよい）\n{existing_qa}\n"
        if existing_qa else ""
    )

    out = pack_dir / QA_SUPPLEMENT_FILENAME
    batch_texts: list[str] = []
    failed_ranges: list[str] = []
    q_index = 0
    for batch_no, (group_name, questions) in enumerate(QA_SUPPLEMENT_GROUPS, start=1):
        batch_start = q_index + 1
        numbered = []
        blocks = []
        for q in questions:
            q_index += 1
            numbered.append(f"Q{q_index}. {q}")
            blocks.append(_qa_supplement_question_block(q_index, q, c_instruction))
        required_sections = [f"Q{n}." for n in range(batch_start, q_index + 1)]

        prompt = f"""\
あなたは日本の転職市場に精通した面接コーチです。転職エージェント面談・一次面接で
定番の設問グループ「{group_name}」について、設問ごとに回答を設計してください。

# 対象設問（この {len(questions)} 問すべてに答える）
{chr(10).join(numbered)}

# 設計原則
1. 各設問は JD・事実リスト・要件対位メモ・応募者プロフィールから最も関連する経験を選ぶ
   （経歴を平均的に紹介しない）。
2. 話し言葉として自然な、落ち着いたビジネス日本語。誇張・広告的表現なし。過度な謙遜もしない。
   「貴社」ではなく「御社」。
3. {lang_directive("qa_supplement")}

{QA_SUPPLEMENT_GROUNDING_RULES}

{_context_block(job, facts, with_gap=True)}
{existing_block}
# 出力（{len(questions)} 問すべてを、この構造・見出しどおりに Markdown で出力すること）
{chr(10).join(blocks)}
"""
        tmp_out = pack_dir / f"_qa_supplement_batch{batch_no}.tmp.md"
        ok = _llm_or_prompt(prompt, tmp_out, pack_dir, no_llm, timeout=480,
                            accept={"minChars": 250 * len(questions), "includesAll": required_sections})
        if ok:
            batch_texts.append(tmp_out.read_text(encoding="utf-8"))
            tmp_out.unlink(missing_ok=True)
        else:
            failed_ranges.append(f"Q{batch_start}〜Q{q_index}（{group_name}）")

    if not batch_texts:
        print("  ✗ 全バッチ失敗 — 出力なし", file=sys.stderr)
        return

    text = "\n\n".join(batch_texts)
    if "貴社" in text:
        text = text.replace("貴社", "御社")
        print("  → 「貴社」を「御社」へ機械置換")
    text, redacted = redact(text)
    if redacted:
        print(f"  ⚠ 取引先ブランド名 {len(redacted)} 件を一般語に置換: {', '.join(redacted)}",
              file=sys.stderr)

    out.write_text(text, encoding="utf-8")
    ok_count = len(QA_SUPPLEMENT_GROUPS) - len(failed_ranges)
    print(f"  ✓ {out.name}（{ok_count}/{len(QA_SUPPLEMENT_GROUPS)} バッチ成功）")
    if failed_ranges:
        print(f"  ⚠ 失敗したバッチ: {'、'.join(failed_ranges)} — _prompts/ に prompt を落地済み",
              file=sys.stderr)


def interview_checklist(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """日本求職 checklist"""
    print("[interview 3/6] 面接 checklist")
    if not CHECKLIST_SRC.exists():
        print(f"  ✗ テンプレートなし: {CHECKLIST_SRC}", file=sys.stderr)
        return
    text = (CHECKLIST_SRC.read_text(encoding="utf-8")
            .replace("{{COMPANY}}", job.get("company") or "{{要確認}}")
            .replace("{{TITLE}}", job["title"])
            .replace("{{DATE}}", date.today().isoformat()))
    (pack_dir / "02_checklist.md").write_text(text, encoding="utf-8")
    print("  ✓ 02_checklist.md")


def interview_slides(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """面接スライド — 母版 slide15/16 差し替え（tools/interview_slides.py）"""
    print("[interview 4/6] 面接スライド")
    from tools.interview_slides import generate
    generate(job, pack_dir / "03_slides.pptx", no_llm=no_llm,
             prompts_dir=pack_dir / "_prompts")


def interview_script(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """シアター対話台本 — QA md + brief + JD → 9 幕インタラクティブ script.json。

    逐幕 LLM 生成 + 機械ゲート（構造/事実錨定/口語敬語）検収、不合格幕は平板版に降級。
    詳細は interview/theater_script.py。voice stage の前に実行（音声は台本に追従）。
    """
    print("[interview 5/6] シアター対話台本（9 幕・機械ゲート検収）")
    if no_llm:
        print("  → no-llm: スキップ（tts.theater の平板台本が既定で使われます）")
        return
    from interview.theater_script import generate
    stats = generate(pack_dir, job)
    if stats["degraded"]:
        print(f"  ⚠ 降級幕あり: {stats['degraded']} — theater/review_report.md を確認", file=sys.stderr)


def interview_voice(job: dict, facts: str, pack_dir: Path, no_llm: bool) -> None:
    """想定問答を音声化 → Telegram 送信。

    エンジンは config/tts.yaml の `voice_engine`（既定 gpt = ChatGPT の読み上げを
    捕捉。1 問あたり 40 秒前後かかるので、問数が多いと数十分に及ぶ）。失敗時は
    edge_tts 逐句合成（theater 資産）へ自動で落ちる。詳細は tools/interview_voice。
    """
    print("[interview 6/6] 音声化")
    from tools.interview_voice import OUT_NAME, generate
    out_path = pack_dir / OUT_NAME
    if not generate(pack_dir, out_path, job):
        return
    from notify import send_audio
    performer = f"{job.get('company') or '{{要確認}}'} / {job['title'][:30]}"
    send_audio(out_path, caption=f"#{job['id']} 想定問答音声", title="想定問答", performer=performer)


INTERVIEW_STAGES = {
    "qa": interview_qa,
    "jikoshoukai": interview_jikoshoukai,
    "checklist": interview_checklist,
    "slides": interview_slides,
    "script": interview_script,
    "voice": interview_voice,
    "qa_supplement": interview_qa_supplement,
}

# qa_supplement は正典 49 問と重なる設問を含む補助生成物で LLM 呼び出しがバッチ 5 回に
# 及ぶため、デフォルト実行対象からは外す（`--stage qa_supplement` 明示時のみ）。
INTERVIEW_DEFAULT_STAGES = [name for name in INTERVIEW_STAGES if name != "qa_supplement"]


def _write_interview_readme(job: dict, pack_dir: Path) -> None:
    (pack_dir / "00_README.md").write_text(
        tpl(
            "interview_readme",
            company=job.get("company"),
            title=job["title"][:40],
            date=date.today().isoformat(),
            job_id=job["id"],
            score=job.get("score"),
            url=job.get("url"),
        ),
        encoding="utf-8",
    )


# ================================================================
#  main
# ================================================================

def main() -> None:
    p = argparse.ArgumentParser(
        description="求職準備 — apply（投遞包）or interview（面試包）")
    p.add_argument("job_id", type=int)
    p.add_argument("mode", choices=["apply", "interview"],
                   help="apply=投遞包（公司調研+履歷+志望動機）, interview=面試包（問答+checklist）")
    p.add_argument("--lang", default="auto", choices=["auto", "jp", "en"],
                   help="auto=CJK 比例自動判斷（預設）, en=外資英文包, jp=日文包")
    p.add_argument("--stage", default=None,
                   help="指定 stage（カンマ区切り）。apply: brief,resume,shibou / apply --lang en: brief_cover,resume / interview: qa,jikoshoukai,checklist,slides,script,voice（既定）+ qa_supplement（要明示指定）")
    p.add_argument("--facts", default=None,
                   help="Web 調査済み事実 Markdown のパス")
    p.add_argument("--no-llm", action="store_true",
                   help="LLM を呼ばず prompt を落地")
    args = p.parse_args()

    job = _load_job(args.job_id)

    from tools.blacklist import guard as blacklist_guard
    blacklist_guard(job.get("company") or "")

    facts = Path(args.facts).read_text(encoding="utf-8") if args.facts else ""
    slug = _slug(job.get("company") or "")

    lang = _detect_lang(job) if args.lang == "auto" else args.lang
    if args.lang == "auto":
        print(f"  [auto-detect] lang={lang} (CJK ratio: {len(_CJK_RE.findall(job.get('raw_jd') or ''))/(len(job.get('raw_jd') or '') or 1):.0%})")

    if args.mode == "apply" and lang == "en":
        pack_dir = APPLY_DIR / f"{job['id']}_{slug}_en"
        stages = APPLY_EN_STAGES
        write_readme = _write_en_apply_readme
        default_names = list(APPLY_EN_STAGES)
        label = "外資投遞包 (EN)"
    elif args.mode == "apply" and lang == "jp":
        pack_dir = APPLY_DIR / f"{job['id']}_{slug}"
        stages = APPLY_STAGES
        write_readme = _write_apply_readme
        default_names = ["brief", "resume", "shibou"]  # mail は直投流程専用
        label = "投遞包"
    else:
        pack_dir = INTERVIEW_DIR / f"{job['id']}_{slug}"
        stages = INTERVIEW_STAGES
        write_readme = _write_interview_readme
        default_names = INTERVIEW_DEFAULT_STAGES
        label = "面試包"

    pack_dir.mkdir(parents=True, exist_ok=True)

    # --facts 未指定でも、pack に既にある会社事実は拾う。ここを拾わないと
    # 面接 QA が「会社事実なし」で生成され、JD の再話に寄った答えになる。
    if not facts:
        for name in ("_facts.md", "00_company_brief.md", "01_company_brief.md"):
            candidate = pack_dir / name
            if candidate.exists():
                facts = candidate.read_text(encoding="utf-8")
                print(f"  [facts] {name} を使用（{len(facts)} 字）")
                break

    names = [s.strip() for s in args.stage.split(",")] if args.stage else default_names
    for name in names:
        if name not in stages:
            sys.exit(f"未知 stage: {name}（{args.mode} --lang {args.lang} の候補: {', '.join(stages)}）")
        stages[name](job, facts, pack_dir, args.no_llm)

    write_readme(job, pack_dir)

    if args.mode == "apply":
        from tools.match_brief import generate as gen_match_brief
        result = gen_match_brief(job, pack_dir)
        if result:
            print("[match_brief] 05_match_brief.md 生成完了")

    from tracker.db import update_pack_status
    update_pack_status(job["id"], args.mode, str(pack_dir.relative_to(ROOT)))
    print(f"\n✓ {label}: {pack_dir.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
