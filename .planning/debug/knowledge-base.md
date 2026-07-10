# GSD Debug Knowledge Base

Resolved debug sessions. Used by `gsd-debugger` to surface known-pattern hypotheses at the start of new investigations.

---

## tdata-archive-import-fails — bulk import silently recognizes 0 accounts when vendor ships Telegram Desktop tdata instead of .session+.json
- **Date:** 2026-07-10
- **Error patterns:** bulk import, account import, archive, zip, tdata, key_datas, D877F783D5D3EF8C, TDF$, matched=0, unpack_and_pair, "не могу загрузить архив", preview empty, Telegram Desktop
- **Root cause:** `app/services/account_import.py::unpack_and_pair` recognizes accounts ONLY as `<phone>.json` + `<phone>.session` pairs (grouping strictly by file extension). A vendor tdata export (folders `+<phone>/tdata/` with `key_datas` / `D877F783D5D3EF8C…` / magic `TDF$`, no .session/.json) yields an all-empty recognized set with no error — user sees "can't upload archive". Not a regression; importer never supported tdata.
- **Fix:** (1) One-off out-of-product converter `scripts/tdata_to_session.py` (opentele in an isolated py3.11 container — opentele pulls its own Telethon and breaks on 3.13, needs libglib2.0-0 for PyQt5; `TDesktop(tdata).ToTelethon(UseCurrentSession)` is OFFLINE) → .session + minimal .json → normal importer. (2) UX: `UnsupportedArchiveError` (422 UNSUPPORTED_ARCHIVE) + `_looks_like_tdata`; `unpack_and_pair` now raises a clear message (tdata-specific when detected) instead of a mute empty result. Imported 10/10 as senders (job ecc46772), each got a distinct proxy_pool row.
- **Files changed:** app/services/account_import.py, tests/test_account_import.py, scripts/tdata_to_session.py
---
