---
status: awaiting_human_verify
trigger: "пробовали загрузить базу 77 контактов. промапили все поля, но загрузились только username"
created: 2026-08-11
updated: 2026-08-11
---

# Debug Session: csv-contact-mapping-only-username-saved

## Symptoms

- **Expected behavior:** При загрузке CSV в UI (загрузка контактов в кампанию, CSV upload + маппинг колонок) все смапленные поля (имя, телефон, компания, переменные и т.д.) должны сохраниться в записи контакта.
- **Actual behavior:** После загрузки базы из 77 контактов с полным маппингом всех полей, в базе данных сохранилось только поле `username` — остальные смапленные поля (имя, телефон, переменные и т.д.) не сохранились. Пользователь не уверен в точной картине — нужно смотреть в базе (`contacts` / `contacts_cache` / соответствующая таблица кампании).
- **Error messages:** Неизвестно — пользователь не проверял логи UI/API/listener на момент загрузки.
- **Timeline:** Первая попытка загрузки такого рода для пользователя — неизвестно, работал ли маппинг полей (кроме username) корректно раньше на этом проекте.
- **Reproduction:** Загрузка CSV с 77 контактами через UI загрузки контактов в кампанию (CSV upload + маппинг колонок), с маппингом всех доступных полей.

## Current Focus

hypothesis: "CONFIRMED — frontend sends `mapping` keyed by CSV column NAME, backend contract is column INDEX-as-string. `apply_import` does `int(col_idx_str)` and on ValueError silently `continue`s, so every user-selected mapping entry is dropped. Only the backend's own index-keyed `suggested_mapping` entries survive — in this file only the username-alias column matched."
test: "Fix applied (all 4 approved parts) + targeted regression suite via test-overlay"
expecting: "name-keyed mapping resolves; unresolvable keys reported not dropped; custom.<key> round-trips; 63/63 targeted tests green"
next_action: "Human verification: rebuild api (+ deploy frontend), re-upload the CSV, confirm all mapped fields land. Then run the backfill dry-run for the existing 77 rows."
reasoning_checkpoint: null
tdd_checkpoint: null

## Evidence

- timestamp: 2026-08-11 (investigation)
  checked: "prod DB — per-minute fill rate of `contacts` columns"
  found: "Batch of exactly 77 rows at 2026-08-11 17:19:14 UTC, folder 6595094a-c693-4a62-8862-670e74cc09ff: has_phone=0, has_username=77, has_name=0, has_source=0, has_custom=0. All rows `custom = '{}'`, `tg_status='registered'`."
  implication: "Symptom reproduced exactly in data. Not a display bug — the columns are genuinely NULL in the DB. tg_status='registered' is a downstream consequence of the username-shortcut in `_insert_contacts_with_dedup`."

- timestamp: 2026-08-11
  checked: "api container logs around 17:17–17:19"
  found: "`import-preview … import_id=e06a338a-… cols=8 sample_rows=50 encoding=utf-8-sig` then `import … total=85 imported=77 dup=8 invalid=0` → 202 Accepted. No errors, no warnings."
  implication: "Confirms the CSV preview+mapping path (POST /contacts/import), not the n8n push path. 8 columns were present in the file. `invalid=0` means apply_import raised no per-row complaints — it silently ignored the mapping instead."

- timestamp: 2026-08-11
  checked: "app/services/csv_import.py::apply_import (lines 208-231) — mapping key parsing"
  found: "Iterates `mapping.items()`, does `col_idx = int(col_idx_str)` inside try/except ValueError → `continue`. Unknown *field* names also fall through all branches with no else. Both failure modes are silent — no log, no counter, no error."
  implication: "Any mapping entry whose key is not an integer string is discarded with zero diagnostics. This is why the bug produced a clean 202 with invalid=0."

- timestamp: 2026-08-11
  checked: "app/schemas/__init__.py::ContactImportRequest (line 388) + suggest_mapping (csv_import.py:141-148)"
  found: "Contract is documented as `mapping: {\"0\": \"phone\", \"1\": \"full_name\", \"2\": \"custom.company\"}` — keys are column INDEX as string. `suggest_mapping` returns `result[str(idx)] = field`, i.e. index-keyed."
  implication: "Backend side of the contract is unambiguous and self-consistent: index-keyed."

- timestamp: 2026-08-11
  checked: "frontend/src/routes/_authenticated/contacts.tsx::ImportModal + RowMap (lines 1172, 1192, 1292-1307, 1400-1425)"
  found: "State seeded from backend: `setMapping(res.suggested_mapping ?? {})` (index-keyed). But the grid renders `preview.columns.map((col) => <RowMap value={mapping[col] ?? \"\"} onChange={… next[col] = v …} />)` — reads AND writes keyed by column NAME `col`. Payload is sent verbatim: `body: { …, mapping }`."
  implication: "ROOT CAUSE. Two compounding effects: (1) the index-keyed suggested_mapping never renders as pre-selected, so every dropdown shows '— skip —' and the user is forced to re-pick everything; (2) each re-pick writes a NAME-keyed entry which the backend then silently discards. The only entries that survive are the original index-keyed suggested ones the user did not touch."

- timestamp: 2026-08-11
  checked: "Why username specifically survived — _COLUMN_ALIASES vs the 8-column file"
  found: "`suggest_mapping` matched only the username-alias header in this file, producing e.g. `{\"3\": \"username\"}`. Final payload = that one index key + ~7 name-keyed user selections. `mapped_fields` validation passed (username present), so no MAPPING_INVALID."
  implication: "Fully explains 'only username loaded'. Also explains why validation did not catch it: the guard checks mapping *values*, never whether the keys resolve."

- timestamp: 2026-08-11
  checked: "Historical batches in `contacts` (regression / differential check)"
  found: "2026-07-20: 376 rows username+source only. 2026-07-08: 366/367 rows username+full_name (+30/21 phone). 2026-06-29/30: batches with phone only. NO batch in the whole table has ever had a non-empty `custom`."
  implication: "Every historically 'successful' field is exactly what `suggest_mapping` auto-detects from header aliases. Consistent with the conclusion that user-selected mappings have NEVER worked. `custom` has never once been populated — see next entry for why."

- timestamp: 2026-08-11
  checked: "frontend TARGET_FIELDS (contacts.tsx:40-45) vs backend-supported fields"
  found: "UI offers only phone / username / full_name / source. Backend `apply_import` additionally supports `custom.<key>` (tested in tests/test_csv_import.py:164) but the UI has no way to produce it."
  implication: "SECOND, INDEPENDENT DEFECT: custom variables ({{company}} etc.) are unreachable from the CSV UI at all. The user's 'промапили все поля … переменные' cannot have worked even with the key bug fixed. Separate scope — needs approval before building UI."

- timestamp: 2026-08-11
  checked: "tests/test_csv_import.py — all apply_import call sites"
  found: "Every test passes index-keyed mappings ({\"0\": \"phone\", \"1\": \"full_name\"}, …). No test exercises a name-keyed or otherwise unresolvable key, and no test asserts on the frontend↔backend key contract."
  implication: "Explains why the suite is green while the live feature is broken: the contract seam between UI and API is untested. A regression test must cover the malformed-key case."

## Eliminated

- hypothesis: "ORM `default=` vs `server_default=` drift — raw-SQL INSERT omitting columns (known codebase pattern from MEMORY)"
  evidence: "`_insert_contacts_with_dedup` (contacts.py:145-159) explicitly passes phone, username, full_name, source, custom, tg_status via `pg_insert(Contact).values(...)`. Nothing is omitted. The values arriving there are already None because apply_import dropped them upstream."
  timestamp: 2026-08-11

- hypothesis: "Backend `apply_import` mis-assigns fields / Contact model missing columns"
  evidence: "Service assigns all five record fields correctly (csv_import.py:218-230) and the model/insert accepts all of them; historical batches prove full_name/source/phone do persist when index-keyed mapping is used. Bug is in the mapping KEYS, not the field handling."
  timestamp: 2026-08-11

- hypothesis: "Contact rows were written correctly but the UI/list endpoint fails to display the other fields"
  evidence: "Direct SQL read of the 77 rows shows phone/full_name/source NULL and custom='{}' in the database itself."
  timestamp: 2026-08-11

- hypothesis: "Rows were silently rejected/partially skipped by dedup or validation (data loss at INSERT)"
  evidence: "Log line `total=85 imported=77 dup=8 invalid=0` reconciles exactly with 77 DB rows; the 8 dups are real duplicate usernames. No rows were lost — the rows are present but under-populated."
  timestamp: 2026-08-11

- hypothesis: "`contacts_cache` workspace-isolation / poisoned-cache interaction (prior known incident)"
  evidence: "Irrelevant to this failure mode — the missing data never reached the INSERT. contacts_cache only affects tg_status resolution, not phone/full_name/source/custom persistence."
  timestamp: 2026-08-11

## Resolution

root_cause: |
  Frontend/backend key-space mismatch in the CSV column-mapping contract.

  The backend contract (documented at app/schemas/__init__.py:388, produced by
  csv_import.py::suggest_mapping, consumed by csv_import.py::apply_import) keys the
  `mapping` dict by CSV column INDEX as a string: {"0": "phone", "1": "full_name"}.

  The frontend ImportModal (frontend/src/routes/_authenticated/contacts.tsx:1292-1307,
  RowMap at 1400-1425) reads and writes that same dict keyed by CSV column NAME:
  `value={mapping[col]}` / `next[col] = v`.

  Two compounding effects:
  1. Backend-suggested index-keyed entries never render as pre-selected, so every
     dropdown shows "— skip —" and the user re-picks every field.
  2. Every re-pick writes a NAME-keyed entry. In apply_import, `int(col_idx_str)`
     raises ValueError and the code silently `continue`s — the entry is discarded
     with no log, no `skipped_invalid` counter, and no error.

  Net result: only the untouched, index-keyed auto-suggested entries survive. In this
  file suggest_mapping matched only the username-alias header, so exactly one field
  persisted for all 77 contacts. The `mapped_fields` guard passed because it inspects
  mapping VALUES and never checks that the KEYS resolve to a real column.

  Secondary independent defect: frontend TARGET_FIELDS offers only
  phone/username/full_name/source, so `custom.<key>` variables — which the backend
  fully supports — are unreachable from the UI. `custom` has never been non-empty
  for any contact in prod.
fix: |
  Four parts, all approved by the user (checkpoint 2026-08-11).

  1. Backend tolerant + LOUD resolver (app/services/csv_import.py) — new
     `resolve_mapping(mapping, headers)` resolves each mapping key as a column
     index (canonical) and falls back to a case-insensitive header-NAME match.
     Anything unresolvable is returned in `unresolved_mapping_keys`, invalid
     target fields in `unknown_mapping_fields` — both `logger.warning`'d instead
     of the old silent `continue`. If the key carrying phone/username resolves
     to nothing, apply_import now raises MAPPING_INVALID (422) instead of
     importing empty contacts. THIS PART ALONE FIXES PROD on an api rebuild,
     before any frontend deploy.
  2. Router surface (app/routers/contacts.py + app/schemas/__init__.py) — new
     `ContactImportSummary.mapping_warnings: List[str]` in the 202 payload +
     a logger.warning line; the UI shows them on the "Import complete" screen.
  3. Frontend index-keyed mapping (frontend/src/routes/_authenticated/contacts.tsx)
     — the mapping grid now iterates `preview.columns.map((col, idx) => …)` and
     reads/writes `mapping[String(idx)]`. Fixes the never-preselected
     auto-suggestion and the duplicate-column-name case. Sample cell stays
     name-keyed (sample_rows really is name-keyed).
  4. Custom-variable UI (same file) — new "Custom variable…" option per column
     with an editable key input (defaulted to a slug of the column name),
     producing `custom.<key>`; helper text shows the `{{key}}` template form.

  Backfill for the already-imported 77 rows: scripts/backfill_csv_import_20260811.py
  — written, dry-run verified, NOT EXECUTED (awaiting explicit go-ahead).
verification: |
  - Targeted suite via test-overlay (NEVER bare `docker compose run api pytest`):
    `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api
     pytest tests/test_csv_import.py tests/test_contacts.py tests/test_check_contacts.py -q`
    → 63 passed. Includes 9 new unit tests (name keys, mixed keys, case-insensitive
    headers, unresolvable key reported, unknown field reported, MAPPING_INVALID when
    the phone key resolves to nothing, duplicate headers, index-over-name precedence,
    custom.<key> round-trip) and 2 new router tests (name-keyed end-to-end with
    custom.company landing in the DB; unresolvable key surfaced in mapping_warnings).
  - Frontend: `tsc --noEmit` → 3 errors, all pre-existing (/login search params in
    __root.tsx, _authenticated.tsx, settings.tsx); 0 in contacts.tsx. eslint on
    contacts.tsx → 26 errors/1 warning, byte-identical count to the HEAD baseline
    (pre-existing prettier formatting debt).
  - Backfill script dry-run against prod (read-only) with a synthetic CSV built from
    the real 77 usernames: count guard passed, 77/77 matched, plan printed, nothing
    written (`max(updated_at)` on the folder still 2026-08-11 17:19:14).
  - UPDATE shape + idempotency guard validated on the ephemeral test DB
    (`UPDATE 1` then `UPDATE 0` on re-run; probe table dropped).
  - NOT yet verified by human: real CSV re-upload after deploy. api rebuild required
    (`docker compose up -d --build api`) — verified the running image still has the
    old code; frontend needs `./deploy-frontend.sh`.
files_changed:
  - app/services/csv_import.py
  - app/routers/contacts.py
  - app/schemas/__init__.py
  - frontend/src/routes/_authenticated/contacts.tsx
  - frontend/src/types/api.ts
  - tests/test_csv_import.py
  - tests/test_contacts.py
  - scripts/backfill_csv_import_20260811.py (new, NOT executed)
