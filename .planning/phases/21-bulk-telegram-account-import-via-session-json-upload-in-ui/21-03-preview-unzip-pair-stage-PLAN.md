---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 03
type: execute
wave: 2
depends_on: ["21-01"]
files_modified:
  - app/services/account_import.py
  - app/routers/account_import.py
  - app/main.py
  - tests/test_account_import.py
autonomous: true
requirements: [IMPT-01]
must_haves:
  truths:
    - "User POSTs a ZIP and gets back a synchronous summary: matched pairs, unpaired files, malformed JSON — NO Telegram connect happens"
    - "Pairs are matched by basename via the session_file field; a .json with no .session (and vice versa) is reported unpaired, not imported"
    - "Malformed / schema-invalid JSON is reported per-file and does not abort the preview"
    - "The raw ZIP bytes are staged in account_import_stagings with a TTL so confirm can re-read them"
    - "ZIP-bomb / path-traversal / oversized batches are rejected before extraction"
  artifacts:
    - path: "app/services/account_import.py"
      provides: "VendorAccountJson schema + unpack_and_pair(zip_bytes)"
      contains: "class VendorAccountJson"
    - path: "app/routers/account_import.py"
      provides: "POST /api/v1/accounts/import/preview"
      contains: "/import/preview"
  key_links:
    - from: "app/routers/account_import.py preview"
      to: "account_import_stagings"
      via: "insert ZIP bytes + summary with expires_at TTL"
      pattern: "AccountImportStaging"
    - from: "app/main.py"
      to: "app/routers/account_import.py"
      via: "include_router"
      pattern: "account_import.router"
---

<objective>
Deliver step 1 of the two-step flow (D-08a): a fast, synchronous preview endpoint that unzips the uploaded archive, pairs `<base>.json` ↔ `<base>.session` by basename, validates each vendor JSON against a Pydantic schema, and returns a recognized/unpaired/malformed summary WITHOUT touching Telegram. It stages the raw ZIP bytes (BYTEA + TTL, `csv_imports` pattern) so the async confirm step (21-05) can re-read them, keyed by `import_id`.

Purpose: The client must see what was recognized before committing to import; preview is where pairing + JSON validation + ZIP-safety happen, and it must be cheap enough to run inside one HTTP request (no per-account connect).
Output: `app/services/account_import.py` (schema + pairing), `app/routers/account_import.py` (preview endpoint), router registration.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-NOTES.md

<interfaces>
<!-- Extracted from the running codebase. -->

Multipart-upload + BYTEA-staging precedent (COPY this shape): app/routers/contacts.py::import_preview (lines 301-365):
```python
@router.post("/import/preview", response_model=...)
async def import_preview(file: UploadFile = File(...), ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    raw = await file.read()
    if len(raw) > MAX_CSV_BYTES: raise HTTPException(413, {"code": "FILE_TOO_LARGE", ...})
    ...
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    row = CsvImport(workspace_id=ctx.workspace_id, file_data=raw, ..., expires_at=expires_at)
    db.add(row); await db.flush(); await db.commit()
    return ...(import_id=row.id, ...)
```

Auth dependency: `from app.dependencies import auth_dep` / `ctx.workspace_id` (see any Phase-4+ router, e.g. app/routers/knowledge_bases.py header).
DB dependency: `from app.database import get_db`.

ORM (from 21-01): AccountImportStaging(id, workspace_id, zip_data, summary JSONB, created_at, expires_at).

Router registration site: app/main.py line ~204 (after llm_settings.router):
    app.include_router(llm_settings.router)
    # add: app.include_router(account_import.router)  # Phase 21 — bulk account import

Vendor JSON real field set (from 21-NOTES.md / verified sample +18646884306.json):
  app_id(2040), app_hash, device("KVM"), sdk("Windows 10 x64"), app_version("6.8.2 x64"),
  system_lang_pack("en-US"), system_lang_code("en-US"), lang_pack("tdesktop"), lang_code("en"),
  twoFA(null), role(""), id(null), phone(null), username(null), proxy(null), ipv6(false),
  session_file("+18646884306")   ← REQUIRED, the pairing key = shared basename
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: account_import.py — VendorAccountJson schema + unpack_and_pair</name>
  <read_first>
    - 21-NOTES.md (§"Что внутри JSON" — the exact vendor field set)
    - 21-RESEARCH.md (§Code Examples → "Vendor JSON schema" + §Pitfalls → Pitfall 7 ZIP safety)
    - tests/test_account_import.py::test_preview_pairing (the RED contract from 21-01)
    - app/config.py lines 60-80 (Field(...) knob pattern, if adding size/count knobs)
  </read_first>
  <behavior>
    - unpack_and_pair(zip_bytes) on a ZIP with {A.json, A.session, B.json (no session), C.session (no json), D.json (malformed)} returns:
        matched == [ {basename:'A', json:<parsed>, session_bytes:b'...'} ]
        unpaired == ['B.json' (json without session), 'C.session' (session without json)]
        malformed == ['D.json' (invalid/unparseable/schema-invalid)]
    - A member with an absolute path or '..' is rejected (never extracted).
    - VendorAccountJson requires session_file; app_id/app_hash are accepted but ignored (D-03).
  </behavior>
  <action>
    Create `app/services/account_import.py`. Add:

    1. `class VendorAccountJson(BaseModel)` (Pydantic v2) — the field set the worker needs, everything else ignored via `model_config = ConfigDict(extra="ignore")`:
    ```python
    class VendorAccountJson(BaseModel):
        model_config = ConfigDict(extra="ignore")
        session_file: str            # REQUIRED — shared basename, the pairing key
        device: str | None = None            # -> device_model
        sdk: str | None = None               # -> system_version
        app_version: str | None = None       # -> app_version
        lang_code: str | None = None
        system_lang_code: str | None = None
        twoFA: str | None = None             # -> Fernet-encrypt at import (D-05)
        proxy: dict | None = None            # -> senders.proxy if set, else pool (D-15)
        phone: str | None = None
        # app_id / app_hash intentionally NOT declared (ignored, D-03)
    ```

    2. `def build_fingerprint(v: VendorAccountJson) -> dict` — the D-01 JSON→Telethon mapping:
    ```python
    return {"device_model": v.device, "system_version": v.sdk, "app_version": v.app_version,
            "lang_code": v.lang_code, "system_lang_code": v.system_lang_code}
    # NOTE: lang_pack is NOT included here — make_telegram_client always forces 'tdesktop' (D-04).
    ```

    3. `def unpack_and_pair(zip_bytes: bytes) -> dict` using stdlib `zipfile.ZipFile(io.BytesIO(zip_bytes))`:
       - Enforce ZIP safety BEFORE reading contents: reject any member whose name is absolute or contains `..`; use `os.path.basename(member)` only. Sum `ZipInfo.file_size`; if total uncompressed > `MAX_IMPORT_UNCOMPRESSED_BYTES` (config knob, default 50 MB) → raise a ValueError mapped to 413 by the router. Count distinct basenames; if > `MAX_IMPORT_ACCOUNTS` (config knob, default 500) → ValueError → 422.
       - Group members by basename with the extension stripped (`+18646884306.json` and `+18646884306.session` → basename `+18646884306`). Use the JSON's `session_file` field as the authoritative basename when present; the archived filename basename is the fallback pairing key.
       - For each basename: if it has BOTH a `.json` (that parses AND validates as VendorAccountJson) AND a `.session` → `matched`. A `.json` that fails json.loads OR VendorAccountJson validation → `malformed` (with the filename + short reason). A basename with only one of the two files → `unpaired` (with the filename).
       - Return `{"matched": [...], "unpaired": [...], "malformed": [...]}`. `matched` entries carry the parsed vendor dict + raw `.session` bytes (bytes stay in memory here; the router stages them). Do NOT connect to Telegram anywhere in this module.
    Add two config knobs to `app/config.py` mirroring the `kb_ingest_poll_interval` Field(...) style: `MAX_IMPORT_UNCOMPRESSED_BYTES` (default 52428800) and `MAX_IMPORT_ACCOUNTS` (default 500).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py::test_preview_pairing -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "class VendorAccountJson(BaseModel)" app/services/account_import.py` succeeds
    - `grep -q "def unpack_and_pair" app/services/account_import.py` succeeds
    - `grep -q "def build_fingerprint" app/services/account_import.py` succeeds
    - `grep -q "session_file: str" app/services/account_import.py` succeeds (required field)
    - `grep -qi "\\.\\." app/services/account_import.py` shows a path-traversal guard present
    - `grep -q "MAX_IMPORT" app/config.py` succeeds (both knobs added)
    - test_preview_pairing passes (matched/unpaired/malformed lists correct)
    - No Telethon import in account_import.py yet performs a connect (grep: no `.connect(` in this module)
  </acceptance_criteria>
  <done>account_import.py exposes VendorAccountJson, build_fingerprint, and unpack_and_pair with basename pairing + ZIP safety; malformed/unpaired reported without aborting; no Telegram connect.</done>
</task>

<task type="auto">
  <name>Task 2: POST /accounts/import/preview endpoint + staging + router registration</name>
  <read_first>
    - app/routers/contacts.py lines 298-366 (import_preview — the multipart + BYTEA-staging shape to mirror)
    - app/services/account_import.py (unpack_and_pair from Task 1)
    - app/models/__init__.py AccountImportStaging (from 21-01)
    - app/main.py lines 189-204 (include_router block)
  </read_first>
  <action>
    Create `app/routers/account_import.py` with `router = APIRouter(prefix="/api/v1/accounts", tags=["account-import"])`.

    Add `POST /import/preview` (synchronous):
    - Signature: `async def import_preview(file: UploadFile = File(...), ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db))`.
    - `raw = await file.read()`; guard `len(raw) > settings.MAX_IMPORT_UNCOMPRESSED_BYTES` → 413 `{"code":"FILE_TOO_LARGE"}` (compressed-size fast guard; unpack_and_pair does the uncompressed guard).
    - `try: result = unpack_and_pair(raw)` and map its `ValueError` to 413/422 (`{"code":"ZIP_TOO_LARGE"|"TOO_MANY_ACCOUNTS"|"BAD_ZIP", "message": str(e)}`) — a totally-invalid ZIP is a 422, not a 500.
    - Build a `summary` = counts + the filename lists (matched basenames, unpaired filenames, malformed filenames+reasons). Do NOT put raw session bytes in the summary response.
    - Stage the row: `AccountImportStaging(workspace_id=ctx.workspace_id, zip_data=raw, summary=summary, expires_at=datetime.now(timezone.utc)+timedelta(minutes=30))`; `db.add`; `await db.flush(); await db.commit()`.
    - Return a Pydantic response `{ import_id: UUID, matched: [...], unpaired: [...], malformed: [...] }` (matched carries basename + phone-from-filename + a bool has_2fa/has_proxy derived from the parsed JSON — NEVER the twoFA value itself).
    - Log only counts + import_id + workspace (never session bytes, never twoFA). Define the request/response Pydantic models at the top of this router file (co-located, like knowledge_bases.py).

    Register the router in `app/main.py`: `from app.routers import account_import` (add to the imports block) and `app.include_router(account_import.router)  # Phase 21 — bulk account import` right after the `llm_settings.router` line (~204).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -k "preview" -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "/import/preview" app/routers/account_import.py` succeeds
    - `grep -q "AccountImportStaging(" app/routers/account_import.py` succeeds
    - `grep -q "expires_at" app/routers/account_import.py` succeeds
    - `grep -q "account_import.router" app/main.py` succeeds AND `grep -q "from app.routers import account_import" app/main.py` (or equivalent import) succeeds
    - The preview test passes: a ZIP posted to the endpoint returns import_id + matched/unpaired/malformed and stages a row with a future expires_at
    - `grep -qi "twoFA\|session_blob\|zip_data" app/routers/account_import.py` shows twoFA/session bytes are NOT placed into any response model field (only counts/flags)
  </acceptance_criteria>
  <done>POST /accounts/import/preview unzips + pairs + validates synchronously, stages the ZIP with a 30-min TTL, returns the recognized-set summary, and is registered in main.py. No secret bytes in the response.</done>
</task>

</tasks>

<verification>
- Preview runs entirely in one HTTP request with no Telegram connect.
- Staging row persists the ZIP + summary with a future expires_at; import_id is returned.
- ZIP safety guards reject bombs / traversal / oversized batches with structured 4xx (not 500).
</verification>

<success_criteria>
- IMPT-01 delivered: matched/unpaired/malformed reported, ZIP staged, no import side effects.
</success_criteria>

<output>
After completion, create `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-03-SUMMARY.md`
</output>
