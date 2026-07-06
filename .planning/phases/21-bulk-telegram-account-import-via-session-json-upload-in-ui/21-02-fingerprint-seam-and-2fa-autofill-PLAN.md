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
  - app/routers/senders.py
  - tests/test_account_import.py
autonomous: true
requirements: [IMPT-04, IMPT-10]
must_haves:
  truths:
    - "make_telegram_client(fingerprint=None) is byte-identical to today — the working 13 senders connect unchanged"
    - "A non-NULL fingerprint dict overrides device/version/locale while lang_pack stays 'tdesktop'"
    - "Every hot-path caller threads sender.client_fingerprint so imported senders reconnect with their own fingerprint"
    - "api_id/api_hash remain the global settings values — never per-account (D-03)"
    - "For an imported account, the Phase 20 2FA-change endpoint uses the stored (decrypted) password as current_password when the request omits it; plaintext never returned"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "fingerprint override seam on make_telegram_client + get_client, strict NULL fallback"
      contains: "fingerprint"
    - path: "app/routers/senders.py"
      provides: "IMPT-10 stored-2FA autofill in update_sender_2fa"
      contains: "twofa_password_enc"
  key_links:
    - from: "app/services/queue.py / warmup.py / senders.py / listener.py / checker.py"
      to: "app/services/telegram.py make_telegram_client/get_client"
      via: "pass sender.client_fingerprint alongside sender.proxy"
      pattern: "fingerprint="
    - from: "app/routers/senders.py update_sender_2fa"
      to: "sender.twofa_password_enc"
      via: "decrypt_session fallback for current_password"
      pattern: "decrypt_session"
---

<objective>
Wire the two new `senders` columns from 21-01 into their existing consumers. First: give `make_telegram_client`/`get_client` an optional `fingerprint` override with a STRICT NULL fallback and thread `sender.client_fingerprint` through every hot-path caller, so imported accounts reconnect with the fingerprint that created them (Pitfall 1) without any behavior change for the phone-onboarded 13 (Pitfall 2). Second: make the Phase 20 2FA-change endpoint auto-use the stored, encrypted 2FA password for imported accounts (D-06) so bulk-imported accounts don't require manual per-account 2FA entry — while still never returning the plaintext (D-07).

Purpose: Storing a fingerprint (21-04) is useless unless the clients actually connect with it; and the 2FA column (21-04) is only useful if a downstream consumer reads it. This plan is the "consume the new columns" seam.
Output: additive `fingerprint` param + threaded call sites; IMPT-10 read-path in `update_sender_2fa`.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md

<interfaces>
<!-- Extracted from the running codebase. -->

app/services/telegram.py — the seam to change:
```python
# line ~152
_CLIENT_FINGERPRINT = {
    "device_model": "Desktop", "system_version": "Windows 10",
    "app_version": "5.3.1", "lang_code": "ru", "system_lang_code": "ru-RU",
}
# line ~233
def make_telegram_client(session, proxy=None, flood_sleep_threshold=60, client_class=TelegramClient):
    client = client_class(session, settings.telegram_api_id, settings.telegram_api_hash,
                          flood_sleep_threshold=flood_sleep_threshold,
                          proxy=build_proxy_tuple(proxy), **_CLIENT_FINGERPRINT)
    client._init_request.lang_pack = "tdesktop"
    return client
# line ~291 (inside TelegramService)
async def get_client(self, sender_slug, sender_id, encrypted_session, proxy=None) -> TelegramClient:
    ...
    client = make_telegram_client(StringSession(session_string), proxy=proxy)
```

get_client / make_telegram_client CALL SITES that read a sender row (thread sender.client_fingerprint here):
- app/services/queue.py:879  → telegram_service.get_client(sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy)
- app/services/warmup.py:714 → telegram_service.get_client(...)
- app/routers/senders.py:882, 1249, 1315 → telegram_service.get_client(...)  (all read `sender`)
- app/services/listener.py:1439 → make_telegram_client(...) directly (reads a sender row nearby)
- app/services/checker.py:240 → make_telegram_client(...) inside _get_client(encrypted_session, proxy) — checker rows are senders too; thread checker sender.client_fingerprint through _get_client

Phase 20 2FA endpoint (app/routers/senders.py line ~1371) — the IMPT-10 seam:
```python
@router.post("/senders/{slug}/2fa")
async def update_sender_2fa(slug, request: TwoFAPasswordUpdate, ctx, db):
    sender = await _load_sender_by_slug(db, ctx, slug)
    await telegram_service.edit_2fa(sender.slug, str(sender.id), sender.session_string,
        current_password=request.current_password, new_password=request.new_password,
        hint=request.hint or "", proxy=sender.proxy)
    return {"success": True}
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
    Do NOT change any call site in this task (that is Task 2) — the new param defaults to None so all existing callers keep today's behavior.
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
  <name>Task 2: Thread sender.client_fingerprint at every hot-path call site + regression assert</name>
  <read_first>
    - app/services/queue.py line 879 (get_client call)
    - app/services/warmup.py line 714 (get_client call)
    - app/routers/senders.py lines 882, 1249, 1315 (get_client calls)
    - app/services/listener.py line 1439 (make_telegram_client call)
    - app/services/checker.py lines 218-245 (_get_client signature + make_telegram_client call at ~240) and its call sites (422/487/638)
  </read_first>
  <action>
    Thread the per-account fingerprint through EVERY place a client is built for a sender, so imported senders connect with their own fingerprint. In each case the caller already has the `sender` row and passes `proxy=sender.proxy`; add `fingerprint=sender.client_fingerprint` right beside it:
    - `app/services/queue.py:879` → add `fingerprint=sender.client_fingerprint`.
    - `app/services/warmup.py:714` → add `fingerprint=sender.client_fingerprint`.
    - `app/routers/senders.py:882, 1249, 1315` → add `fingerprint=sender.client_fingerprint`.
    - `app/services/listener.py:1439` → the `make_telegram_client(...)` here builds a client for a sender row; add `fingerprint=<that sender row>.client_fingerprint` (read the surrounding code to name the sender variable; it loads a sender/session just above).
    - `app/services/checker.py`: add `fingerprint: dict | None = None` to `_get_client(...)` and pass it into the `make_telegram_client(...)` at ~240; then at each `_get_client(...)` call site (~422, ~487, ~638) pass `fingerprint=<checker sender row>.client_fingerprint` (checkers are senders — the row is already loaded to get encrypted_session + proxy).
    Because `client_fingerprint` is NULL for all 13 existing phone-onboarded senders, every one of these calls resolves to the strict fallback (Task 1) → zero behavior change for the working pool.

    Add a regression test to `tests/test_account_import.py` (extend `test_fingerprint_override_and_strict_fallback` or add `test_null_fingerprint_matches_global`) asserting a NULL-fingerprint client's constructor kwargs equal today's `_CLIENT_FINGERPRINT` and lang_pack=='tdesktop' (proves no regression).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -x -q && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/ -k "queue or warmup or checker or listener or sender" -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "fingerprint=sender.client_fingerprint" app/services/queue.py app/services/warmup.py app/routers/senders.py` totals at least 5 (queue 1 + warmup 1 + senders 3)
    - `grep -q "fingerprint" app/services/checker.py` succeeds and `_get_client` signature contains `fingerprint: dict | None = None`
    - `grep -q "fingerprint=" app/services/listener.py` succeeds at the make_telegram_client call
    - The NULL-fingerprint regression test passes
    - The existing queue/warmup/checker/listener/sender tests all still pass (no regression to the working 13)
  </acceptance_criteria>
  <done>All 6 hot-path client-build sites pass sender.client_fingerprint; NULL resolves to today's behavior (regression test proves it); no existing test breaks.</done>
</task>

<task type="auto">
  <name>Task 3: IMPT-10 — Phase 20 2FA-change autofill from stored twofa_password_enc</name>
  <read_first>
    - app/routers/senders.py lines 1366-1408 (update_sender_2fa endpoint)
    - app/services/encryption.py (decrypt_session)
    - .planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md (D-06 auto-fill, D-07 never-return)
  </read_first>
  <action>
    In `app/routers/senders.py::update_sender_2fa` (~line 1383), after loading `sender`, resolve the current password server-side so imported accounts don't require manual re-entry (D-06) while never returning the plaintext (D-07):
    ```python
    from app.services.encryption import decrypt_session
    current_pw = request.current_password
    if current_pw is None and getattr(sender, "twofa_password_enc", None):
        current_pw = decrypt_session(sender.twofa_password_enc)  # IMPT-10 (D-06): imported-account autofill, server-side only
    ```
    Pass `current_password=current_pw` to `telegram_service.edit_2fa(...)` instead of `request.current_password`.
    Do NOT add `current_pw` (or any decrypted password) to the response body, to logs, or to any error detail — the endpoint still returns only `{"success": True}` (D-07). Do NOT change the recovery-email endpoints in this task.
    Add/extend a test in `tests/test_account_import.py` (e.g. `test_2fa_autofill_uses_stored_password`) that: creates an imported sender with a Fernet-encrypted twofa_password_enc, monkeypatches `telegram_service.edit_2fa` to capture kwargs, POSTs `/senders/{slug}/2fa` with `current_password=None`, and asserts edit_2fa received the decrypted stored password as `current_password` AND that the plaintext appears nowhere in the JSON response.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -k "2fa" -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "decrypt_session(sender.twofa_password_enc)" app/routers/senders.py` succeeds
    - `grep -q "current_password=current_pw" app/routers/senders.py` succeeds
    - The 2FA autofill test passes and asserts the plaintext is NOT in the response body
    - `update_sender_2fa` still returns exactly `{"success": True}` (grep confirms no password field added to the return)
  </acceptance_criteria>
  <done>update_sender_2fa falls back to the stored, decrypted twofa_password_enc as current_password when the request omits it; the plaintext is used only server-side and never returned or logged.</done>
</task>

</tasks>

<verification>
- `make_telegram_client(fingerprint=None)` is byte-identical to pre-phase behavior (regression test).
- All hot-path callers thread sender.client_fingerprint; the 13 phone-onboarded senders (NULL fingerprint) are unaffected.
- 2FA autofill uses the stored password server-side only; never returned.
- Full targeted suites (queue/warmup/checker/listener/sender + account_import) green.
</verification>

<success_criteria>
- IMPT-04 seam live with strict fallback + threaded call sites, no regression to the working pool.
- IMPT-10 read-path live and secret-safe.
</success_criteria>

<output>
After completion, create `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-02-SUMMARY.md`
</output>
