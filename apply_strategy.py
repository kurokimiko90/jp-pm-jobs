"""投遞策略引擎 — 選 → 備 → 投，分波節奏管理。"""
import argparse
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "jobs.sqlite"
APPLY_DIR = ROOT / "output" / "apply"
CONFIG_PATH = ROOT / "apply_strategy.yaml"

JAPAN_PATTERNS = (
    "%Tokyo%", "%東京%", "%Japan%", "%大阪%", "%Osaka%",
    "%京都%", "%Kyoto%", "%リモート%", "%在宅%",
)
REMOTE_PATTERNS = ("%Remote%", "%Hybrid%")


def _load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ensure_apply_waves_table(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS apply_waves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wave INTEGER NOT NULL,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            rank INTEGER NOT NULL,
            weighted REAL NOT NULL,
            pack_ready INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'planned',
            created_at DATE NOT NULL DEFAULT (date('now')),
            UNIQUE(job_id)
        );
    """)


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    _ensure_apply_waves_table(c)
    return c


def _location_score(location: str | None) -> int:
    loc = location or ""
    for pat in JAPAN_PATTERNS:
        if pat.strip("%") in loc:
            return 100
    for pat in REMOTE_PATTERNS:
        if pat.strip("%") in loc:
            return 80
    if loc:
        return 30
    return 50


def _pack_exists(job_id: int, company: str) -> bool:
    if not APPLY_DIR.exists():
        return False
    slug = re.sub(r"[^\w]", "", company or "")[:20]
    for d in APPLY_DIR.iterdir():
        if d.is_dir() and d.name.startswith(f"{job_id}_"):
            return True
    return False


def _weighted(row, cfg):
    sc = cfg["scoring"]
    # 排序用未截斷 raw（可 >100）：95 顯示天花板下同分職缺靠 raw 拉開
    rec = row["recommend_raw"] or row["recommend_score"] or 0
    score = row["score"] or 0
    loc_s = _location_score(row["location"])
    w = rec * sc["recommend_weight"] + loc_s * sc["location_weight"] + score * sc["score_weight"]
    if row["posting_type"] == "agent":
        w -= sc["agent_penalty"]
    return round(w, 1)


# ── plan ─────────────────────────────────────────────────────────


def cmd_plan(args):
    cfg = _load_config()
    conn = _conn()
    rows = conn.execute(
        "SELECT id, title, company, score, tier, posting_type, location, "
        "CAST(json_extract(gap_analysis, '$.recommend_score') AS INTEGER) recommend_score, "
        "CAST(json_extract(gap_analysis, '$.recommend_raw') AS INTEGER) recommend_raw "
        "FROM jobs WHERE gap_analysis IS NOT NULL "
        "AND CAST(json_extract(gap_analysis, '$.recommend_score') AS INTEGER) >= 60 "
        "AND id NOT IN (SELECT job_id FROM applications)"
    ).fetchall()

    scored = []
    for r in rows:
        scored.append({**dict(r), "weighted": _weighted(r, cfg)})

    scored.sort(key=lambda x: -x["weighted"])

    seen_companies: dict[str, int] = {}
    dedup_max = cfg["dedup"]["same_company_max"]
    filtered = []
    for s in scored:
        co = (s["company"] or "").strip()
        seen_companies[co] = seen_companies.get(co, 0) + 1
        if seen_companies[co] > dedup_max:
            continue
        filtered.append(s)

    wave_sizes = cfg["waves"]["max_per_wave"]
    conn.execute("DELETE FROM apply_waves")

    wave_num = 0
    offset = 0
    for ws in wave_sizes:
        wave_num += 1
        batch = filtered[offset:offset + ws]
        if not batch:
            break
        for rank, item in enumerate(batch, 1):
            pack = _pack_exists(item["id"], item["company"])
            conn.execute(
                "INSERT INTO apply_waves (wave, job_id, rank, weighted, pack_ready, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'planned', date('now'))",
                (wave_num, item["id"], rank, item["weighted"], int(pack)),
            )
        offset += ws

    conn.commit()

    _print_plan(conn, cfg)
    conn.close()


def _print_plan(conn, cfg):
    waves = conn.execute(
        "SELECT w.*, j.title, j.company, j.score, j.tier, j.posting_type, j.location, "
        "CAST(json_extract(j.gap_analysis, '$.recommend_score') AS INTEGER) recommend_score "
        "FROM apply_waves w JOIN jobs j ON j.id = w.job_id "
        "ORDER BY w.wave, w.rank"
    ).fetchall()

    if not waves:
        print("無候選職缺。先跑 gap 分析。")
        return

    labels = {1: "本週投遞", 2: "Wave 1 後 5 天", 3: "備選池"}
    current_wave = 0

    print(f"\n{'═' * 60}")
    print(f"  投遞策略  {date.today()}")
    sc = cfg["scoring"]
    print(f"  加權: recommend×{sc['recommend_weight']} + location×{sc['location_weight']}"
          f" + score×{sc['score_weight']} − agent×{sc['agent_penalty']}")
    print(f"{'═' * 60}\n")

    for w in waves:
        if w["wave"] != current_wave:
            current_wave = w["wave"]
            count = sum(1 for x in waves if x["wave"] == current_wave)
            lbl = labels.get(current_wave, "")
            print(f"  Wave {current_wave} ({count}家) — {lbl}")
            print(f"  {'─' * 56}")
            print(f"  {'順':>2}  {'ID':>5}  {'加權':>5}  {'推薦':>4}  {'評分':>4}  {'地域':<6}  {'投稿':<4}  {'包':<2}  会社・ポジション")
            print(f"  {'─' * 56}")

        loc_label = "日本" if _location_score(w["location"]) == 100 else (
            "遠端" if _location_score(w["location"]) == 80 else "海外")
        pt = "直接" if w["posting_type"] == "direct" else "仲介"
        pack = "✓" if w["pack_ready"] else "✗"
        title = (w["company"] or "")[:12] + " · " + (w["title"] or "")[:20]

        print(f"  {w['rank']:>2}  {w['job_id']:>5}  {w['weighted']:>5.1f}  "
              f"{w['recommend_score'] or 0:>4}  {w['score'] or 0:>4}  "
              f"{loc_label:<6}  {pt:<4}  {pack:<2}  {title}")

    print()
    not_ready = sum(1 for w in waves if w["wave"] == 1 and not w["pack_ready"])
    if not_ready:
        print(f"  ⚠ Wave 1 有 {not_ready} 家未備包 → 先跑 prep.py")
    print()


# ── today ────────────────────────────────────────────────────────


def cmd_today(args):
    cfg = _load_config()
    conn = _conn()

    today_applied = conn.execute(
        "SELECT COUNT(*) n FROM applications WHERE applied_at = date('now')"
    ).fetchone()["n"]
    max_daily = cfg["daily"]["max_submit"]
    remaining = max(0, max_daily - today_applied)
    weekday = datetime.now().strftime("%A")
    jp_hrs = cfg["daily"]["jp_hours"]

    wave1 = conn.execute(
        "SELECT w.*, j.title, j.company, j.score, j.tier, j.posting_type, j.location, "
        "CAST(json_extract(j.gap_analysis, '$.recommend_score') AS INTEGER) recommend_score "
        "FROM apply_waves w JOIN jobs j ON j.id = w.job_id "
        "WHERE w.wave = 1 ORDER BY w.rank"
    ).fetchall()

    if not wave1:
        print("尚無投遞計畫。先跑: python3 apply_strategy.py plan")
        return

    submitted = [w for w in wave1 if w["status"] == "submitted"]
    ready = [w for w in wave1 if w["status"] == "planned" and w["pack_ready"]]
    need_prep = [w for w in wave1 if w["status"] == "planned" and not w["pack_ready"]]

    print(f"\n{'═' * 50}")
    print(f"  今日投遞  {date.today()} ({weekday})")
    print(f"{'═' * 50}\n")
    print(f"  今日已投: {today_applied}/{max_daily}    可投: {remaining} 家\n")

    if ready and remaining > 0:
        print("  推薦投遞:")
        for w in ready[:remaining]:
            is_jp = _location_score(w["location"]) == 100
            hour = f"{jp_hrs[0]:02d}:00" if is_jp else f"{cfg['daily']['foreign_hours'][0]:02d}:00"
            print(f"    {hour} → #{w['job_id']}  {w['company'][:15]} "
                  f"(加權 {w['weighted']:.1f})  包✓")
        print()

    if need_prep:
        print("  未備包（先跑 prep）:")
        for w in need_prep:
            print(f"    #{w['job_id']}  {w['company'][:15]} — python3 prep.py {w['job_id']} apply")
        print()

    total = len(wave1)
    done = len(submitted)
    bar = "█" * done + "░" * (total - done)
    print(f"  Wave 1 進度: {bar} {done}/{total} 已投")

    if done == total:
        cd = cfg["waves"]["cooldown_days"]
        print(f"  Wave 1 已完成！Wave 2 可於 {cd} 天後觸發 or 收到回覆後。")
    print()
    conn.close()


# ── prep ─────────────────────────────────────────────────────────


def cmd_prep(args):
    import subprocess as sp

    conn = _conn()
    wave_filter = args.wave or 1
    rows = conn.execute(
        "SELECT w.job_id, j.company FROM apply_waves w JOIN jobs j ON j.id = w.job_id "
        "WHERE w.wave = ? AND w.pack_ready = 0 AND w.status = 'planned' ORDER BY w.rank",
        (wave_filter,),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"Wave {wave_filter} 全部已備包。")
        return

    print(f"開始備包 Wave {wave_filter}：{len(rows)} 家\n")
    for r in rows:
        jid = r["job_id"]
        print(f"  → #{jid} {r['company'][:20]}")
        result = sp.run(
            ["python3", str(ROOT / "prep.py"), str(jid), "apply"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        if result.returncode == 0:
            conn2 = _conn()
            conn2.execute("UPDATE apply_waves SET pack_ready = 1 WHERE job_id = ?", (jid,))
            conn2.commit()
            conn2.close()
            print(f"    ✓ 完成")
        else:
            print(f"    ✗ 失敗: {result.stderr[:100]}")
    print()


# ── submit ───────────────────────────────────────────────────────


def cmd_submit(args):
    cfg = _load_config()
    conn = _conn()

    today_applied = conn.execute(
        "SELECT COUNT(*) n FROM applications WHERE applied_at = date('now')"
    ).fetchone()["n"]
    max_daily = cfg["daily"]["max_submit"]

    if args.job_id:
        job_ids = [args.job_id]
    else:
        remaining = max(0, max_daily - today_applied)
        if remaining == 0:
            print(f"今日已達上限 ({max_daily} 家)。明天再投。")
            conn.close()
            return
        rows = conn.execute(
            "SELECT w.job_id FROM apply_waves w "
            "WHERE w.wave = ? AND w.status = 'planned' AND w.pack_ready = 1 "
            "ORDER BY w.rank LIMIT ?",
            (args.wave or 1, remaining),
        ).fetchall()
        job_ids = [r["job_id"] for r in rows]

    if not job_ids:
        print("無可投遞職缺。")
        conn.close()
        return

    for jid in job_ids:
        today_applied += 1
        if today_applied > max_daily:
            print(f"⚠ 超過每日上限 ({max_daily})，#{jid} 仍標記已投。")

        existing = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?", (jid,)
        ).fetchone()
        if existing:
            print(f"  #{jid} 已在 applications 表，跳過。")
            continue

        conn.execute(
            "INSERT INTO applications (job_id, status, applied_at, last_updated) "
            "VALUES (?, 'applied', date('now'), datetime('now'))",
            (jid,),
        )
        conn.execute(
            "UPDATE apply_waves SET status = 'submitted' WHERE job_id = ?", (jid,),
        )
        job = conn.execute("SELECT company, title FROM jobs WHERE id = ?", (jid,)).fetchone()
        print(f"  ✓ #{jid} {job['company'][:15]} — 已標記 applied")

    conn.commit()
    conn.close()
    print()


# ── status ───────────────────────────────────────────────────────


def cmd_status(args):
    conn = _conn()

    waves_data = conn.execute(
        "SELECT w.wave, COUNT(*) total, "
        "SUM(w.status = 'submitted') submitted, "
        "SUM(w.status = 'planned' AND w.pack_ready = 1) ready, "
        "SUM(w.status = 'planned' AND w.pack_ready = 0) need_prep "
        "FROM apply_waves w GROUP BY w.wave ORDER BY w.wave"
    ).fetchall()

    if not waves_data:
        print("尚無投遞計畫。先跑: python3 apply_strategy.py plan")
        conn.close()
        return

    total_apps = conn.execute("SELECT COUNT(*) n FROM applications").fetchone()["n"]
    replied = conn.execute(
        "SELECT COUNT(*) n FROM applications WHERE status != 'applied'"
    ).fetchone()["n"]

    print(f"\n{'═' * 50}")
    print(f"  投遞進度")
    print(f"{'═' * 50}\n")

    for wd in waves_data:
        w = wd["wave"]
        total = wd["total"]
        sub = wd["submitted"]
        bar = "█" * sub + "░" * (total - sub)
        print(f"  Wave {w} ({total}家)  {bar}  {sub}/{total} 已投")

    print()
    if total_apps > 0:
        rate = replied / total_apps * 100 if total_apps else 0
        print(f"  回覆率: {rate:.0f}% ({replied}/{total_apps})")
    print()
    conn.close()


# ── next ─────────────────────────────────────────────────────────


def cmd_next(args):
    conn = _conn()

    wave1_left = conn.execute(
        "SELECT COUNT(*) n FROM apply_waves WHERE wave = 1 AND status = 'planned'"
    ).fetchone()["n"]
    need_prep = conn.execute(
        "SELECT w.job_id, j.company FROM apply_waves w JOIN jobs j ON j.id = w.job_id "
        "WHERE w.wave = 1 AND w.pack_ready = 0 AND w.status = 'planned' ORDER BY w.rank LIMIT 3"
    ).fetchall()

    if wave1_left > 0:
        if need_prep:
            print("\n  下一步: 備包")
            for r in need_prep:
                print(f"    python3 prep.py {r['job_id']} apply  # {r['company'][:20]}")
            print(f"\n  或批次: python3 apply_strategy.py prep --wave 1")
        else:
            print("\n  下一步: 投遞")
            print("    python3 apply_strategy.py submit --wave 1")
            print("    (或指定: python3 apply_strategy.py submit --job-id 123)")
    else:
        wave2_exists = conn.execute(
            "SELECT COUNT(*) n FROM apply_waves WHERE wave = 2"
        ).fetchone()["n"]
        if wave2_exists:
            print("\n  Wave 1 已完成。")
            print("  下一步: python3 apply_strategy.py today  (看 Wave 2 是否可觸發)")
        else:
            print("\n  所有 Wave 已完成！")

    print()
    conn.close()


# ── main ─────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(description="投遞策略引擎")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("plan", help="生成分波投遞計畫")
    sub.add_parser("today", help="今日推薦投遞")
    sub.add_parser("status", help="投遞進度總覽")
    sub.add_parser("next", help="下一步建議")

    pp = sub.add_parser("prep", help="批次備包")
    pp.add_argument("--wave", type=int, default=1)

    sp = sub.add_parser("submit", help="標記已投遞")
    sp.add_argument("--wave", type=int, default=1)
    sp.add_argument("--job-id", type=int, default=None)

    args = p.parse_args()
    cmds = {
        "plan": cmd_plan, "today": cmd_today, "status": cmd_status,
        "next": cmd_next, "prep": cmd_prep, "submit": cmd_submit,
    }
    fn = cmds.get(args.cmd)
    if fn:
        fn(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
