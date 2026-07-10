---
slug: tg-import-archive-errors
status: resolved
trigger: "Пробовали загрузить 2 архива новых аккаунтов Telegram через UI — были ошибки с загрузкой"
created: 2026-07-07
updated: 2026-07-07
goal: find_and_fix
root_cause: "Two vendor-format mismatches — proxy shipped as a list (schema wanted dict → malformed), and .session tables carry an extra tmp_auth_key column that breaks Telethon's SELECT * unpack (→ empty_or_invalid_session)."
fix: "app/services/account_import.py — proxy field typed Any + _normalize_vendor_proxy; sqlite_to_string_session reads dc_id/server/port/auth_key by explicit column and rebuilds StringSession. + 4 regression tests."
verification: "On the two REAL staged archives: arch1 0→13 matched (convert 13/13), arch2 6→12 matched (convert 12/12), 0 malformed. 19 account-import tests pass via test-overlay. api+listener rebuilt, clean startup."
files_changed: "app/services/account_import.py, tests/test_account_import.py"
---

# Debug: bulk account import archive errors (Phase 21)

## Symptoms
- User uploaded 2 ZIP archives of new Telegram accounts via UI (`/accounts/import/preview` → confirm).
- Archive 1 (`c102bb4a`): `matched=0 unpaired=0 malformed=13` — nothing imported.
- Archive 2 (`1b3efeac`): `matched=6 malformed=6`; confirmed 6 → job `81c1afd4` → **all 6 `convert_failed (empty_or_invalid_session)`**.
- Net: 0 of ~19 accounts imported across both archives.

## Root causes (BOTH confirmed with reproducible evidence)

### Bug A — `proxy` field type mismatch → "schema validation failed: 1 error(s)"
- Vendor JSON ships `proxy` as a **list** (PySocks-tuple form), e.g.
  `[3, "resident.proxyshard.com", 8080, true, "user", "pass"]`.
- `VendorAccountJson.proxy` is typed `dict | None` (app/services/account_import.py:100).
- List → exactly 1 pydantic error → item bucketed `malformed` and **silently skipped**.
- Records with `proxy: null` validate fine (the 6 "matched" in archive 2).
- Evidence: archive 2 malformed cluster `79326*` all have `proxy=[...]`; matched cluster `79327*` all have `proxy=null`.

### Bug B — vendor `.session` has extra column → "empty_or_invalid_session"
- The 6 paired `.session` files are **valid Telethon SQLite sessions** with a real 256-byte `auth_key`, dc_id=2, server 149.154.167.51:443. The accounts are fine.
- BUT their `sessions` table has a **6th column `tmp_auth_key`** (created by a patched/forked Telethon used by the vendor). Stock schema is 5 columns.
- Telethon 1.42.0 `SQLiteSession.__init__` runs `select * from sessions` and unpacks into exactly 5 vars →
  `ValueError: too many values to unpack (expected 5)` → our `except Exception` in
  `sqlite_to_string_session` (account_import.py:282-284) maps it to `empty_or_invalid_session`.
- Reproduced in api container (telethon 1.42.0): `sqlite_to_string_session(bytes)` →
  `RAISED ValueError empty_or_invalid_session / CAUSE too many values to unpack (expected 5)`.

## Proposed fixes (pending user confirmation — CLAUDE.md: no code before approval)
1. **Bug A:** widen `VendorAccountJson.proxy` to accept list/str/dict, and normalize the
   list/tuple form in `resolve_import_proxy` (or fall back to pool if shape unknown). Do not
   silently drop the account just because proxy shape differs.
2. **Bug B:** make `sqlite_to_string_session` schema-tolerant — read `dc_id, server_address,
   port, auth_key` explicitly from the `sessions` table via sqlite3 and build the
   `StringSession` directly, instead of relying on Telethon's fragile `SELECT *`. Keep the
   empty / NULL-auth_key guard. Add a regression test with a 6-column vendor session fixture.

## Evidence artifacts
- Staged ZIPs still in DB `account_import_stagings` (`1b3efeac`, `c102bb4a`) until ~12:53–12:54 UTC.
- Extracted sample: `79327152566.session` = 28672 B, `SQLite format 3`, auth_key present len=256.

## Current Focus
hypothesis: two independent vendor-format mismatches (proxy=list; session table +tmp_auth_key col)
next_action: await user go-ahead, then implement fixes 1 & 2 + regression tests
