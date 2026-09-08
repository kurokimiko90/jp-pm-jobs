#!/usr/bin/env python3
"""投遞追蹤 CLI — 記錄投遞、更新漏斗狀態、查看漏斗數據。

用法:
    python3 -m tracker.apply top --limit 10 --min-score 70   # 挑高分未投職缺
    python3 -m tracker.apply apply 42 --resume jp_v0.6        # 記錄投遞 job_id=42
    python3 -m tracker.apply status 42 recruiter --notes "收到面談邀約"
    python3 -m tracker.apply list --status recruiter           # 列出某階段投遞
    python3 -m tracker.apply funnel --since 2026-06-01         # 6 月起的漏斗

漏斗階段: applied → casual（カジュアル面談，非每家都有）→ recruiter → tech → onsite → offer / rejected
"""

from __future__ import annotations

import argparse

from analyzer.role_filter import filter_pm_roles
from tracker import db


def _fmt_job(r) -> str:
    return f"[{r['id']}] {r['score']:>3} {r['title']} @ {r['company'] or '?'} ({r['tier'] or '?'})"


def cmd_top(args: argparse.Namespace) -> None:
    candidates = db.top_scored(limit=args.limit * 4, min_score=args.min_score)
    rows = (candidates if args.include_eng else filter_pm_roles(candidates))[: args.limit]
    applied_ids = {a["job_id"] for a in db.list_applications()}
    print(f"高分職缺 top {args.limit}（min_score={args.min_score}，★=已投）：")
    for r in rows:
        mark = "★" if r["id"] in applied_ids else " "
        print(f" {mark} {_fmt_job(r)}")
        if r["url"]:
            print(f"      {r['url']}")


def cmd_apply(args: argparse.Namespace) -> None:
    app_id = db.record_application(
        args.job_id, status="applied", resume_version=args.resume, notes=args.notes
    )
    print(f"✅ 已記錄投遞 job_id={args.job_id}（application id={app_id}）")


def cmd_status(args: argparse.Namespace) -> None:
    db.update_application_status(args.job_id, args.stage, notes=args.notes)
    print(f"✅ job_id={args.job_id} → {args.stage}")


def cmd_list(args: argparse.Namespace) -> None:
    rows = db.list_applications(status=args.status)
    if not rows:
        print("（無投遞紀錄）")
        return
    for r in rows:
        note = f" — {r['notes']}" if r["notes"] else ""
        print(f"[{r['status']:>9}] {r['applied_at']} {_fmt_job(r)}{note}")


def cmd_funnel(args: argparse.Namespace) -> None:
    f = db.funnel_stats(since=args.since)
    scope = f"（{args.since} 起）" if args.since else "（全部）"
    print(f"📊 投遞漏斗{scope}")
    for stage in db.FUNNEL_STAGES:
        print(f"  {stage:>9}: {f['by_status'][stage]}")
    print(f"  ── 總投遞 {f['total']}｜已回覆 {f['replied']}"
          f"（{f['replied_rate']*100:.0f}%）｜平均分 {f['avg_score']}")


def main() -> None:
    p = argparse.ArgumentParser(description="投遞追蹤 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("top", help="列出高分職缺（標記已投）")
    t.add_argument("--limit", type=int, default=10)
    t.add_argument("--min-score", type=int, default=60)
    t.add_argument("--include-eng", action="store_true", help="不過濾純工程職")
    t.set_defaults(func=cmd_top)

    a = sub.add_parser("apply", help="記錄一筆投遞")
    a.add_argument("job_id", type=int)
    a.add_argument("--resume", help="履歷版本標記，如 jp_v0.6")
    a.add_argument("--notes")
    a.set_defaults(func=cmd_apply)

    s = sub.add_parser("status", help="更新漏斗狀態")
    s.add_argument("job_id", type=int)
    s.add_argument("stage", choices=db.FUNNEL_STAGES)
    s.add_argument("--notes")
    s.set_defaults(func=cmd_status)

    ls = sub.add_parser("list", help="列出投遞")
    ls.add_argument("--status", choices=db.FUNNEL_STAGES)
    ls.set_defaults(func=cmd_list)

    fn = sub.add_parser("funnel", help="漏斗統計")
    fn.add_argument("--since", help="YYYY-MM-DD")
    fn.set_defaults(func=cmd_funnel)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
