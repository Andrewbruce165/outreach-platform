# Proposal: Sender Pool Resilience & Failover

**Status:** Design agreed (2026-06-22), not yet planned into ROADMAP. No code written.
**Builds on:** Phase 4 (Campaigns), Migration 028 (sender restriction), 260622-gxt (SpamBot self-check guard).
**Trigger:** Campaign b7cc7d06 hung at 0 pending — 37 contacts terminally `failed` by an antispam
signal, no auto-resume. Live re-occurrence during the requeue (PEER_FLOOD, account spam_limited).

---

## Problem

If a campaign concentrates all sends on one account and that account hits a spam freeze, the whole
campaign stalls until the account recovers — and currently some of that work is killed terminally
(antispam path) with no auto-resume, while new contacts can still be routed onto the frozen account.

## Goal

A campaign sends evenly across a **pool** of accounts. When one account is spam-limited:
- only that account's **cold outreach** stops (others keep working) — damage bounded to its slice;
- its **un-contacted** cold backlog **fails over** to healthy pool accounts (zero idle wait);
- its **existing conversations keep replying from the same account** (Telegram does not block replies
  to established dialogues; switching account mid-thread would break continuity);
- the account is **re-checked via @SpamBot** on a timer and **auto-resumed** when free.

---

## Finalised freeze policy (the missing rule)

When a sender hits a **soft** spam limit (`restriction_status='spam_limited'`, PEER_FLOOD or antispam signal):

| Traffic type | Behaviour |
|---|---|
| Cold outreach to **un-contacted** numbers | STOP on this account → pause + **failover** to a healthy pool account |
| **Existing conversations** (someone already replied / dialogue started) | **Keep replying from the SAME account.** Do NOT disable AI. Do NOT move. |
| Recheck | @SpamBot reconcile on `restricted_until` timer → free → resume cold outreach on this account too |

**Hard freeze / ban** (`auth_status='banned'` / `frozen`) is different: read-only, replies also fail →
the account leaves the pool entirely; its dialogues wait for unban or go to a manager. The
"keep replying" rule is for the soft `spam_limited` state only.

---

## What already works (do NOT rebuild)

- **Pool model:** `campaign_senders` (M2M); `CampaignCreate.sender_ids: List[UUID]` validated + written on create.
- **Even distribution:** enqueue assigns least-loaded sender per contact (`rotation.get_or_assign_sender`
  → `_pick_least_loaded`), sticky via `campaign_contact_assignments`; worker `_tick`
  ([queue.py:155-243](../../app/services/queue.py#L155-L243)) round-robins ALL eligible senders each tick,
  each at its own rate limit (4/min, 20/h, 150/day).
- **Per-sender isolation on PEER_FLOOD** ([queue.py:733](../../app/services/queue.py#L733)): pauses only that
  sender's pending +24h, flags `spam_limited` + `restricted_until`; worker skips only it ([queue.py:401](../../app/services/queue.py#L401)).
- **Auto-resume:** restriction reconcile ([listener.py:1352-1449](../../app/services/listener.py#L1352-L1449)) re-pings
  @SpamBot when `restricted_until <= NOW()`; on **free** clears flag + pulls paused pending back to `NOW()`.
- **Replies not gated by restriction:** `ai_engine.py` has no `restriction_status` check — replies flow
  regardless of the flag (gated only by `ai_enabled` / manager takeover).

## Gaps (the actual work)

1. **Antispam-signal path over-blocks** — [`_handle_antispam_signal`](../../app/services/listener.py#L881-L957)
   sets queue items to terminal `failed` (no auto-resume) AND disables AI in ALL conversations
   (`ai_enabled=false`). Inconsistent with the PEER_FLOOD path and with the freeze policy.
2. **Rotation can park new contacts on a limited account** — candidate filter
   ([rotation.py:112-125](../../app/services/rotation.py#L112-L125)) checks `lifecycle_status='active'` +
   `auth_status='ok'` but NOT `restriction_status`; a `spam_limited` sender stays `active/ok`.
3. **No failover** — a frozen account's un-contacted backlog waits for its own recovery (sticky), instead
   of moving to healthy pool accounts.
4. **Cannot grow the pool of an existing campaign** — `sender_ids` set only at create; PATCH ignores it
   ([schemas:625](../../app/schemas/__init__.py#L625)); no attach/detach endpoint (only the response builder
   uses `CampaignSenderAttach`). All 4 existing campaigns have exactly 1 sender.
5. **Low visibility** — per-sender 'limited' derived ([senders.py:74-84](../../app/routers/senders.py#L74-L84))
   but no campaign-level "N active / K limited until T".

---

## Phases

### Phase A — Unified freeze policy (backend correctness) — highest value, smallest
- **A1** Rewrite `_handle_antispam_signal` to match PEER_FLOOD: instead of terminal `failed`, set
  `restriction_status='spam_limited'` + `restricted_until` and pause that sender's pending; let the
  existing reconcile auto-resume. **Stop disabling AI wholesale** — keep replies on existing dialogues alive.
- **A2** Add `AND s.restriction_status = 'none'` to the rotation candidate filter so new cold contacts
  never get parked on a limited/frozen account.
- **A3** (verify) worker already skips restricted senders — no change expected, add a regression test.
- **Acceptance:** antispam signal → cold outreach pauses + sender flagged + reconcile resumes; AI replies
  in existing conversations continue; new cold contacts route only to healthy senders.
- **Migrations:** none (028 columns exist). **Tests:** extend 260622-gxt antispam tests + rotation filter test.

### Phase B — Pool management + even distribution
- **B1** `POST /campaigns/{id}/senders` + `DELETE /campaigns/{id}/senders/{sid}` (workspace validation
  helper already exists) so a running campaign's pool can grow/shrink.
- **B2** Frontend multi-select for campaign senders (separate repo `aimly-tg-outreach`).
- **B3** Confirm/strengthen even spread across the pool; optional rebalance when a sender is added mid-flight.
- **Acceptance:** attach 2-3 accounts to a campaign; enqueue spreads contacts; worker sends from all in parallel.

### Phase C — Failover for cold contacts (Level 2)
- **C1** On freeze, reassign the frozen sender's **un-contacted** pending items (never sent, no started
  conversation) to healthy pool senders via `get_or_assign_sender`; leave engaged dialogues on the sender.
  Define the "un-contacted" predicate precisely (no `sent` queue row for that phone + no outbound message).
- **C2** Safety: don't dogpile one healthy sender — respect rate headroom, cap the failover batch, log what moved.
- **Acceptance:** frozen account's cold backlog drains via healthy accounts; engaged dialogues stay put and keep replying.

### Phase D — Visibility (optional, recommended)
- Campaign response surfaces pool health (N active / K limited until T); frontend badge.

---

## Non-goals (v1 of this feature)
- Failover of **engaged** conversations to another account (breaks continuity — they wait for their account).
- "Cool down replies too" mode on soft limit — default is keep replying; could become a workspace setting later.
- Cross-campaign sender load awareness (a sender shared by 2 campaigns) — out of scope unless it bites.

## Open decisions
- Exact "un-contacted / safe-to-failover" predicate (C1).
- Failover batch cap + health threshold for receiving accounts (C2).
- Whether attach/detach is allowed while campaign is `running` (B1) — likely yes, gated by lock checks.
