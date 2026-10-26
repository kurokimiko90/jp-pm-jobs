"""upsert_job の「同公司併入」分支と、その回避（allow_company_dup）の検証。

r-agent「気になる」（星付き）は本人が選んだ求人なので、他サイト経由で同じ会社が
既に在庫にあっても併入せず独立 row を作る — 併入すると r-agent の source_id /
URL / JD が残らず、r-agent 経由で応募できなくなるため。
"""

import pytest

from tracker import db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    db.init_db()
    return db


def _job(source, source_id, company="株式会社テスト", title="【PdM】プロダクトマネージャー"):
    return {
        "source": source,
        "source_id": source_id,
        "url": f"https://example.com/{source_id}",
        "title": title,
        "company": company,
        "location": "東京都",
        "raw_jd": "職務内容 …",
    }


def test_same_company_merges_by_default(tmp_db):
    first_id, is_new = tmp_db.upsert_job(_job("green", "g-1"))
    assert is_new

    merged_id, is_new2 = tmp_db.upsert_job(_job("recruiter_agent", "108014834"))
    assert merged_id == first_id
    assert not is_new2

    with tmp_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1
        # 併入なので r-agent の source_id は残らない
        assert conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE source='recruiter_agent'"
        ).fetchone()["c"] == 0


def test_allow_company_dup_keeps_own_row(tmp_db):
    first_id, _ = tmp_db.upsert_job(_job("green", "g-1"))

    own_id, is_new = tmp_db.upsert_job(
        _job("recruiter_agent", "108014834"), allow_company_dup=True
    )
    assert is_new
    assert own_id != first_id

    with tmp_db.connect() as conn:
        row = conn.execute(
            "SELECT source_id, url FROM jobs WHERE source='recruiter_agent'"
        ).fetchone()
        assert row["source_id"] == "108014834"
        assert row["url"].endswith("108014834")


def test_allow_company_dup_still_dedups_same_source_id(tmp_db):
    first_id, _ = tmp_db.upsert_job(_job("recruiter_agent", "108014834"), allow_company_dup=True)
    again_id, is_new = tmp_db.upsert_job(
        _job("recruiter_agent", "108014834"), allow_company_dup=True
    )
    assert again_id == first_id
    assert not is_new


def test_dedup_fuzzy_keeps_starred_row_and_inherits(tmp_db):
    other_id, _ = tmp_db.upsert_job(
        dict(_job("green", "g-1"), raw_jd="職務内容 " * 500)
    )
    with tmp_db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET score = 77, recommend_score = 88, gap_analysis = ? WHERE id = ?",
            ('{"x": 1}', other_id),
        )

    starred = dict(_job("recruiter_agent", "108014834"), keyword="mypage_interest", raw_jd="短い JD")
    starred_id, _ = tmp_db.upsert_job(starred, allow_company_dup=True)

    tmp_db.dedup_fuzzy()

    with tmp_db.connect() as conn:
        rows = conn.execute("SELECT id, source, raw_jd, score, recommend_score, gap_analysis FROM jobs").fetchall()
    assert [r["id"] for r in rows] == [starred_id]          # 星付きが残る
    assert len(rows[0]["raw_jd"]) > 100                      # 長い JD を引き継ぐ
    assert (rows[0]["score"], rows[0]["recommend_score"]) == (77, 88)
    assert rows[0]["gap_analysis"] == '{"x": 1}'


def test_dedup_fuzzy_default_keeps_longest_jd(tmp_db):
    """星付きが絡まない簇は従来どおり「JD が長い方」を残す。"""
    short_id, _ = tmp_db.upsert_job(dict(_job("green", "g-1"), raw_jd="短い"))
    with tmp_db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (source, source_id, url, title, company, company_norm, raw_jd, "
            "first_seen, last_seen) VALUES ('indeed_jp','i-1','https://e/i-1', ?, ?, "
            "(SELECT company_norm FROM jobs WHERE id = ?), ?, date('now'), date('now'))",
            ("【PdM】プロダクトマネージャー", "株式会社テスト", short_id, "職務内容 " * 500),
        )
        long_id = conn.execute("SELECT id FROM jobs WHERE source='indeed_jp'").fetchone()["id"]

    tmp_db.dedup_fuzzy()

    with tmp_db.connect() as conn:
        assert [r["id"] for r in conn.execute("SELECT id FROM jobs")] == [long_id]


def test_mark_starred_appends_to_existing_keyword(tmp_db):
    jid, _ = tmp_db.upsert_job(dict(_job("recruiter_agent", "108014834"), keyword="search:PdM"))
    other, _ = tmp_db.upsert_job(
        dict(_job("recruiter_agent", "999", company="別会社"), keyword="search:PdM")
    )

    assert tmp_db.mark_starred("recruiter_agent", ["108014834"]) == 1
    assert tmp_db.mark_starred("recruiter_agent", ["108014834"]) == 0  # 冪等

    with tmp_db.connect() as conn:
        kws = {r["id"]: r["keyword"] for r in conn.execute("SELECT id, keyword FROM jobs")}
    assert kws[jid] == "search:PdM|mypage_interest"
    assert kws[other] == "search:PdM"


def test_dedup_fuzzy_protects_starred_marked_by_backfill(tmp_db):
    """後から星印を追記した既存 row（keyword 併記）も keep 側に回る。"""
    starred_id, _ = tmp_db.upsert_job(
        dict(_job("recruiter_agent", "108014834"), keyword="search:PdM", raw_jd="短い JD")
    )
    tmp_db.mark_starred("recruiter_agent", ["108014834"])
    with tmp_db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (source, source_id, url, title, company, company_norm, raw_jd, "
            "first_seen, last_seen) VALUES ('green','g-1','https://e/g-1', ?, ?, "
            "(SELECT company_norm FROM jobs WHERE id = ?), ?, date('now'), date('now'))",
            ("【PdM】プロダクトマネージャー", "株式会社テスト", starred_id, "職務内容 " * 500),
        )

    tmp_db.dedup_fuzzy()

    with tmp_db.connect() as conn:
        rows = conn.execute("SELECT id, raw_jd FROM jobs").fetchall()
    assert [r["id"] for r in rows] == [starred_id]
    assert len(rows[0]["raw_jd"]) > 100


def test_dedup_fuzzy_repoints_inbox_mails_before_delete(tmp_db):
    """消す側を inbox_mails が指していても FK で落ちず、keep へ付け替えて消す。"""
    long_id, _ = tmp_db.upsert_job(_job("green", "g-1"))
    short_id, _ = tmp_db.upsert_job(_job("indeed_jp", "i-1"), allow_company_dup=True)
    with tmp_db.connect() as conn:
        conn.execute("UPDATE jobs SET raw_jd = ? WHERE id = ?", ("長い JD " * 100, long_id))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS inbox_mails (id INTEGER PRIMARY KEY, "
            "gmail_msg_id TEXT NOT NULL UNIQUE, job_id INTEGER REFERENCES jobs(id))"
        )
        conn.execute("INSERT INTO inbox_mails (gmail_msg_id, job_id) VALUES ('m1', ?)", (short_id,))

    plan = tmp_db.dedup_fuzzy()

    assert [p["id"] for p in plan] == [short_id]
    with tmp_db.connect() as conn:
        assert conn.execute("SELECT job_id FROM inbox_mails").fetchone()["job_id"] == long_id
        assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1


def test_dedup_fuzzy_skips_without_moving_app_when_direct_conflicts(tmp_db):
    """direct_apply が両方にある簇では、投遞記錄も動かさず保留する。"""
    keep_id, _ = tmp_db.upsert_job(_job("green", "g-1"))
    other_id, _ = tmp_db.upsert_job(_job("indeed_jp", "i-1"), allow_company_dup=True)
    with tmp_db.connect() as conn:
        conn.execute("UPDATE jobs SET raw_jd = ? WHERE id = ?", ("長い JD " * 100, keep_id))
        for jid in (keep_id, other_id):
            conn.execute("INSERT INTO direct_apply (job_id) VALUES (?)", (jid,))
        conn.execute(
            "INSERT INTO applications (job_id, status, applied_at, last_updated) VALUES (?, 'applied', '2026-10-01', '2026-10-01')",
            (other_id,),
        )

    assert tmp_db.dedup_fuzzy() == []
    with tmp_db.connect() as conn:
        assert conn.execute("SELECT job_id FROM applications").fetchone()["job_id"] == other_id
