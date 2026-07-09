---
status: resolved
trigger: "бот кинул чат на handoff + manual. Я вернул обратно в AI. handoff остался"
created: 2026-07-09
updated: 2026-07-09
---

## Resolution

root_cause: |
  POST /conversations/{id}/enable-ai only reset status='manual' → 'active',
  deliberately leaving 'handoff' intact (old D-03 decision); but the listener
  gates AI dispatch strictly on ai_enabled=true AND status=='active'
  (listener.py:1010), so a handoff conversation switched back to AI ended up
  with ai_enabled=true but status stuck on 'handoff' — the listener never
  re-armed (AI stayed silent) and the inbox StatusPill badge never cleared,
  since both render directly off the same status column.
fix: |
  Extended the enable-ai CASE to reset both manager-takeover states —
  `status = CASE WHEN status IN ('manual','handoff') THEN 'active' ELSE status END` —
  while still preserving genuine conversation outcomes ('lead'/'finished'/
  'bot_ignored'); updated tests/test_phase5_inbox_manager_mode.py's
  handoff parametrized case (was asserting the buggy handoff→handoff
  behavior, now asserts handoff→active) and the D-03 docstrings in both
  files.
verification: |
  tests/test_phase5_inbox_manager_mode.py — 6/6 passed via docker
  test-overlay. Deployed 2026-07-09 08:50 (api container rebuilt, startup
  clean, migrations clean). Live prod confirmation 2026-07-09: user reopened
  a previously-stuck handoff chat, clicked "switch to AI" again — the
  handoff badge cleared and the AI answered the next incoming message.
  Decision: do NOT bulk-reset the 6 other pre-existing stuck handoff
  conversations — user explicitly chose to clear them one-by-one via the
  same UI click as needed, since the underlying fix now makes that click
  work correctly.
files_changed:
  - app/routers/conversations.py (enable-ai CASE + docstrings)
  - tests/test_phase5_inbox_manager_mode.py (expected handoff→active + docstrings)

## Symptoms

- **Expected:** After switching a conversation back to AI mode via the inbox UI toggle/button, the AI should resume answering in that chat, and the `handoff` badge/status should clear.
- **Actual:** AI does not answer at all after the switch — the switch back to AI did not take effect functionally. The `handoff` badge remains visible in the inbox UI. `manual` was also set at the same time as `handoff` (two distinct fields/statuses set simultaneously by the bot when it disengaged).
- **Where observed:** UI (inbox) only so far — DB state (`conversations` table mode/status fields) not yet inspected directly.
- **Scope:** Reproduced on exactly one chat so far; not yet tested on other chats in handoff.
- **Reproduction:** Bot auto-triggered handoff+manual on a chat (auto-pause trigger fired). User used the UI toggle/button in inbox to switch the chat back to AI mode. AI subsequently did not respond to new incoming messages in that chat, and the handoff badge is still shown.
- **Timeline:** Observed today (2026-07-09). Not yet known if this ever worked correctly for a full handoff→AI-return cycle.
- **Errors:** None reported/observed yet — no error surfaced in UI when clicking the switch.

## Current Focus

- hypothesis: CONFIRMED — `enable-ai` endpoint only resets `status='manual'→'active'`, leaving `status='handoff'` intact. The listener gates AI on `ai_enabled=true AND status=='active'`, so a handoff conversation returned to AI has ai_enabled=true but status='handoff' → AI stays silent + handoff badge persists.
- test: trace all three layers (auto-pause write, enable-ai write, listener gate) + confirm UI toggle calls enable-ai.
- expecting: enable-ai leaves status='handoff' → matches symptom exactly.
- next_action: RESOLVED. Fix applied, verified via test-overlay, deployed, and confirmed live by user in prod inbox. No further action — session closed.

## Evidence

- timestamp: 2026-07-09
  checked: uncommitted diff in app/services/listener.py
  found: only DEBOUNCE_MIN/MAX timing change (20/180 → 40/120). Unrelated to handoff.
  implication: local changes are NOT the cause; ignore for this bug.

- timestamp: 2026-07-09
  checked: app/models/__init__.py Conversation (lines 340-366)
  found: two relevant fields — `ai_enabled` (Boolean) and `status` String CHECK ('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply'). "handoff" and "manual" are BOTH values of the single `status` column (mutually exclusive), plus ai_enabled bool. User perceived "handoff + manual" as badge=handoff + AI-off (manager mode).
  implication: only ONE status can be set; auto-pause set status='handoff' AND ai_enabled=false.

- timestamp: 2026-07-09
  checked: app/services/ai_engine.py:477-505 (transfer_to_manager signal)
  found: auto-pause writes `status='handoff', ai_enabled=false, paused_at, paused_reason`.
  implication: handoff origin confirmed — single UPDATE sets status='handoff' + ai_enabled=false.

- timestamp: 2026-07-09
  checked: app/routers/conversations.py:843-874 (POST /enable-ai)
  found: UPDATE sets `ai_enabled=true, status = CASE WHEN status='manual' THEN 'active' ELSE status END`. Docstring/comment (D-03) deliberately preserves 'handoff'/'lead'/'finished'/'bot_ignored'. So a handoff conversation → ai_enabled=true but status STAYS 'handoff'.
  implication: enable-ai does NOT clear handoff. Root of the persisting badge.

- timestamp: 2026-07-09
  checked: app/services/listener.py:1010 (AI dispatch gate)
  found: `if conv["ai_enabled"] and conv["status"] == "active":` — strict equality on 'active'.
  implication: status='handoff' fails the gate → AI never responds even with ai_enabled=true. Explains "AI does not answer".

- timestamp: 2026-07-09
  checked: aimly-tg-outreach frontend src/routes/_authenticated/inbox.tsx:979-981
  found: the "switch to AI" button (enableAiMut) calls POST /api/v1/conversations/{id}/enable-ai. StatusPill renders c.status directly (line 831) so badge = raw status column.
  implication: UI toggle hits the exact endpoint that fails to clear handoff → both symptoms (silent AI + persistent badge) explained by one bug.

- timestamp: 2026-07-09
  checked: tests/test_phase5_inbox_manager_mode.py:95-101
  found: existing parametrized test asserts ("handoff","handoff") — it encodes the current buggy behavior as intended (D-03).
  implication: fix must update this test to ("handoff","active") and revise the D-03 rationale (handoff is a manager-takeover state, symmetric to manual).

- timestamp: 2026-07-09
  checked: fix applied + tests/test_phase5_inbox_manager_mode.py rerun via docker test-overlay
  found: 6/6 passed including the corrected handoff→active parametrized case.
  implication: fix behaves as designed under test; ready for deploy.

- timestamp: 2026-07-09
  checked: live prod inbox after deploy (user action)
  found: user reopened a previously-stuck handoff chat, clicked "switch to AI" — badge cleared, AI answered the next incoming message.
  implication: fix confirmed end-to-end in the real environment, not just under test.

## Eliminated

- hypothesis: uncommitted listener.py changes cause the bug
  evidence: diff is only debounce timing (DEBOUNCE_MIN/MAX); no status/handoff logic touched
  timestamp: 2026-07-09

- hypothesis: it's only a UI display bug (badge stale, AI actually working)
  evidence: listener gate at listener.py:1010 requires status=='active'; status='handoff' truly blocks AI dispatch — functional, not cosmetic
  timestamp: 2026-07-09
