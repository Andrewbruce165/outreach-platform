"""One-off: resync every sender's cached Telegram profile from the live account.

Mirrors app/routers/senders.py::resync_sender_profile field mapping exactly.
Run inside the api container so DATABASE_URL / telegram_api_id / telegram_api_hash
are already configured:

    docker exec -it outreach-platform-api python -m scripts.bulk_resync_profiles

Read-only against Telegram (get_me + GetFullUserRequest + download_profile_photo),
writes only to the local `senders` cache columns. Skips auth-dead sessions
(session_expired/revoked/banned) and reports flood waits without retrying.
"""
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Sender
from app.services.telegram import telegram_service, SessionAuthError

DELAY_SECONDS = 3


async def main():
    async with AsyncSessionLocal() as db:
        senders = (await db.execute(select(Sender))).scalars().all()
        print(f"{len(senders)} senders found")

        ok, skipped, failed = 0, 0, 0
        for s in senders:
            try:
                res = await telegram_service.fetch_profile(
                    s.slug, str(s.id), s.session_string,
                    proxy=s.proxy, fingerprint=s.client_fingerprint,
                )
            except SessionAuthError as e:
                print(f"SKIP {s.slug}: auth error ({e.auth_status})")
                skipped += 1
                await asyncio.sleep(DELAY_SECONDS)
                continue
            except Exception as e:  # noqa: BLE001 — best-effort bulk pass, log and move on
                print(f"FAIL {s.slug}: {type(e).__name__}: {e}")
                failed += 1
                await asyncio.sleep(DELAY_SECONDS)
                continue

            res = res or {}
            before_name = s.name
            s.tg_username = res.get("username")
            s.tg_bio = res.get("bio")
            s.tg_premium = bool(res.get("premium", False))
            if res.get("first_name") is not None:
                composed = (
                    (res.get("first_name") or "")
                    + (" " + res["last_name"] if res.get("last_name") else "")
                ).strip()
                s.name = composed or s.name
            photo = res.get("photo")
            if photo is not None:
                s.tg_photo = photo
                s.tg_photo_mime = res.get("photo_mime") or "image/jpeg"
            elif res.get("has_photo") is False:
                s.tg_photo = None
                s.tg_photo_mime = None

            await db.commit()
            ok += 1
            changed = " (name changed: %r -> %r)" % (before_name, s.name) if before_name != s.name else ""
            print(f"OK   {s.slug}: username={s.tg_username!r} name={s.name!r}{changed}")
            await asyncio.sleep(DELAY_SECONDS)

        print(f"\nDone. ok={ok} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
