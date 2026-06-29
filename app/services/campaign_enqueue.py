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
from app.services.recontact import protected_conversation_sql
from app.services.template import render_template
from app.utils.phone import contact_identity_key

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
                SELECT id, workspace_id, folder_id, message_template, start_date,
                       allow_recontact, recontact_min_age_days
                FROM campaigns
                WHERE status = 'running'
            """))
            campaigns = campaigns_rows.fetchall()
            total_enqueued = 0
            for c in campaigns:
                # Auto-pause (029): if the campaign can no longer send (no
                # eligible sender) while work remains, flip it to paused with a
                # reason so the UI shows it needs attention — then skip enqueue.
                if await self._maybe_autopause(db, c):
                    continue
                enqueued = await self._tick_one_campaign(db, c)
                total_enqueued += enqueued
            if total_enqueued > 0:
                logger.info(
                    "📤 CampaignEnqueueWorker tick: enqueued %s items across %s campaigns",
                    total_enqueued, len(campaigns),
                )
            return total_enqueued

    async def _maybe_autopause(self, db: AsyncSession, c) -> bool:
        """Auto-pause a running campaign that can no longer send (029).

        Hard-blocker only: a campaign is paused iff it has ZERO eligible senders
        (pool empty, or every attached sender restricted/offline/auth-failed)
        AND there is still outstanding work (pending/processing items, or
        registered contacts not yet assigned). A campaign with nothing left to do
        is left alone — it is effectively finished, not blocked.

        Sets ``status='paused'``, ``pause_reason`` and ``paused_at`` so the UI can
        surface why the outreach stopped. The reason is cleared on start/resume.
        Returns True if the campaign was paused (caller skips enqueue).
        """
        # Eligible-sender predicate copied from rotation.py:113-123 — keep in sync.
        eligible = (await db.execute(
            text("""
                SELECT COUNT(*)
                FROM campaign_senders cs
                JOIN senders s ON s.id = cs.sender_id
                WHERE cs.campaign_id = :cid
                  AND s.lifecycle_status = 'active'
                  AND s.auth_status = 'ok'
                  AND s.role = 'sender'
                  AND s.restriction_status = 'none'
            """),
            {"cid": str(c.id)},
        )).scalar()
        if eligible and eligible > 0:
            return False

        # No eligible sender — pause only if there is still work to do.
        has_pending = (await db.execute(
            text("""
                SELECT EXISTS(
                    SELECT 1 FROM message_queue
                    WHERE campaign_id = :cid AND status IN ('pending', 'processing')
                )
            """),
            {"cid": str(c.id)},
        )).scalar()

        has_unassigned = False
        if c.folder_id is not None:
            has_unassigned = (await db.execute(
                text("""
                    SELECT EXISTS(
                        SELECT 1 FROM contacts ct
                        WHERE ct.folder_id = :fid
                          AND ct.workspace_id = :wid
                          AND ct.tg_status = 'registered'
                          AND (ct.phone IS NOT NULL OR ct.username IS NOT NULL)
                          AND COALESCE(ct.phone, '@' || ct.username) NOT IN (
                              SELECT contact_phone FROM campaign_contact_assignments
                              WHERE campaign_id = :cid
                          )
                    )
                """),
                {"fid": str(c.folder_id), "wid": str(c.workspace_id), "cid": str(c.id)},
            )).scalar()

        if not (has_pending or has_unassigned):
            return False  # nothing to send — leave it running (effectively done)

        attached = (await db.execute(
            text("SELECT COUNT(*) FROM campaign_senders WHERE campaign_id = :cid"),
            {"cid": str(c.id)},
        )).scalar()
        reason = "no_senders_attached" if not attached else "senders_unavailable"

        await db.execute(
            text("""
                UPDATE campaigns
                SET status = 'paused', pause_reason = :reason, paused_at = NOW()
                WHERE id = :cid AND status = 'running'
            """),
            {"reason": reason, "cid": str(c.id)},
        )
        await db.commit()
        logger.warning(
            "⏸ Campaign %s auto-paused: %s (no eligible senders, work pending)",
            c.id, reason,
        )
        return True

    async def _tick_one_campaign(self, db: AsyncSession, c) -> int:
        """Process one campaign per tick. Atomic per-contact transaction (savepoint).

        Phase 02.1 CR-03 pattern: explicit ``workspace_id`` guard in JOIN.
        AUDIT Q5: INSERT cca + INSERT message_queue inside one savepoint —
        rollback if any fails; next tick re-selects the same contact.
        """
        # Re-contact dedup. A prior conversation blocks this campaign's cold
        # opener only when it belongs to the SAME campaign OR is handled by a
        # sender that is also in THIS campaign's pool (i.e. the same agent could
        # be routed to). A different campaign run by a different agent (no sender
        # overlap) is free to re-contact — see `conv_identity_scope` below.
        # On top of that identity scope: by default ANY such conversation blocks;
        # when the campaign opts in via allow_recontact, only a PROTECTED (live &
        # fresh) dialog blocks — closed/stale ones become eligible again. The
        # protected predicate is shared with queue._upsert_conversation via
        # recontact.py.
        params = {
            "fid": str(c.folder_id),
            "wid": str(c.workspace_id),
            "cid": str(c.id),
            "lim": self.batch_size,
        }
        if getattr(c, "allow_recontact", False):
            conv_dedup_filter = "AND " + protected_conversation_sql("age_days")
            params["age_days"] = int(c.recontact_min_age_days)
        else:
            conv_dedup_filter = ""  # strict: any conversation blocks re-contact

        # SELECT eligible contacts (Pitfall 8: explicit workspace_id guard in WHERE).
        # M4 (revision per plan): tg_status='registered' confirmed in CHECK
        # constraint of migration 013 (lines 39-40).
        contacts_rows = await db.execute(
            text(f"""
                SELECT id, phone, full_name, username, source, custom,
                       workspace_id, folder_id
                FROM contacts
                WHERE folder_id = :fid
                  AND workspace_id = :wid
                  AND tg_status = 'registered'
                  AND (phone IS NOT NULL OR username IS NOT NULL)
                  -- identity key: phone wins, else '@username' (migration 025)
                  AND COALESCE(phone, '@' || username) NOT IN (
                      SELECT contact_phone FROM campaign_contact_assignments
                      WHERE campaign_id = :cid
                  )
                  -- Re-contact dedup, scoped to identity (same campaign OR a
                  -- sender shared with this campaign's pool). A different
                  -- campaign run by a different agent re-contacts freely.
                  -- conversations.campaign_id is SET NULL on campaign deletion,
                  -- so a deleted campaign only keeps blocking via sender overlap.
                  AND COALESCE(phone, '@' || username) NOT IN (
                      SELECT contact_phone FROM conversations
                      WHERE workspace_id = :wid AND contact_phone IS NOT NULL
                        AND (
                            campaign_id = :cid
                            OR sender_id IN (
                                SELECT sender_id FROM campaign_senders
                                WHERE campaign_id = :cid
                            )
                        )
                      {conv_dedup_filter}
                  )
                LIMIT :lim
            """),
            params,
        )
        contacts = contacts_rows.fetchall()
        if not contacts:
            return 0

        now_utc = datetime.now(timezone.utc)
        scheduled_at = max(now_utc, c.start_date) if c.start_date else now_utc

        enqueued = 0
        for contact in contacts:
            # Identity key: phone (+7…) or '@username'. Used as the pipeline key
            # across rotation, queue and conversation (migration 025).
            identity = contact_identity_key(contact.phone, contact.username)
            if identity is None:
                # Defensive: SELECT already filters this out, but never enqueue
                # a contact with neither phone nor username.
                continue
            try:
                # Q5: atomic per-contact transaction (savepoint inside outer).
                async with db.begin_nested():
                    # 1. Rotation — assign sender (commit=False per M2 revision).
                    sender = await get_or_assign_sender(
                        c.id, identity, db, commit=False
                    )
                    if sender is None:
                        logger.warning(
                            "CampaignEnqueueWorker: no sender for contact %s in campaign %s",
                            identity, c.id,
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
                        phone=identity,
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
                            "phone": identity,
                            "name": contact.full_name or "",
                            "text": rendered,
                            "scheduled": scheduled_at,
                        },
                    )
                    enqueued += 1
            except Exception as exc:  # noqa: BLE001 — savepoint rolled back; try next
                logger.error(
                    "CampaignEnqueueWorker: error enqueuing contact %s in campaign %s: %s",
                    identity, c.id, exc, exc_info=True,
                )
                continue

        # Commit the outer transaction (savepoint commits/rolls back per contact).
        await db.commit()
        return enqueued


async def rerender_pending_queue(db: AsyncSession, campaign) -> int:
    """Re-render message_text of all pending queue items for `campaign`.

    The queue snapshots the rendered opener at enqueue time, so editing
    `campaign.message_template` afterwards does NOT reach rows already sitting in
    the queue. This re-renders every `status='pending'`, `item_type='message'` row
    for the campaign with the CURRENT template, using the same render path the
    enqueue worker uses (render_template + the contact's fields).

    Each pending row is matched back to its contact by identity key
    (`recipient_phone == COALESCE(phone,'@'||username)`) within the campaign's
    folder, so `{{variables}}` render with the same data. When the contact is no
    longer in the folder (moved / deleted), it falls back to a minimal contact
    built from the queue row (`recipient_name` + phone) so `{{имя}}`/`{{name}}`
    still resolve from the stored name.

    Safety:
      - Empty / blank template → no-op (returns 0); never blanks a message.
      - `UPDATE … WHERE id=:id AND status='pending'` re-checks status per row, so a
        row the send worker grabbed meanwhile is skipped (no clobber of in-flight).
      - Does NOT commit — the caller owns the transaction.

    Returns: number of pending rows actually re-rendered.
    """
    template = (campaign.message_template or "").strip()
    if not template:
        return 0

    pending = (await db.execute(
        text("""
            SELECT id, recipient_phone, recipient_name
            FROM message_queue
            WHERE campaign_id = :cid
              AND status = 'pending'
              AND item_type = 'message'
        """),
        {"cid": str(campaign.id)},
    )).fetchall()
    if not pending:
        return 0

    # Build identity → contact_dict map from the campaign's folder (for {{vars}}).
    contacts_by_identity: dict[str, dict] = {}
    if campaign.folder_id is not None:
        crows = (await db.execute(
            text("""
                SELECT phone, username, full_name, source, custom
                FROM contacts
                WHERE folder_id = :fid AND workspace_id = :wid
            """),
            {"fid": str(campaign.folder_id), "wid": str(campaign.workspace_id)},
        )).fetchall()
        for r in crows:
            identity = contact_identity_key(r.phone, r.username)
            if identity is None:
                continue
            contacts_by_identity[identity] = {
                "full_name": r.full_name,
                "username": r.username,
                "phone": r.phone,
                "source": r.source,
                "custom": r.custom or {},
            }

    updated = 0
    for row in pending:
        contact_dict = contacts_by_identity.get(row.recipient_phone) or {
            # Contact gone from the folder — fall back to the stored snapshot fields
            # so {{имя}}/{{name}} still render; other vars smart-trim to empty.
            "full_name": row.recipient_name or "",
            "username": None,
            "phone": row.recipient_phone,
            "source": None,
            "custom": {},
        }
        rendered = render_template(
            template,
            contact_dict,
            campaign_id=str(campaign.id),
            phone=row.recipient_phone,
        )
        res = await db.execute(
            text("""
                UPDATE message_queue
                SET message_text = :txt
                WHERE id = :id AND status = 'pending'
            """),
            {"txt": rendered, "id": str(row.id)},
        )
        updated += res.rowcount or 0

    return updated


# Module-level singleton — registered in app/main.py lifespan.
campaign_enqueue_worker = CampaignEnqueueWorker()
