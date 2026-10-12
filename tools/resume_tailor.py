"""依 JD 自動 tailor 履歷。

流程:
  1. 從 DB 撈 JD（title + raw_jd + company + tier）
  2. 萃取 top-K JD 關鍵詞
  3. 從 data/cognitive_bullets.md 5 條優勢中，挑與 JD 餘弦相似度最高的 3 條
  4. 依 tier 調整 cover-letter 語氣（ai_startup / mega_venture / sier）
  5. ATS 關鍵字覆蓋率診斷 — 找出 JD 有但履歷缺的關鍵詞
  6. LLM 10 維度履歷審計（--audit）— 以人資視角全面檢查履歷品質
  7. 套到履歷模板，輸出 output/tailored/{job_id}_{slug}.md
  8. 回寫 jobs.tailored_resume_path

用法:
    python3 -m tools.resume_tailor --job-id 123
    python3 -m tools.resume_tailor --job-id 123 --audit   # 含 LLM 審計
    python3 -m tools.resume_tailor --top 3
    python3 -m tools.resume_tailor --min-score 70 --limit 5
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import yaml

from interview._llm import call as llm_call
from tracker.db import connect, top_scored, update_tailored_path

ROOT = Path(__file__).parent.parent
TAILORED_DIR = ROOT / "output" / "tailored"
BULLETS_PATH = ROOT / "data" / "cognitive_bullets.md"
PROFILE_PATH = ROOT / "data" / "candidate_profile.yaml"

# 投遞用の完成版 PDF（差し替えは config/resume.yaml — tools/resume_assets.py 参照）
from tools import resume_assets

RIREKISHO_PDF = resume_assets.rirekisho_pdf()
SHOKUMU_PDF = resume_assets.shokumu_pdf()

# 三類企業的 tone hint
TIER_TONE = {
    "ai_startup": {
        "lang": "en",
        "lead": "I build production AI systems. Here is why my last 90 days map directly to your roadmap:",
        "closing": "Open to a working-session interview (live code/design) — preferred over slide pitches.",
    },
    "mega_venture": {
        "lang": "jp",
        "lead": "貴社の {{product_keyword}} における意思決定スピードと既存ユーザー基盤の融合に関心があります。",
        "closing": "データドリブンな実証（A/B、コホート）の経験を即戦力としてお持ち込みできます。",
    },
    "traditional_sier": {
        "lang": "jp",
        "lead": "大規模・高信頼性システムにおける PM 経験と、最近の AI 領域における技術判断力をご提供できます。",
        "closing": "コンプライアンス（資金決済法・個人情報保護法）への配慮を前提とした AI 導入の経験があります。",
    },
    "unknown": {
        "lang": "en",
        "lead": "Quick note on why this role fits my recent platform-PM track record:",
        "closing": "Happy to walk through specifics on a call.",
    },
}

STOPWORDS = {
    # English fillers
    "the", "a", "an", "and", "or", "for", "of", "in", "on", "to", "is", "are",
    "we", "you", "our", "your", "with", "by", "be", "will", "have", "has",
    "that", "this", "they", "their", "them", "from", "about", "which", "what",
    "can", "may", "would", "should", "could", "also", "such", "more", "than",
    "support", "experience", "company", "quality", "team", "work", "role",
    "https", "http", "nbsp", "amp",
    # Japanese particles / fillers
    "が", "を", "に", "は", "で", "の", "と", "や", "も", "から", "まで",
    "する", "です", "ます", "こと", "もの", "ある", "いる", "など", "について",
    "ため", "ように", "ことが", "ことを", "ことで", "より", "なる", "なり",
    # Indeed / scraping noise
    "円まで", "万円", "勤務地", "東京都", "株式会社", "正社員", "雇用形態",
    "募集", "応募", "求人", "選考", "詳細", "経験", "業務", "業界",
    "求人検索", "応募画面に進む", "経験者歓迎", "件のクチコミ", "年収",
    "歓迎", "必須", "スキル", "以上", "以下", "未満", "程度", "相当",
    "転職", "エージェント", "人材",
    "求人番号", "採用企業名", "職務内容詳細", "勤務時間", "休日休暇",
    "福利厚生", "待遇", "選考プロセス", "給与", "職種", "アクセス",
    # HTML / URL noise（JD に混じるタグ属性・CSS クラス名）
    "div", "span", "class", "strong", "style", "href", "target",
    "rel", "src", "alt", "img", "width", "height", "padding",
    "margin", "font", "color", "background", "border", "display",
    "com", "www", "http", "https", "html", "blank", "none",
    "true", "false", "type", "name", "value", "text", "content",
    "every", "range", "who", "working", "into", "data-stringify-type",
    "pre-line", "white-space", "normal", "not", "but", "its", "all",
    "new", "how", "between", "across", "through", "within", "well",
    "both", "other", "using", "based", "when", "like", "been", "over",
}


_NOISE_PATTERN = re.compile(
    r"^[a-z]{1,3}\d{4,}"       # job ID (njb2176580)
    r"|^・"                      # 中點前綴 (・生成, ・アプリ)
    r"|^\d+$"                   # 純數字
)


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z][a-z0-9\-]{2,}|[぀-ヿ一-鿿]{2,}", text)
    return [t for t in tokens
            if t not in STOPWORDS and not _NOISE_PATTERN.match(t)]


def extract_jd_keywords(jd_text: str, top_k: int = 15) -> list[tuple[str, int]]:
    counts = Counter(_tokenize(jd_text))
    return counts.most_common(top_k)


def parse_bullets() -> list[dict]:
    """從 cognitive_bullets.md 解出 5 條優勢（含 EN/JP）。"""
    if not BULLETS_PATH.exists():
        return []
    text = BULLETS_PATH.read_text(encoding="utf-8")
    bullets: list[dict] = []
    # 用 ### N. 開頭切塊
    blocks = re.split(r"\n### \d+\. ", text)
    for blk in blocks[1:]:  # 跳過開頭區塊
        title = blk.split("\n", 1)[0].strip()
        en = re.search(r"\*\*EN:\*\*\s*(.+)", blk)
        jp = re.search(r"\*\*JP:\*\*\s*(.+)", blk)
        ev = re.search(r"\*\*Evidence:\*\*\s*(.+)", blk)
        if en and jp:
            bullets.append({
                "title": title,
                "en": en.group(1).strip(),
                "jp": jp.group(1).strip(),
                "evidence": ev.group(1).strip() if ev else "",
            })
        if len(bullets) >= 5:
            break
    return bullets


def rank_bullets(jd_keywords: list[str], bullets: list[dict], lang: str) -> list[dict]:
    """以「JD 關鍵詞在 bullet 文本出現次數」近似餘弦排序。"""
    if not bullets:
        return []
    kw_set = set(jd_keywords)

    def score(b: dict) -> int:
        text = (b["en"] + " " + b["jp"]).lower()
        return sum(1 for kw in kw_set if kw in text)

    ranked = sorted(bullets, key=score, reverse=True)
    return ranked


# ── ATS 覆蓋率診斷 ──────────────────────────────────────────

def _load_resume_text() -> str:
    """從 candidate_profile.yaml 把所有 highlights / skills 拼成一塊文本。"""
    if not PROFILE_PATH.exists():
        return ""
    data = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}
    parts: list[str] = []
    for exp in data.get("experience", []):
        parts.extend(exp.get("highlights", []))
    for proj in (data.get("proof_projects", {}).get("projects", [])):
        parts.extend(proj.get("highlights", []))
    skills = data.get("skills", {})
    for v in skills.values():
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
    pos = data.get("positioning", {})
    if isinstance(pos, dict):
        parts.append(pos.get("one_liner", ""))
        parts.append(pos.get("tagline", ""))
    parts.extend(data.get("match_summary", {}).get("strengths_i_bring", []))
    return " ".join(parts)


def audit_ats_coverage(jd_text: str, top_k: int = 20) -> dict:
    """比對 JD 關鍵詞 vs 履歷全文，回傳覆蓋率 + 缺失清單。"""
    jd_kws = [kw for kw, _ in extract_jd_keywords(jd_text, top_k=top_k)]
    resume_text = _load_resume_text().lower()
    resume_tokens = set(_tokenize(resume_text))

    hit = [kw for kw in jd_kws if kw in resume_tokens or kw in resume_text]
    miss = [kw for kw in jd_kws if kw not in hit]
    rate = len(hit) / len(jd_kws) * 100 if jd_kws else 0
    return {"rate": round(rate, 1), "hit": hit, "miss": miss, "total": len(jd_kws)}


# ── LLM 10 維度履歷審計 ───────────────────────────────────

AUDIT_MODEL = "claude-haiku-4-5"
MAX_RESUME_CHARS = 8000
MAX_JD_CHARS = 4000

AUDIT_DIMENSIONS = [
    "summary_length",
    "chronology_flow",
    "experience_focus",
    "achievement_gap",
    "trim_candidates",
    "readability",
    "portfolio_value",
    "photo_fit",
    "rejection_risk",
    "hr_verdict",
]


def _load_profile_for_audit() -> str:
    if not PROFILE_PATH.exists():
        return ""
    return PROFILE_PATH.read_text(encoding="utf-8")[:MAX_RESUME_CHARS]


def _build_audit_prompt(profile_text: str, row) -> str:
    jd = (row["raw_jd"] or "")[:MAX_JD_CHARS]
    return f"""あなたはベテランの人事採用担当（10 年以上の書類選考経験）です。
以下の候補者の職務経歴書（YAML 形式）を、対象求人票と照らして 10 の観点で厳密に審査してください。

# 候補者の職務経歴書
```yaml
{profile_text}
```

# 対象求人票
タイトル: {row['title']}
会社: {row['company'] or '不明'}
本文:
{jd or '(本文なし)'}

# 審査基準（10 の観点）
各項目について、問題がなければ "ok"、改善が必要なら具体的な指摘と改善案を記載すること。

1. **summary_length**: 冒頭の自己紹介・one_liner は長すぎないか（3 行以内が理想）
2. **chronology_flow**: 自己 PR や経歴説明が時系列の羅列（流水帳）になっていないか。ストーリー性があるか
3. **experience_focus**: 各職歴の bullet で「何をしたか」だけでなく「なぜ重要か・何が変わったか」が伝わるか。重点が不明確な bullet を最大 5 つ指摘
4. **achievement_gap**: 成果感が不足している箇所。数値・規模・before/after が欠けている bullet を指摘
5. **trim_candidates**: 冗長で削るべき箇所。この JD に無関係な情報、繰り返し表現
6. **readability**: 全体の読みやすさ（情報密度、セクション構成、bullet の長さバランス）。閲覧疲労を起こすか
7. **portfolio_value**: 個人プロジェクト（proof_projects）が対象職種にとって加点材料になるか、ならないか
8. **photo_fit**: 写真に関する記載（path の有無）。職種に適した印象を与えるか
9. **rejection_risk**: 書類選考で最も落とされやすいポイントを最大 3 つ
10. **hr_verdict**: あなたが人事なら面接に呼ぶか？ yes/no + 理由 + 確信度 0-100

# 出力（JSON のみ。前後に説明やコードフェンス以外の文字を書かない）
{{
  "summary_length": {{"verdict": "ok|too_long|too_short", "detail": "..."}},
  "chronology_flow": {{"verdict": "ok|issue", "issues": ["具体箇所と理由"], "suggestion": "..."}},
  "experience_focus": {{"weak_bullets": [{{"bullet_head": "最初20文字...", "reason": "..."}}], "detail": "..."}},
  "achievement_gap": {{"missing": [{{"bullet_head": "最初20文字...", "fix": "こう書き換える"}}]}},
  "trim_candidates": {{"sections": [{{"target": "...", "reason": "..."}}]}},
  "readability": {{"score": 1-5, "issues": ["..."]}},
  "portfolio_value": {{"verdict": "strong|neutral|weak|missing", "detail": "..."}},
  "photo_fit": {{"verdict": "ok|missing|needs_review", "detail": "..."}},
  "rejection_risk": {{"top_risks": ["リスク1", "リスク2", "リスク3"]}},
  "hr_verdict": {{"invite": true/false, "reason": "...", "confidence": 0-100}}
}}"""


def _extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # 嘗試直接 parse
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # 抽出最外層 { ... }
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        raw = m.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # LLM 常見錯誤修復：true/false 未加引號、trailing comma、未轉義引號
        fixed = raw
        fixed = re.sub(r'(?<=: )true(?=[,\s\}])', '"true"', fixed)
        fixed = re.sub(r'(?<=: )false(?=[,\s\}])', '"false"', fixed)
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
        # 嘗試截斷到最後一個完整的 key-value
        for end in range(len(fixed), max(len(fixed) - 500, 0), -1):
            candidate = fixed[:end]
            depth = candidate.count('{') - candidate.count('}')
            if depth > 0:
                candidate += '}"' * 0 + '}' * depth
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"LLM 審計の JSON 抽出失敗：\n{text[:300]}")


def audit_resume_quality(row) -> dict | None:
    """LLM に 10 維度の履歴書審査を依頼。Haiku で実行。"""
    profile_text = _load_profile_for_audit()
    if not profile_text:
        return None
    prompt = _build_audit_prompt(profile_text, row)
    print("  [audit] LLM 審査中…")
    raw = llm_call(prompt, timeout=120, model=AUDIT_MODEL)
    return _extract_json(raw)


def _render_audit_md(audit: dict) -> list[str]:
    """審査結果を Markdown 行リストに変換。"""
    lines = ["## 履歴書 10 維度審査（LLM）", ""]

    # 1. summary_length
    sl = audit.get("summary_length", {})
    v = sl.get("verdict", "?")
    icon = {"ok": "✅", "too_long": "⚠️", "too_short": "⚠️"}.get(v, "❓")
    lines.append(f"### 1. 簡介長度 {icon}")
    lines.append(sl.get("detail", "—"))
    lines.append("")

    # 2. chronology_flow
    cf = audit.get("chronology_flow", {})
    icon = "✅" if cf.get("verdict") == "ok" else "⚠️"
    lines.append(f"### 2. 流水帳チェック {icon}")
    for issue in cf.get("issues", []):
        lines.append(f"- {issue}")
    if cf.get("suggestion"):
        lines.append(f"- 💡 {cf['suggestion']}")
    lines.append("")

    # 3. experience_focus
    ef = audit.get("experience_focus", {})
    lines.append("### 3. 経歴の重点")
    for wb in ef.get("weak_bullets", []):
        lines.append(f"- **{wb.get('bullet_head', '?')}** → {wb.get('reason', '')}")
    if ef.get("detail"):
        lines.append(f"- {ef['detail']}")
    lines.append("")

    # 4. achievement_gap
    ag = audit.get("achievement_gap", {})
    lines.append("### 4. 成果感の不足")
    for m in ag.get("missing", []):
        lines.append(f"- **{m.get('bullet_head', '?')}** → _{m.get('fix', '')}_")
    lines.append("")

    # 5. trim_candidates
    tc = audit.get("trim_candidates", {})
    lines.append("### 5. 削減候補")
    for s in tc.get("sections", []):
        lines.append(f"- **{s.get('target', '?')}**: {s.get('reason', '')}")
    lines.append("")

    # 6. readability
    rd = audit.get("readability", {})
    score = rd.get("score", "?")
    lines.append(f"### 6. 読みやすさ（{score}/5）")
    for issue in rd.get("issues", []):
        lines.append(f"- {issue}")
    lines.append("")

    # 7. portfolio_value
    pv = audit.get("portfolio_value", {})
    icon = {"strong": "✅", "neutral": "➖", "weak": "⚠️", "missing": "❌"}.get(
        pv.get("verdict", ""), "❓")
    lines.append(f"### 7. 作品集の加点効果 {icon}")
    lines.append(pv.get("detail", "—"))
    lines.append("")

    # 8. photo_fit
    pf = audit.get("photo_fit", {})
    icon = {"ok": "✅", "missing": "❌", "needs_review": "⚠️"}.get(
        pf.get("verdict", ""), "❓")
    lines.append(f"### 8. 写真 {icon}")
    lines.append(pf.get("detail", "—"))
    lines.append("")

    # 9. rejection_risk
    rr = audit.get("rejection_risk", {})
    lines.append("### 9. 最も落とされやすいポイント")
    for i, risk in enumerate(rr.get("top_risks", []), 1):
        lines.append(f"{i}. {risk}")
    lines.append("")

    # 10. hr_verdict
    hv = audit.get("hr_verdict", {})
    invite = hv.get("invite", False)
    icon = "✅ 面接に呼ぶ" if invite else "❌ 見送り"
    conf = hv.get("confidence", "?")
    lines.append(f"### 10. 人事判定：{icon}（確信度 {conf}%）")
    lines.append(hv.get("reason", "—"))
    lines.append("")

    return lines


def slugify(text: str, max_len: int = 30) -> str:
    text = re.sub(r"[^a-zA-Z0-9一-龥ぁ-んァ-ヶー]+", "-", (text or "unknown"))
    return text.strip("-")[:max_len] or "unknown"


def tailor_for_row(row, run_audit: bool = False) -> Path:
    tier = row["tier"] or "unknown"
    tone = TIER_TONE[tier]
    lang = tone["lang"]

    jd_text = " ".join(filter(None, [row["title"], row["raw_jd"]]))
    jd_keywords = [kw for kw, _ in extract_jd_keywords(jd_text)]
    product_keyword = next(
        (kw for kw in jd_keywords if len(kw) >= 3),
        "AIプロダクト",
    )

    bullets = parse_bullets()
    ranked = rank_bullets(jd_keywords, bullets, lang)
    top3 = ranked[:3]

    lines = []
    lines.append(f"<!-- Tailored for: {row['company']} / {row['title']} (job_id={row['id']}, tier={tier}, score={row['score']}) -->")
    lines.append("")
    lines.append(f"# Cover Note — {row['company']}")
    lines.append("")
    lines.append(f"**応募職種**: {row['title']}")
    if row["url"]:
        lines.append(f"**JD URL**: {row['url']}")
    lines.append("")
    lines.append(tone["lead"].replace("{{product_keyword}}", product_keyword))
    lines.append("")
    lines.append("**JD 関連性が高い実績 (top 3)**：")
    for b in top3:
        line = b["jp"] if lang == "jp" else b["en"]
        lines.append(f"- {line}")
        if b.get("evidence"):
            lines.append(f"  - _evidence_: `{b['evidence']}`")
    lines.append("")
    lines.append(f"**Detected JD signals**: `{', '.join(jd_keywords[:10])}`")
    lines.append("")
    lines.append(tone["closing"])
    lines.append("")

    # ── ATS 覆蓋率診斷（規則式，免費）──
    ats = audit_ats_coverage(jd_text)
    lines.append("---")
    lines.append("")
    lines.append("## ATS キーワードカバー率")
    lines.append("")
    lines.append(f"**{ats['rate']}%** ({len(ats['hit'])}/{ats['total']} keywords)")
    lines.append("")
    if ats["miss"]:
        lines.append("**履歴書に不足しているキーワード：**")
        lines.append(f"  `{', '.join(ats['miss'])}`")
        lines.append("")
    if ats["hit"]:
        lines.append(f"<details><summary>✅ カバー済 ({len(ats['hit'])} 件)</summary>")
        lines.append("")
        lines.append(f"`{', '.join(ats['hit'])}`")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ── LLM 10 維度審計（--audit 時のみ）──
    if run_audit:
        try:
            audit = audit_resume_quality(row)
            if audit:
                lines.extend(_render_audit_md(audit))
        except Exception as e:
            lines.append(f"## 履歴書審査（エラー）\n\n`{e}`\n")

    lines.append("---")
    lines.append("")
    lines.append("## 同送付書類")
    lines.append("")
    lines.append(f"- 履歴書: `{RIREKISHO_PDF.relative_to(ROOT)}`")
    lines.append(f"- 職務経歴書: `{SHOKUMU_PDF.relative_to(ROOT)}`")
    lines.append("")
    lines.append("> 履歴情報の source of truth: `resume/jp/data.yaml`。更新後 `python3 -m resume.jp.render` で再生成。")
    lines.append("")
    out_text = "\n".join(lines)

    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TAILORED_DIR / f"{row['id']:04d}_{slugify(row['company'])}_{tier}.md"
    out_path.write_text(out_text, encoding="utf-8")
    update_tailored_path(row["id"], str(out_path.relative_to(ROOT)))
    return out_path


def tailor_top(min_score: int = 70, limit: int = 10,
               run_audit: bool = False) -> list[Path]:
    rows = top_scored(limit=limit, min_score=min_score)
    if not rows:
        print(f"[resume_tailor] 無分數 ≥ {min_score} 的職缺（找到 0 筆）")
        return []
    paths = []
    for row in rows:
        p = tailor_for_row(row, run_audit=run_audit)
        paths.append(p)
        print(f"  ✓ {p.name}  (score={row['score']}, tier={row['tier']})")
    print(f"[resume_tailor] 已產生 {len(paths)} 份 tailored 履歷")
    return paths


def tailor_by_id(job_id: int, run_audit: bool = False) -> Path:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise SystemExit(f"job_id {job_id} 不存在")
    p = tailor_for_row(row, run_audit=run_audit)
    print(f"✓ {p}")
    return p


def main() -> None:
    parser = argparse.ArgumentParser(description="依 JD tailor 履歷")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--job-id", type=int)
    g.add_argument("--top", type=int, help="對 TOP N 高分職缺各產一份")
    g.add_argument("--min-score", type=int, help="對 score >= N 的全部職缺各產一份")
    parser.add_argument("--limit", type=int, default=10, help="與 --min-score 配合，限制最多份數")
    parser.add_argument("--audit", action="store_true",
                        help="LLM 10 維度履歷審査を実行（Haiku 1 回/件）")
    args = parser.parse_args()

    if args.job_id is not None:
        tailor_by_id(args.job_id, run_audit=args.audit)
    elif args.top is not None:
        tailor_top(min_score=0, limit=args.top, run_audit=args.audit)
    elif args.min_score is not None:
        tailor_top(min_score=args.min_score, limit=args.limit,
                   run_audit=args.audit)


if __name__ == "__main__":
    main()
