---
phase: quick-260709-clq
plan: 01
subsystem: listener
tags: [ai-answerer, anti-spam, human-like-behavior, telethon]
requires: []
provides:
  - "compute_typing_hold() pure function + TYPING_CPS_MIN/MAX, TYPING_HOLD_MIN/MAX constants in app/services/listener.py"
  - "Length-proportional typing hold in _send_to_ai before client.send_message"
affects: []
tech-stack:
  added: []
  patterns:
    - "Pure deterministic hold-computation with randomness at call site (cps parameter)"
    - "Second safe_typing context for the hold-sleep AFTER the DB session block closes"
key-files:
  created:
    - tests/test_typing_hold.py
  modified:
    - app/services/listener.py
decisions:
  - "LLM generation time counts toward the typing budget: hold = max(0, clamp(len/cps, 4, 40) - elapsed)"
  - "cps randomized per-message in [3.0, 5.0] at the call site; compute_typing_hold stays deterministic/testable"
  - "Hold-sleep runs outside AsyncSessionLocal — DB connection not held during up-to-40s sleep"
metrics:
  duration: ~5min
  completed: 2026-07-09
---

# Quick Task 260709-clq: Typing Indicator Hold Before AI Send Summary

**Human-like typing hold: after gpt-5-mini generates a reply, the listener keeps showing "typing…" for clamp(len(reply)/cps, 4s, 40s) minus generation time, with cps randomized in [3.0, 5.0] per message.**

## What Was Done

### Task 1: compute_typing_hold + wiring in _send_to_ai (TDD)

- **RED** (`5f9bf33`): `tests/test_typing_hold.py` — 6 pure-function unit tests (ceiling 200@5cps=40, clamp 300@3cps→40, floor 8@4cps→4, elapsed subtraction 100@4cps−10s=15, never-negative 20@4cps−60s=0, zero-length→floor). Confirmed failing (ImportError) via test-overlay.
- **GREEN** (`3acc81c`): in `app/services/listener.py`:
  - Module-level constants above `class TelegramListener` (lines 132-139): `TYPING_CPS_MIN=3.0`, `TYPING_CPS_MAX=5.0`, `TYPING_HOLD_MIN=4.0`, `TYPING_HOLD_MAX=40.0`.
  - Pure `compute_typing_hold(reply_len, elapsed, cps)` (line 142) — clamp + elapsed subtraction, never negative.
  - `_send_to_ai` wiring: `gen_start = time.monotonic()` immediately before the existing `safe_typing`-wrapped `generate_response`; after the `AsyncSessionLocal` block closes, inside `if reply and client:` and before `client.send_message`, a randomized-cps hold sleeps inside a **second** `safe_typing` context (`asyncio.sleep`, no `time.sleep`). Log line: `⌨️ Typing hold {hold:.1f}с …`.
  - Empty/None reply path unchanged (branch not entered → no hold).
- All 6 tests GREEN via test-overlay: `pytest tests/test_typing_hold.py` → `6 passed in 1.62s`.

### Task 2: Deploy listener + verify clean start

- Sanity-import check: `compute_typing_hold(200, 5.0, 4.0)` → `35.0` (200/4=50 → clamped 40 → −5 elapsed). Correct.
- `docker compose up -d --build listener` — listener-only rebuild (telegram.py untouched, api not rebuilt).
- Startup verified: all senders connecting and listening (`✅ sender-… — слушаем сообщения`), reconcile loop (30s) + restriction reconcile loop (900s) started, **0 tracebacks**, container `Up`.
- Live behavior observable on next inbound AI reply: `⌨️ Typing hold …` followed by `📤 AI ответил`.

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| `5f9bf33` | test | Failing tests for compute_typing_hold (RED) |
| `3acc81c` | feat | Typing-hold constants, pure function, _send_to_ai wiring (GREEN) |

## Verification

- [x] `pytest tests/test_typing_hold.py` GREEN via test-overlay (6/6)
- [x] `grep compute_typing_hold|TYPING_HOLD_MAX` shows constants (139), function (142/148), call site (370)
- [x] Hold-sleep NOT inside `async with AsyncSessionLocal()` (visual diff inspection — it's in the dedented `if reply and client:` block)
- [x] Listener container Up, 0 tracebacks after rebuild
- [x] No `time.sleep`; DEBOUNCE_*/queue intervals untouched; `app/services/telegram.py` untouched

## Deviations from Plan

None - plan executed exactly as written. Note: the plan warned `app/services/listener.py` might carry pre-existing uncommitted hunks from parallel work — by execution time the tree was clean for that file (the earlier edit had been committed as `f782fd9 tune(listener): debounce window 40s min / 120s max` by another session), so no special handling was needed. Plan's Task 2 single-commit step was superseded by the TDD per-phase commits (test + feat), covering the same two files.

## Known Stubs

None.

## Self-Check: PASSED

- FOUND: app/services/listener.py (constants + compute_typing_hold + call site)
- FOUND: tests/test_typing_hold.py
- FOUND: commit 5f9bf33
- FOUND: commit 3acc81c
- Listener container Up, 0 tracebacks
