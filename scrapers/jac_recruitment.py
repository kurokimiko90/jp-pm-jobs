"""JAC Recruitment — Gmail 郵件職缺來源（1 通 = 1 求人）。

コンサルタントの「[JAC Recruitment]求人情報のご案内」メールを解析。本文（HTML）に
求人No. / 社名 / 事業内容 / 職種 / 仕事内容 / 勤務地 / 雇用形態 / 給与条件 / 年収 が
【ラベル】形式で構造化されており、添付「求人票.pdf」には応募条件（必須/歓迎要件）・
勤務条件・従業員数まで入る。PDF が読めれば応募条件を raw_jd に足す
（pdfplumber 未導入なら本文のみで動作、機能は落ちるがエラーにはならない）。

非公開求人のため公開 JD URL は存在しない → url は MyPage + 求人No.（404 しない安定 URL）。

去重は 3 層：
  1. 本 run 内：同一求人No. が複数メールに出ても 1 件（seen set）
  2. DB：UNIQUE(source, source_id=求人No.) → 再送メールは last_seen 更新のみ
  3. DB：upsert_job の company_norm マージ → 他サイト由来の同社求人と統合

不走 Playwright，直接用 Gmail API 讀郵件（recruiter_agent と同方針）。
"""

from __future__ import annotations

import base64
import io
import re
import unicodedata
from datetime import datetime

from bs4 import BeautifulSoup

from inbox.auth import get_service
from tools.app_config import load as _load_app_config
from tools.pii_gate import scrub_for_external

PROVIDER_META = {
    "id": "jac_recruitment",
    "name": "JAC Recruitment",
    "requires_login": False,
    "base_url": "https://www.jac-recruitment.jp",
    "description": "コンサルタントからの求人紹介メール（Gmail）",
}

# 担当コンサルタントの個人アドレスは config/scraping.yaml の
# jac_recruitment.sender_query で絞り込める（未設定ならドメイン全体）。
_CFG = _load_app_config("scraping").get("jac_recruitment") or {}
SENDER_QUERY = _CFG.get("sender_query", "from:jac-recruitment.jp")
SUBJECT_QUERY = _CFG.get("subject_query", 'subject:"求人情報のご案内"')

# 求人No. NJB2384628（本文）/ No.NJB2384628（PDF ヘッダ）
JOB_NO_RE = re.compile(r"求人\s*No\.?\s*([A-Za-z]{2,5}\d{4,10})")

# 【年収（想定額）】8,500,000円 ～13,500,000円
_YEN_RANGE_RE = re.compile(r"([\d,]{6,})\s*円\s*[～〜~\-–]\s*([\d,]{6,})\s*円")
_YEN_SINGLE_RE = re.compile(r"([\d,]{6,})\s*円")

# 本文のトップレベル欄位（【】は仕事内容の中にも現れるので whitelist で判定）
_FIELD_LABELS = ("社名", "事業内容", "職種", "仕事内容", "勤務地", "雇用形態", "給与条件")
_SALARY_LABEL = "年収"  # 【年収（想定額）】— 括弧の全角/半角揺れを避けて前方一致
_LABEL_RE = re.compile(r"^【\s*([^】]{1,24})\s*】\s*(.*)$")

# 本文/PDF 共通の定型フッター（担当者氏名を含む行も丸ごと落とす）
_FOOTER_MARKERS = (
    "上記求人の詳細内容に関しまして",
    "求人内容には非公開情報",
    "ご不用の際は",
    "最終的な雇用条件",
    "担当者：",
    "担当者:",
    "回答ページ",
    "配信停止",
    "個人情報",
)

_PDF_CONDITIONS_MARKERS = ("■■ 応募条件 ■■", "■■応募条件■■", "応募条件")
_PDF_OVERVIEW_END = ("求人要項",)

MAX_RAW_JD = 8000        # gap_analyzer は 6000 字で切るので余裕分だけ持つ
MIN_ENRICHED_JD = 1500   # これ以上の JD が既にある既存求人は PDF を再取得しない


# ───────────────────────── 本文パース ─────────────────────────


def _html_to_text(html: str) -> str:
    """JAC のメールは <p> 1 行構成。<br> を改行に直してから可視テキストを取る。"""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return soup.get_text("\n")


def _is_footer(line: str) -> bool:
    return any(marker in line for marker in _FOOTER_MARKERS)


def _match_field_label(label: str) -> str | None:
    """【ラベル】がトップレベル欄位なら正規化キーを返す。JD 本文中の【】は None。"""
    if label.startswith(_SALARY_LABEL):
        return _SALARY_LABEL
    return label if label in _FIELD_LABELS else None


def _parse_fields(text: str) -> dict[str, str]:
    """本文 → {欄位: 値}。最初の【社名】より前（宛名・挨拶＝本人氏名を含む）は捨てる。"""
    fields: dict[str, list[str]] = {}
    current: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("---"):
            continue
        if _is_footer(line):
            if current:  # フッターに入ったら以降は全部捨てる
                break
            continue

        m = _LABEL_RE.match(line)
        key = _match_field_label(m.group(1)) if m else None
        if key and key not in fields:
            current = key
            fields[key] = [m.group(2).strip()] if m.group(2).strip() else []
            continue
        if current:
            fields[current].append(line)

    return {k: "\n".join(v).strip() for k, v in fields.items()}


def _parse_salary(salary_raw: str) -> tuple[int | None, int | None]:
    """「8,500,000円 ～13,500,000円」→ (850, 1350)。単位は万円。"""
    if not salary_raw:
        return None, None
    text = unicodedata.normalize("NFKC", salary_raw)

    m = _YEN_RANGE_RE.search(text)
    if m:
        lo = int(m.group(1).replace(",", "")) // 10000
        hi = int(m.group(2).replace(",", "")) // 10000
        return (lo, hi) if hi >= lo else (lo, None)

    m = _YEN_SINGLE_RE.search(text)
    if m:
        return int(m.group(1).replace(",", "")) // 10000, None
    return None, None


# ───────────────────────── 添付 PDF（求人票）─────────────────────────


def _pdf_text(data: bytes) -> str:
    """求人票 PDF → テキスト。pdfplumber 未導入 / 解析失敗なら空文字（本文のみで続行）。"""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        return ""


def _strip_boilerplate(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if line.strip() and not _is_footer(line)
    ).strip()


def _split_pdf(text: str) -> tuple[str, str]:
    """求人票 PDF → (会社概要, 応募条件＋勤務条件)。見つからない部分は空文字。"""
    clean = _strip_boilerplate(text)
    if not clean:
        return "", ""

    cond_idx = next(
        (clean.find(m) for m in _PDF_CONDITIONS_MARKERS if clean.find(m) >= 0), -1
    )
    conditions = clean[cond_idx:].strip() if cond_idx >= 0 else ""

    ov_idx = next((clean.find(m) for m in _PDF_OVERVIEW_END if clean.find(m) > 0), -1)
    if ov_idx > 0:
        overview = clean[:ov_idx].strip()
    else:
        # 「求人要項」の切れ目が無い＝レイアウト想定外。応募条件が取れているなら
        # 概要は捨てて重複を避け、何も取れていないなら全文をそのまま使う。
        overview = "" if conditions else clean

    return overview, conditions


# ───────────────────────── Gmail 取得 ─────────────────────────


def _walk_parts(payload: dict, texts: dict[str, str], attachments: list[dict]) -> None:
    filename = payload.get("filename") or ""
    body = payload.get("body", {})
    if filename:
        if body.get("attachmentId"):
            attachments.append({"filename": filename, "id": body["attachmentId"]})
    elif body.get("data"):
        mime = payload.get("mimeType", "")
        decoded = base64.urlsafe_b64decode(body["data"]).decode("utf-8", "replace")
        if mime == "text/html":
            texts.setdefault("html", decoded)
        elif mime == "text/plain":
            texts.setdefault("text", decoded)
    for part in payload.get("parts", []):
        _walk_parts(part, texts, attachments)


def _build_raw_jd(fields: dict[str, str], salary_raw: str, pdf_text: str) -> str:
    """raw_jd 組み立て。gap 分析が 6000 字で切るので重要度順に並べる。

    社名/職種/年収 → 仕事内容 → 応募条件（PDF）→ 会社概要（PDF）。
    """
    smin, smax = _parse_salary(salary_raw)
    blocks = [f"{fields.get('社名', '')} {fields.get('職種', '')}".strip()]

    if salary_raw:
        blocks.append(f"【年収（想定額）】{salary_raw}")
    # salary_parser / jd_scorer の「年収 X万円〜Y万円」正則が拾える形も併記
    if smin and smax:
        blocks.append(f"年収 {smin}万円 ～ {smax}万円")
    elif smin:
        blocks.append(f"年収 {smin}万円")

    for label in ("雇用形態", "給与条件", "勤務地", "事業内容", "仕事内容"):
        value = fields.get(label)
        if value:
            blocks.append(f"【{label}】\n{value}" if "\n" in value else f"【{label}】{value}")

    overview, conditions = _split_pdf(pdf_text)
    if conditions:
        blocks.append(f"【応募条件・勤務条件（求人票）】\n{conditions}")
    if overview:
        blocks.append(f"【会社概要（求人票）】\n{overview}")

    raw_jd = "\n\n".join(b for b in blocks if b)[:MAX_RAW_JD]
    # 本人氏名などが混じっていた場合の最終防衛（LLM prompt に流れる欄位）
    scrubbed, _ = scrub_for_external(raw_jd)
    return scrubbed.strip()


def _existing_jd_lengths() -> dict[str, int]:
    from tracker.db import connect

    with connect() as conn:
        return {
            r["source_id"]: r["jd_len"]
            for r in conn.execute(
                "SELECT source_id, length(COALESCE(raw_jd, '')) AS jd_len "
                "FROM jobs WHERE source = ?",
                (PROVIDER_META["id"],),
            ).fetchall()
        }


def fetch_from_gmail(days: int = 30, max_results: int = 50) -> list[dict]:
    """求人紹介メールから職缺を抽出。

    Args:
        days: 直近 N 日のメールを対象。
        max_results: 走査するメール数の上限。

    Returns:
        upsert_job() に渡せる dict のリスト（同一求人No. は 1 件に集約済み）。
    """
    svc = get_service()
    query = f"{SENDER_QUERY} {SUBJECT_QUERY} newer_than:{days}d".strip()
    resp = svc.users().messages().list(
        userId="me", maxResults=max_results, q=query
    ).execute()
    messages = resp.get("messages", [])

    if not messages:
        print(f"  [jac_recruitment] 無郵件 (query: {query})")
        return []

    print(f"  [jac_recruitment] 掃描 {len(messages)} 封郵件...")
    existing = _existing_jd_lengths()
    jobs: list[dict] = []
    seen: set[str] = set()

    for m in messages:
        try:
            msg = svc.users().messages().get(
                userId="me", id=m["id"], format="full"
            ).execute()
        except Exception as e:
            print(f"  ✗ メール取得失敗 {m['id']}: {type(e).__name__}: {e}")
            continue

        texts: dict[str, str] = {}
        attachments: list[dict] = []
        _walk_parts(msg["payload"], texts, attachments)

        body = _html_to_text(texts["html"]) if texts.get("html") else texts.get("text", "")
        if not body:
            continue

        no_match = JOB_NO_RE.search(body)
        fields = _parse_fields(body)
        company = fields.get("社名", "").strip()
        title = fields.get("職種", "").strip()
        if not (no_match and company and title):
            continue  # 求人紹介以外のメール（テンプレ変更・お礼など）

        job_no = no_match.group(1)
        if job_no in seen:
            continue  # 同一求人の再送メール（新しい方を採用）
        seen.add(job_no)

        # 既に充実した JD がある求人は PDF を再取得しない（raw_jd=None で既存値を保持）
        enriched = existing.get(job_no, 0) >= MIN_ENRICHED_JD
        pdf_text = ""
        if not enriched:
            for att in attachments:
                if not att["filename"].lower().endswith(".pdf"):
                    continue
                try:
                    data = svc.users().messages().attachments().get(
                        userId="me", messageId=m["id"], id=att["id"]
                    ).execute()
                    pdf_text = _pdf_text(base64.urlsafe_b64decode(data["data"]))
                except Exception as e:
                    print(f"  ⚠ [{job_no}] 添付解析失敗: {type(e).__name__}: {e}")
                if pdf_text:
                    break

        salary_raw = fields.get(_SALARY_LABEL, "")
        smin, smax = _parse_salary(salary_raw)
        location = (fields.get("勤務地", "").splitlines() or [""])[0].strip()

        jobs.append({
            "source": PROVIDER_META["id"],
            "source_id": job_no,
            "title": title,
            "company": company,
            "location": location,
            "url": f"https://www.jac-recruitment.jp/mypage/?jobNo={job_no}",
            "raw_jd": None if enriched else _build_raw_jd(fields, salary_raw, pdf_text),
            "salary_min": smin,
            "salary_max": smax,
            "keyword": "jac_recommend",
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
        })

    print(f"  [jac_recruitment] 提取 {len(jobs)} 筆職缺（{len(messages)} 封郵件）")
    return jobs


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="JAC Recruitment 郵件職缺抽出")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--max-results", type=int, default=50)
    p.add_argument("--dry-run", action="store_true", help="DB に書かず JSON 出力のみ")
    args = p.parse_args()

    result = fetch_from_gmail(days=args.days, max_results=args.max_results)
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        from tracker.db import init_db, upsert_job

        init_db()
        new = sum(1 for job in result if upsert_job(job)[1])
        print(f"新增 {new} / {len(result)} 筆")
