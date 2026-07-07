"""AccountImportWorker (Phase 21 — IMPT-02 / IMPT-07).

Background asyncio task in the api container's lifespan, mirroring
``KnowledgeIngestWorker`` (lifecycle, ``_run`` loop, single-op ``_tick``):

- Claim ONE ``account_import_items`` row ``WHERE status='pending' ORDER BY created_at
  LIMIT 1 FOR UPDATE SKIP LOCKED`` (concurrency-safe — a second worker / a stray start
  skips locked rows), flip it to ``processing`` and COMMIT immediately so a UI poll sees
  ``processing`` while the per-account network work runs.
- OUTSIDE the claim transaction, in a fresh session, call the 21-04 per-account routine
  ``account_import.import_one_account(db, item)`` — called by MODULE REFERENCE so tests
  monkeypatch ``app.services.account_import.import_one_account`` to a deterministic stub
  (mirrors the KB worker patching ``embed_texts``). The routine returns a result-code
  STRING (``imported`` / ``already_connected`` / ``auth_failed`` / ``convert_failed`` /
  ``banned`` / ``not_authorized`` / ``connect_failed`` / ``malformed_json`` / ``failed``)
  and NEVER raises for a per-account failure (D-10) — a broken pair fails its own item,
  the batch keeps going.
- In a fresh committed transaction: write the item's terminal ``ok`` / ``failed`` status +
  the result code, CLEAR the live session bytes (``session_blob = NULL`` — security once
  terminal), bump ``account_import_jobs.processed`` and flip the job to ``done`` once
  ``processed >= total``.
- The loop logs and CONTINUES on any per-item error (never dies — mirrors
  ``KnowledgeIngestWorker._run``). A crash mid-item leaves the item stuck in ``processing``
  (acceptable for v1, same note as the KB worker).

Lifecycle: ``start()`` / ``stop()`` — registered in ``app/main.py`` lifespan next to
``kb_ingest_worker``. ``_tick`` processes ONE item per call (predictable for callers/tests).
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import text

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services import account_import  # module ref so tests patch import_one_account

logger = logging.getLogger(__name__)

# Result-codes from import_one_account that mean the account IS connected/available (a
# successful outcome). Everything else is a per-file failure. ``already_connected`` is a
# successful dedup (D-14 / IMPT-06), not an error.
_OK_RESULTS = {"imported", "already_connected"}


class AccountImportWorker:
    """Background worker: claim a pending account_import_item → import one account → terminal.

    Singleton instance per process (module scope below). Lifecycle mirrors
    ``KnowledgeIngestWorker`` — ``start()`` in ``app/main.py`` lifespan, ``stop()`` in shutdown.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.poll_interval = get_settings().account_import_poll_interval

    def start(self):
        """Start the background task. Idempotent (a repeat start is a no-op)."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="account-import-worker")
            logger.info(
                f"📥 AccountImportWorker started (poll={self.poll_interval}s)"
            )

    async def stop(self):
        """Stop the background task gracefully (cancel + await, swallow Cancelled)."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📥 AccountImportWorker stopped")

    async def _run(self):
        """Main loop — sleep after each tick. Must never die on a per-item error."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — worker must not die
                logger.error(
                    f"❌ AccountImportWorker tick error: {exc}", exc_info=True
                )
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> int:
        """One tick: claim ONE pending item → import → terminal ok/failed. Returns 1 if an
        item was processed, 0 if none pending.

        Concurrency: ``FOR UPDATE SKIP LOCKED`` + an immediate committed flip to
        ``processing`` means a parallel worker / second tick skips an item already being
        worked. A worker crash mid-item leaves it stuck in ``processing`` — acceptable for
        v1 (same note as the KB worker).
        """
        # 1) Claim ONE pending item and flip it to processing in its own committed
        #    transaction, so a UI poll sees `processing` while the network work runs.
        async with AsyncSessionLocal() as db:
            async with db.begin():
                row = (await db.execute(
                    text(
                        """
                        SELECT id, job_id, workspace_id, basename, session_blob, vendor_json
                        FROM account_import_items
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """
                    )
                )).first()

                if row is None:
                    return 0

                item_id = str(row.id)
                job_id = str(row.job_id)
                workspace_id = str(row.workspace_id)
                basename = row.basename
                session_blob = row.session_blob
                vendor_json = row.vendor_json

                await db.execute(
                    text(
                        "UPDATE account_import_items "
                        "SET status = 'processing', updated_at = NOW() "
                        "WHERE id = :id"
                    ),
                    {"id": item_id},
                )
            # committed → UI poll sees `processing`

        # 2) Run the per-account import OUTSIDE the claim TX, in its own session. The
        #    routine is called by MODULE REFERENCE so tests can monkeypatch it. It reads
        #    workspace_id/basename/session_blob/vendor_json off the item and resolves the
        #    role from the item's job; it NEVER raises for a per-account failure (D-10) —
        #    the try/except is a belt-and-braces guard so an unexpected raise never stalls
        #    the item in `processing`.
        item = {
            "id": item_id,
            "job_id": job_id,
            "workspace_id": workspace_id,
            "basename": basename,
            # bytea may arrive as memoryview from the driver — normalise to bytes.
            "session_blob": bytes(session_blob) if session_blob is not None else b"",
            "vendor_json": vendor_json,
        }
        try:
            async with AsyncSessionLocal() as db:
                result = await account_import.import_one_account(db, item)
        except Exception as exc:  # noqa: BLE001 — never let a per-item error kill the loop
            logger.error(
                f"❌ AccountImportWorker import failed for item {item_id}: {exc}",
                exc_info=True,
            )
            result = "failed"

        result = result or "failed"
        status = "ok" if result in _OK_RESULTS else "failed"

        # 3) Write the terminal status + job progress in a fresh committed transaction.
        #    session_blob = NULL clears the live auth_key bytes once the item is terminal
        #    (security); processed bumps and the job flips to `done` at total.
        try:
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    await db.execute(
                        text(
                            """
                            UPDATE account_import_items
                            SET status = :status,
                                result = :result,
                                reason = :reason,
                                session_blob = NULL,
                                updated_at = NOW()
                            WHERE id = :id
                            """
                        ),
                        {
                            "status": status,
                            "result": result,
                            # a failed item carries the result-code as its reason for the
                            # UI report; an ok item has no failure reason.
                            "reason": None if status == "ok" else result,
                            "id": item_id,
                        },
                    )
                    await db.execute(
                        text(
                            "UPDATE account_import_jobs "
                            "SET processed = processed + 1, updated_at = NOW() "
                            "WHERE id = :job_id"
                        ),
                        {"job_id": job_id},
                    )
                    await db.execute(
                        text(
                            "UPDATE account_import_jobs "
                            "SET status = 'done', updated_at = NOW() "
                            "WHERE id = :job_id AND processed >= total"
                        ),
                        {"job_id": job_id},
                    )
            logger.info(
                f"📥 account-import item {item_id} -> {status} ({result})"
            )
        except Exception as exc:  # noqa: BLE001 — mark-failed path itself must not die
            logger.error(
                f"❌ AccountImportWorker could not record terminal state for item "
                f"{item_id}: {exc}",
                exc_info=True,
            )
            return 0
        return 1


# Module-scope singleton (mirrors kb_ingest_worker) — started in app/main.py lifespan.
account_import_worker = AccountImportWorker()
