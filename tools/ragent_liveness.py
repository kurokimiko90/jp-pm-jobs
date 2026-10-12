#!/usr/bin/env python3
"""recruiter_agent (リクルートエージェント) 職缺存活檢測。

r-agent 的 `/joboffers/{id}` 裸 URL 一定回「エラーが発生しました」，必須帶流入元標籤
（`scrapers.recruiter_agent.joboffer_url()`）。
本腳本：
  1. 自動啟動 / 重用 CDP Chrome（預設 port 9270 / profile ~/.chrome-bizreach，已登入帳號）
  2. 用 Playwright 連 CDP 逐筆開 JD，判讀 active / expired
  3. 更新 jobs.liveness_status + liveness_checked_at

CDP profile 預設 9270/~/.chrome-bizreach（該 profile 已同時登入
Recruit Agent）；換 profile 用 --cdp / --profile-dir 覆寫。

用法:
    python3 -m tools.ragent_liveness --top 30            # 高分前 30 筆
    python3 -m tools.ragent_liveness --min-score 70      # 分數 >=70
    python3 -m tools.ragent_liveness --days 14           # last_seen 14 天前
    python3 -m tools.ragent_liveness --ids 2710,2676     # 指定 job_id
    python3 -m tools.ragent_liveness --all               # 全部 r-agent 職缺
    python3 -m tools.ragent_liveness --top 30 --dry-run  # 不寫 DB
    python3 -m tools.ragent_liveness --cdp 9270 --profile-dir ~/.chrome-bizreach

注意：`--mail-days` / `--mail-max` 是舊 Gmail 方式的殘留參數，已無作用（保留只為不破壞
既有排程指令），下次改動時可刪。
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from tracker import db
from tools.liveness import Liveness

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# 已登入 Recruit Agent 帳號的 profile（與 bizreach 共用同一 Chrome profile）
DEFAULT_PORT = 9270
DEFAULT_PROFILE = str(Path.home() / ".chrome-bizreach")
LANDING_URL = "https://mypage.r-agent.com/"

# title / body 命中即判已關閉（含裸 URL 報錯字樣）
EXPIRED_SIGNALS = [
    "エラーが発生", "掲載が終了", "掲載は終了", "掲載期間が終了", "募集終了", "募集は終了",
    "応募を締め切", "応募受付は終了", "この求人は終了", "この求人は既に", "この求人票は無効",
    "求人が見つかりません", "ご紹介を終了", "公開を終了", "お探しのページ", "ページが見つか",
    "受付終了",
    "this job is no longer",
]
# final_url 命中 → 推薦被撤回 / 失效
EXPIRED_REDIRECTS = ["/recommend", "/error", "/expired"]
LOGIN_SIGNALS = ["OIDCLogin", "/login"]


def _ensure_cdp(port: int, profile_dir: str) -> subprocess.Popen | None:
    """重用既有 CDP 或啟動新的。回傳 Popen（新啟）或 None（重用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"  既存 Chrome 再利用 (port={port})", file=sys.stderr)
            return None
    print(f"  Chrome 起動中 (port={port}, profile={profile_dir})...", file=sys.stderr)
    cmd = [
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=IsolateOrigins,site-per-process",
        LANDING_URL,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(24):
        time.sleep(0.5)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc
    proc.terminate()
    raise RuntimeError(f"Chrome CDP port={port} 啟動失敗")


def _select_jobs(args) -> list[dict]:
    sql = ("SELECT id, source_id, url, title, company, score, last_seen "
           "FROM jobs WHERE source = 'recruiter_agent'"
           " AND id NOT IN (SELECT job_id FROM applications)")
    params: list = []
    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    else:
        if args.days is not None:
            cutoff = (date.today() - timedelta(days=args.days)).isoformat()
            sql += " AND last_seen <= ?"
            params.append(cutoff)
        if args.min_score is not None:
            sql += " AND score >= ?"
            params.append(args.min_score)
    sql += " ORDER BY score DESC"
    if args.top:
        sql += " LIMIT ?"
        params.append(args.top)
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _classify(page, url: str) -> dict:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2800)
        final = page.url
        title = page.title() or ""
        body = page.inner_text("body")[:8000]
    except Exception as e:
        return {"liveness": Liveness.ERROR.value, "error": str(e)[:160]}

    if any(s in final for s in LOGIN_SIGNALS) or "ログイン" in title:
        return {"liveness": "not_logged_in", "final_url": final}
    # スカウト承諾などの onboarding 画面に飛ばされた＝求人の生死は判定できない。
    # active/expired を返さないので DB は書き換わらない（_update_db 参照）。
    if "/onboarding" in final:
        return {"liveness": "onboarding_gate", "final_url": final}
    if any(s in title for s in EXPIRED_SIGNALS) or any(s in body for s in EXPIRED_SIGNALS):
        hit = next((s for s in EXPIRED_SIGNALS if s in title or s in body), "")
        return {"liveness": Liveness.EXPIRED.value, "final_url": final, "evidence": hit}
    if any(r in final for r in EXPIRED_REDIRECTS):
        return {"liveness": Liveness.EXPIRED.value, "final_url": final, "evidence": "redirect"}
    return {"liveness": Liveness.ACTIVE.value, "final_url": final, "page_title": title[:60]}


def _update_db(results: list[dict]) -> int:
    today = date.today().isoformat()
    n = 0
    with db.connect() as conn:
        for r in results:
            if r["liveness"] in (Liveness.ACTIVE.value, Liveness.EXPIRED.value):
                conn.execute(
                    "UPDATE jobs SET liveness_status = ?, liveness_checked_at = ? WHERE id = ?",
                    (r["liveness"], today, r["job_id"]),
                )
                n += 1
    return n


def run(args) -> dict:
    jobs = _select_jobs(args)
    if not jobs:
        print("  対象なし", file=sys.stderr)
        return {"checked": 0, "results": [], "summary": {}}

    print(f"  {len(jobs)} 筆 r-agent 職缺待検測", file=sys.stderr)
    from scrapers.recruiter_agent import joboffer_url

    from playwright.sync_api import sync_playwright

    proc = _ensure_cdp(args.cdp, args.profile_dir)
    results: list[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.cdp}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            try:
                for i, job in enumerate(jobs):
                    base = {"job_id": job["id"], "company": job["company"],
                            "title": job["title"], "score": job["score"]}
                    if not job["source_id"]:
                        base["liveness"] = "no_source_id"
                        print(f"  [{i+1}/{len(jobs)}] {job['company']} — source_id なし",
                              file=sys.stderr)
                        results.append(base)
                        continue
                    rec = _classify(page, joboffer_url(job["source_id"]))
                    base.update(rec)
                    flag = {"active": "✓", "expired": "✗ 終了", "not_logged_in": "⚠ 未ログイン"}.get(
                        rec["liveness"], "?")
                    print(f"  [{i+1}/{len(jobs)}] {flag} {job['company']} ({job['score']})",
                          file=sys.stderr)
                    results.append(base)
                    if rec["liveness"] == "not_logged_in" and i == 0:
                        print("  ⚠️ 未ログイン — このプロフィールで Recruit ID にログインしてください",
                              file=sys.stderr)
                        break
                    time.sleep(1.2)
            finally:
                page.close()
    finally:
        # 不終結 proc：保持 Chrome 登入狀態供下次重用
        pass

    summary: dict[str, int] = {}
    for r in results:
        summary[r["liveness"]] = summary.get(r["liveness"], 0) + 1

    updated = 0 if args.dry_run else _update_db(results)
    if not args.dry_run:
        print(f"  DB 更新 {updated} 筆 liveness", file=sys.stderr)
    return {"checked": len(results), "updated": updated, "results": results, "summary": summary}


def main() -> None:
    p = argparse.ArgumentParser(description="r-agent 職缺存活檢測（CDP + Gmail full URL）")
    p.add_argument("--top", type=int, help="高分前 N 筆")
    p.add_argument("--min-score", type=int, help="分數下限")
    p.add_argument("--days", type=int, help="只查 last_seen N 天前的職缺")
    p.add_argument("--ids", help="指定 job_id（逗號分隔）")
    p.add_argument("--all", action="store_true", help="全部 r-agent 職缺（等同無篩選）")
    p.add_argument("--cdp", type=int, default=DEFAULT_PORT, help=f"CDP port（預設 {DEFAULT_PORT}）")
    p.add_argument("--profile-dir", default=DEFAULT_PROFILE,
                   help=f"Chrome user-data-dir（預設 {DEFAULT_PROFILE}）")
    p.add_argument("--mail-days", type=int, default=120, help="Gmail 回溯天數（預設 120）")
    p.add_argument("--mail-max", type=int, default=300, help="Gmail 最大郵件數（預設 300）")
    p.add_argument("--dry-run", action="store_true", help="不寫 DB")
    args = p.parse_args()

    if not any([args.top, args.min_score, args.days, args.ids, args.all]):
        p.error("需指定 --top / --min-score / --days / --ids / --all 之一")
    run_result = run(args)
    print(json.dumps(run_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
