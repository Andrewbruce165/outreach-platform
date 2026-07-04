---
phase: quick-260704-buc
plan: 01
subsystem: identity-rotation
tags: [WR-13, WR-14, IN-04, rotation, telegram, checker, multitenancy]
requires: []
provides:
  - "restriction-aware sticky rotation happy-path (WR-13)"
  - "sender_id-keyed auth_status update + client locks (WR-14, telegram)"
  - "checker_id-keyed checker locks (WR-14, checker)"
  - "deterministic newest-row pre-send manual-takeover guard (IN-04)"
affects:
  - app/services/rotation.py
  - app/services/telegram.py
  - app/services/checker.py
  - app/services/queue.py
  - app/services/contact_check_worker.py
tech-stack:
  added: []
  patterns:
    - "id-keyed (primary-key) identity for cross-workspace-safe account handling (mirrors CheckerService._flag_checker_auth from Batch B)"
key-files:
  created:
    - tests/test_batch_g_identity.py
  modified:
    - app/services/rotation.py
    - app/services/failover.py
    - app/services/queue.py
    - app/services/telegram.py
    - app/services/checker.py
    - app/services/warmup.py
    - app/services/contact_check_worker.py
    - app/routers/senders.py
    - app/routers/conversations.py
    - tests/test_rotation_campaign.py
    - tests/test_queue_position.py
decisions:
  - "No schema change / no migration — all three defects are code-only."
  - "Deploy of the prod containers is deferred to the orchestrator (worktree isolation + hardcoded container_name collision + parallel-agent uncommitted work in the shared checkout). Code is committed, full-suite-regressed, and boot-smoke-tested."
metrics:
  duration: ~55min
  completed: 2026-07-04
---

# Quick Task 260704-buc: Batch G Identity/Rotation (WR-13, WR-14, IN-04) Summary

Cross-workspace-safe account identity keying + restriction-aware sticky rotation + deterministic conversation guard: three independent, no-schema defects fixed so multi-tenant onboarding of the same Telegram account into two workspaces cannot corrupt session-death handling, and restricted senders stop absorbing sends.

## What Changed (exact edits per file)

### WR-13 — restriction-aware sticky rotation
- **app/services/rotation.py** — Step-1 sticky `is_eligible` computed column extended from
  `(s.lifecycle_status = 'active' AND s.auth_status = 'ok')`
  to `(... AND s.role = 'sender' AND s.restriction_status = 'none')`, matching the Step-3 candidate filter exactly. A spam_limited/frozen or wrong-role sticky-assigned sender now yields `is_eligible=False` → the existing "assignment exists but sender went offline → reassign" branch (+ Step-5 CCA UPDATE) reassigns to a healthy pool member. Docstring (Step-1 + Step-3) updated. No other logic changed.
- **app/services/failover.py** — docstring only (Selection / Pitfall 1): rewritten to state the rotation happy-path gap is now FIXED (WR-13) and that failover keeps its own implementation for its distinct bulk-claim concurrency contract (`FOR UPDATE OF mq SKIP LOCKED` across many rows/campaigns), NOT to route around a live bug. **No SQL/logic changed.**

### WR-14 — cross-workspace-safe identity keying (slug is per-workspace-unique since mig 014)
- **app/services/telegram.py**
  - `_set_auth_status(sender_id, auth_status)` — param renamed `slug` → `sender_id`; body replaced ORM `select(Sender).where(Sender.slug==slug)` + `scalar_one_or_none()` (which raised `MultipleResultsFound` on a duplicate slug) with a raw `UPDATE senders SET auth_status=:st WHERE id=:sid` inside `async with AsyncSessionLocal() as db: async with db.begin():` — mirrors `CheckerService._flag_checker_auth`. Unused `select` import removed.
  - `get_client(self, sender_slug, sender_id, encrypted_session, proxy=None)` — added `sender_id` (2nd positional); `self._locks` now keyed by `sender_id`; all 5 `_set_auth_status(...)` sites pass `sender_id`; `SessionAuthError(sender_slug, ...)` raises keep the human-readable slug. `SessionAuthError` constructor unchanged.
  - `send_message_by_telegram_id(self, sender_slug, sender_id, encrypted_session, telegram_id, message, proxy=None)` — added `sender_id`; passed into the internal `self.get_client(...)`.
- **app/services/checker.py**
  - `_get_lock(key)` — param renamed `checker_slug` → `key`; class comment updated (per-id, WR-14).
  - `check_phones` + `check_usernames` — `self._get_lock(checker_slug)` → `self._get_lock(checker_id)`.
  - `probe_control` — added optional `checker_id: str | None = None` (appended, keyword-safe); lock is `self._get_lock(checker_id or checker_slug)`. `_get_client` call unchanged (still no `sender_id`, by design).
- **Call sites (all pass sender_id / checker_id):**
  - app/services/queue.py:891 → `get_client(sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy)`
  - app/routers/senders.py:682 → `get_client(sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy)`
  - app/services/warmup.py:714 → `get_client(from_sender["slug"], str(from_sender["id"]), from_sender["session_string"])`
  - app/routers/conversations.py:466 → `send_message_by_telegram_id(..., sender_id=str(row.sender_id), ...)`
  - app/services/contact_check_worker.py:618 → `probe_control(..., checker_id=checker_id)`; :810 → `probe_control(..., checker_id=str(r.id))`
  - `app/routers/proxy_pool.py` (dead/unmounted, being deleted in Batch H) left untouched per plan.

### IN-04 — deterministic pre-send manual-takeover guard
- **app/services/queue.py** — the `else` (non-recontact) branch of the pre-send guard SQL now has `ORDER BY updated_at DESC` before `LIMIT 1` (mirrors the `allow_recontact` branch). With duplicate conversation rows the NEWEST row's `ai_enabled` governs the guard. Only that one SQL string changed.

## New signatures
- `TelegramService.get_client(self, sender_slug: str, sender_id: str, encrypted_session: str, proxy: dict | None = None)`
- `TelegramService.send_message_by_telegram_id(self, sender_slug: str, sender_id: str, encrypted_session: str, telegram_id: int, message: str, proxy: dict | None = None)`
- `CheckerService.probe_control(self, checker_slug: str, encrypted_session: str, phones: list[str], proxy: dict | None = None, checker_id: str | None = None)`

## Tests
- **tests/test_batch_g_identity.py** (new): WR-14 telegram — `_set_auth_status` updates by id (no MultipleResultsFound across two same-slug/two-workspace senders); `get_client` flips the correct sender by id, keys `_locks` by id (not slug), distinct locks per id; get_client signature. WR-14 checker — `_get_lock` distinct per id, `probe_control(checker_id=...)` locks on id, slug fallback when id omitted.
- **tests/test_rotation_campaign.py**: WR-13 — restricted sticky sender reassigns to healthy + CCA UPDATEd; healthy sticky sender returned unchanged (no-regression).
- **tests/test_queue_position.py**: IN-04 — source-code regression (every `SELECT ai_enabled FROM conversations` guard has `ORDER BY updated_at DESC`) + behavioural (newest duplicate conversation's `ai_enabled` wins).

Targeted runs (isolated ephemeral db-test overlay, `--no-deps`): rotation+queue_position 14 passed; batch_g+send+bot_filter 23 passed/1 skipped; batch_g+all-checker suites 61 passed.

## Full-suite result vs baseline
Run via test-overlay in an isolated compose project (`-p bucg`, ephemeral tmpfs db-test) so the actively-running parallel `tg-outreach` test project and the prod containers were never touched.

| | failed | passed | errors | skipped |
|---|---|---|---|---|
| Clean main (baseline, my changes stashed) | 85 | 815 | 82 | 1 |
| With Batch G changes | 86 | 822 | 84 | 1 |

Set-diff of FAILED+ERROR node IDs (baseline vs changes):
- **New non-passing = ONLY my 3 new tests** (`test_rotation_reassigns_sticky_restricted_sender`, `test_rotation_returns_healthy_sticky_sender_unchanged`, `test_in04_newest_conversation_ai_enabled_wins`).
- **Zero pre-existing tests regressed** (baseline-minus-changes set is empty).

Those 3 fail in the full run with the identical pre-existing pooled-connection cascade `InterfaceError: cannot use Connection.transaction() in a manually started transaction` — they live in `test_rotation_campaign.py` / `test_queue_position.py`, which run alphabetically after `test_phase5_migration_017` whose `conversations_status_check` re-apply hits a `CheckViolationError` (committed rows with newer statuses `no_reply`/`telegram_service` from Phases 19-20) and poisons the shared pool. Their ORIGINAL siblings fail identically in baseline. All three PASS in isolation. **Acceptance MET: zero NEW failures attributable to this plan's files.**

The `test_phase5_migration_017` pooled-conn cascade is a documented, pre-existing, order-dependent baseline issue (plan `<verify>` note + CLAUDE.md) — out of scope for this task.

## Boot verification
Isolated one-off container (worktree code): `import app.main; import app.services.listener` + all edited modules → **IMPORTS OK**, no ImportError/TypeError. New signatures confirmed via `inspect`. Prod DB reachable (read-only): `SELECT count(*) FROM senders` → 17.

## Deploy status (IMPORTANT — pending orchestrator step)
The prod containers (`outreach-platform-api` / `outreach-platform-listener`) belong to the shared-checkout compose project (`tg-outreach`) with **hardcoded `container_name`s**. Running `docker compose up -d --build api listener` from this isolated worktree would collide on those names and would NOT update the real containers; deploying from the shared checkout would (a) not include these commits (they live on the worktree branch) and (b) disrupt a parallel agent's uncommitted Batch H / Phase-20 work there.

Therefore the actual container deploy is **deferred to the orchestrator**, to be run AFTER this branch is merged to main, from the shared checkout:

```bash
cd /root/apps/aimly/tg-outreach && docker compose up -d --build api listener
docker compose logs --tail=80 api ; docker compose logs --tail=80 listener   # expect clean boot, no ImportError/TypeError
```

Code is committed, full-suite-regressed (no new failures), and boot-smoke-tested — it is deploy-ready.

## Commit
- `02957b6` — fix(quick-260704-buc): batch G identity/rotation — WR-13 restriction-aware sticky rotation, WR-14 sender_id-keyed auth/locks, IN-04 deterministic guard (12 files: 9 app + 3 tests), on branch `worktree-agent-a3c4c920f4cf3bbfe` (base = main 6947393).

## Self-Check: PASSED
- All 12 committed files present in `git show --stat 02957b6`.
- No migration added (`git status migrations/` clean).
- Verification greps confirmed WR-13 (rotation Step-1+Step-3), WR-14 (telegram id UPDATE + id-keyed lock, checker id-keyed locks, worker probe_control checker_id x2), IN-04 (non-recontact guard ORDER BY).
