#!/usr/bin/env python3
"""One-off backfill for the 2026-08-11 CSV import that landed username-only.

WHY
---
Bug (root cause: frontend keyed `mapping` by CSV column NAME while the backend
contract is column INDEX-as-string; apply_import silently dropped unresolvable
keys) → 77 contacts were imported into folder 6595094a-c693-4a62-8862-670e74cc09ff
at 2026-08-11 17:19:14 UTC with ONLY `username` populated. phone / full_name /
source / custom are all empty.
See .planning/debug/resolved/csv-contact-mapping-only-username-saved.md

The original CSV blob is NOT recoverable on the server: `csv_imports` rows are
deleted at the end of a successful import and the nightly dumps run at 03:05,
so no backup captured it. Therefore this script requires the operator to supply
the SAME source CSV file again (--csv) — it never invents data.

WHAT IT DOES
------------
* Parses the supplied CSV with the SAME production code path
  (app.services.csv_import.parse_preview + apply_import), so the mapping
  semantics are identical to a real import.
* Matches CSV rows to the existing 77 contacts by `username`
  (case-insensitive, '@' stripped) scoped to folder_id + the exact import
  timestamp. It NEVER inserts, never deletes, never touches other batches.
* Only fills columns that are currently empty (phone/full_name/source IS NULL,
  custom = '{}'). Non-empty values are left untouched → re-running is a no-op
  (idempotent).
* Guards, all abort before any write:
    - target row count must equal --expected-rows (default 77)
    - a CSV username must match exactly one target row (0 or >1 → skipped+reported)
    - a phone that already exists on another contact in the same workspace is
      skipped+reported (unique index idx_contacts_workspace_phone_unique)
* tg_status / tg_* resolution columns are NOT modified (these rows are already
  'registered' via the username shortcut).

USAGE (default is DRY-RUN — read-only SELECTs, nothing is written)
-----------------------------------------------------------------
The prod api image does not contain scripts/, so mount it for a one-off run.
NOTE: this uses the PROD DATABASE_URL on purpose — never add `pytest` to such a
command (see docker-compose.test.yml header for why).

    cd /root/apps/aimly/tg-outreach
    docker compose run --rm --no-deps \
        -v /root/apps/aimly/tg-outreach/scripts:/app/scripts:ro \
        -v /path/to/base.csv:/tmp/base.csv:ro \
        api python scripts/backfill_csv_import_20260811.py \
            --csv /tmp/base.csv \
            --map "Юзернейм=username" --map "Телефон=phone" \
            --map "ФИО=full_name" --map "Компания=custom.company"

    # or let the header heuristic pick the canonical fields:
    #   … api python scripts/backfill_csv_import_20260811.py --csv /tmp/base.csv --auto

    # then, and only after reviewing the dry-run plan, append --apply

Take a fresh dump before --apply:  /root/apps/aimly/tg-outreach/backup.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.csv_import import (  # noqa: E402
    apply_import,
    parse_preview,
    suggest_mapping,
)

DEFAULT_FOLDER_ID = "6595094a-c693-4a62-8862-670e74cc09ff"
DEFAULT_BATCH_TS = "2026-08-11 17:19:14+00"
DEFAULT_EXPECTED_ROWS = 77

# Columns this backfill is allowed to write. Deliberately excludes username
# (the join key) and every tg_* column.
FILLABLE = ("phone", "full_name", "source")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True, help="path to the ORIGINAL source CSV")
    p.add_argument("--folder-id", default=DEFAULT_FOLDER_ID)
    p.add_argument("--batch-ts", default=DEFAULT_BATCH_TS)
    p.add_argument("--expected-rows", type=int, default=DEFAULT_EXPECTED_ROWS)
    p.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="KEY=FIELD",
        help="mapping entry, e.g. --map 'Телефон=phone' --map 'Компания=custom.company'. "
        "KEY = column name or index; FIELD = phone|username|full_name|source|custom.<key>",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="use suggest_mapping() on the header row (canonical fields only)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="actually write. WITHOUT this flag the script only prints the plan.",
    )
    return p.parse_args()


def build_mapping(args: argparse.Namespace, columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if args.auto:
        mapping.update(suggest_mapping(columns))
    for entry in args.map:
        if "=" not in entry:
            sys.exit(f"ABORT: --map entry {entry!r} is not KEY=FIELD")
        key, field = entry.split("=", 1)
        mapping[key.strip()] = field.strip()
    if not mapping:
        sys.exit("ABORT: no mapping given (use --auto and/or --map KEY=FIELD)")
    # username is required as the join key.
    if "username" not in mapping.values():
        sys.exit(
            "ABORT: mapping must include a username column — it is the join key "
            "used to match CSV rows to the existing contacts."
        )
    return mapping


async def main() -> int:
    args = parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        sys.exit(f"ABORT: {csv_path} not found (docker cp the CSV into the container)")
    file_bytes = csv_path.read_bytes()

    preview = parse_preview(file_bytes)
    mapping = build_mapping(args, preview["columns"])
    print(f"CSV columns : {preview['columns']}")
    print(f"encoding    : {preview['encoding']}  delimiter: {preview['delimiter']!r}")
    print(f"mapping     : {mapping}")

    applied = apply_import(
        file_bytes,
        mapping=mapping,
        delimiter=preview["delimiter"],
        encoding=preview["encoding"],
    )
    if applied["unresolved_mapping_keys"] or applied["unknown_mapping_fields"]:
        sys.exit(
            "ABORT: mapping does not resolve cleanly against this CSV: "
            f"unresolved={applied['unresolved_mapping_keys']} "
            f"unknown={applied['unknown_mapping_fields']}"
        )

    # username → csv record (last wins is NOT allowed: duplicates abort loudly)
    by_username: dict[str, dict] = {}
    dup_usernames: list[str] = []
    for rec in applied["rows_to_insert"]:
        if not rec["username"]:
            continue
        k = rec["username"].lstrip("@").lower()
        if k in by_username:
            dup_usernames.append(k)
            continue
        by_username[k] = rec
    print(
        f"CSV parsed  : {applied['total']} data rows, "
        f"{len(by_username)} unique usernames, "
        f"{len(dup_usernames)} duplicate usernames ignored, "
        f"{applied['skipped_invalid']} invalid rows"
    )

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, workspace_id, username, phone, full_name, source, custom
                      FROM contacts
                     WHERE folder_id = :folder_id
                       AND date_trunc('second', created_at) = CAST(:batch_ts AS timestamptz)
                     ORDER BY created_at, username
                    """
                ),
                {
                    "folder_id": UUID(args.folder_id),
                    # asyncpg wants a real datetime, not a string literal.
                    "batch_ts": datetime.fromisoformat(args.batch_ts),
                },
            )
        ).mappings().all()

        print(f"DB targets  : {len(rows)} contacts in folder {args.folder_id} @ {args.batch_ts}")
        if len(rows) != args.expected_rows:
            sys.exit(
                f"ABORT (count guard): expected {args.expected_rows} target rows, "
                f"found {len(rows)}. Refusing to touch prod."
            )

        workspace_ids = {r["workspace_id"] for r in rows}
        if len(workspace_ids) != 1:
            sys.exit(f"ABORT: targets span {len(workspace_ids)} workspaces — refusing")
        workspace_id = workspace_ids.pop()

        # Phones already used elsewhere in this workspace (unique index guard).
        taken = {
            r["phone"]: r["id"]
            for r in (
                await db.execute(
                    text(
                        "SELECT id, phone FROM contacts "
                        " WHERE workspace_id = :ws AND phone IS NOT NULL"
                    ),
                    {"ws": workspace_id},
                )
            ).mappings()
        }

        planned: list[tuple[UUID, str, dict]] = []
        no_csv_match: list[str] = []
        nothing_to_do: list[str] = []
        phone_conflicts: list[str] = []

        for r in rows:
            uname = (r["username"] or "").lstrip("@").lower()
            rec = by_username.get(uname)
            if rec is None:
                no_csv_match.append(r["username"] or "<null>")
                continue

            updates: dict[str, object] = {}
            for col in FILLABLE:
                new = rec.get(col)
                if new and not r[col]:  # fill only what is empty
                    if col == "phone":
                        owner = taken.get(new)
                        if owner is not None and owner != r["id"]:
                            phone_conflicts.append(f"{r['username']} → {new}")
                            continue
                        taken[new] = r["id"]
                    updates[col] = new

            merged_custom = dict(r["custom"] or {})
            for k, v in (rec.get("custom") or {}).items():
                if v and k not in merged_custom:
                    merged_custom[k] = v
            if merged_custom != (r["custom"] or {}):
                updates["custom"] = merged_custom

            if not updates:
                nothing_to_do.append(r["username"] or "<null>")
                continue
            planned.append((r["id"], r["username"], updates))

        print()
        print(f"PLAN: {len(planned)} rows to update, {len(nothing_to_do)} already complete")
        if no_csv_match:
            print(f"  ! {len(no_csv_match)} DB contacts have NO row in this CSV: {no_csv_match[:10]}")
        if phone_conflicts:
            print(f"  ! {len(phone_conflicts)} phone(s) already used by another contact — skipped: {phone_conflicts[:10]}")
        if dup_usernames:
            print(f"  ! duplicate usernames in CSV (first occurrence used): {dup_usernames[:10]}")
        for cid, uname, upd in planned[:10]:
            print(f"    {uname:<24} {upd}")
        if len(planned) > 10:
            print(f"    … and {len(planned) - 10} more")

        if not args.apply:
            print()
            print("DRY-RUN — nothing written. Re-run with --apply to commit.")
            return 0
        if not planned:
            print("Nothing to do.")
            return 0

        # Idempotent, id-scoped, empty-only UPDATE. No INSERT, no DELETE.
        touched = 0
        for cid, _uname, upd in planned:
            params: dict[str, object] = {"cid": cid}
            set_parts: list[str] = []
            for col, val in upd.items():
                if col == "custom":
                    set_parts.append("custom = CAST(:custom AS jsonb)")
                    params["custom"] = json.dumps(val, ensure_ascii=False)
                else:
                    set_parts.append(f"{col} = :{col}")
                    params[col] = val
            # Re-check emptiness at write time so a concurrent edit is never
            # clobbered (and a second run of this script is a no-op).
            guards = [f"{c} IS NULL" for c in upd if c in FILLABLE]
            where = " AND ".join(["id = :cid"] + guards)
            res = await db.execute(
                text(
                    f"UPDATE contacts SET {', '.join(set_parts)}, updated_at = now() "
                    f" WHERE {where}"
                ),
                params,
            )
            touched += res.rowcount or 0
        await db.commit()
        print(f"APPLIED: {touched} rows updated (planned {len(planned)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
