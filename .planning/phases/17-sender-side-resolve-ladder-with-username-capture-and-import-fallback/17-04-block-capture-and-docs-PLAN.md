---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 04
type: execute
wave: 3
depends_on: ['17-01', '17-03']
files_modified:
  - app/services/telegram.py
  - app/services/queue.py
  - app/routers/senders.py
  - /root/CLAUDE.md
autonomous: true
requirements: [SRLD-08, SRLD-09]
must_haves:
  truths:
    - "When a recipient has blocked the sender, the send raises UserIsBlockedError and a durable sender_restriction_events row (event_type='blocked', category='restriction') is written — without auto-pausing the sender"
    - "A read-only per-sender block-rate endpoint reports blocks_7d and sends_7d (block-rate = blocks/sends over a window) — no control-loop, no auto-pause"
    - "A block on a single recipient fails only that one queue item; it does NOT pause the sender's pending backlog (unlike PEER_FLOOD)"
    - "The /root/CLAUDE.md checker-semantics section frames the US-cannot-resolve-RU country claim as a HYPOTHESIS, not a documented fact"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "send_message catches UserIsBlockedError → structured USER_IS_BLOCKED error code"
      contains: "UserIsBlockedError"
    - path: "app/services/queue.py"
      provides: "USER_IS_BLOCKED branch records the block event in-TX, fails only this item"
      contains: "USER_IS_BLOCKED"
    - path: "app/routers/senders.py"
      provides: "GET /senders/{slug}/block-rate read-only aggregate"
      contains: "block-rate"
    - path: "/root/CLAUDE.md"
      provides: "country-as-fact softened to hypothesis"
      contains: "гипотеза"
  key_links:
    - from: "telegram.py::send_message"
      to: "queue.py error-code dispatch"
      via: "structured error code USER_IS_BLOCKED"
      pattern: "USER_IS_BLOCKED"
    - from: "queue.py USER_IS_BLOCKED branch"
      to: "sender_restriction_events"
      via: "record_restriction_event(sender.id, 'blocked', 'queue_error', None, msg, db=db)"
      pattern: "record_restriction_event"
---

<objective>
Capture the dominant cold-outreach account-killer signal — recipient blocks — as a durable per-sender event (D-15/SRLD-08), expose a read-only block-rate aggregate (no control-loop, D-16), and soften the unproven country-as-fact wording in `/root/CLAUDE.md` to a hypothesis (D-10/SRLD-09).

Purpose: The design doc establishes that blocks/reports → PeerFlood → freeze is the dominant account-killer on cold outreach, independent of the resolve mechanism, and it is the "metric that actually matters." Reports are not observable via Telegram, but a block-on-send IS (`UserIsBlockedError`). We capture it durably and expose a read-only rate; auto-pause/alerting is explicitly Deferred (Phase 10 non-goal). The doc task removes the false "country-gate is fact" framing that this whole phase deliberately did NOT gate on.

Output: `telegram.py` (catch), `queue.py` (record in-TX + fail one item), `senders.py` (read-only endpoint), `/root/CLAUDE.md` (doc). NO migration (`event_type` is free-form VARCHAR, no CHECK; `category='restriction'` already allowed).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-CONTEXT.md
@.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-RESEARCH.md

<interfaces>
<!-- Exact patterns the executor mirrors. -->

telegram.py:705-747 send_message except-chain (CURRENT — add UserIsBlockedError between PeerFloodError and the generic Exception):
```python
except PeerFloodError:
    return {"success": False, "error": {"code": "PEER_FLOOD", "message": "..."}}
except UserNotMutualContactError:
    return {"success": False, "error": {"code": "PRIVACY_RESTRICTED", "message": "..."}}
except Exception as e: ...
```
Telethon error import (verified 1.42.0): `from telethon.errors import UserIsBlockedError`.

queue.py error-code dispatch (CURRENT) — branches at: FLOOD_WAIT (:878), PEER_FLOOD (:924, records spam_limited + pauses ALL pending + failover), ACCOUNT_FROZEN (:977), PRIVACY_RESTRICTED (:1025, records recipient_privacy + fails ONLY this item via _fail_item, NO sender pause). The generic fail is at :1049-1060.
record_restriction_event(sender_id, event_type, source, restricted_until, raw_text, category='restriction', db=...) — restriction_audit.py:48; writes activity_slice; same-TX when db passed. PRIVACY_RESTRICTED branch (:1032) is the closest mirror: record event + _fail_item, NO pause.

senders.py:750-776 list_restriction_events — the read-only, workspace-scoped, _load_sender_by_slug-guarded endpoint pattern to mirror for block-rate.
messages_log: sender_id, message_type ('sent'), created_at (for sends count).
sender_restriction_events: event_type VARCHAR(20) FREE-FORM (no CHECK), category CHECK IN ('restriction','recipient_privacy','flood_wait').
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Catch UserIsBlockedError on send and record a durable block event in-TX (SRLD-08)</name>
  <files>app/services/telegram.py, app/services/queue.py</files>
  <read_first>
    - app/services/telegram.py:639-749 (send_message — full except chain; add the new catch between PeerFloodError:714 and the generic Exception:730)
    - app/services/queue.py:1025-1060 (PRIVACY_RESTRICTED branch — the exact mirror: record event + _fail_item, NO sender pause)
    - app/services/restriction_audit.py:48 (record_restriction_event signature, same-TX when db passed)
    - .planning/phases/17-...-/17-CONTEXT.md D-15 (durable block capture), D-16 (read-only, NO control-loop/auto-pause)
    - .planning/phases/17-...-/17-RESEARCH.md §"Code Examples" (D-15 block capture) + §"Pitfall 6" (event_type='blocked', category='restriction', NO CHECK migration)
    - tests/test_send.py + tests/test_restriction_audit.py (RED tests user_blocked / blocked from 17-01)
  </read_first>
  <behavior>
    - Test user_blocked (test_send.py): resolve succeeds, client.send_message raises UserIsBlockedError → send_message returns error code "USER_IS_BLOCKED".
    - Test blocked event (test_restriction_audit.py): a USER_IS_BLOCKED outcome in the queue writes exactly one sender_restriction_events row event_type='blocked', category='restriction', no CHECK violation.
    - Test (implicit): the sender's pending backlog is NOT paused by a block (no UPDATE message_queue ... status='pending' for the sender, unlike PEER_FLOOD).
  </behavior>
  <action>
    Part A — telegram.py `send_message`: add `from telethon.errors import UserIsBlockedError` to the module imports (verify not already present). In the except chain, ADD (after `except PeerFloodError:` :714, before `except UserNotMutualContactError:` or before the generic Exception — placement just needs to precede the generic handler):
    ```python
    except UserIsBlockedError:
        return {"success": False, "error": {"code": "USER_IS_BLOCKED",
                "message": "Получатель заблокировал отправителя"}}
    ```
    Keep a string-match fallback inside the generic `except Exception` (defence-in-depth, mirroring is_frozen_error): if `"USER_IS_BLOCKED" in str(e)` → return the same structured code. Do the SAME catch in `send_file`'s except chain for parity.

    Part B — queue.py: add an `elif error_code == "USER_IS_BLOCKED":` branch BEFORE the generic fail (mirror the PRIVACY_RESTRICTED branch at :1025, NOT the PEER_FLOOD branch):
    ```python
    elif error_code == "USER_IS_BLOCKED":
        # D-15: durable per-sender block capture. A block by ONE recipient is NOT an
        # account restriction (D-16 — no auto-pause); record it for the read-only
        # block-rate metric and fail ONLY this item. category='restriction' so it sits
        # with account-audit events (the design-doc proxy for accumulated reports→PeerFlood);
        # event_type='blocked' is free-form (no CHECK migration).
        await record_restriction_event(sender.id, "blocked", "queue_error", None, error_msg, db=db)
        if item.callback_url:
            asyncio.create_task(self._fire_callback(... status="failed" ... error=error_msg ...))
        await self._fail_item(db, item, error_msg)
        return
    ```
    Use the SAME `db` session as the surrounding send-loop (in-TX with the item state, like PRIVACY_RESTRICTED). Do NOT UPDATE senders.restriction_status, do NOT pause pending, do NOT call failover (those are PEER_FLOOD-only). Do NOT use `category=` other than the default 'restriction'.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send.py tests/test_restriction_audit.py -k "user_blocked or blocked" -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "UserIsBlockedError" app/services/telegram.py` returns ≥1 hit (typed catch).
    - `grep -n "USER_IS_BLOCKED" app/services/queue.py` returns ≥1 hit (dispatch branch).
    - `grep -n "record_restriction_event(sender.id, \"blocked\"" app/services/queue.py` (or equivalent) shows event_type='blocked'.
    - The USER_IS_BLOCKED branch in queue.py contains NO `UPDATE senders SET restriction_status` and NO failover call (block != restriction, D-16).
    - `pytest tests/test_send.py tests/test_restriction_audit.py -k "user_blocked or blocked" -x` exits 0.
  </acceptance_criteria>
  <done>UserIsBlockedError on send → USER_IS_BLOCKED code → durable 'blocked' event in-TX, one item failed, sender NOT paused.</done>
</task>

<task type="auto">
  <name>Task 2: Read-only per-sender block-rate endpoint (SRLD-08, D-16)</name>
  <files>app/routers/senders.py</files>
  <read_first>
    - app/routers/senders.py:750-776 (list_restriction_events — mirror: @router.get, _load_sender_by_slug guard, workspace-scoped, read-only)
    - app/models/__init__.py (SenderRestrictionEvent, MessageLog/messages_log)
    - .planning/phases/17-...-/17-CONTEXT.md D-15 (read-only block-rate), D-16 (NO control-loop)
    - .planning/phases/17-...-/17-RESEARCH.md §"Code Examples" (block-rate SQL)
    - tests/test_restriction_audit.py (RED test block_rate_aggregate from 17-01)
  </read_first>
  <action>
    Add `GET /senders/{slug}/block-rate` to `app/routers/senders.py` mirroring `list_restriction_events` (auth_dep, `_load_sender_by_slug(db, ctx, slug)` for the opaque-404 workspace guard, read-only). Define a small Pydantic response model `SenderBlockRateResponse(blocks_7d: int, sends_7d: int, block_rate: float)` (in the schemas module next to RestrictionEventResponse, or inline if that's the convention). Compute with a single async query (window = 7 days, parametric is fine but 7d default):
    ```sql
    SELECT
      (SELECT COUNT(*) FROM sender_restriction_events e
        WHERE e.sender_id = :sid AND e.workspace_id = :wid
          AND e.event_type = 'blocked'
          AND e.created_at > NOW() - INTERVAL '7 days')                         AS blocks_7d,
      (SELECT COUNT(*) FROM messages_log m
        WHERE m.sender_id = :sid AND m.workspace_id = :wid
          AND m.message_type = 'sent'
          AND m.created_at > NOW() - INTERVAL '7 days')                         AS sends_7d
    ```
    `block_rate = blocks_7d / sends_7d if sends_7d else 0.0`. Return the response model. This is READ-ONLY — it must NOT mutate any sender state, must NOT auto-pause, must NOT write events (D-16). Workspace isolation via `_load_sender_by_slug` + explicit `workspace_id = :wid` in the SQL (defence-in-depth, like the restriction-events endpoint).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_restriction_audit.py -k "block_rate" -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "block-rate" app/routers/senders.py` returns ≥1 hit (route path).
    - `grep -n "event_type = 'blocked'\|event_type='blocked'" app/routers/senders.py` shows the blocks count filter.
    - The handler contains NO UPDATE/INSERT/DELETE (read-only — `grep -n "UPDATE\|INSERT\|DELETE" ` of the new function's body returns 0).
    - `pytest tests/test_restriction_audit.py -k block_rate -x` exits 0 (blocks_7d == N, sends_7d == M for seeded data).
  </acceptance_criteria>
  <done>GET /senders/{slug}/block-rate returns {blocks_7d, sends_7d, block_rate}, workspace-scoped, read-only.</done>
</task>

<task type="auto">
  <name>Task 3: Soften the country-as-fact wording in /root/CLAUDE.md to a hypothesis (SRLD-09, D-10)</name>
  <files>/root/CLAUDE.md</files>
  <read_first>
    - /root/CLAUDE.md § "Семантика checker'а (is_registered)" (the section with the US/cold-resolve country claim — find the line stating US accounts cannot resolve RU phones as a documented fact)
    - .planning/phases/17-...-/17-CONTEXT.md D-10 (country-gate is an UNPROVEN hypothesis; NOT gated in code)
    - .planning/notes/checker-problem-and-history.md §3-4 (4-cause conflation; #4 country is "измерена сегодня" but confounded with cold/throttle — never isolated)
    - memory project-us-senders-cannot-resolve-ru-phones (reclassified to HYPOTHESIS 2026-06-30)
  </read_first>
  <action>
    Edit the `/root/CLAUDE.md` § "Семантика checker'а (is_registered)" section so the US-cannot-resolve-RU-phones claim reads as a HYPOTHESIS, not a documented fact. Specifically: locate the wording that presents "US(+1)/cold account → false-negatives on +79" as established causation, and reframe it to state that (a) country has ALWAYS been confounded with cold/throttle (no clean isolation test was ever run), (b) "warmed beats cold" is supported but "RU beats US" is NOT proven, and (c) Phase 17 deliberately does NOT gate resolve by country in code (D-10). Use the word "гипотеза" explicitly. Add a one-line cross-reference to `.planning/phases/17-.../17-CONTEXT.md` D-10. Keep the rest of the section (privacy false-negative semantics, throttle, username-only confirmation, Phase 14 mechanics) intact — only the country claim is reframed. Stage ONLY /root/CLAUDE.md for this edit (parallel-agent caution: never `git add -A`).
  </action>
  <verify>
    <automated>grep -n "гипотеза\|hypothesis" /root/CLAUDE.md</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "гипотеза" /root/CLAUDE.md` returns ≥1 hit inside the checker-semantics section.
    - `grep -ni "warmed beats cold\|RU beats US\|не доказан" /root/CLAUDE.md` shows the proven-vs-unproven framing.
    - The section no longer asserts country causation as a flat fact (manual read confirms reframing; the privacy/throttle/Phase-14 content is preserved).
  </acceptance_criteria>
  <done>The checker-semantics country claim in /root/CLAUDE.md is reframed as an unproven hypothesis with a D-10 cross-reference.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send.py tests/test_restriction_audit.py -x` GREEN for SRLD-08 tests.
- `grep -n "USER_IS_BLOCKED" app/services/telegram.py app/services/queue.py` shows the catch + dispatch.
- `grep -n "block-rate" app/routers/senders.py` shows the endpoint.
- `grep -n "гипотеза" /root/CLAUDE.md` confirms the doc reframe.
- No migration file added.
</verification>

<success_criteria>
- SRLD-08 (durable block capture + read-only block-rate, no control-loop) and SRLD-09 (doc softened) GREEN/done.
- A recipient block fails only one item and is observable as a per-sender rate; the sender is never auto-paused by a block.
- Full suite green before /gsd:verify-work.
</success_criteria>

<output>
After completion, create `.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-04-SUMMARY.md` noting: where the UserIsBlockedError catch was placed, the chosen category ('restriction') + event_type ('blocked'), the block-rate endpoint path + response shape, and the exact CLAUDE.md lines reframed. Confirm Phase 17 added 0 migrations.
</output>
