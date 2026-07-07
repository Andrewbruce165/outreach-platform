---
slug: tg-import-vendor-proxy-dead
status: resolved
trigger: "После фикса tg-import-archive-errors аккаунты всё равно не загружаются — выдаёт ошибку"
created: 2026-07-07
updated: 2026-07-07
goal: find_and_fix
root_cause: "Vendor archives ship a residential proxy (proxyshard) that is subscription-exhausted / dead — SOCKS5 returns '402: user reached limit' then hard-times-out. resolve_import_proxy honoured the vendor proxy first (D-15), so every account with a vendor proxy stranded at connect_failed. Accounts with proxy=null fell back to our pool and imported fine — which is exactly why the split (25 imported / 13 connect_failed) looked random."
fix: "resolve_import_proxy now IGNORES the vendor JSON proxy entirely and ALWAYS assigns a free workspace ProxyPool row (overrides D-15). Empty pool → (None,None) + WARNING log (no hard-fail). _normalize_vendor_proxy kept (still unit-tested, documents vendor shapes) but no longer selected. + regression test."
verification: "16/16 account-import tests pass via test-overlay (incl. new test_resolve_import_proxy_ignores_vendor_and_uses_pool). Direct SOCKS5 connect through the vendor proxy = timeout (confirmed dead). api+listener rebuilt, clean startup, no more 'user reached limit' in logs after deploy."
files_changed: "app/services/account_import.py, tests/test_account_import.py"
---

# Debug: imported accounts fail at connect — dead vendor proxy (Phase 21, follow-up)

Sequel to `resolved/tg-import-archive-errors.md`. That fix (proxy-as-list schema +
schema-tolerant session converter) WORKED — sessions now convert and accounts reach the
connect stage. This session is the NEXT failure that surfaced there.

## Symptoms
- After the archive-errors fix, re-import produced: **25 imported OK**, but **13 distinct
  accounts stuck `connect_failed`** (26 rows across two duplicate jobs) + 6 leftover
  `convert_failed` from the pre-fix job `81c1afd4`.
- API logs, tight loop: `GeneralProxyError: Socket error: 402: user reached limit` →
  `[account-import] connect_failed for 79325***/79326***: Connection to Telegram failed 5 time(s)`.

## Root cause (CONFIRMED)
- The 13 failing accounts all carry the SAME vendor proxy in their JSON:
  `[3, "resident.proxyshard.com", 8080, true, "skt49EgXXr-country-any-filter-medium", "v16JgqHed3"]`.
- That residential proxy's subscription is exhausted → SOCKS5 `402: user reached limit`,
  then hard timeout. Direct `socks.socksocket().connect(('149.154.167.51',443))` through it
  = `GeneralProxyError: Socket error: timed out` (reproduced in the api container).
- `resolve_import_proxy` honoured the vendor proxy FIRST (D-15). Accounts with `proxy=null`
  fell through to our Decodo pool and imported — hence the 25-ok / 13-failed split.

## Fix (DEPLOYED)
- `resolve_import_proxy` (app/services/account_import.py) — dropped the vendor-proxy-wins
  branch; ALWAYS take a free `proxy_pool` row for the workspace. Empty pool → `(None, None)`
  + WARNING (accounts connect direct, logged, not hard-failed). **Explicitly overrides D-15**
  per user directive "не брать вендоровский прокси, всегда вешаем наши".
- Regression test `test_resolve_import_proxy_ignores_vendor_and_uses_pool`.

## Operational follow-up — the 19 stranded accounts must be RE-UPLOADED (cannot retry in place)
- Attempted an in-place retry (reset the 19 failed items `status→pending`). It FAILED: all
  19 came back `convert_failed (empty_or_invalid_session)`.
- **Why:** the worker NULLs `session_blob` the moment an item goes terminal
  (`account_import_worker.py:180`, security — don't retain live auth_key bytes). The 13
  `connect_failed` items lost their session bytes at 12:50; resetting status can't bring
  them back. Jobs self-healed back to `done` (blobs gone → re-fail → terminal again).
- **Correct path:** re-upload the two ZIP archives via the UI. A fresh import job carries
  fresh session bytes → converts → now routes through our pool → imports. Pool had 59/100
  free at fix time (plenty for 19).
- **Lesson:** never "retry" account-import items in place after they go terminal — the
  session bytes are purged by design. Re-import from the source archive instead.

## Bug #3 (surfaced after the proxy fix) — None fingerprint field → initConnection TypeError
- After the proxy fix, re-upload connected to Telegram fine (proxy OK) but failed with
  `[account-import] connect_failed for 79325***: bytes or str expected, not <class 'NoneType'>`.
- **Root cause:** these vendor JSONs ship `lang_code`/`system_lang_code` as **null** (they
  only carry `lang_pack`/`system_lang_pack`). `build_fingerprint` emitted those as `None`,
  and `make_telegram_client`'s merge `{**_CLIENT_FINGERPRINT, **fingerprint}` let the `None`
  clobber the global default → Telethon serialized `lang_code=None` into `initConnection`
  (the first request after connect) → `TypeError`. Caught by the broad `except` and mislabeled
  `connect_failed`. Earlier-imported accounts had `lang_code` set (different vendor batch).
- **Fix (DEPLOYED):**
  - `make_telegram_client` (telegram.py:261) — merge now drops None-valued override keys:
    `{**_CLIENT_FINGERPRINT, **{k:v for k,v in (fingerprint or {}).items() if v is not None}}`.
  - `build_fingerprint` (account_import.py) — omits keys whose vendor value is None.
  - Regression test `test_fingerprint_none_fields_fall_back_to_global`.
- **VERIFIED end-to-end** on the real previously-failing account `79325116823` (from the live
  staging zip): fingerprint omits lang_code → routed via Decodo pool → `connect()` →
  `is_user_authorized()=True` → `get_me()` returned id 8606728473 / phone 79325116823. 17/17
  account-import tests pass.

## Current Focus
hypothesis: RESOLVED — 3 stacked bugs (proxy list-shape, dead vendor proxy, None fingerprint)
next_action: user re-uploads the 2 archives via UI → accounts now convert + connect + import
