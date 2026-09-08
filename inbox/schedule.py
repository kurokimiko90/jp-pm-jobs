"""日程確定メールから面接日時を抽出 → applications.next_event に自動反映。

對應格式（依近 7 天標星郵件實測）:
  1. リクルートエージェント日程調整システム「日程確定のお知らせ」
       【企業名】株式会社サンプル
       【仕事の名称】【東京/マネージャー】ITコンサルタント/...
       【確定日時】
       　2026/07/08(水)　20:00スタート
  2. TimeRex「〇〇さんとの日程調整が完了しました」
       日時：2026年7月6日 (月) 13:00 - 13:30（Asia/Tokyo）

抽出後以企業名對應 applications（已投遞職缺），命中則更新 next_event；
對不到職缺仍回傳抽出結果供通知（人工確認）。冪等：同值不重寫、不重複通知。
"""
from __future__ import annotations

import re
import unicodedata

from tracker.db import connect

# ── 格式 1: r-agent 日程調整システム ──
_RE_COMPANY = re.compile(r"【企業名】\s*(.+)")
_RE_TITLE = re.compile(r"【仕事の名称】\s*(.+)")
_RE_KAKUTEI = re.compile(
    r"【確定日時】\s*(\d{4}/\d{1,2}/\d{1,2})\s*\(([^)]+)\)\s*(\d{1,2}:\d{2})"
)
_RE_DURATION = re.compile(r"【所要時間】\s*約?(?:(\d+)時間)?(?:(\d+)分)?")

# ── 格式 2: TimeRex ──
_RE_TIMEREX_DT = re.compile(
    r"日時：\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*\(([^)]+)\)\s*"
    r"(\d{1,2}:\d{2})(?:\s*-\s*(\d{1,2}:\d{2}))?"
)
_RE_TIMEREX_WHO = re.compile(r"(.+?)さんとの日程調整が完了しました")

# ── オンライン会議URL ──
# 既知の会議系ドメインを優先（r-agentの【ご紹介URL】等、無関係リンクを拾わないため）。
# Teams等の長いURLはメール本文で折返されるため、ドメインにマッチした後は「改行+字下げ
# して続く印字可能ASCII」を軟折返しとみなして連結する（日本語が来たら即座に打ち切り、
# ミーティングID等の後続行を誤って呑み込まない）。
_KNOWN_MEETING_DOMAINS = (
    r"meet\.google\.com", r"[\w.-]*zoom\.us", r"teams\.microsoft\.com",
    r"teams\.live\.com", r"[\w.-]*webex\.com",
)
_RE_MEETING_URL_DOMAIN = re.compile(
    r"https?://(?:" + "|".join(_KNOWN_MEETING_DOMAINS) + r")"
    r"[\x21-\x7e]*(?:[ \t　]*\r?\n[ \t　]*[\x21-\x7e]+)*"
)
# ラベル付きフォールバック（未知ドメインの自社会議システム等、折返しなし想定）
_RE_MEETING_URL_LABELED = re.compile(
    r"(?:Web会議室|会議URL|ミーティングURL|接続先URL|参加URL|Meeting URL|Zoom URL)\s*[：:]\s*(https?://\S+)"
)


def _extract_meeting_url(body: str) -> str | None:
    m = _RE_MEETING_URL_DOMAIN.search(body)
    if m:
        return re.sub(r"\s+", "", m.group(0))
    m = _RE_MEETING_URL_LABELED.search(body)
    return m.group(1).strip().rstrip("。、") if m else None

# next_event 既有值裡的選考段位詞（保留段位、只更新日時）
_RE_STAGE = re.compile(
    r"(カジュアル面談|[0-9０-９一二三]次選考|[0-9０-９一二三]次面接|最終面接|最終選考)"
)


def _norm(s: str | None) -> str:
    """比對用正規化：全形轉半形、保留日文假名漢字、去空白符號、去法人格前綴。"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"株式会社|（株）|\(株\)|合同会社", "", s)
    return re.sub(r"[^\w]", "", s)


_RE_TRAILING_PAREN = re.compile(r"(.+?)[（(](.+)[）)]\s*$")


def _split_company_title(comp_raw: str) -> tuple[str, str]:
    """「選考情報変更のご連絡」形式は【企業名】に職務名が括弧書きで同居する
    （別行の【仕事の名称】が存在しない）。末尾の括弧を職務名として切り出す。"""
    m = _RE_TRAILING_PAREN.match(comp_raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return comp_raw, ""


def extract_schedule(mail: dict) -> dict | None:
    """從郵件抽出 {company, title, date, weekday, time, format}；非日程確定信回 None。"""
    body = mail.get("body") or ""
    subject = mail.get("subject") or ""

    m = _RE_KAKUTEI.search(body)
    if m:
        comp = _RE_COMPANY.search(body)
        title = _RE_TITLE.search(body)
        dur = _RE_DURATION.search(body)
        duration_min = None
        if dur and (dur.group(1) or dur.group(2)):
            duration_min = int(dur.group(1) or 0) * 60 + int(dur.group(2) or 0)
        company, embedded_title = _split_company_title(comp.group(1).strip()) if comp else ("", "")
        return {
            "company": company,
            "title": title.group(1).strip() if title else embedded_title,
            "date": m.group(1),
            "weekday": m.group(2),
            "time": m.group(3),
            "duration_min": duration_min,
            "meeting_url": _extract_meeting_url(body),
            "format": "r_agent",
        }

    m = _RE_TIMEREX_DT.search(body)
    if m:
        who = _RE_TIMEREX_WHO.search(subject)
        y, mo, d, wd, tm, tm_end = m.groups()
        duration_min = None
        if tm_end:
            sh, sm = (int(x) for x in tm.split(":"))
            eh, em = (int(x) for x in tm_end.split(":"))
            duration_min = (eh * 60 + em) - (sh * 60 + sm)
        return {
            "company": who.group(1).strip() if who else "",
            "title": "",
            "date": f"{y}/{int(mo):02d}/{int(d):02d}",
            "weekday": wd,
            "time": tm,
            "duration_min": duration_min,
            "meeting_url": _extract_meeting_url(body),
            "format": "timerex",
        }
    return None


def match_application(company: str, title: str = "") -> int | None:
    """以企業名對應已投遞職缺（applications JOIN jobs）；同公司多職缺時用職缺名挑選。"""
    comp_n = _norm(company)
    if not comp_n:
        return None
    with connect() as conn:
        rows = conn.execute(
            "SELECT j.id, j.company, j.title FROM applications a "
            "JOIN jobs j ON j.id = a.job_id"
        ).fetchall()

    cands = [
        r for r in rows
        if _norm(r["company"]) and (
            comp_n == _norm(r["company"])
            or comp_n in _norm(r["company"])
            or _norm(r["company"]) in comp_n
        )
    ]
    if not cands:
        return None
    if len(cands) == 1 or not title:
        return cands[0]["id"]

    title_n = _norm(title)
    for r in cands:  # 職缺名完全/包含比對
        rn = _norm(r["title"])
        if rn and (rn == title_n or rn in title_n or title_n in rn):
            return r["id"]
    return cands[0]["id"]


def apply_schedule(mail: dict) -> dict | None:
    """抽出 + 對應 + 寫入 applications.next_event。

    回傳 info dict（含 job_id / changed / next_event），非日程確定信回 None。
    changed=False 表示同值重複（リマインド信）或對不到職缺，呼叫端可據此略過通知。
    """
    info = extract_schedule(mail)
    if not info:
        return None
    job_id = match_application(info["company"], info["title"])
    info["job_id"] = job_id
    info["changed"] = False
    if job_id is None:
        return info

    with connect() as conn:
        row = conn.execute(
            "SELECT next_event, meeting_url FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        old = (row["next_event"] or "") if row else ""
        old_url = row["meeting_url"] if row else None
        stage_m = _RE_STAGE.search(old)
        stage = stage_m.group(1) if stage_m else "面接"
        event = f"{stage} 確定 {info['date']}({info['weekday']}) {info['time']}"
        # 新信抽不到URL時保留舊值，不要用 None 把已知連結洗掉
        new_url = info.get("meeting_url") or old_url
        if old == event and old_url == new_url:
            info["next_event"] = event
            info["meeting_url"] = new_url
            return info
        conn.execute(
            "UPDATE applications SET next_event = ?, meeting_url = ?, "
            "last_updated = date('now','localtime') WHERE job_id = ?",
            (event, new_url, job_id),
        )
    info["next_event"] = event
    info["meeting_url"] = new_url
    info["changed"] = True

    from inbox.calendar_sync import upsert_event
    upsert_event(
        job_id, info["company"], info["title"],
        info["date"], info["time"], info.get("duration_min"),
        meeting_url=new_url,
    )
    return info
