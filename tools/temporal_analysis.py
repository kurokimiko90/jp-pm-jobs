#!/usr/bin/env python3
"""Temporal pattern mining — 從 history.jsonl 時戳挖工作節奏。

無需 LLM、純 Python。輸出時間分布、單日深度、持續專注區段。

用法:
    python3 -m tools.temporal_analysis
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HISTORY = Path.home() / ".claude" / "history.jsonl"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "temporal_profile.yaml"

# 連續 prompt 之間若 <= GAP 分鐘，算同一 "focus block"
FOCUS_GAP_MIN = 8


def load_timestamps() -> list[tuple[datetime, str, str]]:
    out: list[tuple[datetime, str, str]] = []
    with HISTORY.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("timestamp")
            if not ts:
                continue
            out.append((
                datetime.fromtimestamp(ts / 1000),
                d.get("project", ""),
                d.get("sessionId", ""),
            ))
    return out


def analyze(events: list[tuple[datetime, str, str]]) -> dict:
    if not events:
        return {}
    events.sort(key=lambda e: e[0])
    total = len(events)
    first, last = events[0][0], events[-1][0]
    days = (last - first).days or 1

    # 1) Hour-of-day distribution
    hour_c = Counter(e[0].hour for e in events)
    # 2) Day-of-week (0=Mon)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_c = Counter(dow_names[e[0].weekday()] for e in events)
    # 3) Per-day prompt count
    by_day: dict[str, int] = defaultdict(int)
    for e in events:
        by_day[e[0].date().isoformat()] += 1
    daily_counts = sorted(by_day.values(), reverse=True)
    active_days = len(by_day)

    # 4) Focus blocks (gap <= FOCUS_GAP_MIN minutes)
    blocks: list[tuple[datetime, datetime, int]] = []
    block_start = events[0][0]
    block_count = 1
    last_ts = events[0][0]
    for ts, _, _ in events[1:]:
        if (ts - last_ts).total_seconds() / 60 <= FOCUS_GAP_MIN:
            block_count += 1
        else:
            blocks.append((block_start, last_ts, block_count))
            block_start = ts
            block_count = 1
        last_ts = ts
    blocks.append((block_start, last_ts, block_count))

    block_durations_min = [(end - start).total_seconds() / 60 for start, end, _ in blocks]
    sustained = [d for d in block_durations_min if d >= 15]
    deep_blocks = [d for d in block_durations_min if d >= 45]

    # 5) Late night vs business hours
    business_hours = sum(c for h, c in hour_c.items() if 9 <= h < 18)
    late_night = sum(c for h, c in hour_c.items() if h >= 22 or h < 5)

    # 6) Day intensity buckets
    high_intensity_days = sum(1 for c in daily_counts if c >= 100)
    moderate_days = sum(1 for c in daily_counts if 30 <= c < 100)
    low_days = sum(1 for c in daily_counts if c < 30)

    return {
        "summary": {
            "total_prompts": total,
            "date_range": {
                "start": first.isoformat(timespec="minutes"),
                "end": last.isoformat(timespec="minutes"),
                "span_days": days,
            },
            "active_days": active_days,
            "active_day_pct": round(100 * active_days / days, 1),
            "avg_prompts_per_active_day": round(total / active_days, 1),
        },
        "hour_of_day_pct": {
            f"{h:02d}": round(100 * hour_c.get(h, 0) / total, 1) for h in range(24)
        },
        "day_of_week_pct": {
            name: round(100 * dow_c.get(name, 0) / total, 1) for name in dow_names
        },
        "daily_intensity": {
            "high_intensity_days_100plus": high_intensity_days,
            "moderate_30_to_99": moderate_days,
            "low_under_30": low_days,
            "peak_day_count": daily_counts[0] if daily_counts else 0,
            "median_daily_count": daily_counts[len(daily_counts) // 2] if daily_counts else 0,
        },
        "focus_blocks": {
            "total_blocks": len(blocks),
            "blocks_15min_plus": len(sustained),
            "blocks_45min_plus": len(deep_blocks),
            "avg_block_duration_min": round(sum(block_durations_min) / len(block_durations_min), 1),
            "longest_block_min": round(max(block_durations_min), 1),
            "top_10_longest_blocks": [
                {
                    "start": start.isoformat(timespec="minutes"),
                    "duration_min": round(dur, 1),
                    "prompt_count": cnt,
                }
                for (start, end, cnt), dur in sorted(
                    zip(blocks, block_durations_min), key=lambda x: -x[1]
                )[:10]
            ],
        },
        "circadian": {
            "business_hours_9_18_pct": round(100 * business_hours / total, 1),
            "late_night_22_to_5_pct": round(100 * late_night / total, 1),
            "peak_hour": max(hour_c.items(), key=lambda x: x[1])[0],
        },
    }


def main() -> None:
    events = load_timestamps()
    print(f"Loaded {len(events)} timestamped prompts")
    profile = analyze(events)
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\n✓ Temporal profile: {OUT_PATH}")
    print("\n--- Summary ---")
    print(yaml.safe_dump(profile["summary"], allow_unicode=True, sort_keys=False))
    print("--- Daily intensity ---")
    print(yaml.safe_dump(profile["daily_intensity"], allow_unicode=True, sort_keys=False))
    print("--- Focus blocks ---")
    fb = profile["focus_blocks"]
    print(f"  Total blocks: {fb['total_blocks']}")
    print(f"  ≥15min blocks: {fb['blocks_15min_plus']}")
    print(f"  ≥45min blocks: {fb['blocks_45min_plus']}")
    print(f"  Avg block: {fb['avg_block_duration_min']} min")
    print(f"  Longest: {fb['longest_block_min']} min")
    print("--- Circadian ---")
    print(yaml.safe_dump(profile["circadian"], allow_unicode=True, sort_keys=False))
    print("--- Day of week (% of prompts) ---")
    print(yaml.safe_dump(profile["day_of_week_pct"], allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
