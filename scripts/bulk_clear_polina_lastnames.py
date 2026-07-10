"""One-off: clear the Telegram last_name for every sender whose current name
starts with "Polina"/"Polna" and has a last-name part set.

Uses the real write path (telegram_service.update_profile with an explicit
last_name="" — clears the field, per the just-fixed null-vs-empty-string
behaviour) and its post-write verification, so a silent Telegram-side
rejection (rate limit) raises ProfileChangeRejectedError instead of a false
success. Mirrors app/routers/senders.py::update_sender_profile field mapping.

Run inside the api container:

    docker exec -it outreach-platform-api python -m scripts.bulk_clear_polina_lastnames

Writes to real Telegram accounts — this is NOT read-only.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, or_
from telethon.tl.functions.account import UpdateProfileRequest

from app.database import AsyncSessionLocal
from app.models import Sender
from app.services.telegram import (
    telegram_service,
    SessionAuthError,
    ProfileChangeRejectedError,
)

DELAY_SECONDS = 5


def _stamp(sender: Sender, field: str) -> None:
    changed = dict(sender.profile_field_changed_at or {})
    changed[field] = datetime.now(timezone.utc).isoformat()
    sender.profile_field_changed_at = changed


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Sender).where(
                or_(Sender.name.ilike("Polina%"), Sender.name.ilike("Polna%"))
            )
        )
        senders = [s for s in result.scalars().all() if " " in s.name.strip()]
        print(f"{len(senders)} senders with a last name to clear")

        ok, rejected, skipped, failed = 0, 0, 0, 0
        for s in senders:
            first_name_only = s.name.strip().split(" ", 1)[0]
            req = UpdateProfileRequest(first_name=None, last_name="", about=None)
            try:
                await telegram_service.update_profile(
                    s.slug, str(s.id), s.session_string, req,
                    proxy=s.proxy, fingerprint=s.client_fingerprint,
                )
            except SessionAuthError as e:
                print(f"SKIP {s.slug}: auth error ({e.auth_status})")
                skipped += 1
                await asyncio.sleep(DELAY_SECONDS)
                continue
            except ProfileChangeRejectedError as e:
                print(f"REJECTED {s.slug} ({s.name!r}): Telegram did not apply — {e.fields}")
                rejected += 1
                await asyncio.sleep(DELAY_SECONDS)
                continue
            except Exception as e:  # noqa: BLE001 — best-effort bulk pass
                print(f"FAIL {s.slug} ({s.name!r}): {type(e).__name__}: {e}")
                failed += 1
                await asyncio.sleep(DELAY_SECONDS)
                continue

            before = s.name
            s.name = first_name_only
            _stamp(s, "name")
            await db.commit()
            ok += 1
            print(f"OK   {s.slug}: {before!r} -> {s.name!r}")
            await asyncio.sleep(DELAY_SECONDS)

        print(f"\nDone. ok={ok} rejected={rejected} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
