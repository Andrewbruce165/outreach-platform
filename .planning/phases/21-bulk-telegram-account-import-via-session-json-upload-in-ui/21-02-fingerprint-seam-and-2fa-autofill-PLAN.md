---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 02
type: execute
wave: 2
depends_on: ["21-01"]
files_modified:
  - app/services/telegram.py
  - app/services/queue.py
  - app/services/warmup.py
  - app/services/listener.py
  - app/services/checker.py
  - app/services/contact_check_worker.py
  - app/routers/senders.py
  - tests/test_account_import.py
autonomous: true
requirements: [IMPT-04, IMPT-10]
must_haves:
  truths:
    - "make_telegram_client(fingerprint=None) is byte-identical to today — the working 13 senders connect unchanged (regression asserted on the built-client kwargs)"
    - "A non-NULL fingerprint dict overrides device/version/locale while lang_pack stays 'tdesktop'"
    - "For a sender row WITH a stored client_fingerprint, EACH automated hot path (queue send, listener reconnect, warmup, checker phone-resolve + control-probe) actually passes THAT fingerprint into make_telegram_client — not None — verified on the built-client kwargs via the Telethon stub"
    - "For a sender row WITH a stored client_fingerprint, EACH Phase-20 profile/2FA path (edit_2fa, update_profile, username, photo, resync, recovery-email) passes THAT fingerprint into get_client — the TelegramService methods accept a fingerprint param, so the router call does not raise TypeError"
    - "api_id/api_hash remain the global settings values — never per-account (D-03)"
    - "For an imported account, the Phase 20 2FA-change endpoint uses the stored (decrypted) password as current_password when the request omits it AND connects with the account fingerprint; plaintext never returned"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "fingerprint override seam on make_telegram_client + get_client + every TelegramService profile/2FA method, strict NULL fallback"
      contains: "fingerprint"
    - path: "app/services/contact_check_worker.py"
      provides: "client_fingerprint selected on the checker-row SELECTs + threaded into check_phones/check_usernames/probe_control"
      contains: "client_fingerprint"
    - path: "app/routers/senders.py"
      provides: "IMPT-10 stored-2FA autofill in update_sender_2fa + fingerprint threaded at every profile/2FA call site"
      contains: "twofa_password_enc"
  key_links:
    - from: "app/services/queue.py (ORM sender) + app/routers/senders.py:882 (ORM sender)"
      to: "app/services/telegram.py get_client"
      via: "pass fingerprint=sender.client_fingerprint alongside proxy=sender.proxy"
      pattern: "fingerprint=sender.client_fingerprint"
    - from: "app/services/listener.py / warmup.py / contact_check_worker.py (raw-SQL DICT rows — NOT ORM)"
      to: "make_telegram_client / get_client / checker _get_client"
      via: "add client_fingerprint to each raw SELECT + dict, then pass fingerprint=row['client_fingerprint'] (via .get for the dict paths)"
      pattern: "client_fingerprint"
    - from: "app/routers/senders.py profile/2FA endpoints"
      to: "app/services/telegram.py TelegramService profile/2FA methods → self.get_client"
      via: "add fingerprint param to each canonical + alias method + pass fingerprint=sender.client_fingerprint at the router call sites"
      pattern: "fingerprint="
    - from: "app/routers/senders.py update_sender_2fa"
      to: "sender.twofa_password_enc"
      via: "decrypt_session fallback for current_password"
      pattern: "decrypt_session"
---

<objective>
Wire the two new `senders` columns from 21-01 into their existing consumers. First: give `make_telegram_client`/`get_client` an optional `fingerprint` override with a STRICT NULL fallback (Task 1), then thread `sender.client_fingerprint` through EVERY place a Telethon client is built for a sender — the automated 24/7 paths (queue, listener, warmup, checker resolve+probe; Task 2) AND the Phase-20 profile/2FA `TelegramService` methods (Task 3) — so imported accounts reconnect with the fingerprint that created them (Pitfall 1) with zero behavior change for the phone-onboarded 13 (Pitfall 2). Second: make the Phase 20 2FA-change endpoint auto-use the stored, encrypted 2FA password for imported accounts (D-06) — while still never returning the plaintext (D-07).

Purpose: Storing a fingerprint (21-04) is useless unless the clients actually connect with it. The threading is materially larger than "add one kwarg": the listener/warmup/checker paths hold a raw-SQL DICT (not an ORM row), so their SELECTs must add the column; and the senders.py profile/2FA endpoints call `TelegramService` METHODS (not `get_client` directly), so every one of those ~16 methods must accept + forward a `fingerprint` param before the router can pass one (else `TypeError`). The 2FA-autofill (Task 3) reconnects-and-mutates an imported account via `edit_2fa`, so it MUST be able to pass that account's fingerprint — which is exactly why the method threading and the autofill land together.
Output: additive `fingerprint` param on the seam + every client-build caller threaded; IMPT-10 read-path in `update_sender_2fa`.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md

<interfaces>
<!-- VERIFIED against the running codebase 2026-07-06. The first draft's call-site map was
     factually wrong (dict-vs-ORM, alias-vs-get_client); this is the corrected map. -->

app/services/telegram.py — the seam (Task 1 target; Task 3 threads the profile/2FA methods):
```python
# line ~152
_CLIENT_FINGERPRINT = {"device_model": "Desktop", "system_version": "Windows 10",
    "app_version": "5.3.1", "lang_code": "ru", "system_lang_code": "ru-RU"}
# line ~233
def make_telegram_client(session, proxy=None, flood_sleep_threshold=60, client_class=TelegramClient):
    client = client_class(session, settings.telegram_api_id, settings.telegram_api_hash, ...,
                          proxy=build_proxy_tuple(proxy), **_CLIENT_FINGERPRINT)
    client._init_request.lang_pack = "tdesktop"
# line ~291
async def get_client(self, sender_slug, sender_id, encrypted_session, proxy=None) -> TelegramClient:
    ... make_telegram_client(StringSession(session_string), proxy=proxy)
```

CLIENT-BUILD PATHS, grouped by HOW the caller holds the sender (the corrected map):

A. ORM `sender` object — has `.client_fingerprint` after 21-01, read it directly:
   - app/services/queue.py:879 → telegram_service.get_client(sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy)
   - app/routers/senders.py:882 → telegram_service.get_client(sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy)  ← the ONLY direct get_client in senders.py

B. Raw-SQL DICT rows — NO `.client_fingerprint` attribute; add the column to the SELECT + returned dict, then read via `.get("client_fingerprint")`:
   - listener.py: `get_active_senders()` (~414) SELECTs `id, slug, phone, session_string, proxy, workspace_id` → builds a dict; `start_client(sender_info: dict)` (~1428) calls `make_telegram_client(...)` at ~1439. `sender_info` is a DICT — there is NO ORM row and NO `.client_fingerprint`.
   - warmup.py: `senders_map` SELECT (~325) SELECTs `id, slug, phone, session_string, lifecycle_status, auth_status, workspace_id, restriction_status, restricted_until`; `_send_via_telethon(from_sender: dict)` (~699) calls `telegram_service.get_client(from_sender["slug"], str(from_sender["id"]), from_sender["session_string"])` at ~714. `from_sender` is a DICT; writing `sender.client_fingerprint` = NameError (no such var). NB proxy is not passed here today — leave that unchanged.
   - contact_check_worker.py (NOT in the first draft's files_modified — ADDED): three raw checker-row SELECTs feed client builds:
       * `_tick` LATERAL (~256): `SELECT id, slug, session_string, proxy` → builds `common = dict(...)` (~351) → `check_phones(**common)` / `check_usernames(**common)`
       * `probe_checker` (~609): `SELECT workspace_id, slug, session_string, proxy` → `probe_control(...)` (~618)
       * `_recover_checkers` (~809): `SELECT id, workspace_id, slug, session_string, proxy` → `probe_control(...)` (~836)
     (The `_probe_cycle` SELECT at ~526 selects only `id` and does NOT build a client — leave it alone.)

C. checker.py primitive-only methods — NO row in scope; thread a `fingerprint` param down the call graph:
   - `_get_client(encrypted_session, proxy, sender_id, sender_slug)` (~218) → `make_telegram_client(...)` at ~240
   - `check_phones` (~365) → `_check_phones_locked` (~470) → `_get_client` (~487)
   - `probe_control` (~389) → `_get_client` (~422)
   - `check_usernames` (~595) → `_check_usernames_locked` (~619) → `_get_client` (~638)

D. TelegramService profile/2FA methods — senders.py calls these METHODS (not `get_client`); they internally call `self.get_client` and DO NOT accept `fingerprint` today (adding `fingerprint=` at the router without adding the param = TypeError):
   Canonical (call self.get_client): `send_message_by_telegram_id`(~1092), `update_profile`(~1153), `check_username`(~1179), `set_username`(~1209), `set_profile_photo`(~1258), `delete_profile_photo`(~1299), `resync_profile`(~1338), `change_2fa_password`(~1411), `start_recovery_email`(~1480), `confirm_recovery_email`(~1537)
   Aliases (delegate to a canonical, no own get_client): `update_username`→set_username(~1219), `upload_profile_photo`→set_profile_photo(~1268), `delete_profile_photos`→delete_profile_photo(~1308), `fetch_profile`→resync_profile(~1363), `edit_2fa`→change_2fa_password(~1423), `set_recovery_email`→start_recovery_email(~1499)
   senders.py router call sites (all read ORM `sender`): 882(get_client direct), 1053(check_username), 1115(update_profile), 1130(update_username), 1196(upload_profile_photo), 1248(delete_profile_photos), 1314(fetch_profile), 1385(edit_2fa — the IMPT-10 path), 1425(set_recovery_email), 1463(confirm_recovery_email)

DEFERRED (documented — NOT in this plan): app/routers/conversations.py:466 calls `send_message_by_telegram_id` with a raw-SQL `row` (row.sender_slug/sender_id/session_string/proxy). The METHOD gains the `fingerprint` param (Task 3, so no signature drift), but this single manual-inbox-send caller keeps passing fingerprint=None. Accepted low risk: one user-initiated message, not the persistent listener connect — D-04 classifies the locale mismatch as a weak antifraud signal, not a logout. Flag for a follow-up if imported-account manual replies show antifraud friction.

Phase 20 2FA endpoint (app/routers/senders.py ~1371, `update_sender_2fa`) — the IMPT-10 seam:
```python
await telegram_service.edit_2fa(sender.slug, str(sender.id), sender.session_string,
    current_password=request.current_password, new_password=request.new_password,
    hint=request.hint or "", proxy=sender.proxy)   # Task 3 adds fingerprint=sender.client_fingerprint
```
encryption helpers: app/services/encryption.py::decrypt_session(encrypted) -> str
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add fingerprint override to make_telegram_client + get_client (strict NULL fallback)</name>
  <read_first>
    - app/services/telegram.py lines 145-258 (_CLIENT_FINGERPRINT, build_proxy_tuple, make_telegram_client)
    - app/services/telegram.py lines 291-320 (get_client body — where make_telegram_client is called)
    - tests/test_account_import.py::test_fingerprint_override_and_strict_fallback (the RED contract from 21-01)
  </read_first>
  <behavior>
    - make_telegram_client(session, fingerprint=None) → client constructed with EXACTLY `{**_CLIENT_FINGERPRINT}` kwargs and `_init_request.lang_pack == 'tdesktop'` (byte-identical to today).
    - make_telegram_client(session, fingerprint={"device_model":"KVM","system_version":"Windows 10 x64","app_version":"6.8.2 x64","lang_code":"en","system_lang_code":"en-US"}) → those keys override the global, but `lang_pack` is STILL forced to 'tdesktop'.
    - api_id/api_hash are always settings.telegram_api_id/telegram_api_hash regardless of fingerprint (D-03).
  </behavior>
  <action>
    In `app/services/telegram.py`:
    - Change the signature to `def make_telegram_client(session, proxy=None, flood_sleep_threshold=60, client_class=TelegramClient, fingerprint: dict | None = None):`.
    - Compute `fp = {**_CLIENT_FINGERPRINT, **(fingerprint or {})}` and pass `**fp` to the client constructor (replacing `**_CLIENT_FINGERPRINT`). `None` → `{**_CLIENT_FINGERPRINT}` exactly (D-02 strict fallback).
    - Keep `client._init_request.lang_pack = "tdesktop"` UNCONDITIONALLY after construction (D-04 — do NOT drop it; it is the field that terminates sessions when empty).
    - Do NOT touch `settings.telegram_api_id/telegram_api_hash` — they stay global (D-03).
    - Update the docstring to note the optional per-account fingerprint override + strict NULL fallback.
    - In `TelegramService.get_client`, add `fingerprint: dict | None = None` as the last param and pass it through: `make_telegram_client(StringSession(session_string), proxy=proxy, fingerprint=fingerprint)`.
    Do NOT change any call site in this task (that is Task 2/3) — the new param defaults to None so all existing callers keep today's behavior.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py::test_fingerprint_override_and_strict_fallback -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "fingerprint: dict | None = None" app/services/telegram.py` succeeds (present on BOTH make_telegram_client and get_client)
    - `grep -q "fp = {\*\*_CLIENT_FINGERPRINT" app/services/telegram.py` succeeds
    - `grep -c "lang_pack = \"tdesktop\"" app/services/telegram.py` unchanged (still exactly one unconditional assignment in make_telegram_client)
    - `grep -q "settings.telegram_api_id" app/services/telegram.py` still present in make_telegram_client (api_id stays global)
    - test_fingerprint_override_and_strict_fallback passes (GREEN)
  </acceptance_criteria>
  <done>make_telegram_client + get_client accept an optional fingerprint dict, NULL is byte-identical to today, non-NULL overrides device/version/locale while forcing lang_pack='tdesktop', api_id stays global.</done>
</task>

<task type="auto">
  <name>Task 2: Thread client_fingerprint through the automated hot paths (queue, listener, warmup, checker + worker) + kwargs/regression tests</name>
  <read_first>
    - app/services/queue.py line 879 (ORM sender get_client call)
    - app/services/listener.py lines 400-436 (get_active_senders raw SELECT + dict) and 1428-1443 (start_client + make_telegram_client)
    - app/services/warmup.py lines 320-362 (senders_map raw SELECT + dict) and 699-718 (_send_via_telethon get_client call — note: from_sender DICT, no proxy passed today, NO `sender` var)
    - app/services/checker.py lines 218-243 (_get_client + make_telegram_client at ~240), 365-388 (check_phones), 389-422 (probe_control), 470-490 (_check_phones_locked), 595-641 (check_usernames + _check_usernames_locked)
    - app/services/contact_check_worker.py lines 246-357 (_tick LATERAL SELECT + common dict), 605-624 (probe_checker SELECT + probe_control call), 806-842 (_recover_checkers SELECT + probe_control call)
  </read_first>
  <action>
    Thread the per-account fingerprint through EVERY automated place a client is built for a sender. Each edit is small and localized; the volume (5 files) is why this is its own task. NULL fingerprint (all 13 phone-onboarded senders) resolves to Task 1's strict fallback → zero behavior change for the working pool.

    A. ORM path — read `sender.client_fingerprint` directly:
    - `app/services/queue.py:879` → add `fingerprint=sender.client_fingerprint` to the get_client call (sender is an ORM Sender with the 21-01 column).

    B. Raw-SQL DICT paths — add the column to the SELECT + dict, then read via `.get`:
    - `app/services/listener.py`:
      * In `get_active_senders()` (~414) add `client_fingerprint` to the SELECT column list, and add `"client_fingerprint": r[6]` (shift the index to match the new column position) to the returned dict.
      * At `make_telegram_client(...)` (~1439) add `fingerprint=sender_info.get("client_fingerprint")` (sender_info is a DICT — use `.get`, NOT attribute access). This is the persistent 24/7 reconnect path (BLOCKER 1) — imported accounts reconnect here forever.
    - `app/services/warmup.py`:
      * In the `senders_map` SELECT (~325) add `client_fingerprint` to the column list, and add `"client_fingerprint": r[9]` (match the new index) to each dict entry (~348).
      * At the `get_client(...)` call in `_send_via_telethon` (~714) add `fingerprint=from_sender.get("client_fingerprint")` (BLOCKER 2 — `from_sender` DICT; there is NO `sender` var, so `sender.client_fingerprint` would NameError). Do NOT add proxy here (out of scope — behavior-preserving).

    C. checker.py — thread a `fingerprint` param down the primitive-only call graph:
    - `_get_client(...)` (~218): add `fingerprint: dict | None = None` param and pass `fingerprint=fingerprint` into `make_telegram_client(...)` at ~240.
    - `check_phones(...)` (~365): add `fingerprint: dict | None = None`; forward it into `_check_phones_locked(...)`.
    - `_check_phones_locked(...)` (~470): add the param; pass `fingerprint=fingerprint` into `_get_client(...)` at ~487.
    - `probe_control(...)` (~389): add the param; pass `fingerprint=fingerprint` into `_get_client(...)` at ~422.
    - `check_usernames(...)` (~595): add the param; forward it into `_check_usernames_locked(...)`.
    - `_check_usernames_locked(...)` (~619): add the param; pass `fingerprint=fingerprint` into `_get_client(...)` at ~638.

    D. contact_check_worker.py — select the checker row's fingerprint and pass it to the checker methods (BLOCKER 3 — per D-17 imported checkers MUST reconnect with their fingerprint; the project's checker-throttle history makes this non-optional):
    - `_tick` LATERAL SELECT (~256): change `SELECT id, slug, session_string, proxy` → `SELECT id, slug, session_string, proxy, client_fingerprint`; then in the `common = dict(...)` (~351) add `fingerprint=first.client_fingerprint` (threads into both `check_phones(**common)` and `check_usernames(**common)`).
    - `probe_checker` SELECT (~609): change to `SELECT workspace_id, slug, session_string, proxy, client_fingerprint`; then at the `probe_control(...)` call (~618) add `fingerprint=row.client_fingerprint`.
    - `_recover_checkers` SELECT (~809): change to `SELECT id, workspace_id, slug, session_string, proxy, client_fingerprint`; then at the `probe_control(...)` call (~836) add `fingerprint=r.client_fingerprint`.

    E. Tests in `tests/test_account_import.py` (do NOT just grep for the literal — prove the value FLOWS):
    - `test_null_fingerprint_matches_global` — build via `make_telegram_client(session, fingerprint=None)` and assert the constructor kwargs equal today's `_CLIENT_FINGERPRINT` and `lang_pack=='tdesktop'` (regression — the 13 are unchanged).
    - `test_checker_get_client_threads_fingerprint` — monkeypatch `app.services.checker.make_telegram_client` to a factory that CAPTURES the `fingerprint=` kwarg and returns a stub client (connect/is_user_authorized/disconnect AsyncMock'd); call `CheckerService()._get_client(<enc_session>, proxy=None, fingerprint={"device_model":"KVM",...})` and assert the captured kwarg == that dict. Then call `_get_client(..., fingerprint=None)` and assert the captured kwarg is None (proves the seam forwards, and NULL stays NULL).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -x -q && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/ -k "queue or warmup or checker or listener or contact_check or sender" -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "fingerprint=sender.client_fingerprint" app/services/queue.py` succeeds
    - `grep -q "fingerprint=sender_info.get(\"client_fingerprint\")" app/services/listener.py` succeeds AND `get_active_senders`'s SELECT now lists `client_fingerprint` (grep the SELECT block)
    - `grep -q "fingerprint=from_sender.get(\"client_fingerprint\")" app/services/warmup.py` succeeds AND the senders_map SELECT now lists `client_fingerprint`
    - `app/services/checker.py` `_get_client` signature contains `fingerprint: dict | None = None`, and `check_phones` / `probe_control` / `_check_phones_locked` / `check_usernames` / `_check_usernames_locked` each accept + forward it (`grep -c "fingerprint" app/services/checker.py` returns >= 12)
    - `grep -c "client_fingerprint" app/services/contact_check_worker.py` returns >= 4 (3 SELECTs + the `common` dict) AND `grep -q "fingerprint=first.client_fingerprint" app/services/contact_check_worker.py` and `grep -c "fingerprint=row.client_fingerprint\|fingerprint=r.client_fingerprint" app/services/contact_check_worker.py` >= 2 succeed
    - test_null_fingerprint_matches_global + test_checker_get_client_threads_fingerprint pass (GREEN)
    - The existing queue/warmup/checker/listener/contact_check/sender suites all still pass (no regression to the working 13)
  </acceptance_criteria>
  <done>Every automated client-build path (queue ORM, listener + warmup dict SELECTs, checker resolve + control-probe + recovery via contact_check_worker) threads the checker/sender's client_fingerprint; NULL resolves to today's behavior (regression + kwargs-capture tests prove it); no existing test breaks.</done>
</task>

<task type="auto">
  <name>Task 3: Thread fingerprint through the Phase-20 TelegramService profile/2FA methods + senders.py, then IMPT-10 2FA autofill</name>
  <read_first>
    - app/services/telegram.py lines 1067-1547 (the 10 canonical self.get_client methods + 6 delegating aliases — see interfaces map D)
    - app/routers/senders.py lines 882 (direct get_client), 1053/1115/1130/1196/1248/1314/1385/1425/1463 (TelegramService method calls), 1366-1408 (update_sender_2fa)
    - app/services/encryption.py (decrypt_session)
    - .planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md (D-06 auto-fill, D-07 never-return)
  </read_first>
  <action>
    The 2FA autofill (Part C) reconnects-and-mutates an IMPORTED account via `edit_2fa` → `change_2fa_password` → `self.get_client`. For that connect to use the account's own fingerprint (the phase's Key Risk), the method chain must accept + forward a `fingerprint` param FIRST — so Parts A/B are prerequisites of C, and all three land here.

    A. `app/services/telegram.py` — add `fingerprint: dict | None = None` to every method that touches `self.get_client`, and forward it:
    - Canonical methods (add the param, then change `self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy)` → `self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)`): `send_message_by_telegram_id`, `update_profile`, `check_username`, `set_username`, `set_profile_photo`, `delete_profile_photo`, `resync_profile`, `change_2fa_password`, `start_recovery_email`, `confirm_recovery_email`.
    - Alias methods (add the param, then forward it in the delegating call): `update_username`→set_username, `upload_profile_photo`→set_profile_photo, `delete_profile_photos`→delete_profile_photo, `fetch_profile`→resync_profile, `edit_2fa`→change_2fa_password, `set_recovery_email`→start_recovery_email.
    - Place `fingerprint` as a keyword-only param (after the existing `*,` where present, alongside `proxy`) so callers pass it by name.

    B. `app/routers/senders.py` — pass `fingerprint=sender.client_fingerprint` at every profile/2FA call site (sender is an ORM Sender with the 21-01 column):
    - 882 `get_client` (direct), 1053 `check_username`, 1115 `update_profile`, 1130 `update_username`, 1196 `upload_profile_photo`, 1248 `delete_profile_photos`, 1314 `fetch_profile`, 1385 `edit_2fa`, 1425 `set_recovery_email`, 1463 `confirm_recovery_email`. (Verify the exact line numbers on read — they shift as the file changes; the method NAMES above are the anchor.)
    - Do NOT touch app/routers/conversations.py in this plan (its `send_message_by_telegram_id` caller is the documented DEFERRED item — the method still gains the param in Part A, so no signature drift).

    C. IMPT-10 — `update_sender_2fa` (~1383) stored-2FA autofill (D-06) + fingerprint-safe connect, never returning plaintext (D-07):
    ```python
    from app.services.encryption import decrypt_session
    current_pw = request.current_password
    if current_pw is None and getattr(sender, "twofa_password_enc", None):
        current_pw = decrypt_session(sender.twofa_password_enc)  # IMPT-10 (D-06): imported-account autofill, server-side only
    ```
    Call `edit_2fa(..., current_password=current_pw, ..., proxy=sender.proxy, fingerprint=sender.client_fingerprint)` (the Part-B threading at site 1385 supplies the fingerprint). Do NOT add `current_pw` (or any decrypted password) to the response body, to logs, or to any error detail — the endpoint still returns only `{"success": True}` (D-07). Do NOT change the recovery-email endpoints' behavior beyond adding the fingerprint kwarg.

    D. Tests in `tests/test_account_import.py`:
    - `test_2fa_autofill_uses_stored_password` — create an imported sender with a Fernet-encrypted `twofa_password_enc` AND a stored `client_fingerprint`; monkeypatch `telegram_service.edit_2fa` to capture kwargs; POST `/senders/{slug}/2fa` with `current_password=None`; assert `edit_2fa` received (a) the decrypted stored password as `current_password` AND (b) `fingerprint == <the stored fingerprint dict>`; assert the plaintext appears NOWHERE in the JSON response.
    - `test_profile_method_accepts_fingerprint` — call one TelegramService profile method (e.g. `telegram_service.update_profile(slug, sid, enc, req, proxy=None, fingerprint={"device_model":"KVM",...})`) with `get_client` monkeypatched to capture kwargs; assert the captured `fingerprint` == that dict (proves the router can pass one without TypeError).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -k "2fa or profile_method" -x -q && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/ -k "sender or profile or 2fa" -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "fingerprint: dict | None = None" app/services/telegram.py` returns >= 18 (make_telegram_client + get_client from Task 1, plus the 10 canonical + 6 alias methods)
    - Every canonical method's `self.get_client(...)` call now includes `fingerprint=fingerprint` (`grep -c "fingerprint=fingerprint" app/services/telegram.py` returns >= 10) and each alias forwards it in its delegating call
    - `grep -c "fingerprint=sender.client_fingerprint" app/routers/senders.py` returns >= 10 (the 882 direct get_client + 9 profile/2FA method calls)
    - `grep -q "decrypt_session(sender.twofa_password_enc)" app/routers/senders.py` and `grep -q "current_password=current_pw" app/routers/senders.py` succeed
    - `update_sender_2fa` still returns exactly `{"success": True}` (grep confirms no password field added to the return)
    - test_2fa_autofill_uses_stored_password asserts BOTH the decrypted password AND the fingerprint reached edit_2fa, and that the plaintext is NOT in the response; test_profile_method_accepts_fingerprint passes
    - The existing sender/profile/2fa suites still pass (no TypeError, no regression)
  </acceptance_criteria>
  <done>Every TelegramService profile/2FA method accepts + forwards a fingerprint param, senders.py passes sender.client_fingerprint at all 10 profile/2FA call sites, and update_sender_2fa falls back to the stored decrypted twofa_password_enc as current_password while connecting under the account fingerprint — plaintext used only server-side, never returned or logged.</done>
</task>

</tasks>

<verification>
- `make_telegram_client(fingerprint=None)` is byte-identical to pre-phase behavior (regression test asserts on constructor kwargs).
- Every automated hot path (queue ORM, listener + warmup dict SELECTs, checker resolve + probe + recovery) threads the row's client_fingerprint; the 13 phone-onboarded senders (NULL fingerprint) are unaffected.
- Every Phase-20 profile/2FA TelegramService method accepts + forwards fingerprint; senders.py passes sender.client_fingerprint at all 10 sites; the router calls do not raise TypeError.
- 2FA autofill uses the stored password server-side only AND connects with the account fingerprint; never returned.
- Full targeted suites (queue/warmup/checker/listener/contact_check/sender/profile/2fa + account_import) green.
</verification>

<success_criteria>
- IMPT-04 seam live with strict fallback + threaded through every automated AND profile/2FA client-build path, no regression to the working pool.
- IMPT-10 read-path live, fingerprint-correct, and secret-safe.
</success_criteria>

<output>
After completion, create `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-02-SUMMARY.md`
</output>
