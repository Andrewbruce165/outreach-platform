"""One-off: convert Telegram Desktop **tdata** folders to Telethon ``.session`` files.

Context (2026-07-10 debug ``tdata-archive-import-fails``): a vendor shipped 10 new
accounts as tdata (``+<phone>/tdata/`` with ``key_datas`` / ``D877F783D5D3EF8Cs`` /
``D877F783D5D3EF8C/maps``, magic ``TDF$``). The product's bulk-import only recognizes
``.session`` + ``.json`` pairs, so nothing was imported. This script is a **one-off,
out-of-product** converter (chosen Variant C) — it is NOT wired into the product and
uses ``opentele`` in an isolated Python 3.11 environment (opentele pulls its own
Telethon and breaks on 3.13, so it must never enter the prod api container / requirements).

It runs OFFLINE: ``TDesktop(tdata).ToTelethon(flag=UseCurrentSession)`` reuses the
existing desktop authorization key and writes a Telethon ``.session`` without opening a
socket. Liveness (``get_me``) is confirmed SEPARATELY via the product's proxy path — not
here — so this step is network-free and low-risk.

Usage (inside a python:3.11 container with opentele installed):
    python tdata_to_session.py /work/extracted /work/out

Produces ``/work/out/<phone>.session`` + ``/work/out/<phone>.json`` for each account
folder, then prints a JSON summary to stdout.
"""

import asyncio
import json
import os
import sys


async def convert_one(tdata_path: str, session_path: str) -> dict:
    """Convert one tdata folder → a Telethon .session file, OFFLINE. Returns a status dict."""
    from opentele.td import TDesktop
    from opentele.api import UseCurrentSession

    tdesk = TDesktop(tdata_path)
    if not tdesk.isLoaded() or len(tdesk.accounts) == 0:
        return {"ok": False, "reason": "tdata_not_loaded"}

    # UseCurrentSession reuses the existing auth key from tdata — no login, no connect.
    # ToTelethon writes the .session (sessions table: dc_id/server_address/port/auth_key).
    client = await tdesk.ToTelethon(session=session_path, flag=UseCurrentSession)
    # Persist the session file without connecting (offline).
    client.session.save()
    try:
        await client.disconnect()
    except Exception:
        pass

    dc_id = getattr(tdesk.accounts[0], "MainDcId", None)
    return {"ok": True, "dc_id": int(dc_id) if dc_id is not None else None}


async def main() -> None:
    base, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    folders = sorted(d for d in os.listdir(base) if d.startswith("+"))
    results = []
    for folder in folders:
        phone = folder  # folder name IS the phone, e.g. +19154552285
        tdata_path = os.path.join(base, folder, "tdata")
        session_path = os.path.join(out, f"{phone}.session")
        json_path = os.path.join(out, f"{phone}.json")
        entry = {"phone": phone}
        try:
            res = await convert_one(tdata_path, session_path)
            entry.update(res)
            if res.get("ok"):
                # Minimal vendor JSON the importer accepts: session_file (pairing key) +
                # phone. proxy/2FA/device omitted → import falls back to pool proxy, no 2FA,
                # global desktop fingerprint (all handled by import_one_account).
                with open(json_path, "w") as fh:
                    json.dump({"session_file": phone, "phone": phone}, fh)
                entry["session_bytes"] = os.path.getsize(session_path)
        except Exception as exc:  # noqa: BLE001 — report per-account, never abort the batch
            entry.update({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
        results.append(entry)

    print("=== SUMMARY_JSON ===")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
