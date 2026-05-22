"""Phase 4 D-17: CampaignEnqueueWorker — generates queue items for running campaigns.

Pattern: ``ContactCheckWorker`` (Phase 2).
Module-level singleton, ``start()`` / ``stop()`` registered in FastAPI lifespan.

Tick algorithm (D-17):
    1. SELECT all running campaigns.
    2. Per campaign: SELECT contacts from ``folder_id`` where
       ``tg_status='registered'`` AND contact NOT IN ``campaign_contact_assignments``
       (LIMIT ``campaign_enqueue_batch_size``).
    3. Per contact:
        a. ``get_or_assign_sender(campaign_id, phone, db, commit=False)``
           inside ``begin_nested()`` savepoint (M2 revision: avoid double-commit).
        b. ``render_template(campaign.message_template, contact)``.
        c. INSERT into ``message_queue`` with ``campaign_id`` set.
        d. If anything fails — savepoint rolls back, next contact tried.
    4. ``db.commit()`` after the per-campaign batch.

Workspace isolation (Phase 02.1 CR-01 pattern): every INSERT carries
``workspace_id`` from the campaign — defence-in-depth against future FK
divergence.

Atomicity (AUDIT Q5): per-contact transaction via ``begin_nested()``.
``ON CONFLICT (campaign_id, contact_phone) DO NOTHING`` on cca protects
against concurrent worker races (v2 horizontal scale).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services.rotation import get_or_assign_sender
from app.services.template import render_template

logger = logging.getLogger(__name__)


class CampaignEnqueueWorker:
    """Background worker generating queue items from running campaigns' folder contacts."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        settings = get_settings()
        self.batch_size = settings.campaign_enqueue_batch_size
        self.poll_interval = settings.campaign_enqueue_tick_seconds

    def start(self):
        """Start background task. Idempotent."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="campaign-enqueue-worker")
            logger.info(
                "📤 CampaignEnqueueWorker started (batch=%s, poll=%ss)",
                self.batch_size, self.poll_interval,
            )

    async def stop(self):
        """Stop background task gracefully (cancel + await)."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📤 CampaignEnqueueWorker stopped")

    async def _run(self):
        """Main loop — sleep after tick so startup is not blocked."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — worker must not die
                logger.error("❌ CampaignEnqueueWorker tick error: %s", exc, exc_info=True)
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> int:
        """One tick — process all running campaigns. Returns total enqueued count."""
        async with AsyncSessionLocal() as db:
            campaigns_rows = await db.execute(text("""
                SELECT id, workspace_id, folder_id, message_template, start_date
                FROM campaigns
                WHERE status = 'running'
            """))
            campaigns = campaigns_rows.fetchall()
            total_enqueued = 0
            for c in campaigns:
                enqueued = await self._tick_one_campaign(db, c)
                total_enqueued += enqueued
            if total_enqueued > 0:
                logger.info(
                    "📤 CampaignEnqueueWorker tick: enqueued %s items across %s campaigns",
                    total_enqueued, len(campaigns),
                )
            return total_enqueued

    async def _tick_one_campaign(self, db: AsyncSession, c) -> int:
        """Process one campaign per tick. Atomic per-contact transaction (savepoint).

        Phase 02.1 CR-03 pattern: explicit ``workspace_id`` guard in JOIN.
        AUDIT Q5: INSERT cca + INSERT message_queue inside one savepoint —
        rollback if any fails; next tick re-selects the same contact.
        """
        # SELECT eligible contacts (Pitfall 8: explicit workspace_id guard in WHERE).
        # M4 (revision per plan): tg_status='registered' confirmed in CHECK
        # constraint of migration 013 (lines 39-40).
        contacts_rows = await db.execute(
            text("""
                SELECT id, phone, full_name, username, source, custom,
                       workspace_id, folder_id
                FROM contacts
                WHERE folder_id = :fid
                  AND workspace_id = :wid
                  AND tg_status = 'registered'
                  AND phone NOT IN (
                      SELECT contact_phone FROM campaign_contact_assignments
                      WHERE campaign_id = :cid
                  )
                LIMIT :lim
            """),
            {
                "fid": str(c.folder_id),
                "wid": str(c.workspace_id),
                "cid": str(c.id),
                "lim": self.batch_size,
            },
        )
        contacts = contacts_rows.fetchall()
        if not contacts:
            return 0

        now_utc = datetime.now(timezone.utc)
        scheduled_at = max(now_utc, c.start_date) if c.start_date else now_utc

        enqueued = 0
        for contact in contacts:
            try:
                # Q5: atomic per-contact transaction (savepoint inside outer).
                async with db.begin_nested():
                    # 1. Rotation — assign sender (commit=False per M2 revision).
                    sender = await get_or_assign_sender(
                        c.id, contact.phone, db, commit=False
                    )
                    if sender is None:
                        logger.warning(
                            "CampaignEnqueueWorker: no sender for contact %s in campaign %s",
                            contact.phone, c.id,
                        )
                        # Skip this contact — savepoint will commit (no-op since nothing inserted).
                        continue

                    # 2. Render template.
                    contact_dict = {
                        "full_name": contact.full_name,
                        "username": contact.username,
                        "phone": contact.phone,
                        "source": contact.source,
                        "custom": contact.custom or {},
                    }
                    rendered = render_template(
                        c.message_template,
                        contact_dict,
                        campaign_id=str(c.id),
                        phone=contact.phone,
                    )

                    # 3. INSERT queue item.
                    # workspace_id from campaign (defence-in-depth Pitfall 8).
                    await db.execute(
                        text("""
                            INSERT INTO message_queue
                                (workspace_id, campaign_id, sender_id, item_type, status,
                                 recipient_phone, recipient_name, message_text,
                                 scheduled_at, created_at)
                            VALUES
                                (:wid, :cid, :sid, 'message', 'pending',
                                 :phone, :name, :text,
                                 :scheduled, NOW())
                        """),
                        {
                            "wid": str(c.workspace_id),
                            "cid": str(c.id),
                            "sid": str(sender.id),
                            "phone": contact.phone,
                            "name": contact.full_name or "",
                            "text": rendered,
                            "scheduled": scheduled_at,
                        },
                    )
                    enqueued += 1
            except Exception as exc:  # noqa: BLE001 — savepoint rolled back; try next
                logger.error(
                    "CampaignEnqueueWorker: error enqueuing contact %s in campaign %s: %s",
                    contact.phone, c.id, exc, exc_info=True,
                )
                continue

        # Commit the outer transaction (savepoint commits/rolls back per contact).
        await db.commit()
        return enqueued


# Module-level singleton — registered in app/main.py lifespan.
campaign_enqueue_worker = CampaignEnqueueWorker()
