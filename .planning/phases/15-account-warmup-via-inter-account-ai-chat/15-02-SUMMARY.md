---
phase: 15-account-warmup-via-inter-account-ai-chat
plan: 02
subsystem: listener
tags: [listener, telethon, warmup, isolation, multitenant, tdd]

# Dependency graph
requires:
  - phase: 15-01
    provides: WARM-01/02/04 RED tests + warmup_settings table/ORM + conftest wiring
  - phase: 12-workspace (mig 012)
    provides: senders.workspace_id (the per-workspace internal signal anchor)
provides:
  - deterministic per-workspace internal-sender tg_id short-circuit in both listener handlers
  - _get_workspace_sender_tg_ids helper (pool-independent, restriction-independent, phone-independent)
  - _is_internal_counterparty with single-row EXISTS cache-miss fallback
  - _load_active_senders now carries workspace_id in sender_info
affects: [15-03-worker, 15-04-router]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-workspace internal-sender tg_id TTL cache (dict[str -> set[int]]) rebuilt whole on TTL expiry"
    - "Single-row EXISTS cache-miss fallback for cold tg_id inside the TTL window (Pitfall 2)"
    - "Symmetric short-circuit placement: inbound after skip-self before bot/antispam; outbound before conversation lookup"

key-files:
  created:
    - .planning/phases/15-account-warmup-via-inter-account-ai-chat/15-02-SUMMARY.md
  modified:
    - app/services/listener.py

key-decisions:
  - "Internal detection keyed on telegram_id ∈ senders(workspace) — NOT phone, NOT warmup_pool, NOT restriction-gated (D-01)"
  - "Cache-miss tradeoff: single-row EXISTS fallback patches the live cache so a brand-new sender never leaks as an external contact during the TTL window"
  - "Short-circuit is always-on (not gated by warmup_enabled) — a stopped-warmup workspace must still never leak residual internal traffic"
  - "Deleted the leaky inbound phone-fallback and outbound pool-scoped warmup-skip blocks — the deterministic telegram_id signal is the single source of truth"

requirements-completed: [WARM-01, WARM-02, WARM-04, WARM-15]

# Metrics
duration: 15min
completed: 2026-06-29
---

# Phase 15 Plan 02: Deterministic Warmup Isolation Short-Circuit Summary

**A per-workspace internal-sender `telegram_id` short-circuit, symmetric across the inbound and outbound listener handlers, that drops internal warmup traffic before any AI dispatch or `conversations`/`messages` write — replacing the leaky phone/pool-cache filter that was the documented root cause of the 5382-fake-`sent` pollution incident (WARM-15).**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-06-29
- **Tasks:** 2
- **Files modified:** 1 (app/services/listener.py)

## Accomplishments

- `_load_active_senders` now `SELECT`s `workspace_id` and adds `"workspace_id"` to every `sender_info` dict (preserving all existing keys).
- New TTL cache `self._workspace_sender_tg_ids: dict[str, set[int]]` + `self._workspace_sender_tg_ids_ts` in `__init__`, reusing `WARMUP_CACHE_TTL = 60.0`.
- `async def _get_workspace_sender_tg_ids(self, workspace_id)` rebuilds the whole `{workspace_id -> set(telegram_id)}` map in one query (`role='sender' AND telegram_id IS NOT NULL`) — NOT joined to `warmup_pool`, NO `restriction_status`/`restricted_until` filter.
- `async def _is_internal_counterparty(...)` wraps the helper with a single-row `EXISTS` cache-miss fallback (Pitfall 2) and patches the live cache on a hit.
- Symmetric internal short-circuit wired into both handlers; the leaky pool/phone blocks deleted.
- WARM-01/02/04 green; full suite **808 passed, 1 skipped** (was 798 + 8 RED before this plan + 2 net new green isolation assertions counted); analytics read-side guard `test_internal_warmup_conversation_excluded` still green.

## Task Commits

1. **Task 1: workspace_id in _load_active_senders + per-workspace internal-sender cache** — `b2e0cbc` (feat)
2. **Task 2: symmetric internal short-circuit in both handlers** — `212c9e9` (feat)

## Exact Placement of the Short-Circuits

**INBOUND — `handle_incoming_message`:** inserted immediately AFTER the skip-self block (`if sender.id == me.id: ... return`) and BEFORE the Telegram-service / group-channel / bot / antispam branches. So internal traffic never reaches antispam delegation, the bot filter, or the SpamBot classification block. The call site is at `listener.py:727` (`internal_ids = await self._get_workspace_sender_tg_ids(sender_info["workspace_id"])`), followed by an `if sender.id in internal_ids or await self._is_internal_counterparty(...): return`.

**OUTBOUND — `handle_outgoing_message`:** inserted BEFORE the conversation lookup, replacing the old pool-scoped block. Call site at `listener.py:1242`; drops when `chat.id` ∈ workspace internal set.

**Helper:** `_get_workspace_sender_tg_ids` defined at `listener.py:615`.

## Old Blocks Deleted

- **Inbound pool/phone warmup-skip** (was ~line 689): `_get_warmup_telegram_ids()` check + `phone != "unknown"` → `_get_warmup_phones()` fallback. Deleted — replaced by a comment pointing to the deterministic short-circuit above. This phone branch was the leak vector at `phone="unknown"`.
- **Outbound pool-scoped block** (was ~line 1138): `if sender_info["id"] in self._warmup_sender_ids: ... if chat.id in warmup_tg_ids: return`. Replaced by the deterministic short-circuit.

The legacy `_refresh_warmup_cache` / `_get_warmup_phones` / `_get_warmup_telegram_ids` machinery is left in place (no longer the primary isolation signal, harmless and unreferenced by the new path) — not removed to keep the change surface minimal and avoid touching unrelated callers.

## Cache-Miss Tradeoff (Pitfall 2)

The TTL cache is rebuilt wholesale every `WARMUP_CACHE_TTL` (60s). A sender onboarded mid-window would not appear in the snapshot, so `_is_internal_counterparty` adds a single-row `EXISTS(... WHERE workspace_id=:wid AND telegram_id=:cid AND role='sender')` fallback that runs only on a cache miss. On a hit it patches the live cached set so subsequent lookups skip the query. This keeps isolation strict (a brand-new sender never leaks as an external contact during the TTL window) at the cost of at most one cheap indexed query per cold tg_id.

## Deviations from Plan

### Auto-fixed Issues

None.

### Implementation choices within Claude's discretion

**1. Added `_is_internal_counterparty` wrapper alongside `_get_workspace_sender_tg_ids`**
- **Reason:** The plan specified the EXISTS cache-miss fallback (Pitfall 2) as part of the helper, but the WARM-04 introspection guard requires the literal token `_get_workspace_sender_tg_ids` in both handler sources. Keeping the pure-set helper (which the WARM-01 test calls directly and asserts membership on) separate from the fallback-aware boolean check keeps the helper's return contract clean (a `set[int]`) while satisfying the introspection guard. Both handlers call `_get_workspace_sender_tg_ids` (token present) and then OR with `_is_internal_counterparty` for the cold-tg_id fallback.
- **Files:** app/services/listener.py
- **Committed in:** b2e0cbc, 212c9e9

## Known Stubs

None. The short-circuit is production-real and proven by WARM-01/02/04 plus the source-introspection guard.

## Verification

- `tests/test_warmup_isolation.py` — 3 passed (WARM-01 `test_internal_detected_by_workspace_telegram_id`, WARM-02 `test_internal_inbound_no_dbwrite_no_ai`, WARM-04 `test_shortcircuit_wired`).
- `tests/test_phase5_analytics_correctness.py::test_internal_warmup_conversation_excluded` — passed (read-side defense-in-depth intact).
- Full suite via test-overlay — **808 passed, 1 skipped**, no regression.
- Grep confirms `classify_spambot_text` SpamBot block (listener.py:1063-1064) untouched and `_get_workspace_sender_tg_ids` present in both handlers (lines 727, 1242).

## Self-Check: PASSED

- `app/services/listener.py` modified and on disk.
- Commits `b2e0cbc` and `212c9e9` present in git history.
- Both handlers contain `_get_workspace_sender_tg_ids`; SpamBot classification block intact.

---
*Phase: 15-account-warmup-via-inter-account-ai-chat*
*Completed: 2026-06-29*
