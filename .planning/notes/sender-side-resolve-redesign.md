---
title: Sender-side resolve redesign — checker as pure filter, sender resolves+reaches itself
date: 2026-06-30
context: /gsd:explore session triggered by campaign "Barter - ВЭД хук" — 22 live RU mobiles terminally failed on ResolvePhone despite being marked registered/high/clean
status: design-agreed, ready to seed a phase
related:
  - .planning/notes/checker-false-negatives.md
  - .planning/notes/checker-pool-throttle-spike.md
  - .planning/debug/checker-fn-igor-base.md
  - memory: project-us-senders-cannot-resolve-ru-phones, project-checker-trip-count-reset-defeats-escalation
---

# Sender-side resolve redesign

## Trigger (the live incident)
Campaign **Barter - ВЭД хук** (`ff6e2d10…`, running): queue = 22 failed + 2 sent. All 22
are live RU mobiles (`+79…`), all sent by one account (sender-7375001431, +79933702359),
each retried 3× → terminal `failed`. Single error on all 22:
`No user is associated to the specified phone number (caused by ResolvePhoneRequest)`.

Why they failed:
- They were flagged **registered / high / clean** — but resolved by **us-account-4**
  (+16184955130, **US**, now itself `spam_limited`). A US account on RU numbers is a
  documented false-positive machine, AND its resolve **does not transfer** to the sender.
- The **sender's own** ResolvePhone returned PhoneNotOccupied — either privacy-hidden
  numbers or the sender account itself throttled.
- The send path has **no ImportContacts fallback**, so privacy-hidden-but-registered
  numbers fail at send 100% of the time.

## First-party truth (Telethon docs — verified, not assumed)
From https://docs.telethon.dev/en/stable/concepts/entities.html:
1. **access_hash is per-account**: "the access hash is different for each account, so
   trying to reuse the access hash from one account in another will not work." → a checker's
   resolve can NEVER be handed to a sender.
2. **No shortcut for a cold phone**: "the phone number must be in your contact list before
   you can use it." `send_message('+79…')` for a stranger raises "Could not find the input
   entity". There is no auto-resolve by phone.
3. **Entity cache** is populated only by entities the account itself encounters — dialogs,
   group participants, username resolve, or its own phone resolve/import. None of these is
   free for a cold first-touch phone.

**Conclusion that this locks in:** the sending account MUST perform a per-recipient
phone→user lookup itself (ResolvePhone or ImportContacts). This cannot be delegated or
removed. The only choice is *which* lookup.

## The design we agreed on
1. **Checker = pure, non-authoritative filter.** It only writes an `is_registered`
   exists/not flag to cut dead numbers before a sender spends a lookup on them. Its verdict
   is NEVER treated as authoritative reachability and its access_hash is never reused.
2. **Sender resolves AND reaches the contact itself**, using **ImportContacts** (not only
   ResolvePhone) as the mechanism — because import additionally surfaces privacy-hidden
   registered numbers that ResolvePhone misses. This directly fixes the 22-failed class.
   - Likely shape: ResolvePhone first (lighter touch) → **ImportContacts fallback** when
     ResolvePhone comes back empty AND the checker flagged the number registered. (Import-only
     is the alternative — sub-decision for the phase.)
3. **Lazy + paced import** — one import per send, right before sending, never a batch of 50
   up front. The 4/min send limit naturally spreads it well under the measured burst onset
   (~47–49 consecutive lookups). A morning batch of 50 would sit right on the onset → forbidden.
4. **Cleanup**: import → send → `DeleteContacts` to keep the address book clean. (Caveat:
   delete does not un-log the import event; it only prevents accumulation.)

## Update: username capture = cheap, transferable top tier
`ResolvePhoneRequest` returns the **full User object incl. `username`** (we already read
`user.username` in code). Unlike access_hash, a public **@username IS transferable** — it is
not account-bound. So if the checker **captures and stores the username** (currently
`resolve_phone_with_fallback` throws it away — returns only `{is_registered, telegram_id}`),
the sender can later do **`ResolveUsernameRequest(@username)`** → gets its OWN access_hash →
send. This is the **safest resolve** (same as opening a t.me link, barely flagged), **does
not pollute the address book** (no ImportContacts), and **bypasses phone-privacy**
(username-resolve works regardless of "find me by phone").

This makes the sender resolve a **3-tier ladder**:
1. **Username captured by checker** → `ResolveUsername` (cheap / safe / transferable / no book pollution). ← top tier
2. **No username, number discoverable** → `ResolvePhone`.
3. **ResolvePhone empty + checker said registered** (privacy-hidden) → `ImportContacts` fallback (accept risk).

Caveats (do not oversell): (a) only the subset that has a **public username** — a minority on
cold RU bases; **measurable** from existing `contacts_cache` usernames. (b) Only numbers
discoverable by phone in the first place — privacy="Nobody/Contacts" returns nothing, so no
username to capture. (c) Usernames can change/drop between check and send → fall back to phone
path. (d) Resolve ≠ deliverable — message-privacy (PRIVACY_RESTRICTED) still applies to all tiers.

Concrete change this implies: **checker must persist username** (and any cheap public fields)
at resolve time, into `contacts_cache`, so the sender can take tier 1.

## Truths established (so the phase doesn't relitigate them)
- **Import is also a resolve.** "Without ResolvePhone" does not mean "without a per-recipient
  lookup" — it swaps a lighter op for a heavier one. There is no version where the sender
  avoids the lookup for a cold number.
- **At ~50 new contacts/day/account, spread out, import is NOT "mass importer."** The op
  itself is a second-order risk at that volume. "Mass" = bursts (dozens in minutes) or
  thousands accumulated + the pattern import-stranger→cold-DM→blocked.
- **The dominant account-killer on cold outreach is recipient blocks/reports → PeerFlood →
  freeze** — independent of resolve vs import. Resolve-mechanism choice is second-order; the
  metric that actually matters is block/report rate per sender, which we currently don't track.
- **"100% reach" is false**: privacy = "Nobody (find me by phone)" returns empty even from
  ImportContacts.
- The checker remains unreliable (4 conflated false-negative causes: truly-dead / privacy /
  checker-throttle / US-cold-account). A *reliable cheap filter* still matters because
  importing a dead number wastes a risky sender op.

## Open sub-decisions for the phase
- ResolvePhone-then-Import-fallback **vs** Import-only on the sender.
- Cleanup policy: delete-after-send vs keep (and any cap on retained contacts).
- **Country-gate** resolvers/senders — never let a US (+1) account resolve RU (+79). (This
  caused half the 22.)
- Stop trusting checker verdicts as terminal: a `not_registered` from a suspect/US/throttled
  checker must not finalize-discard a contact.
- Fix **cache cross-contamination** (a false-negative cached by checker A is served to
  checker B / to the sender, defeating inline throttle detection) — related, see debug file.
- Add the **block/report-rate metric** per sender account.

## Scope-fence (NOT in this phase)
- Rebuilding the checker pool health machinery (Phase 14 already did this).
- Warmup of new RU accounts (operational, separate track).
