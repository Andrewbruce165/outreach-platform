---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 04
type: execute
wave: 3
depends_on: ["21-02", "21-03"]
files_modified:
  - app/services/account_import.py
  - tests/test_account_import.py
autonomous: true
requirements: [IMPT-03, IMPT-05, IMPT-06, IMPT-07]
must_haves:
  truths:
    - "A vendor SQLite .session converts offline to a valid, round-trippable StringSession (no network)"
    - "A per-account import connects with the account's own fingerprint, calls get_me, creates one active sender row, and disconnects"
    - "The 2FA password from the JSON is stored Fernet-encrypted; the proxy is JSON-supplied or a free pool entry"
    - "A second import of the same telegram_id is skipped + reported 'already_connected' — the existing session is NOT overwritten"
    - "One broken pair fails its own item with a reason; it does not raise into the batch"
    - "Imported senders start lifecycle_status='active' + restriction_status='none' (no @SpamBot probe)"
  artifacts:
    - path: "app/services/account_import.py"
      provides: "sqlite_to_string_session + import_one_account routine"
      contains: "def import_one_account"
  key_links:
    - from: "import_one_account"
      to: "make_telegram_client(fingerprint=...)"
      via: "connect with per-account fingerprint (21-02 seam)"
      pattern: "make_telegram_client"
    - from: "import_one_account"
      to: "senders (INSERT)"
      via: "dedup-by-slug SELECT then create, mirroring _create_sender_from_session"
      pattern: "sender-"
---

<objective>
Build the heart of the import: given one file pair (vendor `.session` bytes + parsed JSON), (1) convert the SQLite session to an encrypted StringSession OFFLINE, (2) connect with the account's own fingerprint, run `get_me`, (3) dedup by telegram_id and create exactly one `active` sender row with the fingerprint, Fernet-encrypted 2FA, and a proxy (JSON-supplied or free pool), or skip+report if already connected — never raising into the batch. This routine is called once per `account_import_items` row by the worker (21-05).

Purpose: This is the ~10% genuinely-new logic of the phase; it recomposes the verified offline-conversion recipe (D-12), the fingerprint seam (21-02), and the onboarding create path (`_create_sender_from_session`) into a single testable, per-account routine with partial-failure semantics (D-10).
Output: `sqlite_to_string_session` + `import_one_account` in `app/services/account_import.py`, turning the 21-01 RED tests green.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md

<interfaces>
<!-- Extracted from the running codebase + the empirically-verified conversion recipe. -->

VERIFIED offline conversion recipe (21-RESEARCH.md §Pattern 1 — executed on the real sample):
```python
from telethon.sessions import SQLiteSession, StringSession
sqlite_sess = SQLiteSession("/tmp/xxx.session")   # loads locally, NO connect
assert sqlite_sess.auth_key is not None            # empty => wrong file (Pitfall 4) => fail item
string = StringSession.save(sqlite_sess)           # '1A...' StringSession
sqlite_sess.close()
encrypted = encrypt_session(string)                # app/services/encryption.py
```
SQLiteSession appends '.session' only if the arg lacks it — write the vendor bytes to a temp path ENDING in '.session' and pass that exact path (Pitfall 4).

Onboarding create path to MIRROR (app/routers/onboarding.py::_create_sender_from_session, lines 295-410):
```python
me = await client.get_me()
tg_id = me.id
tg_username = getattr(me, "username", None)
slug = f"sender-{tg_id}"
existing = (await db.execute(select(Sender).where(Sender.slug==slug, Sender.workspace_id==ws_id))).scalars().first()
# onboarding UPSERTS (re-auth contract). IMPORT MUST NOT overwrite — dedup = skip+report (D-14).
sender = Sender(workspace_id=..., slug=slug, name=name or first_name or slug, phone=..., telegram_id=tg_id,
    session_string=encrypt_session(session_string), role=..., proxy=..., auth_status="ok",
    lifecycle_status="active", tg_username=tg_username)
db.add(sender)
try: await db.commit()
except IntegrityError: await db.rollback(); <re-SELECT winner; report already_connected>   # Pitfall 8
```

Fingerprint client build (21-02 seam): `make_telegram_client(StringSession(string), proxy=resolved_proxy, fingerprint=build_fingerprint(vendor))`.
Proxy resolution: JSON proxy dict if present, else a free ProxyPool row for the workspace (app/routers/onboarding.py::_resolve_proxy + ProxyPool model; free = assigned_to_sender_id IS NULL). build_fingerprint is from 21-03.
encryption: encrypt_session(plaintext) -> str  (app/services/encryption.py).
AUTH errors: from app.services.telegram import AUTH_ERRORS, SessionAuthError (session_expired / banned classification).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Offline SQLite→StringSession conversion + proxy resolution + 2FA encrypt helpers</name>
  <read_first>
    - 21-RESEARCH.md §Pattern 1 (verified recipe) + §Pitfall 4 (filename handling)
    - app/services/encryption.py (encrypt_session)
    - app/routers/onboarding.py lines 135-165 (_resolve_proxy — pool selection shape)
    - app/models/__init__.py ProxyPool (line ~479 — free = assigned_to_sender_id IS NULL)
    - tests/test_account_import.py::test_sqlite_to_stringsession_offline (RED contract)
    - tests/test_account_import.py::test_twofa_encrypted_at_rest (RED contract)
  </read_first>
  <behavior>
    - sqlite_to_string_session(session_bytes) → writes bytes to a temp '.session' path, loads via SQLiteSession, asserts auth_key present, returns StringSession.save(...) ('1A'-prefixed), closes + deletes the temp file in a finally. Round-trips: StringSession(returned).auth_key.key == original.
    - Given twoFA plaintext, the stored ciphertext != plaintext and decrypt_session(ciphertext) == plaintext.
    - assign_free_proxy(db, workspace_id) returns a free ProxyPool row dict or None; JSON proxy takes precedence when present.
  </behavior>
  <action>
    In `app/services/account_import.py` add (async where DB is touched):
    1. `def sqlite_to_string_session(session_bytes: bytes) -> str`:
       - Write `session_bytes` to a unique temp path ending in `.session` under the OS temp dir (`tempfile.mkstemp(suffix=".session")`), `os.chmod(path, 0o600)`.
       - `sess = SQLiteSession(path)`; if `sess.auth_key is None` → close + delete + raise `ValueError("empty_or_invalid_session")` (Pitfall 4 — do NOT connect an empty session).
       - `s = StringSession.save(sess)`; `sess.close()`; return `s`.
       - Delete the temp file in a `finally` (never leave vendor `.session` bytes on disk — Pitfall 9).
    2. `def encrypt_twofa(twofa: str | None) -> str | None`: return `None` if falsy else `encrypt_session(twofa)` (reuse the existing Fernet path — D-05).
    3. `async def resolve_import_proxy(db, workspace_id, json_proxy: dict | None) -> dict | None`: if `json_proxy` is a non-empty dict → return it (D-15); else SELECT one `ProxyPool` row for the workspace WHERE `assigned_to_sender_id IS NULL` LIMIT 1 and return `{"type":"socks5","host":...,"port":...,"username":...,"password":...}` or `None` if the pool is empty (do NOT hard-fail — proxy is optional). (The actual `assigned_to_sender_id` write happens after the sender is created, in Task 2.)
    Never log the StringSession, the auth_key, or the twoFA plaintext anywhere in this module (Pitfall 9).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -k "offline or twofa" -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "def sqlite_to_string_session" app/services/account_import.py` succeeds
    - `grep -q "suffix=\".session\"" app/services/account_import.py` succeeds (correct temp filename — Pitfall 4)
    - `grep -q "auth_key is None" app/services/account_import.py` succeeds (empty-session guard)
    - `grep -q "os.remove\|os.unlink\|finally" app/services/account_import.py` shows temp-file cleanup
    - `grep -q "def encrypt_twofa" app/services/account_import.py` and `grep -q "assigned_to_sender_id IS NULL\|assigned_to_sender_id == None\|assigned_to_sender_id.is_(None)" app/services/account_import.py` succeed
    - test_sqlite_to_stringsession_offline + test_twofa_encrypted_at_rest pass (GREEN); the conversion performs NO network call
  </acceptance_criteria>
  <done>Offline conversion returns a round-trippable StringSession from vendor bytes with a temp-file lifecycle; 2FA encrypts via the shared Fernet path; proxy resolves JSON-first then free pool. No secrets logged.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: import_one_account routine (connect/get_me/dedup/create, partial-failure-safe)</name>
  <read_first>
    - app/routers/onboarding.py lines 295-410 (_create_sender_from_session — slug derivation, INSERT fields, IntegrityError recovery)
    - app/services/telegram.py lines 233-258 (make_telegram_client with fingerprint from 21-02) + AUTH_ERRORS/SessionAuthError
    - app/services/account_import.py (Task 1 helpers + build_fingerprint from 21-03)
    - tests/test_account_import.py::test_dedup_skip_and_proxy + ::test_partial_success_and_start_state (RED contracts)
  </read_first>
  <behavior>
    - import_one_account(db, workspace_id, role, basename, session_bytes, vendor_dict) returns a result dict {status:'ok'|'failed', result:'imported'|'already_connected'|<reason-code>, reason:str|None, sender_id:UUID|None} and NEVER raises for a per-account failure (convert error, auth error, get_me failure) — it captures and reports.
    - On success: exactly one new Sender row with lifecycle_status='active', restriction_status defaulting to 'none', role=batch role, client_fingerprint=build_fingerprint(vendor), twofa_password_enc=encrypt_twofa(vendor.twoFA), proxy=resolved proxy, tg_username=me.username, telegram_id=me.id, slug='sender-{me.id}'.
    - Dedup: slug already exists in workspace → return {'status':'failed','result':'already_connected'} and DO NOT overwrite the existing session_string.
    - The Telethon client is disconnected in a finally (Pitfall 5) — never left connected.
  </behavior>
  <action>
    In `app/services/account_import.py` add `async def import_one_account(db, workspace_id, role, basename, session_bytes, vendor_dict) -> dict`:
    - Parse `vendor = VendorAccountJson.model_validate(vendor_dict)` (already validated at preview, but re-validate defensively; on failure → return failed result `malformed_json`).
    - `try: string = sqlite_to_string_session(session_bytes)` — on ValueError → return `{status:'failed', result:'convert_failed', reason:str(e)}`.
    - Resolve proxy: `proxy = await resolve_import_proxy(db, workspace_id, vendor.proxy)`.
    - Build client: `client = make_telegram_client(StringSession(string), proxy=proxy, fingerprint=build_fingerprint(vendor))`.
    - `try / finally` around connect: `await client.connect()` — catch `AUTH_ERRORS`/`UserDeactivatedBanError` → return failed `auth_failed`/`banned`; if `not await client.is_user_authorized()` → return failed `not_authorized`. `me = await client.get_me()`. In `finally`, `await client.disconnect()` (Pitfall 5).
    - Compute `tg_id = me.id`, `tg_username = getattr(me, "username", None)`, `first_name = getattr(me, "first_name", "") or ""`, `phone = getattr(me, "phone", None) or basename` (filename fallback per D-11), `slug = f"sender-{tg_id}"`.
    - Dedup (D-14 skip, NOT overwrite): SELECT Sender WHERE slug==slug AND workspace_id==workspace_id; if found → return `{status:'failed', result:'already_connected', sender_id:existing.id}` (do NOT touch existing.session_string).
    - Else INSERT a Sender mirroring `_create_sender_from_session` PLUS the Phase-21 fields: `role=role`, `client_fingerprint=build_fingerprint(vendor)`, `twofa_password_enc=encrypt_twofa(vendor.twoFA)`, `proxy=proxy`, `lifecycle_status="active"`, `auth_status="ok"`, `tg_username=tg_username`, `telegram_id=tg_id`, `name=first_name or slug`, `phone=phone`, `session_string=encrypt_session(string)`. (restriction_status defaults to 'none' via server_default — D-11; do NOT probe @SpamBot.)
    - Wrap `await db.commit()` in the same IntegrityError recovery as onboarding (Pitfall 8): on IntegrityError → rollback, re-SELECT winner, return `already_connected`.
    - If the sender was created AND a free pool proxy was used, set `assigned_to_sender_id = new_sender.id` on that ProxyPool row in the same commit (so the pool proxy is marked taken). A JSON-supplied proxy touches no pool row.
    - Return `{status:'ok', result:'imported', sender_id:new_sender.id}`.
    - Log only slug/tg_id + phone-prefix (`phone[:6]+"***"`) + result — never session/2FA/auth_key (Pitfall 9).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "async def import_one_account" app/services/account_import.py` succeeds
    - `grep -q "lifecycle_status=\"active\"" app/services/account_import.py` succeeds
    - `grep -q "client_fingerprint=build_fingerprint" app/services/account_import.py` succeeds
    - `grep -q "twofa_password_enc=encrypt_twofa" app/services/account_import.py` succeeds
    - `grep -q "already_connected" app/services/account_import.py` succeeds AND the dedup path does NOT assign to existing.session_string (verify by reading — the SELECT-found branch returns without mutating)
    - `grep -q "disconnect" app/services/account_import.py` inside a finally (client never left connected)
    - `grep -q "IntegrityError" app/services/account_import.py` succeeds (race recovery)
    - test_dedup_skip_and_proxy + test_partial_success_and_start_state pass; imported sender has lifecycle_status='active' + restriction_status='none'
    - Whole tests/test_account_import.py file is GREEN except worker-specific cases (owned by 21-05)
  </acceptance_criteria>
  <done>import_one_account converts+connects+get_me with the account fingerprint, creates one active sender (fingerprint/2FA/proxy/none), skips+reports duplicates without overwriting, disconnects always, and returns a per-account result without raising into the batch.</done>
</task>

</tasks>

<verification>
- Offline conversion is network-free and round-trips (test).
- import_one_account is partial-failure-safe (broken pair → its own failed result, no exception escapes).
- Dedup never overwrites a live session; imported accounts are active/none.
- tests/test_account_import.py green (worker cases deferred to 21-05).
</verification>

<success_criteria>
- IMPT-03/05/06/07 delivered as a single testable per-account routine ready for the worker.
</success_criteria>

<output>
After completion, create `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-04-SUMMARY.md`
</output>
