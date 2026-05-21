"""Persistent onboarding state (Phase 2 — D-16, D-17).

Helpers around the ``onboarding_sessions`` table that replace the legacy
in-memory ``_onboarding_sessions: dict`` from ``app/routers/onboarding.py``:

* ``save_state`` / ``load_state`` / ``update_status`` / ``delete_session``
  encapsulate workspace-scoped CRUD over ``onboarding_sessions``.
* ``OnboardingCleanupWorker`` is a periodic asyncio worker that deletes rows
  whose ``expires_at`` has passed (TTL = 10 min, cleanup interval = 5 min).

NB: Telethon's ``TelegramClient`` object is NOT serialisable (D-17), so it
stays in an in-process dict inside ``app/routers/onboarding.py``. Only the
``phone_code_hash`` + encrypted ``session_string`` (which already carries DC
routing after ``send_code_request``) are persisted here — that is sufficient
to recover the flow after an api-container restart by decrypting the session
string and reconnecting Telethon.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import OnboardingSession
from app.services.encryption import encrypt_session

logger = logging.getLogger(__name__)

# D-16: TTL for an onboarding flow. Telegram code typically expires faster
# (~2 min), but we give the user some slack to retry verify-code without
# losing the saved client state.
ONBOARDING_TTL_MINUTES = 10

# Cleanup tick interval — overridable via env for testing (default 5 min).
ONBOARDING_CLEANUP_INTERVAL_SEC = int(
    os.environ.get("ONBOARDING_CLEANUP_INTERVAL", "300")
)

_VALID_ROLES = ("sender", "checker")
_VALID_STATUSES = ("code_sent", "awaiting_2fa", "completed", "failed")


async def save_state(
    db: AsyncSession,
    workspace_id: UUID,
    phone: str,
    phone_code_hash: str,
    session_string: str,
    role: str = "sender",
    proxy: Optional[dict] = None,
) -> UUID:
    """Insert an ``onboarding_sessions`` row and return its id.

    ``session_string`` is encrypted via ``encrypt_session`` before persistence.
    Initial ``status`` = ``'code_sent'``, ``expires_at`` = now + TTL.
    Raises ``ValueError`` if ``role`` is not one of ``('sender', 'checker')``.
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ONBOARDING_TTL_MINUTES)
    row = OnboardingSession(
        id=uuid4(),
        workspace_id=workspace_id,
        phone=phone,
        phone_code_hash=phone_code_hash,
        encrypted_session_string=encrypt_session(session_string),
        role=role,
        proxy=proxy,
        status="code_sent",
        expires_at=expires_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    phone_masked = (phone[:6] + "***") if phone else "<empty>"
    logger.info(
        f"[onboarding-state] saved id={str(row.id)[:8]} "
        f"phone={phone_masked} role={role} workspace={str(workspace_id)[:8]}"
    )
    return row.id


async def load_state(
    db: AsyncSession,
    session_id: UUID,
    workspace_id: UUID,
) -> Optional[OnboardingSession]:
    """Load the onboarding row IFF it belongs to ``workspace_id`` and has not
    expired. Returns ``None`` otherwise (caller maps to 404 SESSION_NOT_FOUND).

    Note: this is the workspace-scoped lookup; for an admin-style read that
    needs to see expired rows (e.g. ``qr-status`` "completed_or_expired"
    branch), the router does its own raw ``SELECT``.
    """
    result = await db.execute(
        select(OnboardingSession).where(
            OnboardingSession.id == session_id,
            OnboardingSession.workspace_id == workspace_id,
            # TODO(v2-rls): app-level filter replaced by RLS policy app.workspace_id
        )
    )
    row = result.scalars().first()
    if row is None:
        return None
    expires_at = row.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        # Defensive: Postgres returns tz-aware, but SQLite-in-tests may not.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        return None
    return row


async def update_status(
    db: AsyncSession,
    session_id: UUID,
    status: str,
    encrypted_session_string: Optional[str] = None,
) -> None:
    """Update ``status`` (and optionally ``encrypted_session_string``).

    ``encrypted_session_string`` is passed already encrypted (callers refresh
    it from the Telethon client between flow steps).
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    if encrypted_session_string is not None:
        await db.execute(
            text(
                """
                UPDATE onboarding_sessions
                SET status = :status,
                    encrypted_session_string = :ess
                WHERE id = :sid
                """
            ),
            {"status": status, "ess": encrypted_session_string, "sid": str(session_id)},
        )
    else:
        await db.execute(
            text("UPDATE onboarding_sessions SET status = :status WHERE id = :sid"),
            {"status": status, "sid": str(session_id)},
        )
    await db.commit()
    logger.info(
        f"[onboarding-state] updated id={str(session_id)[:8]} status={status}"
    )


async def delete_session(db: AsyncSession, session_id: UUID) -> None:
    """Hard-delete an ``onboarding_sessions`` row (called on success or cancel)."""
    await db.execute(
        text("DELETE FROM onboarding_sessions WHERE id = :sid"),
        {"sid": str(session_id)},
    )
    await db.commit()


class OnboardingCleanupWorker:
    """Periodic cleanup of expired ``onboarding_sessions`` rows (D-16).

    Mirrors the lifecycle of ``QueueWorker`` / ``WarmupWorker`` so it can be
    started/stopped from FastAPI lifespan with the same shape:

        on startup:  onboarding_cleanup_worker.start()
        on shutdown: await onboarding_cleanup_worker.stop()
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.interval = ONBOARDING_CLEANUP_INTERVAL_SEC

    def start(self):
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="onboarding-cleanup")
            logger.info(
                f"[onboarding-cleanup] worker started (interval={self.interval}s)"
            )

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[onboarding-cleanup] worker stopped")

    async def _run(self):
        """Main loop — sleep first so cleanup never blocks startup."""
        while self._running:
            try:
                await asyncio.sleep(self.interval)
                if not self._running:
                    break
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 — worker must not die on errors
                logger.error(
                    f"[onboarding-cleanup] tick error: {e}", exc_info=True
                )

    async def _tick(self) -> int:
        """Delete expired rows; returns the count for tests/logging."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("DELETE FROM onboarding_sessions WHERE expires_at < NOW()")
            )
            await db.commit()
            deleted = result.rowcount or 0
            if deleted:
                logger.info(
                    f"[onboarding-cleanup] deleted {deleted} expired row(s)"
                )
            return deleted


# Module-level singleton — registered in app/main.py lifespan.
onboarding_cleanup_worker = OnboardingCleanupWorker()
