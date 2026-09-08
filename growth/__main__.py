"""CLI — python3 -m growth <job_id> [options]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tracker.db import connect

from . import knowledge, pipeline, prompts

ROOT = Path(__file__).parent.parent


def _load_job(job_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        sys.exit(f"job_id {job_id} が DB に見つかりません")
    job = dict(row)
    if not (job.get("raw_jd") or "").strip():
        sys.exit(f"job_id {job_id} は raw_jd が空。先に JD を取得すること "
                 f"(python3 -m tools.refetch_jd --job-id {job_id})")
    return job


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m growth",
        description="產品技能增長自動流程 — JD から実戦手冊を生成する")
    p.add_argument("job_id", type=int, nargs="?", help="jobs.sqlite の job id")
    p.add_argument("--stage", default=None,
                   help=f"カンマ区切り。候補: {','.join(prompts.STAGE_ORDER)}")
    p.add_argument("--facts", default=None, help="会社調査済み事実 Markdown のパス")
    p.add_argument("--force", action="store_true", help="キャッシュを無視して再生成")
    p.add_argument("--no-llm", action="store_true", help="prompt だけ落として LLM を呼ばない")
    p.add_argument("--list-stages", action="store_true", help="stage 一覧を表示して終了")
    p.add_argument("--dry-run", action="store_true",
                   help="LLM を呼ばず、検索されるタグと案例だけ表示")
    args = p.parse_args()

    if args.list_stages:
        for i, name in enumerate(prompts.STAGE_ORDER, 1):
            meta = prompts.STAGES[name]
            deps = ",".join(meta["deps"]) or "-"
            print(f"{i}. {name:<11} → {meta['file']:<28} deps={deps}")
        return 0

    if args.job_id is None:
        p.error("job_id が必要（--list-stages 以外）")

    job = _load_job(args.job_id)

    from tools.blacklist import guard as blacklist_guard
    blacklist_guard(job.get("company") or "")

    if args.dry_run:
        from . import context as ctx_mod
        tags = knowledge.classify_jd(ctx_mod.jd_text(job))
        cases = knowledge.select_cases(tags, limit=9)
        print(f"求人: {job.get('company')} / {job.get('title')}")
        print(f"タグ: {tags}")
        print(f"選ばれた案例 ({len(cases)}):")
        for c in cases:
            print(f"  - [{c['id']}] {c['name']} ({c['confidence']})")
        print(f"出力先: {pipeline.pack_dir(job).relative_to(ROOT)}/")
        return 0

    names = ([s.strip() for s in args.stage.split(",")] if args.stage
             else list(prompts.STAGE_ORDER))
    for n in names:
        if n not in prompts.STAGES:
            sys.exit(f"未知 stage: {n}（候補: {', '.join(prompts.STAGE_ORDER)}）")

    facts = Path(args.facts).read_text(encoding="utf-8") if args.facts else ""

    print(f"■ {job.get('company')} / {job.get('title')} (job_id={job['id']})")
    print(f"  出力先: {pipeline.pack_dir(job).relative_to(ROOT)}/")
    print(f"  実行 stage: {', '.join(names)}\n")

    results = pipeline.run(job, names, facts, force=args.force, no_llm=args.no_llm)

    print("\n■ 結果")
    bad = 0
    for r in results:
        print(f"  {r.status:<9} {r.name:<11} 試行 {r.attempts} 回 "
              f"{'指摘 ' + str(len(r.errors)) + ' 件' if r.errors else ''}")
        if r.status in ("degraded", "failed"):
            bad += 1
    print(f"\n  → {pipeline.pack_dir(job).relative_to(ROOT)}/README.md")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
