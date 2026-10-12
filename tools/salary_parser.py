"""從 raw_jd 抽取薪資範圍，正規化到「万円」單位寫入 jobs.salary_min/max。

格式覆蓋（依優先序）:
  1. 年収 X〜Y 万円        — 最可靠
  2. 月給 X〜Y 万円         — ×12
  3. 月給 X〜Y 円           — ×12 / 10000
  4. 年収 X 万円（單值）
  5. 月給 X 円（單值）      — ×12 / 10000

用法:
    python3 -m tools.salary_parser           # 對全部 job 重新解析
    python3 -m tools.salary_parser --job 42  # 單筆 debug
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import time

from tracker.db import all_jobs, connect, update_salary

# 共用：分隔符 / 數值（含千分位逗號 + 小數）
_SEP = r"[〜～\-~]"
_NUM = r"([\d,]+(?:\.\d+)?)"
# 允許跨行 + 冒號（LinkedIn 格式：年収:700万）+ 見出しマーカー。`*` を含めないと
# 構造化済み JD（`**想定年収**\n724万円～920万円`）でラベルと値が繋がらず、月給からの
# 逆算に落ちて年収を取り違える。`scrapers/{indeed_jp,recruiter_agent}.py` 参照。
_GAP = r"[\s\n:：*#]{0,40}"

# 1. 年収 X〜Y 万円（最可靠，允許跨行；第一個數字後「万」可省略）
ANNUAL_MAN_RANGE_RE = re.compile(
    rf"年収{_GAP}{_NUM}\s*万?\s*円?\s*{_SEP}\s*{_NUM}\s*万"
)
ANNUAL_MAN_SINGLE_RE = re.compile(rf"年収{_GAP}{_SEP}?\s*{_NUM}\s*万")

# 2. 月給 X〜Y 万円（含小數，如「46.7万円」）
MONTHLY_MAN_RANGE_RE = re.compile(
    rf"月給[^\n]{{0,10}}?{_NUM}\s*万\s*円?\s*{_SEP}\s*{_NUM}\s*万"
)
MONTHLY_MAN_SINGLE_RE = re.compile(rf"月給[^\n]{{0,10}}?{_NUM}\s*万")

# 3. 月給 X〜Y 円（純円，逗號千分位）
MONTHLY_YEN_RANGE_RE = re.compile(
    r"月給[^\n]{0,10}?[￥¥]?\s*([\d,]+)\s*円?\s*[〜～\-~]\s*[￥¥]?\s*([\d,]+)\s*円"
)
MONTHLY_YEN_SINGLE_RE = re.compile(r"月給[^\n]{0,10}?[￥¥]?\s*([\d,]+)\s*円")

# 4. 裸「X万円〜Y万円」（無「年収」前綴）— bizreach 等把薪資單列在標題下方。
#    兩側都要求「円」以降低誤命中；數值 sanity 限年収帶（200〜5000万）→ 排月給範圍。
BARE_MAN_RANGE_RE = re.compile(rf"{_NUM}\s*万\s*円\s*{_SEP}\s*{_NUM}\s*万\s*円")
BARE_ANNUAL_MIN = 200   # 万円：低於此視為月給範圍，不當年収
BARE_ANNUAL_MAX = 5000  # 万円：高於此視為誤命中

MIN_REASONABLE_MONTHLY_YEN = 150_000  # 過濾「諸手当 5,000円」之類誤命中


def _man(s: str) -> int:
    """數字字串（可帶逗號 / 小數）→ 整數萬円（截尾）。"""
    return int(float(s.replace(",", "")))


def parse_salary(jd: str) -> tuple[int | None, int | None]:
    """回 (min_man, max_man) — 单位「万円」。無法解析回 (None, None)。"""
    if not jd:
        return None, None

    m = ANNUAL_MAN_RANGE_RE.search(jd)
    if m:
        return _man(m.group(1)), _man(m.group(2))

    m = MONTHLY_MAN_RANGE_RE.search(jd)
    if m:
        return _man(m.group(1)) * 12, _man(m.group(2)) * 12

    m = MONTHLY_YEN_RANGE_RE.search(jd)
    if m:
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        if lo >= MIN_REASONABLE_MONTHLY_YEN and hi >= lo:
            return lo * 12 // 10000, hi * 12 // 10000

    # 裸範圍：年収帶 sanity 內才採用（排月給「30万円〜50万円」誤判）
    m = BARE_MAN_RANGE_RE.search(jd)
    if m:
        lo, hi = _man(m.group(1)), _man(m.group(2))
        if BARE_ANNUAL_MIN <= lo <= BARE_ANNUAL_MAX and hi >= lo:
            return lo, hi

    m = ANNUAL_MAN_SINGLE_RE.search(jd)
    if m:
        return _man(m.group(1)), None

    m = MONTHLY_MAN_SINGLE_RE.search(jd)
    if m:
        return _man(m.group(1)) * 12, None

    m = MONTHLY_YEN_SINGLE_RE.search(jd)
    if m:
        x = int(m.group(1).replace(",", ""))
        if x >= MIN_REASONABLE_MONTHLY_YEN:
            return x * 12 // 10000, None

    return None, None


def _update_with_retry(job_id: int, smin: int | None, smax: int | None, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            update_salary(job_id, smin, smax)
            return True
        except sqlite3.OperationalError:
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))
    return False


def parse_all() -> tuple[int, int]:
    """對全部 job 重新解析。回 (processed, updated)。"""
    rows = all_jobs()
    updated = 0
    failed = 0
    for r in rows:
        smin, smax = parse_salary(r["raw_jd"] or "")
        if smin is None and smax is None:
            continue
        if _update_with_retry(r["id"], smin, smax):
            updated += 1
        else:
            failed += 1
    if failed:
        print(f"[salary_parser] 已解析 {len(rows)} 筆，更新 {updated} 筆，失敗 {failed} 筆（DB locked）")
    else:
        print(f"[salary_parser] 已解析 {len(rows)} 筆，更新 {updated} 筆")
    return len(rows), updated


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", type=int, help="只解析單筆 job_id（debug 用）")
    args = ap.parse_args()

    if args.job:
        with connect() as conn:
            r = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job,)).fetchone()
        if not r:
            raise SystemExit(f"job_id {args.job} 不存在")
        smin, smax = parse_salary(r["raw_jd"] or "")
        print(f"job {args.job} | title={r['title'][:60]}")
        print(f"  解析結果: min={smin} max={smax} 万円")
    else:
        parse_all()


if __name__ == "__main__":
    main()
