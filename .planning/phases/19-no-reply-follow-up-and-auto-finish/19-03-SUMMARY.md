---
phase: 19-no-reply-follow-up-and-auto-finish
plan: 03
subsystem: listener + queue
tags: [listener, queue, follow-up, no_reply, guard, telegram]

# Dependency graph
requires:
  - phase: 19-01
    provides: conversations.status accepts 'no_reply' + pings_sent + campaign follow-up columns
  - phase: 04-campaigns
    provides: message_queue.campaign_id + campaign_contact_assignments
  - phase: 05-inbox-analytics
    provides: queue pre-send race guard (Phase 5 D-04) precedent
provides:
  - "listener.handle_no_reply_revert(conversation_id): D-03 revert no_reply->active + D-17 first guard (cancel pending pings)"
  - "handle_incoming_message wires the revert before the AI-dispatch check (updates local conv[status])"
  - "queue pre-send D-17 second guard: cancels a follow-up ping if the contact replied since scheduling or the conversation left active/no_reply"
affects: [19-04, follow-up-worker]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "SQL-UPDATE-from-listener (antispam queue-cancel precedent) for the ping-cancel"
    - "queue sibling guard: one extra SELECT + conditional UPDATE beside the Phase 5 pre-send guard"
    - "extra_data.kind=='followup' gate so openers/replies bypass the follow-up guard (Pitfall 1)"

key-files:
  created: []
  modified:
    - app/services/listener.py
    - app/services/queue.py
    - tests/test_follow_up.py

key-decisions:
  - "D-03: incoming reply reverts no_reply->active BEFORE the AI-dispatch check and updates the local conv dict (Pitfall 4) so the normal answerer fires the same turn"
  - "D-17 first guard: listener cancels this conversation's pending pings scoped to sender_id + recipient_phone (+ campaign_id when set)"
  - "D-17 second guard: queue cancels a follow-up ping (status=cancelled, not failed) on reply-since OR conversation status not in (active,no_reply)"
  - "Both guards run without touching empirical rate-limit/interval constants (CLAUDE.md hard rule)"

requirements-completed: [NORP-07, NORP-08]

# Metrics
duration: 12min
completed: 2026-07-03
---

# Phase 19 Plan 03: No-Reply Reply Revert & Follow-Up Send Guards Summary

**The listener now reverts a `no_reply` conversation back to `active` and cancels its pending follow-up pings the moment a genuine contact reply arrives (D-03 + D-17 first guard), and the queue re-checks each follow-up ping at send time — cancelling it if the contact replied after it was scheduled or the conversation left active/no_reply (D-17 second guard). Together these are the safety net that makes it acceptable to snapshot ping text at enqueue time.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-07-03T08:06:00Z (approx)
- **Completed:** 2026-07-03T08:18:00Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- `app/services/listener.py`: new module-level `handle_no_reply_revert(conversation_id)` — (1) D-03 revert `no_reply`→`active` (guarded so manual/lead/finished/handoff/bot_ignored are preserved), (2) D-17 first guard cancels this conversation's pending ping queue rows (scoped to sender_id + recipient_phone + campaign_id when set). Never raises.
- Wired into `handle_incoming_message` after the inbound message is saved and BEFORE the `conv["ai_enabled"] and conv["status"] == "active"` dispatch check; updates the local `conv["status"]` so the normal answerer fires the same turn (Pitfall 4).
- `app/services/queue.py`: D-17 second guard in `_process_next_for_sender`, immediately after the Phase 5 pre-send guard, gated strictly on `extra_data.kind == "followup"` (Pitfall 1). Cancels the ping (status=cancelled) if an inbound `messages` row exists with `created_at > item.created_at`, OR the conversation's current status is not in `('active','no_reply')` (D-06). One extra SELECT + conditional UPDATE.
- `tests/test_follow_up.py`: NORP-07 test strengthened to assert revert + ping cancellation; 3 new NORP-08 guard tests (reply-since cancel, left-active cancel, still-silent passthrough).

## Task Commits

1. **Task 1: Listener revert no_reply→active + cancel pending pings (D-03/D-17)** — `b1d7b57` (feat)
2. **Task 2: Queue pre-send replied-since guard for follow-up pings (D-17)** — `a7b20fd` (feat)

## Files Created/Modified

- `app/services/listener.py` — `handle_no_reply_revert()` module-level function + call site in `handle_incoming_message`
- `app/services/queue.py` — follow-up pre-send guard beside the Phase 5 guard (gated on `kind=='followup'`)
- `tests/test_follow_up.py` — strengthened NORP-07 + 3 NORP-08 tests + `_seed_followup_item` helper

## Decisions Made

- **Revert before dispatch + local dict update** — `conv` is read by `get_or_create_conversation` before the revert, so updating only the DB row would leave the reply saved but unanswered (Pitfall 4). Setting `conv["status"]="active"` locally makes the existing AI-dispatch condition fire this turn.
- **Listener cancel scoped to pending only** — the sent opener is already `status='sent'`; pending rows for an in-conversation contact are follow-up pings. Scope is sender_id + recipient_phone (+ campaign_id when the conversation carries one), matching the antispam queue-cancel precedent.
- **Queue guard cancels (not fails)** — a replied-since ping is not an error; `status=cancelled` distinguishes it from the Phase 5 `failed` takeover case and from send failures.
- **Follow-up gate** — the queue guard runs ONLY for `extra_data.kind == "followup"`; normal openers/replies bypass it entirely (verified by the still-passing opener/pacing/takeover tests).

## Deviations from Plan

None — plan executed exactly as written. The plan's Task 1 described inline edits in `handle_incoming_message`; the revert/cancel logic was factored into the module-level `handle_no_reply_revert(conversation_id)` that the RED scaffold imports, then called from `handle_incoming_message` — this satisfies both the test contract and the plan's placement requirement (before the AI-dispatch check).

## Verification

- `tests/test_follow_up.py::test_reply_cancels_pings` — PASS (was RED after 19-01)
- `tests/test_follow_up.py -k "guard or reply_since"` — 3 PASS
- `tests/test_queue_even_pacing.py` + `tests/test_phase5_bot_filter.py` — 21 passed, 1 skipped (no regression)
- Broader queue regression (campaign_id, enqueue, new_dialog_limit, per_campaign_hours, inbox_send_takeover, rerender_pending_queue) — 37 passed
- Empirical rate-limit / interval constants in queue.py — byte-identical (grep confirms no edits)
- Remaining 5 RED tests in test_follow_up.py (NORP-02/04/06/12) are owned by Plans 19-02 and 19-04 — NOT regressions.

## Issues Encountered

- Worktree branch predated the 19-01 foundation (migration 045 + ORM + RED scaffold). Merged `main` into the worktree branch to pick up the schema before implementing. No conflicts.
- Test-overlay run from the isolated worktree required `COMPOSE_PROJECT_NAME=tg-outreach --env-file /root/apps/aimly/tg-outreach/.env` (same workaround documented in 19-01) — reuses the ephemeral tmpfs `db-test`, no prod data touched.

## Next Phase Readiness

- Plan 19-04 (FollowUpWorker) can now tag its ping queue items with `extra_data={"kind":"followup"}` knowing the send-time guard will suppress stale pings, and knowing an inbound reply reverts + cancels automatically.
- NOT deployed to prod (code lands on next `docker compose up -d --build api`/`listener`) — intentional; deploy after the full phase completes.

## Self-Check: PASSED

- Files verified on disk: app/services/listener.py, app/services/queue.py, tests/test_follow_up.py, 19-03-SUMMARY.md — all FOUND.
- Commits verified: b1d7b57, a7b20fd — all FOUND.
- Target tests GREEN via test-overlay (NORP-07 + 3 NORP-08 guard tests); broader queue regression 37 passed.

---
*Phase: 19-no-reply-follow-up-and-auto-finish*
*Completed: 2026-07-03*
