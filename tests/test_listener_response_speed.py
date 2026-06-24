"""Phase 11 — RT-01: listener response_speed delay assertions.

Tests that `TelegramListener.schedule_ai_response` computes the correct delay
based on context['response_speed'] and context['response_delay_seconds'].

Expected behavior (Phase 11 / RT-01):
  - response_speed='manual' → delay == response_delay_seconds (capped by MAX_BUFFER_TIME guard)
  - response_speed='instant' → delay ≤ ~2s (near-immediate)
  - response_speed='human' (or absent/default) → delay within DEBOUNCE_MIN..DEBOUNCE_MAX range
  - MAX_BUFFER_TIME - buffer_age cap applied for every mode regardless of speed setting

Skip/xfail strategy:
  - The 'manual' and 'instant' tests are xfail(strict=False) because schedule_ai_response
    does not yet read context['response_speed'] (grep confirms no such branch).
    They will flip from xfail → passing once Plan 11-03 rewrites schedule_ai_response.
  - The 'human' default-range test is NOT guarded — the current behavior already
    computes delay within DEBOUNCE_MIN..DEBOUNCE_MAX so it should pass immediately.
  - The MAX_BUFFER_TIME cap test is also NOT guarded — it tests existing behavior.

Run via test-overlay ONLY:
  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_listener_response_speed.py -x
"""
import asyncio
import inspect
import pathlib
import unittest.mock as mock

import pytest

# Detect whether schedule_ai_response already reads context.get("response_speed").
# Used to conditionally skip/xfail tests that depend on Phase 11 implementation.
from app.services.listener import TelegramListener

_LISTENER_SRC = inspect.getsource(TelegramListener.schedule_ai_response)
_SPEED_IMPLEMENTED = "response_speed" in _LISTENER_SRC

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_listener() -> TelegramListener:
    """Minimal TelegramListener instance for delay-calculation tests."""
    return TelegramListener()


def _build_context(response_speed: str | None = None, response_delay_seconds: int | None = None) -> dict:
    """Build a minimal context dict for schedule_ai_response."""
    ctx = {
        "ai_context_id": "00000000-0000-0000-0000-000000000001",
        "contact_name": "Test Contact",
        "contact_phone": "+79000000001",
    }
    if response_speed is not None:
        ctx["response_speed"] = response_speed
    if response_delay_seconds is not None:
        ctx["response_delay_seconds"] = response_delay_seconds
    return ctx


# ── RT-01a: manual mode → exact delay ────────────────────────────────────────

@pytest.mark.xfail(
    not _SPEED_IMPLEMENTED,
    reason="Phase 11 pending: schedule_ai_response does not yet branch on response_speed",
    strict=False,
)
async def test_manual_speed_uses_exact_delay():
    """RT-01 (manual): response_speed='manual' + response_delay_seconds=42 → delay == 42.

    The computed delay must equal response_delay_seconds exactly, subject to
    the MAX_BUFFER_TIME - buffer_age cap (not triggered here since buffer_age ≈ 0).
    """
    listener = _make_listener()
    conversation_id = "conv-manual-001"

    # Intercept the asyncio.create_task call to extract the delay argument
    captured_delay: list[float] = []
    original_create_task = asyncio.create_task

    async def _fake_debounce(conv_id: str, delay: float):
        captured_delay.append(delay)

    with mock.patch.object(listener, "_debounce_timer", side_effect=_fake_debounce):
        with mock.patch("asyncio.create_task", wraps=lambda coro: original_create_task(coro)):
            # Inject context with manual speed + 42s delay
            ctx = _build_context(response_speed="manual", response_delay_seconds=42)
            # Simulate buffer just started (age ≈ 0)
            import time as _time_local
            listener.buffer_start_time[conversation_id] = _time_local.time()

            await listener.schedule_ai_response(conversation_id, ctx)
            # Give the task a moment to register the delay
            await asyncio.sleep(0)

    assert len(captured_delay) >= 1, "RT-01 (manual): no debounce timer was created"
    assert captured_delay[0] == pytest.approx(42.0, abs=0.1), (
        f"RT-01 (manual): expected delay ≈ 42.0s, got {captured_delay[0]}"
    )


# ── RT-01b: instant mode → near-zero delay ───────────────────────────────────

@pytest.mark.xfail(
    not _SPEED_IMPLEMENTED,
    reason="Phase 11 pending: schedule_ai_response does not yet branch on response_speed",
    strict=False,
)
async def test_instant_speed_uses_short_delay():
    """RT-01 (instant): response_speed='instant' → delay ≤ 2.0s.

    Instant mode should bypass the normal debounce range (20-180s) and
    schedule a near-immediate response (definition: ≤ 2 seconds).
    """
    listener = _make_listener()
    conversation_id = "conv-instant-001"

    captured_delay: list[float] = []
    original_create_task = asyncio.create_task

    async def _fake_debounce(conv_id: str, delay: float):
        captured_delay.append(delay)

    with mock.patch.object(listener, "_debounce_timer", side_effect=_fake_debounce):
        with mock.patch("asyncio.create_task", wraps=lambda coro: original_create_task(coro)):
            ctx = _build_context(response_speed="instant")
            import time as _time_local
            listener.buffer_start_time[conversation_id] = _time_local.time()

            await listener.schedule_ai_response(conversation_id, ctx)
            await asyncio.sleep(0)

    assert len(captured_delay) >= 1, "RT-01 (instant): no debounce timer was created"
    assert captured_delay[0] <= 2.0, (
        f"RT-01 (instant): expected delay ≤ 2.0s, got {captured_delay[0]:.1f}s "
        f"(still using default debounce range)"
    )


# ── RT-01c: human/default mode → DEBOUNCE_MIN..DEBOUNCE_MAX range ────────────

async def test_human_speed_uses_debounce_range():
    """RT-01 (human): response_speed absent or 'human' → delay within DEBOUNCE_MIN..DEBOUNCE_MAX.

    This test validates EXISTING behavior (no Phase 11 changes needed) so it is
    NOT xfail/skip-guarded. The current schedule_ai_response always uses the
    random debounce range — this test pins that contract.
    """
    import time as _time

    listener = _make_listener()
    conversation_id = "conv-human-001"

    captured_delay: list[float] = []
    original_create_task = asyncio.create_task

    async def _fake_debounce(conv_id: str, delay: float):
        captured_delay.append(delay)

    with mock.patch.object(listener, "_debounce_timer", side_effect=_fake_debounce):
        with mock.patch("asyncio.create_task", wraps=lambda coro: original_create_task(coro)):
            # No response_speed key → default behavior
            ctx = _build_context()  # no response_speed
            # buffer_start_time uses time.time() — must use the same clock
            listener.buffer_start_time[conversation_id] = _time.time()

            await listener.schedule_ai_response(conversation_id, ctx)
            await asyncio.sleep(0)

    assert len(captured_delay) >= 1, "RT-01 (human): no debounce timer was created"
    delay = captured_delay[0]
    assert listener.DEBOUNCE_MIN <= delay <= listener.DEBOUNCE_MAX, (
        f"RT-01 (human): delay {delay:.1f}s outside "
        f"[{listener.DEBOUNCE_MIN}, {listener.DEBOUNCE_MAX}]"
    )


# ── RT-01d: MAX_BUFFER_TIME cap applies regardless of speed mode ──────────────

async def test_max_buffer_time_cap_applied():
    """RT-01 (cap): MAX_BUFFER_TIME - buffer_age cap applies when buffer is old.

    This tests EXISTING behavior: when buffer_age is large (close to MAX_BUFFER_TIME),
    the computed delay is clamped to MAX_BUFFER_TIME - buffer_age (≈ small positive number).

    NOT xfail/skip-guarded — tests current behavior only.
    """
    import time as _time

    listener = _make_listener()
    conversation_id = "conv-cap-001"

    captured_delay: list[float] = []
    original_create_task = asyncio.create_task

    async def _fake_debounce(conv_id: str, delay: float):
        captured_delay.append(delay)

    with mock.patch.object(listener, "_debounce_timer", side_effect=_fake_debounce):
        with mock.patch("asyncio.create_task", wraps=lambda coro: original_create_task(coro)):
            ctx = _build_context()
            # Simulate buffer that started 290 seconds ago (MAX_BUFFER_TIME=300 → cap = ~10s)
            listener.buffer_start_time[conversation_id] = _time.time() - 290.0

            await listener.schedule_ai_response(conversation_id, ctx)
            await asyncio.sleep(0)

    # If buffer_age >= MAX_BUFFER_TIME, schedule_ai_response returns early (no timer created)
    # If buffer_age < MAX_BUFFER_TIME but close, delay is capped to remainder
    buffer_age = 290.0
    remaining = listener.MAX_BUFFER_TIME - buffer_age  # ≈ 10s

    if captured_delay:
        # Timer was created → delay must be ≤ remaining (the cap)
        assert captured_delay[0] <= remaining + 1.0, (
            f"RT-01 (cap): delay {captured_delay[0]:.1f}s exceeds remaining buffer "
            f"time {remaining:.1f}s — MAX_BUFFER_TIME cap not applied"
        )
    else:
        # schedule_ai_response detected buffer_age >= MAX_BUFFER_TIME and called
        # process_buffered_messages directly → no timer. This is valid behavior.
        pass


@pytest.mark.xfail(
    not _SPEED_IMPLEMENTED,
    reason="Phase 11 pending: manual speed with old buffer should respect cap",
    strict=False,
)
async def test_manual_speed_still_respects_buffer_cap():
    """RT-01 (manual+cap): manual mode delay is capped by MAX_BUFFER_TIME - buffer_age.

    response_delay_seconds=600 but MAX_BUFFER_TIME=300 and buffer_age=250
    → effective delay ≤ 50s (not 600s).
    """
    import time as _time

    listener = _make_listener()
    conversation_id = "conv-manual-cap-001"

    captured_delay: list[float] = []
    original_create_task = asyncio.create_task

    async def _fake_debounce(conv_id: str, delay: float):
        captured_delay.append(delay)

    with mock.patch.object(listener, "_debounce_timer", side_effect=_fake_debounce):
        with mock.patch("asyncio.create_task", wraps=lambda coro: original_create_task(coro)):
            ctx = _build_context(response_speed="manual", response_delay_seconds=600)
            listener.buffer_start_time[conversation_id] = _time.time() - 250.0

            await listener.schedule_ai_response(conversation_id, ctx)
            await asyncio.sleep(0)

    if captured_delay:
        remaining = listener.MAX_BUFFER_TIME - 250.0  # = 50s
        assert captured_delay[0] <= remaining + 1.0, (
            f"RT-01 (manual+cap): delay {captured_delay[0]:.1f}s exceeds remaining {remaining:.1f}s"
        )
