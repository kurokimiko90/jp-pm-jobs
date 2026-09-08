"""自己進化の操作口 — python3 -m proposal.evolve

3 つの回路のうち、機械で回せる 2 つをここから叩く:

  --stats    prompt 版ごとの閘門通過率（零 LLM）。prompt を直した効果を、
             感想ではなく一発通過率と平均試行回数で見る。
  --lessons  過去の紅隊指摘と面接覆盤から「繰り返し出る癖」を抽出し、
             次回以降の main_case / cards の prompt へ差し込む（LLM 1 call）。

3 つ目（実戦で出た新しい問を正典題庫へ戻す）は人が読んで決めることなので
自動化しない — `interview/question-bank/core/` を手で直す。
"""

from __future__ import annotations

import argparse
import sys

from . import history, lessons, prompts


def show_stats() -> None:
    rows = history.stats()
    if not rows:
        print("履歴がまだ無い。提案パックを 1 つ以上生成してから実行すること。")
        return
    print(f"■ prompt 版ごとの成績（現在の版: {prompts.PROMPT_VERSION}）\n")
    print(f"  {'stage@版':<20} {'実行':>4} {'一発通過':>8} {'平均試行':>8} "
          f"{'degraded':>9} {'failed':>7}")
    for key in sorted(rows):
        r = rows[key]
        print(f"  {key:<20} {r['runs']:>4} {r['first_pass_rate']:>8.0%} "
              f"{r['avg_attempts']:>8.2f} {r['degraded']:>9} {r['failed']:>7}")

    freq = history.frequent_gates()
    print("\n■ 繰り返し落ちている閘門（prompt を直す優先順位）")
    if not freq:
        print("  2 回以上落ちた閘門は無い。")
    for gate, n in freq:
        print(f"  {gate:<10} {n} 回")
    print("\n  ※ prompt を直したら prompts.PROMPT_VERSION を上げること。"
          "上げないと直す前後が同じ袋に混ざる。")


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m proposal.evolve",
        description="提案パイプラインの自己進化 — 成績の可視化と教訓の抽出")
    p.add_argument("--stats", action="store_true",
                   help="prompt 版ごとの閘門通過率を表示（零 LLM）")
    p.add_argument("--lessons", action="store_true",
                   help="過去の紅隊指摘・面接覆盤から教訓を抽出（LLM 1 call）")
    p.add_argument("--rebuild", action="store_true",
                   help="既存の教訓庫があっても作り直す")
    p.add_argument("--dry-run", action="store_true",
                   help="--lessons と併用: prompt だけ落として LLM を呼ばない")
    args = p.parse_args()

    if not (args.stats or args.lessons):
        p.error("--stats か --lessons のどちらかを指定すること")

    if args.stats:
        show_stats()

    if args.lessons:
        if lessons.exists() and not args.rebuild and not args.dry_run:
            items = lessons.load()
            print(f"\n既に {len(items)} 件の教訓がある: "
                  f"{lessons.path().relative_to(lessons.ROOT)}")
            for l in items:
                print(f"  - {l.get('pattern')}")
            print("作り直すなら --rebuild（手で直した内容は失われる）")
            return 0
        print()
        lessons.build(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
