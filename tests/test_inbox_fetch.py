from datetime import datetime, timedelta

from inbox.fetch import _within_hours


def test_within_hours_uses_exact_rolling_cutoff():
    now = datetime(2026, 7, 12, 12, 0, 0)
    inside = int((now - timedelta(hours=14, minutes=59)).timestamp() * 1000)
    outside = int((now - timedelta(hours=15, seconds=1)).timestamp() * 1000)

    assert _within_hours(str(inside), 15, now=now) is True
    assert _within_hours(str(outside), 15, now=now) is False
