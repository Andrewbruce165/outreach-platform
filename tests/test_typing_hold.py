"""Unit tests for compute_typing_hold — pure function, no DB, no mocks.

Human-like typing hold: after LLM generation, the listener keeps showing
"typing..." for target = clamp(len(reply)/cps, TYPING_HOLD_MIN, TYPING_HOLD_MAX)
minus the time already spent generating. Never negative.
"""

import pytest

from app.services.listener import (
    TYPING_HOLD_MAX,
    TYPING_HOLD_MIN,
    compute_typing_hold,
)


def test_exact_ceiling_no_elapsed():
    # 200 chars @ 5 cps = 40s — exactly at the ceiling
    assert compute_typing_hold(200, 0.0, 5.0) == pytest.approx(40.0)


def test_long_reply_clamped_to_max():
    # 300 chars @ 3 cps = 100s → clamped to TYPING_HOLD_MAX
    assert compute_typing_hold(300, 0.0, 3.0) == pytest.approx(TYPING_HOLD_MAX)
    assert TYPING_HOLD_MAX == pytest.approx(40.0)


def test_short_reply_floored_to_min():
    # 8 chars @ 4 cps = 2s → floored to TYPING_HOLD_MIN
    assert compute_typing_hold(8, 0.0, 4.0) == pytest.approx(TYPING_HOLD_MIN)
    assert TYPING_HOLD_MIN == pytest.approx(4.0)


def test_elapsed_subtracted_from_target():
    # 100 chars @ 4 cps = 25s target, 10s already spent generating → 15s hold
    assert compute_typing_hold(100, 10.0, 4.0) == pytest.approx(15.0)


def test_never_negative_when_generation_slow():
    # 20 chars @ 4 cps = 5s target, floored to 4... wait: 5s target > floor;
    # 60s elapsed eats the whole budget → 0.0, never negative
    assert compute_typing_hold(20, 60.0, 4.0) == pytest.approx(0.0)


def test_zero_length_still_gets_floor():
    # Degenerate empty reply length: floor applies
    assert compute_typing_hold(0, 0.0, 4.0) == pytest.approx(TYPING_HOLD_MIN)
