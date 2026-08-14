---
status: resolved
trigger: "send-путь игнорирует username, указанный при импорте — резолвит по телефону, а контакты скрыты от поиска по номеру; очередь пересоздаёт задачи и жжёт ImportContacts"
created: 2026-08-14
updated: 2026-08-14
---

## Symptoms

DATA_START
**Expected behavior:**
Контакт, импортированный с username, должен резолвиться и получать сообщения через этот username (владелец дал его именно потому, что телефон скрыт приватностью). Пользователь явно требует: при постановке в очередь identity-ключ должен строиться по правилу «username побеждает», телефон — фолбэк.

**Actual behavior:**
1. При CSV-импорте контакт с username сразу помечается tg_status='registered' и минует чекер (contacts.py:129-144, комментарий «по username мы можем написать напрямую»). Все 145 контактов кампании — registered, у всех есть username.
2. При постановке в очередь identity-ключ строится по правилу «телефон побеждает» (phone.py:73) — в message_queue.recipient_phone попадает телефон, username теряется.
3. Лестница резолва при отправке (telegram.py:687-748) на tier-2 читает только tg_username_resolved (пишется только contact_check_worker'ом). contacts.username она не читает вообще. Чекер для этих контактов не запускался (зашорчены как registered на импорте) → tg_username_resolved = NULL → tier-2 пропущен.
4. Падает в tier-3 ImportContacts по телефону; у получателей приватность «кто может найти по номеру» ≠ все → пусто → is_registered=False → ложная ошибка «Номер +7… не зарегистрирован в Telegram».

**Error messages:**
- Лог: `tier-3 ImportContacts (registered, no live username)`
- Ошибка отправки: «Номер +7… не зарегистрирован в Telegram» (ложь — аккаунт есть, просто скрыт по номеру)

**Timeline:**
Проявилось на текущей кампании (145 контактов, все с username). 13-08 в 11:17 успешно ушли 5 контактов (Wirbelwind84, gansteroid, Oleg_Y1, fp_gt и др.) — у них tg_username_resolved был заполнен старым прогоном чекера → tier-2 сработал. Падают ровно те ~11, у кого захваченного хэндла нет.

**Reproduction:**
Импортировать CSV-контакт с телефоном + username, у владельца приватность по номеру ≠ все → контакт минует чекер, в очередь идёт по телефону, tier-2 пропускается (tg_username_resolved NULL), tier-3 ImportContacts пуст → false «not registered».

**Side effect (активный, жжёт ресурсы):**
Очередь пересоздаёт эти задачи (по 3 ряда на телефон за сегодня) и жжёт ImportContacts на сендерах — десятки импортов в час по одним и тем же номерам. Тот же хвост, что в инциденте 13-08 (17 «запаркованных» телефонов, см. memory project-resolve-carousel-incident-fixed / коммит 44222d8).

**User-requested fix direction:**
При постановке в очередь identity-ключ должен строиться по правилу «username побеждает», потом телефон и т.д. Плюс лестница резолва должна использовать contacts.username (заявленный при импорте), а не только tg_username_resolved.
DATA_END

## Current Focus

hypothesis: CONFIRMED — the send-time resolve ladder never reads `contacts.username`; tier-2 only reads the checker-captured `tg_username_resolved`, which the CSV import shortcut guarantees is NULL for username-imported contacts. Phone-privacy contacts therefore fall to tier-3 ImportContacts, return empty, and are reported as a false "не зарегистрирован".
test: DONE — code read (contacts.py:144, phone.py:65-79, telegram.py:585-748, queue.py:1382-1410/1554-1629/1675-1711, campaign_enqueue.py:342-486) + prod DB reads on campaign 24658b65.
expecting: (met) contacts with a declared username but NULL tg_username_resolved fail; the 17 rows manually backfilled 2026-08-13 10:09 sent successfully at 11:17.
outcome: FIXED, DEPLOYED, VERIFIED IN PROD (2026-08-14 14:18-14:30 UTC). Variant A (ladder-only) shipped as
  commit d3bfe77; api rebuilt 14:18; 9 stuck queue rows requeued at 14:20; 3 previously-doomed contacts sent
  within 60s of the requeue via the declared handle (tier-2), 1 confirmed genuinely stale, 5 pacing.
next_action: DECIDED (variant A, ladder-only). Implement tier-2 COALESCE(tg_username_resolved, contacts.username)
  with a mandatory garbage-handle guard (`sanitize_username`), targeted tests via test-overlay, deploy api
  (+listener if it imports the path), then the guarded prod requeue of the 21 doomed contacts + the "None"
  username cleanup. Identity key stays UNTOUCHED (no key flip, no CCA/conversations migration).

## Evidence

- timestamp: 2026-08-14T14:00Z
  checked: app/routers/contacts.py:129-144 (line numbers accurate)
  found: `tg_status = "registered" if rec.get("username") else default_tg_status` — any imported contact carrying a username is stamped 'registered' and the ContactCheckWorker (which selects only tg_status='pending') never touches it.
  implication: LINK 1 CONFIRMED. `tg_username_resolved` and `tg_checked_at` stay NULL forever for username-imported contacts.

- timestamp: 2026-08-14T14:00Z
  checked: app/utils/phone.py:65-79 `contact_identity_key`
  found: `if phone: return phone` before the username branch — docstring says "Phone wins when present". The same rule is duplicated as SQL in campaign_enqueue.py:352/361 (`COALESCE(phone, '@' || username)`).
  implication: LINK 2 CONFIRMED. The identity key written to message_queue.recipient_phone / campaign_contact_assignments.contact_phone / conversations.contact_phone is the phone whenever a phone exists.

- timestamp: 2026-08-14T14:00Z
  checked: app/services/telegram.py:585-616 `_load_contact_verdict` + 652-748 `resolve_contact`
  found: the verdict SELECT is `SELECT tg_status, tg_username_resolved FROM contacts …` — `contacts.username` is NOT in the query. Tier-2 (line 693) uses only `verdict["captured_username"]`. Tier-3 (line 711) is gated on `tg_status == 'registered'` — which the import shortcut always satisfies.
  implication: LINK 3 CONFIRMED. Declared usernames are invisible to the send path. Combined with link 1 this guarantees tier-2 is skipped for exactly the contacts that were imported with a username.

- timestamp: 2026-08-14T14:00Z
  checked: grep for writers of `tg_username_resolved` across app/, scripts/, migrations/
  found: only app/services/contact_check_worker.py:1062 (the `is_registered` branch) and the DDL in migrations/013_phase2.sql.
  implication: LINK 4 CONFIRMED. The checker is the only writer — and the import shortcut deliberately routes these contacts around the checker. Closed loop.

- timestamp: 2026-08-14T14:00Z
  checked: prod DB — campaign 24658b65 ("C&C - холодный аутрич"), folder 6595094a
  found: 145 contacts, ALL tg_status='registered', 145 have `username`, 140 have `phone`, only 17 have `tg_username_resolved`. `tg_checked_at` is NULL for all 145 and `tg_resolved_by`/`tg_confidence` are NULL for all 145 — the checker never ran on any of them.
  implication: matches the shortcut exactly. 128 contacts enter the send path with no tier-2 input.

- timestamp: 2026-08-14T14:00Z
  checked: provenance of the 17 populated `tg_username_resolved` values
  found: all 145 rows created 2026-08-11 17:19:14; the 17 share an identical `updated_at` of 2026-08-13 10:09:29 with `tg_telegram_id` NULL, `tg_checked_at` NULL, `tg_resolved_by` NULL. No code path produces that shape — it is a manual bulk `UPDATE contacts SET tg_username_resolved = username`.
  implication: CORRECTS the symptom timeline ("старый прогон чекера" is wrong — it was a manual operator backfill). This is the strongest evidence available: the backfill at 10:09 is followed by successful sends at 11:17 to exactly those handles (Wirbelwind84, gansteroid, Oleg_Y1, fp_gt, Titov_pavel_a …). Copying `username` → `tg_username_resolved` IS the fix, proven live in prod.

- timestamp: 2026-08-14T14:00Z
  checked: failing contacts (message_queue, campaign 24658b65)
  found: 21 distinct phones with `RECIPIENT_NOT_IN_TELEGRAM`, 3-5 queue rows each. Sampled 12: 8 have a declared `username` with NULL `tg_username_resolved` (mirandd1966, foksana2004, vsmartex, pvm1979, botticelli_7, evgeny167, dorian_brey, fluk9). Campaign totals: 112 sent / 58 failed / 24 pending / 17 cancelled.
  implication: the failure set is precisely "declared username present, captured username absent". The 112 successes are numbers whose privacy allows a phone import (tier-3 works), so the bug only bites privacy-hidden numbers.

- timestamp: 2026-08-14T14:00Z
  checked: contacts_cache for the folder's phones
  found: 0 rows with `is_registered = false`.
  implication: NO cache poisoning this time — telegram.py:739/748 deliberately does not cache a negative. Distinguishes this from the 2026-07-27 incident; no cache purge needed.

- timestamp: 2026-08-14T14:00Z
  checked: the re-enqueue mechanics — queue.py:1382-1410, 1554-1629, 1675-1711 and campaign_enqueue.py:342-376
  found: a NOT_REGISTERED reroutes across up to RESOLVE_ROTATION_CAP=3 distinct senders (each doing its own ImportContacts, attempts reset to 0 on each hop), then _fail_item retries to MAX_ATTEMPTS=3, then terminally fails and DELETEs the campaign_contact_assignments row while `failed_cnt < COLD_FAIL_RELEASE_CAP=3`, making the contact eligible for the enqueue worker again. ≈6 ImportContacts per queue row × up to 4 rows ≈ 20+ resolves burned per doomed contact.
  implication: LINK 5 CONFIRMED. The "3 rows/phone" the user observed is literally COLD_FAIL_RELEASE_CAP. This is the WR-15 bound working as designed — the loop is bounded, not infinite, and the fix for the burn is to stop the contacts failing, not to re-tune the caps.

- timestamp: 2026-08-14T14:00Z
  checked: current burn rate + CCA state
  found: terminal fails today 08-14: 8 (10:00), 12 (12:00), 14 (13:00). All 145 CCA rows are present (cap reached for the doomed phones → CCA retained → no further re-enqueue for them). 24 pending rows remain: 7 belong to already-failing phones, 17 are first attempts.
  implication: the harm is self-limiting via WR-15 — it will stop on its own once the 24 pending rows drain. An emergency prod write is NOT required; the remaining cost is ~1-2 more hours of resolves.

- timestamp: 2026-08-14T14:00Z
  checked: blast radius of flipping the identity key to "username wins"
  found: 242 campaign_contact_assignments rows and 157 conversations rows are keyed by a phone that belongs to a contact which ALSO has a username. Both enqueue dedup predicates (campaign_enqueue.py:352, 361) match on that key. Repo-wide, 868/1378 conversations and 1307/2018 contacts_cache rows already use '@' keys.
  implication: a naive key flip orphans those 242 CCA + 157 conversation rows → the dedup `NOT IN` misses → already-contacted people (112 in this campaign alone) get enqueued a SECOND time. The user's requested direction #1 is NOT a safe drop-in and needs a data migration or must be dropped.

- timestamp: 2026-08-14T14:00Z
  checked: data hygiene of the folder
  found: contact +79222272580 has the literal string `"None"` as its username (CSV import artifact).
  implication: minor, but a declared-username tier would try to resolve `@None`. Handle a stale/garbage declared handle by falling through to tier-3 (never finalize as not_registered) — same semantics as D-09.

- timestamp: 2026-08-14T14:20Z
  checked: prod state of the doomed set right before the requeue (campaign 24658b65)
  found: 22 distinct phones now carry a `не зарегистрирован` terminal fail (the investigation
    snapshot said 21 — the bug kept failing contacts while the checkpoint was open). ALL 22 have a
    usable declared `contacts.username`. Of the 22: 6 already have a `sent` row (the manual-backfill
    winners of 08-13 — their failed rows predate their successful send), 7 already have a live
    `pending` row (they self-heal under the fixed code), leaving 9 genuinely stuck (0 sent,
    0 pending, CCA retained at the WR-15 cap).
  implication: requeuing all 22 would double-message 6 people and duplicate 7 pending rows. The
    requeue was scoped to the 9 verified-stuck phones — strictly smaller than the approved action,
    and no doomed contact is left behind (9 requeued + 7 already pending + 6 already delivered).

- timestamp: 2026-08-14T14:21Z
  checked: LIVE post-deploy behaviour (docker logs outreach-platform-api + message_queue)
  found: tier-2 now fires on declared handles — `Contact @evgeny167 … ResolveUsernameRequest`,
    `@nlukoyan`, `@pvm1979` — and +79203297097 / +79630389491 / +79161444107 SENT at 14:20:58,
    14:21:01 and 14:21:14, i.e. within a minute of the requeue. Those three phones had been
    permanently failing with "не зарегистрирован" since 08-13. `contacts.tg_username_resolved` +
    `tg_telegram_id` were auto-persisted for exactly those three (folder captured-count 17 → 20).
  implication: the fix is verified in production against the original symptom, not just in tests.

- timestamp: 2026-08-14T14:24Z
  checked: the one requeued contact that failed again (+79826587939, @marveltu)
  found: declared == captured == "marveltu"; tier-2 ResolveUsername returned stale → fell through to
    tier-3 ImportContacts (logged `tier-3 ImportContacts (registered, no live username)`) → empty →
    failed after 3 bounded attempts. Nothing was cached/finalized as a negative.
  implication: correct D-09 behaviour, NOT a regression — this handle is genuinely gone. It also
    explains its 08-13 12:13 failure despite the manual backfill.

## Eliminated

- hypothesis: contacts_cache poisoning (a stale cross-sender `is_registered=false`) is short-circuiting the resolve, as in the 2026-07-27 incident.
  evidence: 0 rows with `is_registered=false` for any of the folder's phones; telegram.py:739/748 explicitly returns without caching the negative. The failures carry no `from_cache` flag (they reroute, which queue.py:1396 skips for cache-sourced verdicts).
  timestamp: 2026-08-14T14:00Z

- hypothesis: the checker ran, resolved these numbers as not_registered, and the send path is honouring that verdict.
  evidence: `tg_checked_at`, `tg_resolved_by`, `tg_confidence` are NULL on all 145 contacts; every row is `tg_status='registered'` from the import shortcut. The checker never saw them.
  timestamp: 2026-08-14T14:00Z

- hypothesis: the WR-15 / RESOLVE_ROTATION_CAP bounds from the 2026-08-13 carousel fix are not working, causing an unbounded re-enqueue loop.
  evidence: the caps ARE firing — every doomed phone stops at 3-5 rows and its CCA row is now retained (all 145 CCA rows present). The observed "3 rows/phone" IS the cap. The burn is bounded and self-limiting.
  timestamp: 2026-08-14T14:00Z

- hypothesis: the 5 successful sends on 2026-08-13 came from an earlier checker run that had captured those handles.
  evidence: the 17 backfilled rows share one identical `updated_at` (10:09:29) with NULL tg_telegram_id / tg_checked_at / tg_resolved_by — a shape no code path produces. It was a manual SQL backfill by the operator.
  timestamp: 2026-08-14T14:00Z

## Resolution

root_cause: |
  The CSV import shortcut (app/routers/contacts.py:144) stamps every username-bearing
  contact as tg_status='registered' so it bypasses the ContactCheckWorker — which is the
  ONLY writer of contacts.tg_username_resolved (app/services/contact_check_worker.py:1062).
  The send-time resolve ladder's tier-2 reads ONLY tg_username_resolved
  (app/services/telegram.py:604, `_load_contact_verdict`) and never reads contacts.username.
  So for exactly the contacts the shortcut created, tier-2 is guaranteed to be skipped, and
  the ladder falls to tier-3 ImportContacts by phone. For recipients whose "who can find me
  by number" privacy excludes strangers, ImportContacts returns empty → false
  RECIPIENT_NOT_IN_TELEGRAM, even though the operator supplied a working @handle at import.
  The resource burn is the (correctly bounded) WR-15/RESOLVE_ROTATION_CAP retry machinery
  amplifying each doomed contact into ~20 ImportContacts calls.
  Proven live: a manual backfill of username → tg_username_resolved on 17 rows at
  2026-08-13 10:09 made those exact contacts send successfully at 11:17.
fix: |
  Variant A (ladder-only, commit d3bfe77) — the identity key was deliberately left UNTOUCHED
  (no key flip, no campaign_contact_assignments / conversations migration, phone.py:73 and the
  COALESCE(phone,'@'||username) enqueue predicates unchanged).

  1. app/services/telegram.py::_load_contact_verdict now also selects `contacts.username`
     (the operator-declared handle) alongside tg_status + tg_username_resolved.
  2. resolve_contact tier-2 resolves COALESCE(tg_username_resolved, contacts.username):
     checker-captured handle first, import-declared handle second. A stale handle still falls
     through to tier-3 (D-09) and is NEVER finalized as not_registered.
  3. On a successful resolve of a DECLARED handle, _persist_resolved_handle writes
     tg_username_resolved + tg_telegram_id so the next send is cheap. Checker-provenance columns
     (tg_confidence / tg_resolved_by / tg_probe_state / tg_status) are deliberately NOT written —
     a sender-side resolve is not a checker verdict.
  4. New guard app/utils/phone.py::sanitize_username rejects placeholder junk a CSV export emits
     (the literal "None"/"null"/"n/a"/…), empty/whitespace and implausible handles. A rejected
     handle means "skip the tier-2 attempt", never "not registered".
  No migration was needed (both columns already exist).

  Prod data actions (backup outreach_20260814_142014.sql.gz taken first):
  - 9 verified-stuck queue rows (0 sent + 0 pending per phone, latest failed row only) re-pended
    on campaign 24658b65 → failed 58→49, pending 24→33. The 6 already-sent and 7 already-pending
    phones of the 22-phone NOT_REGISTERED set were deliberately excluded (double-message risk).
  - contact f2437f61 (+79222272580) whose username was the literal string "None" → username = NULL.
verification: |
  Targeted tests via the test-overlay (docker compose -f docker-compose.yml -f docker-compose.test.yml
  run --rm api pytest …): 146 passed across test_send.py, test_username_sanitize.py,
  test_phone_normalization.py, test_poisoned_cache_recency.py, test_send_file_blob.py,
  test_csv_import.py, test_contacts.py, test_batch_g_identity.py. New coverage:
    - test_declared_username_resolves_and_is_persisted (tier-2 on the declared handle + promotion,
      checker-provenance columns stay NULL)
    - test_captured_username_wins_over_declared (COALESCE order)
    - test_garbage_declared_username_skips_tier2_not_finalized ("None" never resolved, tier-3 still runs)
    - test_stale_declared_username_falls_through_to_import (D-09 preserved, dead handle not promoted)
    - 32 sanitize_username unit cases
  One pre-existing unrelated failure (test_warmup_worker.py::test_restricted_sender_excluded, WARM-14
  RED scaffold) was confirmed failing on a clean tree via git stash — not caused by this change.

  Deploy: docker compose up -d --build api (2026-08-14 14:18 UTC), clean startup, all workers up.
  The listener was NOT rebuilt on purpose: it imports app.services.telegram but never calls
  resolve_contact (the queue worker runs inside api), so a listener restart would only churn live
  Telegram sessions for no benefit.

  LIVE PROD VERIFICATION (the original symptom, not a proxy): +79203297097 (@evgeny167),
  +79630389491 (@nlukoyan) and +79161444107 (@pvm1979) — three contacts that had been permanently
  failing with "Номер +7… не зарегистрирован в Telegram" — were SENT at 14:20:58 / 14:21:01 /
  14:21:14 UTC, each preceded by a `ResolveUsernameRequest` on the DECLARED handle in the api log,
  and each got tg_username_resolved + tg_telegram_id persisted automatically. No new false
  not_registered appeared; the single repeat failure (@marveltu) is a genuinely stale handle that
  correctly fell through to tier-3.
files_changed:
  - app/services/telegram.py (tier-2 COALESCE(captured, declared) + _persist_resolved_handle + verdict SELECT)
  - app/utils/phone.py (new sanitize_username guard)
  - app/routers/contacts.py (docstring: why the import shortcut forces the ladder to read contacts.username)
  - tests/test_send.py (4 new ladder tests + declared-username seed support)
  - tests/test_username_sanitize.py (new, 32 cases)
