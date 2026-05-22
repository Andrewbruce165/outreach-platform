"""Phase 4 Plan 04-03 — unit tests for per-campaign working-window helper.

Tests cover the new `_campaign_in_working_window` helper in app/services/queue.py
that replaces the global MOSCOW_TZ / WORK_HOUR_START / WORK_HOUR_END constants
and the _is_working_hours() / _next_working_window() module-level helpers.

D-08 / D-09 / D-10: per-campaign timezone + hours window + day-of-week mask.

Wave 0 = stubs only (pytest.skip). Task 2 of this plan implements the helper
and replaces the skip bodies with assertions.
"""

import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

pytestmark = pytest.mark.asyncio


async def test_in_working_window_msk_weekday_10am_true():
    """MSK timezone, понедельник 10:00 → True (9-20 окно, Mo-Fri mask=31)."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_outside_working_hours_msk_21pm_false():
    """MSK, понедельник 21:00 → False (за пределами 9-20)."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_before_working_hours_msk_8am_false():
    """MSK, понедельник 8:00 → False."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_weekend_saturday_with_mask_31_false():
    """MSK, суббота 10:00, mask=31 (Mo-Fri) → False."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_weekend_saturday_with_mask_127_true():
    """MSK, суббота 10:00, mask=127 (все дни) → True."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_different_timezone_us_pacific_handled():
    """timezone='America/Los_Angeles', UTC 17:00 четверг (=10:00 LA PDT, в окно 9-20 + mask=31) → True."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_invalid_timezone_falls_back_or_logs():
    """Невалидный campaign.timezone — функция возвращает False + warning."""
    pytest.skip("Wave 0 stub — Task 2 implements")


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
    assert hasattr(q, "MAX_SEND_INTERVAL"), "MAX_SEND_INTERVAL must remain (CLAUDE.md empirical)"
    assert hasattr(q, "FLOOD_HARD_THRESHOLD"), "FLOOD_HARD_THRESHOLD must remain (CLAUDE.md empirical)"
    assert hasattr(q, "LONG_PAUSE_EVERY_MIN"), "LONG_PAUSE_EVERY_MIN must remain (CLAUDE.md empirical)"
    assert hasattr(q, "LONG_PAUSE_EVERY_MAX"), "LONG_PAUSE_EVERY_MAX must remain (CLAUDE.md empirical)"
    assert hasattr(q, "LONG_PAUSE_MIN_SECS"), "LONG_PAUSE_MIN_SECS must remain (CLAUDE.md empirical)"
    assert hasattr(q, "LONG_PAUSE_MAX_SECS"), "LONG_PAUSE_MAX_SECS must remain (CLAUDE.md empirical)"
    assert hasattr(q, "MAX_NEW_CONTACTS_PER_HOUR"), "MAX_NEW_CONTACTS_PER_HOUR must remain (CLAUDE.md empirical)"
