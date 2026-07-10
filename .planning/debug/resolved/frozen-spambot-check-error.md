---
status: resolved
trigger: "когда аккаунт ловит frozen и я делаю запрос к спамботу он получает ответ что мы заблокированы и потом он резко переходит в статус error и перестает реагировать. просит реавторизацию. мы вроде как не теряем сессию"
created: 2026-07-10
updated: 2026-07-10
---

# Debug Session: frozen-spambot-check-error

## Symptoms

- **Expected behavior:** After a sender account goes `frozen` and a manual spambot check is run against it, the account should stay manageable (frozen state tracked normally) without becoming unresponsive or requiring reauthorization — the Telethon session is reportedly still intact.
- **Actual behavior:** The manual `/spambot` check request confirms the account is blocked, and immediately after, the sender's status flips to `error` and it stops reacting to further requests/queue processing. The UI/API then prompts for reauthorization, even though the session file itself is not believed to be lost.
- **Error messages:** Not yet checked — user has not inspected api/listener logs for a specific Telethon exception type (AuthKeyUnregistered / UserDeactivatedBan / SessionRevoked / FloodWait vs. generic traceback).
- **Timeline:** Not a regression — this has "always" behaved this way since the frozen + spambot-check logic existed (no recent deploy correlation).
- **Reproduction:** Deterministic — happens every time a `frozen` sender is manually checked via the spambot endpoint and gets back a "you are blocked/restricted" response.
- **Trigger mechanism:** Manual/operator-initiated request to the spambot check (not the automated reconcile/restriction-check job).

## Current Focus

- hypothesis: CONFIRMED — a manual `/spambot-check` on a `frozen` sender classifies the SpamBot "you are blocked" reply as `suspended`, then irreversibly sets `auth_status='banned'`, conflating Telegram's reversible read-only FREEZE with a permanent BAN. That flips derived status `frozen → error`, drops the sender from all processing, and prompts reauth even though the session is intact.
- test: read the full call path (router `check_spambot` → `telegram_service.check_spambot` → `classify_spambot_text`) and the reconcile twin in listener.py
- expecting: N/A (confirmed by code read)
- next_action: apply fix at both call sites (manual endpoint + reconcile sweep) + add a distinct `frozen` verdict to the classifier

## Evidence

- timestamp: 2026-07-10
  checked: app/services/queue.py:1208-1232 (ACCOUNT_FROZEN handler)
  found: When a send hits a `FROZEN_*` RPC error, the sender is flagged `restriction_status='frozen'`, `restricted_until=recheck_at`, pending paused +24h. `auth_status` is NOT touched (stays `'ok'`). Derived status = `'frozen'`. Session string is untouched.
  implication: The frozen state is a reversible, session-intact restriction, set by a RELIABLE `FROZEN_*` RPC signal. The reconcile sweep is meant to re-check via SpamBot and lift it.

- timestamp: 2026-07-10
  checked: app/services/telegram.py:97-120 (classify_spambot_text + phrase tables)
  found: Classifier buckets are free → limited → suspended → unknown. There is NO `frozen` bucket. `_SPAMBOT_SUSPENDED_PHRASES` includes `"blocked"` and `"заблокирован"`. A frozen account's SpamBot reply (Telegram phrases the 2025 read-only freeze as "your account was blocked …" / «заблокирован») therefore classifies as `suspended`.
  implication: Freeze wording is misclassified as a permanent suspension. Matches user's verbatim report: reply said «заблокированы».

- timestamp: 2026-07-10
  checked: app/routers/senders.py:983-987 (manual /spambot-check verdict handling) + _derive_status:82-100
  found: `verdict == "suspended"` → `sender.auth_status = "banned"` (committed). `_derive_status` returns `'error'` whenever `auth_status != 'ok'` (precedence: error > frozen > limited). So banning flips the derived status from `frozen` to `error`; UI/queue treat `error` as needs-reauth and exclude the sender.
  implication: This is the exact harm — reversible freeze escalated to permanent ban → derived `error` → reauth prompt on a live session, sender stops being processed.

- timestamp: 2026-07-10
  checked: web research (telegram.org freeze feature May 2025; SpamBot behaviour)
  found: Telegram's 2025 "freeze" is an intermediate READ-ONLY enforcement state — the account can still READ and can still MESSAGE @SpamBot (explicitly exempted), and it is REVERSIBLE via appeal. SpamBot reports the freeze with "your account was blocked …" wording. Distinct from a permanent ban/deactivation (which revokes the auth key → AuthKeyUnregistered/UserDeactivatedBan on connect).
  implication: (1) The frozen account CAN run the spambot check (send succeeds), so the reply text is real, not a FROZEN_* exception. (2) A live SpamBot check that succeeded means `get_client` connected → the session authenticates → the account is NOT auth-level banned. Text alone must never escalate frozen→banned; a genuine hard ban surfaces on the AUTH path (SessionAuthError), not via SpamBot text.

- timestamp: 2026-07-10
  checked: app/services/listener.py:1811-1822 (_restriction_reconcile_tick suspended branch)
  found: The automated reconcile sweep has the SAME logic — `verdict == "suspended"` → `auth_status='banned'`. Its SELECT (`restriction_status <> 'none' AND restricted_until <= NOW()`) includes frozen senders, and the doc comment confirms frozen senders are still connected+checked.
  implication: The bug is not unique to the manual endpoint — the automated sweep would inflict the same frozen→banned damage on its own. BOTH call sites must be fixed for a real cure.

## Eliminated

- hypothesis: The Telethon session is actually lost / revoked when frozen.
  evidence: queue.py freeze handler never touches session_string or auth_status; the spambot check itself requires a successfully-connected client (get_client), so the session authenticates. Session is intact — the "needs reauth" prompt is purely a side effect of the derived `error` from the wrongly-set `auth_status='banned'`.
  timestamp: 2026-07-10

- hypothesis: Sending /start to @SpamBot while frozen raises a FROZEN_* error that is mishandled.
  evidence: check_spambot's generic `except Exception` would return status='unknown' (no ban). User reports a real "you are blocked" reply arrived, and Telegram exempts @SpamBot from the read-only freeze — so the send succeeds and the reply text is genuine.
  timestamp: 2026-07-10

## Resolution

root_cause: |
  The SpamBot verdict pipeline conflates Telegram's reversible read-only FREEZE with a permanent BAN.
  `classify_spambot_text` has no `frozen` bucket, so a frozen account's "your account was blocked / заблокирован"
  reply falls into `suspended`. Both the manual endpoint (senders.py) and the reconcile sweep (listener.py)
  then set `auth_status='banned'` on a `suspended` verdict. `_derive_status` maps any `auth_status != 'ok'`
  to `error`, so the sender flips frozen→error, is excluded from processing, and the UI prompts reauth —
  even though the session is fully intact (the freeze is read-only and session-valid; a real ban would fail
  on the auth path instead).
fix: |
  (1) telegram.py: add a distinct `frozen` classification (`_SPAMBOT_FROZEN_PHRASES`), checked BEFORE
      `limited`/`suspended`, so explicit freeze wording is recognised.
  (2) senders.py manual /spambot-check: handle `frozen` verdict (keep restriction_status='frozen',
      auth_status untouched, bump recheck window) AND guard — a `suspended` text verdict must NOT escalate
      a sender already in `restriction_status='frozen'` to `auth_status='banned'`; treat it as still-frozen.
  (3) listener.py reconcile sweep: mirror the same `frozen`-verdict handling + suspended-on-frozen guard.
verification: |
  Unit-level (test-overlay, 39 passed):
    - test_classify_spambot_text_frozen_beats_suspended — freeze wording (even with "blocked"
      keyword) → 'frozen'; pure permanent-ban wording → still 'suspended'.
    - test_restriction_tick_suspended_on_frozen_does_not_ban — frozen sender + 'suspended'
      verdict → banned=0, extended=1, no auth_status='banned' SQL.
    - test_restriction_tick_frozen_verdict_flags_frozen — explicit 'frozen' verdict on a
      spam_limited sender → sets restriction_status='frozen', never bans.
    - test_restriction_tick_bans_on_suspended (updated) — a NON-frozen 'suspended' sender
      still bans (guard is scoped to frozen only).
    - Existing antispam-guard + audit tests still green after receive-path change.
  END-TO-END VERIFIED ON PROD (2026-07-10, api+listener rebuilt & deployed):
    - Live manual /spambot-check run against a REAL frozen sender (sender-8812666662,
      pre-state restriction_status='frozen' auth_status='ok' derived status='frozen').
    - @SpamBot replied verbatim: "Your account was blocked for violations of the
      Telegram Terms of Service based on user reports confirmed by our moderators."
      → contains the generic "blocked" keyword and NO explicit freeze wording, so
      classify_spambot_text alone returns 'suspended'. This is the exact ambiguity that
      caused the original bug — the router's suspended-on-frozen guard re-mapped it to
      'frozen'. (Confirms the guard, not just the phrase table, is load-bearing here.)
    - Endpoint returned HTTP 200 {"status":"frozen","restriction_status_updated":"frozen"}.
    - POST-STATE (DB + derived): restriction_status stayed 'frozen', auth_status stayed
      'ok' (NOT escalated to 'banned'), derived status stayed 'frozen' (NOT 'error'),
      restricted_until bumped forward 09:44→15:55 UTC (recheck window extended). No reauth
      prompt. Sender remains manageable. Bug fixed.
    - Targeted regression: tests/test_spambot_selfcheck.py + tests/test_sender_restriction.py
      = 24 passed via test-overlay against deployed code.
files_changed:
  - app/services/telegram.py (classify_spambot_text: new 'frozen' verdict + phrase table, checked before limited/suspended)
  - app/routers/senders.py (manual /spambot-check: 'frozen' handling + suspended-on-frozen guard)
  - app/services/listener.py (reconcile sweep: suspended-on-frozen guard + 'frozen' verdict flags frozen; unsolicited antispam net flags 'frozen' instead of mislabeling as spam_limited)
  - tests/test_spambot_selfcheck.py (classify frozen-beats-suspended test)
  - tests/test_sender_restriction.py (reconcile guard + frozen-verdict tests; updated ban test)
