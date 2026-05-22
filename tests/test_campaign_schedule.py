"""Phase 4 Plan 04-03 — unit tests for per-campaign working-window helper.

Tests cover the new `_campaign_in_working_window` helper in app/services/queue.py
that replaces the global MOSCOW_TZ / WORK_HOUR_START / WORK_HOUR_END constants
and the _is_working_hours() / _next_working_window() module-level helpers.

D-08 / D-09 / D-10: per-campaign timezone + hours window + day-of-week mask.
"""

from datetime import datetime, timezone

import pytest

from app.services.queue import _campaign_in_working_window

pytestmark = pytest.mark.asyncio


# Reference: 2026-06-01 is a Monday (verified via datetime.weekday() == 0).
# Mo=0 (bit 1), Tu=1 (bit 2), We=2 (bit 4), Th=3 (bit 8), Fr=4 (bit 16),
# Sa=5 (bit 32), Su=6 (bit 64). Mo-Fri mask = 1+2+4+8+16 = 31.


async def test_in_working_window_msk_weekday_10am_true():
    """MSK timezone, понедельник 10:00 → True (9-20 окно, Mo-Fri mask=31)."""
    # 10:00 MSK = 07:00 UTC (MSK is UTC+3 year-round).
    now_utc = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
    assert _campaign_in_working_window(
        campaign_tz="Europe/Moscow",
        work_hour_start=9,
        work_hour_end=20,
        work_days_mask=31,
        now=now_utc,
    ) is True


async def test_outside_working_hours_msk_21pm_false():
    """MSK, понедельник 21:00 → False (за пределами 9-20)."""
    # 21:00 MSK = 18:00 UTC.
    now_utc = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)
    assert _campaign_in_working_window(
        campaign_tz="Europe/Moscow",
        work_hour_start=9,
        work_hour_end=20,
        work_days_mask=31,
        now=now_utc,
    ) is False


async def test_before_working_hours_msk_8am_false():
    """MSK, понедельник 08:00 → False."""
    # 08:00 MSK = 05:00 UTC.
    now_utc = datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc)
    assert _campaign_in_working_window(
        campaign_tz="Europe/Moscow",
        work_hour_start=9,
        work_hour_end=20,
        work_days_mask=31,
        now=now_utc,
    ) is False


async def test_weekend_saturday_with_mask_31_false():
    """MSK, суббота 10:00, mask=31 (Mo-Fri only) → False."""
    # 2026-06-06 is a Saturday. 10:00 MSK = 07:00 UTC.
    now_utc = datetime(2026, 6, 6, 7, 0, tzinfo=timezone.utc)
    assert _campaign_in_working_window(
        campaign_tz="Europe/Moscow",
        work_hour_start=9,
        work_hour_end=20,
        work_days_mask=31,
        now=now_utc,
    ) is False


async def test_weekend_saturday_with_mask_127_true():
    """MSK, суббота 10:00, mask=127 (все 7 дней) → True."""
    now_utc = datetime(2026, 6, 6, 7, 0, tzinfo=timezone.utc)
    assert _campaign_in_working_window(
        campaign_tz="Europe/Moscow",
        work_hour_start=9,
        work_hour_end=20,
        work_days_mask=127,
        now=now_utc,
    ) is True


async def test_different_timezone_us_pacific_handled():
    """timezone='America/Los_Angeles', UTC 17:00 четверг → 10:00 LA PDT — в окно 9-20 + mask=31."""
    # 2026-06-04 is a Thursday. 17:00 UTC → 10:00 PDT (America/Los_Angeles
    # observes DST and is UTC-7 in June).
    now_utc = datetime(2026, 6, 4, 17, 0, tzinfo=timezone.utc)
    assert _campaign_in_working_window(
        campaign_tz="America/Los_Angeles",
        work_hour_start=9,
        work_hour_end=20,
        work_days_mask=31,
        now=now_utc,
    ) is True


async def test_invalid_timezone_falls_back_or_logs():
    """Невалидный campaign.timezone — функция возвращает False + warning (см. logger)."""
    now_utc = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
    assert _campaign_in_working_window(
        campaign_tz="Mars/Olympus",
        work_hour_start=9,
        work_hour_end=20,
        work_days_mask=31,
        now=now_utc,
    ) is False


async def test_no_global_moscow_tz_constant():
    """Verify MOSCOW_TZ / WORK_HOUR_START / WORK_HOUR_END удалены из queue.py module."""
    import app.services.queue as q
    assert not hasattr(q, "MOSCOW_TZ"), "MOSCOW_TZ must be removed (Phase 4 D-08)"
    assert not hasattr(q, "WORK_HOUR_START"), "WORK_HOUR_START must be removed (Phase 4 D-09)"
    assert not hasattr(q, "WORK_HOUR_END"), "WORK_HOUR_END must be removed (Phase 4 D-09)"


async def test_empirical_intervals_unchanged():
    """CLAUDE.md guard — НЕ ТРОГАТЬ эти константы (выставленные эмпирически)."""
    import app.services.queue as q
    assert hasattr(q, "MIN_SEND_INTERVAL"), "MIN_SEND_INTERVAL must remain (CLAUDE.md empirical)"
    assert q.MIN_SEND_INTERVAL == 20, "MIN_SEND_INTERVAL value must not change"
    assert hasattr(q, "MAX_SEND_INTERVAL"), "MAX_SEND_INTERVAL must remain (CLAUDE.md empirical)"
    assert q.MAX_SEND_INTERVAL == 55, "MAX_SEND_INTERVAL value must not change"
    assert hasattr(q, "FLOOD_HARD_THRESHOLD"), "FLOOD_HARD_THRESHOLD must remain (CLAUDE.md empirical)"
    assert q.FLOOD_HARD_THRESHOLD == 300, "FLOOD_HARD_THRESHOLD value must not change"
    assert q.LONG_PAUSE_EVERY_MIN == 12
    assert q.LONG_PAUSE_EVERY_MAX == 25
    assert q.LONG_PAUSE_MIN_SECS == 180
    assert q.LONG_PAUSE_MAX_SECS == 600
    assert q.MAX_NEW_CONTACTS_PER_HOUR == 15
