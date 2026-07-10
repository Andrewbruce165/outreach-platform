---
status: resolved
trigger: "проверь работу no reply и followup которую мы делали в 19 фазе. почему-то диалоги в которых мы ждем ответы не помечаются no reply и не понятно, работает ли логика фолоапов вообще"
created: 2026-07-08
updated: 2026-07-08
---

## Symptoms

- **Expected behavior:** Conversations waiting for a reply past the configured threshold should be marked `no_reply` (per Phase 19 no-reply/followup logic), and the followup scheduler should send follow-up messages to those conversations.
- **Actual behavior:** Conversations that are waiting on a reply are not being marked `no_reply`. It's unclear whether the followup logic is running/firing at all.
- **Error messages:** None reported — this is a silent failure, not a crash. No known error logs identified yet.
- **Timeline:** Built in Phase 19. User is not sure whether it ever worked correctly since then, or whether it worked initially and regressed — needs investigation.
- **Reproduction:** Concrete example — conversation with Илья · @illantonov, campaign `a06c464a-b495-4212-8024-4d510d734b66`. This conversation should currently be marked `no_reply` but is not.

## Current Focus

- hypothesis: RESOLVED. Widened scope implemented, tested, committed (65302c9), deployed, and live-verified.
- test: 21/21 passed via test-overlay (3 new widened-scope tests: paused-included, no-campaign-included, done-excluded).
- expecting: (met) Live first-tick marking pass flipped exactly 316 active→no_reply, 0 reverts — matching the pre-deploy impact query (107 no-campaign + 115 running + 94 paused).
- next_action: DONE. Nothing further required; user may optionally eyeball the inbox UI to see the 316 conversations now surfaced as no_reply.

## Reconciliation Findings (2026-07-08)

### D-02 verbatim (from 19-CONTEXT.md, lines 18-20)

> **D-02:** A conversation flips to `no_reply` only after the first ping interval elapses without an incoming message — NOT immediately after the opener. Status flip and first ping fire off the same timer expiry.
> **D-03:** Any incoming message from the contact returns the conversation to `active` and cancels all scheduled pings for it.
> **D-04:** Silence timer counts from the **last outgoing message** (opener, AI reply, or previous ping). Each ping restarts the timer for the next one.

### DISCUSSION-LOG verbatim (lines 20-24) — Interpretation B was OFFERED and NOT selected originally

The "Семантика «no reply»" timing table listed exactly three options; the ✓ marks the choice:

> | После первого интервала | Статус + первый пинг по одному таймеру | ✓ |
> | Сразу после отправки opener | Любой диалог без входящих = no_reply |  |
> | Отдельный порог | Свой настраиваемый порог для статуса |  |

"Сразу после отправки opener / Любой диалог без входящих = no_reply" == the user's Interpretation B. It was presented and left unchecked in Phase 19. **User has now explicitly decided (2026-07-08) to change this decision** to a hybrid: direction-based marking (closest to Interpretation B) for visibility, while keeping D-02's timer-based ping cadence completely unchanged. This supersedes D-02's original "status flip and first ping fire off the same timer" coupling — status flip is now decoupled from ping timing going forward. **Scope subsequently widened (2026-07-08, same day)** from running-campaigns-only to running+paused+no-campaign (done campaigns remain excluded) — see Current Focus.

### As-built status lifecycle (verified in code, pre-change)

- **Opener sent** (`queue.py:1661` INSERT omits status) → conversation created with DB default `status='active'` (confirmed: `information_schema` default `'active'`, ORM `models/__init__.py:350` server_default='active').
- **Contact replies** → `listener.handle_no_reply_revert` (listener.py:1930-1935) reverts `no_reply→active` (guarded); AI answerer runs (gate = `ai_enabled=true AND status='active'`, conversations.py:851). AI may then classify → `lead`/`handoff`/`finished` (ai_engine.py:460/482/512). Status otherwise stays `active`.
- **Ping interval elapses** → FollowUpWorker (`follow_up.py`) flips `active→no_reply` AT THE MOMENT the first ping enqueues (follow_up.py:257-261, D-02 as originally shipped). Elapsed is computed from `MAX(outbound message)` independently of status (follow_up.py:116-121, 156) — the status flip is a side-effect, NOT the ping trigger. **This decoupling is exactly what makes the new hybrid design implementable without touching ping cadence.**
- **Auto-finish threshold / max pings** → `finished` + finish webhook `reason='no_reply'` (follow_up.py:173-217).
- **Manual UI send** → `manual` (conversations.py:588/816/927); resume → `active` (conversations.py:867).
- **Unknown/system bot** → `bot_ignored`; Telegram service accounts → `telegram_service` (mig 046).

**Answer to the core question:** `active` today does NOT purely mean "waiting on the contact." `active` is the catch-all live state covering (a) opener-sent-never-replied AND (b) mid-dialog conversations where the contact replied and AI is engaged (ball may be in EITHER court). The new design must express "our last message is outbound & unanswered" as a last-message-direction computed state, not a send-time flag — and must revert to `active` on inbound reply (D-03 preserved).

### Real-DB deploy-impact (queried 2026-07-08, live prod, progressive measurements)

Original (pre-implementation) baseline: active=354, finished=14, manual=12, bot_ignored=4, lead=3, handoff=3, telegram_service=1, no_reply=0.

Under the fully unscoped design (no_reply = last message outbound & unanswered, no campaign-state filter at all): **328 of 354** active conversations would flip; **26** stay active.

First implementation (scoped to `campaigns.status='running'` only, re-queried): counts had shifted slightly (active=351) — **115** flip under running-only scope, **6** stay active within running campaigns. Excluded from that scope (intentionally, at the time): **107** no-campaign, **94** paused-campaign, **10** done-campaign active/last-outbound conversations (211 total excluded, 211+115=326 ≈ matches the unscoped 328 modulo count drift).

**Scope now widened** (2026-07-08, user decision) to running+paused+no-campaign, excluding only done. Expected new flip count ≈ 115 + (up to 94 paused) + (up to 107 no-campaign), re-query required at implementation time for the exact figure — record the fresh number in Resolution once measured.

Ping cadence/count is NOT affected by any of this: pings still only fire for campaigns with `follow_up_enabled=true` (currently 1 of 6, unrelated to this fix, unchanged by this diff).

## Evidence

- checked: FollowUpWorker registration + run loop (app/services/follow_up.py, app/main.py:20,72,82)
  found: Worker is a module singleton started in FastAPI lifespan; api logs show "🔔 FollowUpWorker started (poll=300s)" at 11:18:43 and no tick errors since. It IS running and ticking (~300s cadence).
  implication: The feature is not dead due to a missing/crashed worker.
- checked: Worker tick SQL gate (follow_up.py:122-124)
  found: Eligibility requires `cp.status='running' AND cp.follow_up_enabled=true AND c.status IN ('active','no_reply')`.
  implication: follow_up_enabled=true is a hard prerequisite for any no_reply marking / ping / auto-finish under the OLD design. Under the NEW hybrid design, marking must no longer require this; the ping/auto-finish pass keeps requiring it, unchanged.
- checked: campaigns table — all 6 rows
  found: EVERY campaign had follow_up_enabled=false at original measurement (server_default false); repro campaign "Киборг - Танова" (a06c464a…) is running but follow_up_enabled=false. NOTE: at implementation-review time, 1 campaign was found flipped to follow_up_enabled=true on prod independently of this session (not caused by this fix).
  implication: No conversation is ever eligible for PINGING except that 1 campaign → pinging stays essentially dormant. This remains true after the fix and is explicitly OUT OF SCOPE (user instruction: do not touch follow_up_enabled on any campaign as part of this change).
- checked: repro conversation Илья/@illantonov (750eeaaa-…) + its messages
  found: status='active', pings_sent=0. One outbound message at 2026-07-08 10:23:56; age vs now ~2h. follow_up_interval_hours=24, auto_finish_hours=72.
  implication: Under the new hybrid design, this conversation SHOULD already read `no_reply` (last message is outbound, unanswered) as soon as the fix deploys — regardless of the 24h interval, since marking is no longer interval-gated. Ping still would not fire for ~22h (interval unchanged), and never would fire at all unless follow_up_enabled is turned on for this campaign (out of scope).
- checked: whole-DB history — conversations by status + total pings + followup queue items
  found: 0 conversations in status no_reply, sum(pings_sent)=0 across 251 conversations, 0 message_queue rows with metadata->>'kind'='followup'. (9 'finished' are normal lead/handoff/manual finishes, not auto-finish.)
  implication: The no-reply/follow-up machine has NEVER produced a single action under the old design — consistent with follow_up_enabled being off everywhere. Post-fix, the MARKING half will finally activate broadly; the PINGING half stays essentially dormant until follow_up_enabled is enabled more broadly (separate step).
- checked: create/patch/clone endpoints (campaigns.py:377-380, 522-525, 650/721-722, 1055-1058) + schemas (schemas/__init__.py:806-809, 878-881, 942-945) + frontend (aimly-tg-outreach EditCampaignModal.tsx:101-104,152-155,747; campaigns.new.tsx:141-143,217-220,1631)
  found: Backend create/update/clone all persist follow_up_enabled from payload; PATCH uses model_dump(exclude_unset=True)+setattr (persists correctly). Frontend has a working toggle in both the new-campaign wizard and edit modal that sends follow_up_enabled.
  implication: No persistence bug, no missing UI. This flag continues to gate PINGING ONLY after the fix; marking is now (widened) independent of it and independent of most campaign states.
- checked: implemented + reviewed diff (app/services/follow_up.py, tests/test_follow_up.py) — running-only scope version
  found: New `FollowUpWorker._mark_no_reply_pass()` runs as tick() step 0: bulk UPDATE active→no_reply when last message outbound, and no_reply→active when last message inbound, self-contained session, ungated by follow_up_enabled, does not bump updated_at or touch pings_sent. Existing ping/auto-finish pass (step 1) byte-for-byte unchanged. 19/19 tests passed via test-overlay. Consumer re-verification: AI-answerer gate unaffected (revert runs before gate check), inbox filters already support no_reply, D-17 queue guard unaffected (dormant), analytics unaffected (no active vs no_reply distinction in counts).
  implication: Mechanism is sound; only the SCOPE predicate (which conversations are eligible) needs widening per the latest user decision — the marking logic itself (direction-based flip, decoupling from follow_up_enabled) does not need to change.

## Eliminated

- hypothesis: The FollowUpWorker isn't running / not registered / crashed.
  evidence: api logs show it started at 11:18:43 (poll=300s), registered in main.py lifespan, no tick errors logged.
  timestamp: 2026-07-08
- hypothesis: The no_reply marking / update SQL is broken and silently fails to write.
  evidence: The UPDATE is never reached under the old design — 0 eligible rows because follow_up_enabled=false everywhere. No conversation ever entered no_reply, 0 pings, 0 followup queue items.
  timestamp: 2026-07-08
- hypothesis: The API or frontend fails to persist follow_up_enabled=true (toggle not wired).
  evidence: create/patch/clone all set the field from payload; PATCH persists via model_dump(exclude_unset=True); frontend EditCampaignModal + new-campaign wizard both render a toggle and send the field.
  timestamp: 2026-07-08
- hypothesis: The repro conversation (Илья) is a bug — it should already be no_reply under the ORIGINALLY-SHIPPED design.
  evidence: It's ~2h old vs a 24h follow_up_interval; no_reply is set only at the moment the first ping is enqueued (D-02, as shipped). Even with the feature enabled, no action is due for another ~22h under that OLD design. Superseded: under the NEW hybrid design (decided 2026-07-08), this conversation SHOULD flip to no_reply immediately on deploy since it already has an unanswered outbound message. Not re-opened as a "bug" — it's the expected outcome of the new design.
  timestamp: 2026-07-08

## Resolution

- root_cause: "The Phase 19 no-reply/follow-up feature originally coupled TWO concerns behind a single gate: campaigns.follow_up_enabled (server_default false, never enabled on any of the 6 campaigns at time of investigation) controlled both (a) whether a conversation is ever marked no_reply and (b) whether a follow-up ping is ever sent. Because the flag was off everywhere, neither ever fired — 0 conversations ever reached no_reply, 0 pings sent, 0 auto-finishes, despite the worker running correctly and the API/UI wiring the toggle correctly. This was a configuration/expectation gap, not a code defect. Separately, on reconciliation, the user changed the original Phase 19 timing decision (D-02: mark no_reply only at ping-time) to a new hybrid design: no_reply should be a direction-based, immediate, visibility-only status (set when the last message is outbound & unanswered, cleared on reply) — decoupled entirely from follow_up_enabled and from ping timing, which remains interval-gated and untouched. Scope was further widened from running-only to running+paused+no-campaign campaigns (excluding only 'done')."
- fix: "IMPLEMENTED + DEPLOYED (commit 65302c9, 2026-07-08). FollowUpWorker._mark_no_reply_pass() in app/services/follow_up.py runs as tick() step 0: two idempotent bulk UPDATEs in one isolated transaction — (A) active→no_reply when a conversation's LAST message is outbound, (B) no_reply→active when the last message is inbound (self-healing safety net alongside listener D-03). Ungated by follow_up_enabled; sends zero Telegram messages; does NOT touch pings_sent; does NOT bump updated_at. Eligibility predicate WIDENED from the inner-join campaign.status='running' to (campaign_id IS NULL OR EXISTS(campaign in running/paused)) — a LEFT-JOIN-style test so no-campaign/orphan conversations are included instead of dropped; 'done' and other terminal campaign states remain excluded. Existing interval-gated ping/auto-finish sweep (step 1, still gated on follow_up_enabled) is byte-for-byte unchanged. Tests: added test_marking_includes_paused_campaigns, test_marking_includes_no_campaign_conversations, test_marking_excludes_done_campaigns (replaced the now-obsolete running-only scoping test)."
- verification: "VERIFIED LIVE (2026-07-08). 21/21 tests green via test-overlay. Deploy clean: 55 migrations applied, no errors, FollowUpWorker started 15:39:42 (poll=300s), listener started clean. First-tick log: '🔔 FollowUpWorker marking pass: 316 active→no_reply, 0 no_reply→active' — EXACTLY matching the pre-deploy impact query (107 no-campaign + 115 running + 94 paused = 316). DB after: no_reply=316, active=34 (was 350; 350-316=34). Repro conversation Илья/@illantonov (750eeaaa-…) now status='no_reply', pings_sent=0. Sample of newly-flipped no-campaign + paused conversations confirmed no_reply. No side effects: total pings_sent still 0, no new message_queue rows (669 message + 1 file, no followup items), follow_up_enabled unchanged (1 of 6), done-campaign's 10 active conversations left untouched. Containers healthy. Only remaining optional check is a human eyeballing the inbox UI (not blocking — frontend already supports the no_reply filter per prior evidence)."
- files_changed: ["app/services/follow_up.py", "tests/test_follow_up.py"]
- commit: "65302c9"
- widened_flip_count: 316 (107 no-campaign + 115 running + 94 paused; done excluded)
