"""SQLite-backed job + application store.

中央 DB 跨爬蟲共用。所有 scraper 寫入 `jobs` 表，跨日去重靠 (source, source_id) UNIQUE。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

# JOBS_DB 環境變數可覆蓋（demo 截圖 / 測試用替身 DB；與 dashboard/backend/paths.py 一致）
DB_PATH = Path(os.environ.get("JOBS_DB") or Path(__file__).parent.parent / "data" / "jobs.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    raw_jd TEXT,
    keyword TEXT,
    first_seen DATE NOT NULL,
    last_seen DATE NOT NULL,
    -- Phase 2 之後填入 ↓
    job_type TEXT,         -- PdM / PjM / PMO / other
    classify_conf REAL,
    salary_min INTEGER,    -- 萬円
    salary_max INTEGER,
    jp_level TEXT,         -- N1 / N2 / N3 / none
    remote TEXT,           -- full / hybrid / onsite
    sponsor_visa INTEGER,  -- 0 / 1
    tech_stack TEXT,       -- comma-separated
    score INTEGER,         -- 0-100
    -- Phase 3 (2026 AI PM 對標) ↓
    tier TEXT,             -- mega_venture / traditional_sier / ai_startup / unknown
    tier_conf REAL,        -- 0.0-1.0 classifier confidence
    score_breakdown TEXT,  -- JSON: per-dimension scores
    tailored_resume_path TEXT,
    gap_analysis TEXT,     -- JSON: LLM gap 分析（requirements/matched/gaps/recommend_score/reason）
    gap_batch_id INTEGER,
    recommend_score INTEGER,      -- 0-100, gap 分析から抽出
    apply_pack TEXT,              -- JSON: {"status","path","updated_at"}
    interview_pack TEXT,          -- JSON: {"status","path","updated_at"}
    liveness_status TEXT,         -- active / expired / uncertain / error
    liveness_checked_at DATE,
    company_norm TEXT,            -- 正規化公司名（跨來源模糊查重用，見 tools.dedup_match）
    employee_count INTEGER,       -- 從業員數（raw_jd 正則抽取；JD 未提及 = NULL）
    mentions_ai INTEGER,          -- JD 是否提及 AI（1/0；raw_jd 空 = NULL）
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_tier ON jobs(tier);
-- dashboard 的「同公司應募狀態」關聯子查詢按 company 比對，無索引時每行全表掃描
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
-- idx_jobs_company_norm 由 _migrate() 在 ALTER 後建立（既有 DB 尚無該欄）

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    status TEXT NOT NULL,        -- applied / recruiter / tech / onsite / offer / rejected
    applied_at DATE NOT NULL,
    last_updated DATE NOT NULL,
    resume_version TEXT,
    notes TEXT,
    next_event TEXT,
    rejection_stage TEXT,
    rejection_reason TEXT,
    gcal_event_id TEXT,
    meeting_url TEXT,             -- 確定面接のオンライン会議URL（抽出できた場合のみ）
    UNIQUE(job_id)
);

CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);

-- 官網直投（tools/direct_apply.py）：窗口探測 → 人工確認 → Gmail 草稿 → 寄出偵測
CREATE TABLE IF NOT EXISTS direct_apply (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    status TEXT NOT NULL DEFAULT 'pending',
    -- pending → detected / not_found → pack_ready → drafted → sent / skipped / failed
    apply_method TEXT,       -- email / form / none
    careers_url TEXT,        -- 官網求人頁（人工確認 + 面試前重讀用）
    form_url TEXT,           -- 應募フォーム URL（form_only 時）
    emails_json TEXT,        -- [{"email","confidence","source"}]
    confirmed_email TEXT,    -- 人工確認後的收件地址（未確認不建 Gmail 草稿）
    official_domain TEXT,
    pack_dir TEXT,           -- 投遞包目錄（brief / 応募メール / 職務経歴書）
    gmail_draft_id TEXT,
    error TEXT,
    detected_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_direct_apply_status ON direct_apply(status);
"""

# 応募経路（applications.channel）：source → channel 推定。
# direct（官網直投）由 direct_apply 流程顯式寫入，不走此推定。
CHANNEL_BY_SOURCE = {
    "recruiter_agent": "r-agent",
    "jac_recruitment": "jac",
    "bizreach": "bizreach",
    "linkedin_jp": "linkedin",
    "green": "green",
    "indeed_jp": "indeed",
}


def channel_for_source(source: str | None) -> str:
    return CHANNEL_BY_SOURCE.get(source or "", "other")

# 選考歷程（application_events）：trigger 自動記錄 applications.status 每次變化，
# 涵蓋所有寫入路徑（dashboard / Telegram bot / CLI），呼叫端零改動。
# 刪除應募記錄時一併清掉歷程，避免重新應募時混入舊軌跡。
EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    prev_status TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_app_events_job ON application_events(job_id);

CREATE TRIGGER IF NOT EXISTS trg_app_events_insert
AFTER INSERT ON applications
BEGIN
    INSERT INTO application_events (job_id, status, prev_status)
    VALUES (new.job_id, new.status, NULL);
END;

CREATE TRIGGER IF NOT EXISTS trg_app_events_status
AFTER UPDATE OF status ON applications
WHEN old.status IS NOT new.status
BEGIN
    INSERT INTO application_events (job_id, status, prev_status)
    VALUES (new.job_id, new.status, old.status);
END;

CREATE TRIGGER IF NOT EXISTS trg_app_events_delete
AFTER DELETE ON applications
BEGIN
    DELETE FROM application_events WHERE job_id = old.job_id;
END;
"""


def ensure_application_events(conn: sqlite3.Connection) -> None:
    """建立選考歷程表 + trigger（冪等），並為既有應募回填 baseline 事件。

    回填只能還原「應募日起點 + 現狀」兩點；中間階段日期已不可考，不編造。
    """
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='applications'"
    ).fetchone():
        return
    conn.executescript(EVENTS_SCHEMA)
    rows = conn.execute(
        "SELECT a.job_id, a.status, a.applied_at, a.last_updated"
        " FROM applications a"
        " LEFT JOIN application_events e ON e.job_id = a.job_id"
        " WHERE e.id IS NULL"
    ).fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO application_events (job_id, status, prev_status, changed_at)"
            " VALUES (?, 'applied', NULL, ?)",
            (r["job_id"], r["applied_at"]),
        )
        if r["status"] != "applied":
            conn.execute(
                "INSERT INTO application_events (job_id, status, prev_status, changed_at)"
                " VALUES (?, ?, 'applied', ?)",
                (r["job_id"], r["status"], r["last_updated"]),
            )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """既有 DB 的增量遷移（新欄回填）。"""
    from tools.dedup_match import normalize_company

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "company_norm" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_norm TEXT")
    # JD 補充欄位（job_type 值 / 從業員數 / AI 提及）由 analyzer.jd_enrich 回填
    if "employee_count" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN employee_count INTEGER")
    if "mentions_ai" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN mentions_ai INTEGER")

    # 応募経路欄：首次加欄時把既有記錄全部回填 r-agent（2026-07 前的投遞
    # 全數經 r-agent，使用者確認）。之後的新記錄由寫入端依 source 推定。
    app_cols = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
    if app_cols and "channel" not in app_cols:
        conn.execute("ALTER TABLE applications ADD COLUMN channel TEXT")
        conn.execute("UPDATE applications SET channel = 'r-agent'")
    if app_cols and "meeting_url" not in app_cols:
        conn.execute("ALTER TABLE applications ADD COLUMN meeting_url TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_company_norm ON jobs(company_norm)"
    )

    # 同步 company_norm：正規化規則升級（別名表 / 括號注記 / 英文法人格）後
    # 既存列的值會過時，全量重算、只改有差異的列
    rows = conn.execute("SELECT id, company, company_norm FROM jobs").fetchall()
    for r in rows:
        want = normalize_company(r["company"] or "")
        if want != (r["company_norm"] or ""):
            conn.execute(
                "UPDATE jobs SET company_norm = ? WHERE id = ?", (want, r["id"])
            )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        ensure_application_events(conn)


# 廣告轉址等「非職缺專屬」URL 樣式：不可作為去重依據，否則會把不同職缺誤判成同一筆
# （例：indeed 贊助廣告點擊會共用 https://jp.indeed.com/pagead/clk?mo=r，不含職缺 ID）
_GENERIC_URL_PATTERNS = (
    "indeed.com/pagead/clk",
)


def _is_generic_url(url: str) -> bool:
    return any(p in url for p in _GENERIC_URL_PATTERNS)


def upsert_job(job: dict, allow_company_dup: bool = False) -> tuple[int, bool]:
    """Insert or update a job. Returns (job_id, is_new).

    `allow_company_dup=True` は「同公司併入」分支を飛ばして自前の row を作る。
    本人が明示的に選んだ求人（r-agent「気になる」等）用 — 併入すると r-agent の
    source_id / URL / JD が残らず、その求人経由で応募できなくなるため。
    一括スクレイプ由来は既定の False（1 社 1 レコード方針を維持）。

    Required keys: source, source_id, url, title.
    Optional: company, location, raw_jd, keyword, salary_min, salary_max
    (salary_* 供已有結構化年収的來源直接寫入，如 green；沒有結構化資料的
    來源留空即可，之後由 tools.salary_parser 從 raw_jd 用 regex 補上；
    salary_parser 對已有值的欄位只在自己 parse 出結果時才覆蓋)。
    Returns (-1, False) if company is blacklisted or title is engineering-only (non-PM).
    """
    from tools.blacklist import is_blacklisted
    if is_blacklisted(job.get("company", "")):
        return -1, False

    from analyzer.role_filter import is_engineering_only
    if is_engineering_only(job.get("title", "")):
        return -1, False

    from tools.dedup_match import normalize_company

    today = date.today().isoformat()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM jobs WHERE source = ? AND source_id = ?",
            (job["source"], job["source_id"]),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE jobs SET last_seen = ?, url = ?, title = ?, "
                "company = COALESCE(?, company), location = COALESCE(?, location), "
                "raw_jd = COALESCE(?, raw_jd), "
                "salary_min = COALESCE(?, salary_min), salary_max = COALESCE(?, salary_max) "
                "WHERE id = ?",
                (
                    today,
                    job["url"],
                    job["title"],
                    job.get("company"),
                    job.get("location"),
                    job.get("raw_jd"),
                    job.get("salary_min"),
                    job.get("salary_max"),
                    existing["id"],
                ),
            )
            return existing["id"], False

        # URL 完全相同 → 視為同一則職缺，優先於同公司併存判斷（廣告轉址等
        # 非職缺專屬 URL 除外，見 _is_generic_url，否則會把不同職缺誤併）。
        url = job.get("url") or ""
        dup_id = None
        dup_reason = None
        if url and not _is_generic_url(url):
            row = conn.execute(
                "SELECT id FROM jobs WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
            if row:
                dup_id = row["id"]
                dup_reason = "URL相同"

        # 同公司併存：一家公司在庫內只留 1 筆（2026-07-18 政策決定，見
        # tools/oneoff/consolidate_company_duplicates.py）。同公司已有記錄
        # → 併入既有筆（挑 recommend_score / score 最高者），不新增 row。
        cnorm = normalize_company(job.get("company") or "")
        if dup_id is None and cnorm and not allow_company_dup:
            row = conn.execute(
                "SELECT id FROM jobs WHERE company_norm = ? "
                "ORDER BY recommend_score IS NULL, recommend_score DESC, "
                "score IS NULL, score DESC, id ASC LIMIT 1",
                (cnorm,),
            ).fetchone()
            if row:
                dup_id = row["id"]
                dup_reason = "同公司併入"
        if dup_id is not None:
            new_jd = job.get("raw_jd") or ""
            # 併入既有記錄：刷 last_seen、補空欄位、JD 取較長者
            conn.execute(
                "UPDATE jobs SET last_seen = ?, "
                "company = COALESCE(company, ?), location = COALESCE(location, ?), "
                "raw_jd = CASE WHEN length(?) > length(COALESCE(raw_jd, '')) "
                "THEN ? ELSE raw_jd END, "
                "salary_min = COALESCE(salary_min, ?), salary_max = COALESCE(salary_max, ?) "
                "WHERE id = ?",
                (
                    today,
                    job.get("company"),
                    job.get("location"),
                    new_jd,
                    new_jd,
                    job.get("salary_min"),
                    job.get("salary_max"),
                    dup_id,
                ),
            )
            print(
                f"  [dedup] {dup_reason}: {job.get('company', '')[:16]} "
                f"〈{job.get('title', '')[:24]}〉 ↔ existing#{dup_id}"
            )
            return dup_id, False

        cur = conn.execute(
            "INSERT INTO jobs (source, source_id, url, title, company, location, "
            "raw_jd, keyword, first_seen, last_seen, company_norm, salary_min, salary_max) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job["source"],
                job["source_id"],
                job["url"],
                job["title"],
                job.get("company"),
                job.get("location"),
                job.get("raw_jd"),
                job.get("keyword"),
                today,
                today,
                normalize_company(job.get("company") or ""),
                job.get("salary_min"),
                job.get("salary_max"),
            ),
        )
        return cur.lastrowid, True


def new_jobs_today() -> list[sqlite3.Row]:
    today = date.today().isoformat()
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE first_seen = ? ORDER BY source, company", (today,)
        ).fetchall()


def stats_by_source() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT source, COUNT(*) AS n FROM jobs GROUP BY source").fetchall()
        return {r["source"]: r["n"] for r in rows}


def all_jobs() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()


def update_tier(job_id: int, tier: str, conf: float) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET tier = ?, tier_conf = ? WHERE id = ?",
            (tier, conf, job_id),
        )


def update_score(job_id: int, score: int, breakdown_json: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET score = ?, score_breakdown = ? WHERE id = ?",
            (score, breakdown_json, job_id),
        )


def update_gap_analysis(job_id: int, gap_json: str) -> None:
    import json as _json
    rec = None
    try:
        rec = _json.loads(gap_json).get("recommend_score")
    except Exception:
        pass
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET gap_analysis = ?, recommend_score = ?, gap_analyzed_at = datetime('now') WHERE id = ?",
            (gap_json, rec, job_id),
        )


def update_pack_status(job_id: int, pack_type: str, path: str) -> None:
    """pack_type: 'apply' or 'interview'"""
    import json as _json
    from datetime import datetime
    col = "apply_pack" if pack_type == "apply" else "interview_pack"
    val = _json.dumps({"status": "done", "path": path,
                       "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {col} = ? WHERE id = ?", (val, job_id))


def create_gap_batch(source_filter: str | None, min_score: int) -> int:
    from datetime import datetime
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO gap_batches (created_at, source_filter, min_score) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), source_filter, min_score),
        )
        return cur.lastrowid


def assign_gap_batch(job_id: int, batch_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE jobs SET gap_batch_id = ? WHERE id = ?", (batch_id, job_id))


def finalize_gap_batch(batch_id: int, job_count: int, summary_json: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE gap_batches SET job_count = ?, summary_json = ? WHERE id = ?",
            (job_count, summary_json, batch_id),
        )


def update_liveness(job_id: int, status: str, checked_at: str | None = None) -> None:
    checked = checked_at or date.today().isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET liveness_status = ?, liveness_checked_at = ? WHERE id = ?",
            (status, checked, job_id),
        )


def update_raw_jd(job_id: int, raw_jd: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE jobs SET raw_jd = ? WHERE id = ?", (raw_jd, job_id))


def update_posting_type(job_id: int, posting_type: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET posting_type = ? WHERE id = ?",
            (posting_type, job_id),
        )


def update_salary(job_id: int, smin: int | None, smax: int | None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET salary_min = ?, salary_max = ? WHERE id = ?",
            (smin, smax, job_id),
        )


def update_tailored_path(job_id: int, path: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET tailored_resume_path = ? WHERE id = ?",
            (path, job_id),
        )


def top_scored(
    limit: int = 10, min_score: int = 0, source_like: str | None = None
) -> list[sqlite3.Row]:
    """高分職缺。source_like 為 SQL LIKE 樣式（如 'indeed%' / '%-api'）時只取該來源。"""
    sql = "SELECT * FROM jobs WHERE score >= ? AND COALESCE(blacklisted, 0) = 0"
    params: list = [min_score]
    if source_like:
        sql += " AND source LIKE ?"
        params.append(source_like)
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def stats_by_tier() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT COALESCE(tier, 'unclassified') AS tier, COUNT(*) AS n FROM jobs GROUP BY tier"
        ).fetchall()
        return {r["tier"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# dedup — 同公司同職稱且 JD 完全相同 → 保留最新，刪除舊的
# ---------------------------------------------------------------------------

def dedup_jobs() -> int:
    """合併 (company, title) 重複且 raw_jd 完全相同的職缺，保留 id 最大的。回傳刪除筆數。"""
    import hashlib

    with connect() as conn:
        dupes = conn.execute(
            "SELECT company, title, COUNT(*) as cnt "
            "FROM jobs GROUP BY company, title HAVING cnt > 1"
        ).fetchall()

        if not dupes:
            return 0

        to_delete: list[int] = []
        for row in dupes:
            company, title = row["company"], row["title"]
            jobs = conn.execute(
                "SELECT id, raw_jd FROM jobs WHERE company = ? AND title = ? ORDER BY id",
                (company, title),
            ).fetchall()

            by_hash: dict[str, list[int]] = {}
            for j in jobs:
                h = hashlib.md5((j["raw_jd"] or "").encode()).hexdigest()
                by_hash.setdefault(h, []).append(j["id"])

            for ids in by_hash.values():
                if len(ids) > 1:
                    keep = ids[-1]
                    has_app = {
                        r["job_id"]
                        for r in conn.execute(
                            f"SELECT job_id FROM applications WHERE job_id IN ({','.join('?' * len(ids))})",
                            ids,
                        ).fetchall()
                    }
                    for jid in ids:
                        if jid != keep and jid not in has_app:
                            to_delete.append(jid)

        if to_delete:
            conn.execute(
                f"DELETE FROM jobs WHERE id IN ({','.join('?' * len(to_delete))})",
                to_delete,
            )

        deleted = len(to_delete)
        if deleted:
            print(f"[dedup] 刪除 {deleted} 筆重複職缺")
        return deleted


# 本人が r-agent マイページで星を付けた求人の目印（scrapers/ragent_interests.py）
STARRED_KEYWORD = "mypage_interest"


def is_starred(keyword: str | None) -> bool:
    """`keyword` に星付き印が入っているか（他の由来ラベルとの併記 `search:x|mypage_interest` も可）。"""
    return STARRED_KEYWORD in (keyword or "")


def mark_starred(source: str, source_ids: list[str]) -> int:
    """既存 row に星付き印を追記する（既存の由来ラベルは残す）。追記した件数を返す。

    メール推薦や求人検索で先に入った求人に後から星を付けた場合、`upsert_job` の
    UPDATE 分支は keyword を触らないため印が付かない。印が無いと `dedup_fuzzy` の
    星付き優先が効かず、他源の JD が長いというだけで消される。
    """
    if not source_ids:
        return 0
    with connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET keyword = CASE WHEN keyword IS NULL OR keyword = '' "
            f"THEN '{STARRED_KEYWORD}' ELSE keyword || '|{STARRED_KEYWORD}' END "
            f"WHERE source = ? AND source_id IN ({','.join('?' * len(source_ids))}) "
            "AND COALESCE(keyword, '') NOT LIKE ?",
            (source, *source_ids, f"%{STARRED_KEYWORD}%"),
        )
        return cur.rowcount


def _keep_rank(row: sqlite3.Row) -> tuple:
    """dedup_fuzzy の keep 選び。星付き＞ JD 長い＞ score 高い＞ id 新しい。"""
    return (1 if is_starred(row["keyword"]) else 0, row["jdlen"], row["score"], row["id"])


def _inherit_into_starred(conn: sqlite3.Connection, keep_id: int, loser_ids: list[int]) -> None:
    """星付き行を keep にした簇で、消す側の情報を keep に引き継ぐ。

    星付きは「応募窓口」を根拠に keep しているだけで、JD や分析結果は他源の方が
    充実していることがある（dedup_fuzzy の既定 keep 条件がまさにそれ）。消す前に
    raw_jd（長い方）と score / recommend_score / gap_analysis（keep が空のとき）を移す。
    """
    if not loser_ids:
        return
    keep = conn.execute(
        "SELECT raw_jd, score, recommend_score, gap_analysis FROM jobs WHERE id = ?", (keep_id,)
    ).fetchone()
    if keep is None:
        return
    losers = conn.execute(
        "SELECT raw_jd, score, recommend_score, gap_analysis FROM jobs "
        f"WHERE id IN ({','.join('?' * len(loser_ids))})",
        loser_ids,
    ).fetchall()

    updates: dict[str, object] = {}
    best_jd = max((l["raw_jd"] or "" for l in losers), key=len, default="")
    if len(best_jd) > len(keep["raw_jd"] or ""):
        updates["raw_jd"] = best_jd
    for col in ("score", "recommend_score", "gap_analysis"):
        if keep[col] is None:
            val = next((l[col] for l in losers if l[col] is not None), None)
            if val is not None:
                updates[col] = val
    if not updates:
        return
    sets = ", ".join(f"{c} = ?" for c in updates)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*updates.values(), keep_id))
    print(f"  [dedup_fuzzy] 星付き#{keep_id} に引き継ぎ: {', '.join(updates)}")


def dedup_fuzzy(dry_run: bool = False) -> list[dict]:
    """全庫跨來源模糊去重：同正規化公司 + 標題相似度 ≥ 門檻 → 視為同職缺。

    每簇保留「資料最完整」者（JD 較長 > score 較高 > id 較新），刪除其餘。
    例外：本人が星を付けた求人（`keyword='mypage_interest'`）は無条件で keep 側に回す
    — r-agent 経由の応募窓口（source_id / URL）はここでしか手に入らないため。
    その場合だけ、消す側から raw_jd（長い方）と score / recommend_score /
    gap_analysis（keep が空のとき）を引き継いでから消す。
    若被刪筆掛著投遞記錄而 keep 沒有，則先把投遞遷移到 keep 再刪空殼
    （投遞記錄不丟、改指向有 JD 的完整版）。簇內出現兩筆都有投遞時保守保留、不刪。

    Args:
        dry_run: True 時只回傳計畫，不實際刪除 / 遷移。

    Returns:
        計畫清單，每筆含 id/source/keep_id/keep_source/migrated_app。
    """
    from difflib import SequenceMatcher
    from tools.dedup_match import TITLE_SIM_THRESHOLD, level_profile, normalize_title

    plan: list[dict] = []
    with connect() as conn:
        groups = conn.execute(
            "SELECT company_norm FROM jobs "
            "WHERE company_norm IS NOT NULL AND company_norm != '' "
            "GROUP BY company_norm HAVING COUNT(*) > 1"
        ).fetchall()

        has_app = {
            r["job_id"] for r in conn.execute("SELECT job_id FROM applications").fetchall()
        }
        # direct_apply も job_id が FK（PRIMARY KEY REFERENCES jobs(id)）。
        # has_app と同様に保護しないと削除時に FOREIGN KEY constraint failed で
        # dedup_fuzzy 全体が例外落ち → 後続の分類/評分が丸ごと止まる。
        has_direct = {
            r["job_id"] for r in conn.execute("SELECT job_id FROM direct_apply").fetchall()
        }

        to_delete: list[int] = []
        app_migrations: list[tuple[int, int]] = []  # (from_job_id, to_job_id)
        direct_migrations: list[tuple[int, int]] = []  # (from_job_id, to_job_id)
        starred_inherits: list[tuple[int, list[int]]] = []  # (keep_id, [消す側 id])
        for g in groups:
            rows = conn.execute(
                "SELECT id, source, company, title, keyword, "
                "length(COALESCE(raw_jd, '')) AS jdlen, COALESCE(score, 0) AS score "
                "FROM jobs WHERE company_norm = ? ORDER BY id",
                (g["company_norm"],),
            ).fetchall()

            # 貪婪聚類：標題相似度 ≥ 門檻 且 職級 token 一致 歸為同簇
            # （職級守衛與 tools.dedup_match.find_duplicate 一致，
            #  避免入庫時保住的 senior/regular 變體在後處理被誤刪）
            clusters: list[list[sqlite3.Row]] = []
            for r in rows:
                tnorm = normalize_title(r["title"] or "")
                lvl = level_profile(r["title"] or "")
                placed = False
                for cl in clusters:
                    rep = normalize_title(cl[0]["title"] or "")
                    if lvl != level_profile(cl[0]["title"] or ""):
                        continue
                    if SequenceMatcher(None, tnorm, rep).ratio() >= TITLE_SIM_THRESHOLD:
                        cl.append(r)
                        placed = True
                        break
                if not placed:
                    clusters.append([r])

            for cl in clusters:
                if len(cl) < 2:
                    continue
                # 星付き優先 → 資料最完整者（JD 長 > score 高 > id 新）
                keep = max(cl, key=_keep_rank)
                keep_has_app = keep["id"] in has_app
                keep_has_direct = keep["id"] in has_direct
                cluster_deleted: list[int] = []
                for j in cl:
                    if j["id"] == keep["id"]:
                        continue
                    migrated = False
                    if j["id"] in has_app:
                        if keep_has_app:
                            continue  # 兩筆都有投遞，保守保留，不刪
                        app_migrations.append((j["id"], keep["id"]))
                        keep_has_app = True
                        migrated = True
                    if j["id"] in has_direct:
                        if keep_has_direct:
                            continue  # 兩筆都有官網直投包，保守保留，不刪
                        direct_migrations.append((j["id"], keep["id"]))
                        keep_has_direct = True
                        migrated = True
                    to_delete.append(j["id"])
                    cluster_deleted.append(j["id"])
                    plan.append({
                        "id": j["id"], "source": j["source"],
                        "company": j["company"], "title": j["title"],
                        "keep_id": keep["id"], "keep_source": keep["source"],
                        "migrated_app": migrated,
                    })
                # 星付きを keep にした簇だけ、実際に消す行から情報を引き継ぐ
                if is_starred(keep["keyword"]) and cluster_deleted:
                    starred_inherits.append((keep["id"], cluster_deleted))

        if not dry_run:
            for keep_id, loser_ids in starred_inherits:
                _inherit_into_starred(conn, keep_id, loser_ids)
            for from_id, to_id in app_migrations:
                conn.execute(
                    "UPDATE applications SET job_id = ? WHERE job_id = ?",
                    (to_id, from_id),
                )
                try:
                    conn.execute(
                        "UPDATE application_events SET job_id = ? WHERE job_id = ?",
                        (to_id, from_id),
                    )
                except sqlite3.OperationalError:
                    pass  # 歷程表尚未建立（未跑過 init_db / dashboard）
            if app_migrations:
                print(f"[dedup_fuzzy] 遷移 {len(app_migrations)} 筆投遞記錄至完整版")
            for from_id, to_id in direct_migrations:
                conn.execute(
                    "UPDATE direct_apply SET job_id = ? WHERE job_id = ?",
                    (to_id, from_id),
                )
            if direct_migrations:
                print(f"[dedup_fuzzy] 遷移 {len(direct_migrations)} 筆官網直投包至完整版")
            if to_delete:
                conn.execute(
                    f"DELETE FROM jobs WHERE id IN ({','.join('?' * len(to_delete))})",
                    to_delete,
                )
                print(f"[dedup_fuzzy] 刪除 {len(to_delete)} 筆跨來源重複")

    return plan


# ---------------------------------------------------------------------------
# applications — 投遞追蹤（漏斗：applied → recruiter → tech → onsite → offer/rejected）
# ---------------------------------------------------------------------------

# 漏斗順序（用於週報排序與「已回覆」判定）。casual＝カジュアル面談，在一次面接（recruiter）
# 之前的非正式階段，不是每家都有（偶爾 2 家左右）。
FUNNEL_STAGES = ["applied", "casual", "recruiter", "tech", "onsite", "offer", "rejected"]
# 「收到回覆」= 進入 casual 以後任一階段（applied 之外，rejected 也算有回覆）
REPLIED_STAGES = {"casual", "recruiter", "tech", "onsite", "offer", "rejected"}

# 拒絕階段代碼（唯一真實來源，dashboard/backend/applications.py 的 LABELS 對應此表）
REJECTION_STAGES = ["shorui", "casual", "ichiji", "niji", "saishu"]
# 舊格式/中日文標籤 → 代碼（歷史資料混進過這些值，統一在寫入前正規化，不再逐一防禦下游）
_REJECTION_STAGE_SYNONYMS = {
    "書類選考": "shorui",
    "カジュアル面談": "casual",
    "一次面接": "ichiji", "1次選考": "ichiji",
    "二次面接": "niji", "2次選考": "niji",
    "最終面接": "saishu", "最終選考": "saishu",
}


def normalize_rejection_stage(value: str | None) -> str | None:
    """寫入前統一正規化：已是代碼原樣放行，已知舊標籤轉代碼，無法辨識回 None（不捏造階段）。"""
    if not value:
        return None
    if value in REJECTION_STAGES:
        return value
    return _REJECTION_STAGE_SYNONYMS.get(value)


def record_application(
    job_id: int,
    status: str = "applied",
    applied_at: str | None = None,
    resume_version: str | None = None,
    notes: str | None = None,
    channel: str | None = None,
    last_updated: str | None = None,
    rejection_stage: str | None = None,
    rejection_reason: str | None = None,
) -> int:
    """記錄一筆投遞。job_id 已存在則更新狀態（UNIQUE(job_id)）。回傳 application id。

    channel 未指定時依 jobs.source 推定（recruiter_agent → r-agent 等）；
    官網直投流程顯式傳 channel="direct"。既有記錄的 channel 只在顯式傳入時覆蓋。
    last_updated 未指定時預設今天；rejection_stage/reason 未指定時保留既有值不覆蓋
    （inbox.rejection_ack 等自動記錄流程用來標註選考段位/理由，不影響一般呼叫端）。
    """
    if status not in FUNNEL_STAGES:
        raise ValueError(f"status 必須是 {FUNNEL_STAGES} 之一，收到 {status!r}")
    rejection_stage = normalize_rejection_stage(rejection_stage)
    today = applied_at or date.today().isoformat()
    updated = last_updated or date.today().isoformat()
    with connect() as conn:
        explicit_channel = channel
        if channel is None:
            row = conn.execute("SELECT source FROM jobs WHERE id = ?", (job_id,)).fetchone()
            channel = channel_for_source(row["source"] if row else None)
        cur = conn.execute(
            """INSERT INTO applications (job_id, status, applied_at, last_updated,
                                         resume_version, notes, channel,
                                         rejection_stage, rejection_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                   status = excluded.status,
                   last_updated = excluded.last_updated,
                   resume_version = COALESCE(excluded.resume_version, applications.resume_version),
                   notes = COALESCE(excluded.notes, applications.notes),
                   channel = CASE WHEN ? IS NOT NULL THEN excluded.channel
                                  ELSE COALESCE(applications.channel, excluded.channel) END,
                   rejection_stage = COALESCE(excluded.rejection_stage, applications.rejection_stage),
                   rejection_reason = COALESCE(excluded.rejection_reason, applications.rejection_reason)""",
            (job_id, status, today, updated, resume_version, notes, channel,
             rejection_stage, rejection_reason, explicit_channel),
        )
        row = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row["id"] if row else cur.lastrowid


# ---------------------------------------------------------------------------
# direct_apply — 官網直投（探測 → 確認 → 草稿 → 寄出）
# ---------------------------------------------------------------------------

DIRECT_APPLY_STATUSES = [
    "pending", "detected", "not_found", "pack_ready",
    "drafted", "sent", "skipped", "failed",
]


def upsert_direct_apply(job_id: int, **fields) -> None:
    """寫入/更新一筆直投記錄。fields 只更新傳入的欄位。"""
    allowed = {
        "status", "apply_method", "careers_url", "form_url", "emails_json",
        "confirmed_email", "official_domain", "pack_dir", "gmail_draft_id",
        "error", "detected_at",
    }
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"未知欄位: {bad}")
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO direct_apply (job_id, updated_at) VALUES (?, ?) "
            "ON CONFLICT(job_id) DO NOTHING", (job_id, now))
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE direct_apply SET {sets}, updated_at = ? WHERE job_id = ?",
                (*fields.values(), now, job_id))


def get_direct_apply(job_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM direct_apply WHERE job_id = ?", (job_id,)).fetchone()


def direct_apply_candidates(
    min_recommend: int = 80, min_score: int = 55, limit: int = 5
) -> list[sqlite3.Row]:
    """常駐掃描的選件：推薦分 > 門檻、基礎評分 > 門檻、未 blacklist、未失效、
    同公司（company_norm）沒有既有應募、同公司沒有其他非失敗直投記錄
    （failed 例外 — LLM 故障輪的殘留可重試，管線冪等不重耗）、同公司多筆職缺
    只取推薦分最高一筆。日本職缺過濾在呼叫端。"""
    with connect() as conn:
        return conn.execute(
            """SELECT * FROM (
                   SELECT j.*,
                          ROW_NUMBER() OVER (
                              PARTITION BY CASE WHEN j.company_norm IS NULL OR j.company_norm = ''
                                                 THEN 'row_' || j.id ELSE j.company_norm END
                              ORDER BY j.recommend_score DESC, j.score DESC
                          ) AS _company_rank
                   FROM jobs j
                   LEFT JOIN direct_apply d ON d.job_id = j.id
                   WHERE j.recommend_score > ?
                     AND j.score > ?
                     AND COALESCE(j.blacklisted, 0) = 0
                     AND COALESCE(j.liveness_status, 'active') NOT IN ('expired', 'dead', 'closed')
                     AND (d.job_id IS NULL OR d.status = 'failed')
                     AND NOT EXISTS (
                         SELECT 1 FROM applications a JOIN jobs j2 ON j2.id = a.job_id
                         WHERE j2.company_norm = j.company_norm AND j.company_norm != '')
                     AND NOT EXISTS (
                         SELECT 1 FROM direct_apply d2 JOIN jobs j3 ON j3.id = d2.job_id
                         WHERE j3.company_norm = j.company_norm AND j.company_norm != ''
                           AND d2.job_id != j.id AND d2.status != 'failed')
               )
               WHERE _company_rank = 1
               ORDER BY recommend_score DESC, score DESC
               LIMIT ?""",
            (min_recommend, min_score, limit * 4),  # 日本職缺過濾後可能剩不足，多抓 4 倍
        ).fetchall()


def update_application_status(job_id: int, status: str, notes: str | None = None) -> None:
    """更新已投遞職缺的漏斗狀態。"""
    if status not in FUNNEL_STAGES:
        raise ValueError(f"status 必須是 {FUNNEL_STAGES} 之一，收到 {status!r}")
    with connect() as conn:
        conn.execute(
            """UPDATE applications
               SET status = ?, last_updated = ?,
                   notes = COALESCE(?, notes)
               WHERE job_id = ?""",
            (status, date.today().isoformat(), notes, job_id),
        )


def list_applications(status: str | None = None) -> list[sqlite3.Row]:
    """列出投遞（join jobs 取標題/公司/分數）。"""
    sql = """SELECT a.id, a.job_id, a.status, a.applied_at, a.last_updated,
                    a.resume_version, a.notes,
                    j.title, j.company, j.url, j.score, j.tier
             FROM applications a JOIN jobs j ON j.id = a.job_id"""
    params: tuple = ()
    if status:
        sql += " WHERE a.status = ?"
        params = (status,)
    sql += " ORDER BY a.last_updated DESC"
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def funnel_stats(since: str | None = None) -> dict:
    """投遞漏斗統計。since=YYYY-MM-DD 則只算該日(含)之後投遞的。

    回傳 {by_status, total, replied, replied_rate, avg_score}。
    """
    sql = "SELECT a.status, j.score FROM applications a JOIN jobs j ON j.id = a.job_id"
    params: tuple = ()
    if since:
        sql += " WHERE a.applied_at >= ?"
        params = (since,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    by_status = {s: 0 for s in FUNNEL_STAGES}
    scores = []
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["score"] is not None:
            scores.append(r["score"])
    total = len(rows)
    replied = sum(1 for r in rows if r["status"] in REPLIED_STAGES)
    return {
        "by_status": by_status,
        "total": total,
        "replied": replied,
        "replied_rate": round(replied / total, 3) if total else 0.0,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
    }


if __name__ == "__main__":
    init_db()
    print(f"DB initialized: {DB_PATH}")
    print(f"Sources: {stats_by_source()}")
