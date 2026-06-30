"""KnowledgeIngestWorker (Phase 16 — KB-02 / KB-03).

Background asyncio task in the api container's lifespan, mirroring
``ContactCheckWorker`` (lifecycle, ``_run`` loop, single-op ``_tick``):

- Claim ONE ``kb_documents`` row ``WHERE status='pending' ORDER BY created_at
  LIMIT 1 FOR UPDATE SKIP LOCKED`` (concurrency-safe — a second worker / a stray
  start skips locked rows), flip it to ``processing`` and COMMIT immediately so a
  UI poll sees ``processing`` while we parse.
- Parse → chunk → embed → store, off the event loop where it's CPU-bound:
  ``kb_ingest.extract_text_async`` (to_thread) → ``kb_ingest.chunk_text`` (to_thread)
  → ``kb_ingest.embed_texts`` (native async AsyncOpenAI).
- **Re-index idempotency (Pitfall 8):** ``DELETE FROM kb_chunks WHERE document_id``
  ALWAYS runs before inserting new chunks, so a re-run (a doc flipped back to
  ``pending``) never doubles ``chunk_count``.
- Empty / whitespace document → ``indexed`` with ``chunk_count=0`` (NOT failed).
- Any parse/embed/store failure → ``status='failed'`` + ``error`` set, the worker
  loop logs and CONTINUES (never dies — mirrors ContactCheckWorker._run).

The worker calls ``kb_ingest.embed_texts`` by module reference so tests
monkeypatch ``app.services.kb_ingest.embed_texts`` to a deterministic stub
(no OpenAI network). ``_tick`` processes ONE doc per call — a predictable,
single-batch operation for callers/tests.

Lifecycle: ``start()`` / ``stop()`` — registered in ``app/main.py`` lifespan
next to ``contact_check_worker``.
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import text

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services import kb_ingest  # module ref so tests patch kb_ingest.embed_texts

logger = logging.getLogger(__name__)


class KnowledgeIngestWorker:
    """Background worker: claim a pending kb_document → parse/chunk/embed → index.

    Singleton instance per process (module scope below). Lifecycle mirrors
    ``ContactCheckWorker`` — ``start()`` in ``app/main.py`` lifespan, ``stop()``
    in shutdown.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.poll_interval = get_settings().kb_ingest_poll_interval

    def start(self):
        """Start the background task. Idempotent (a repeat start is a no-op)."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="kb-ingest-worker")
            logger.info(
                f"📚 KnowledgeIngestWorker started (poll={self.poll_interval}s)"
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
        logger.info("📚 KnowledgeIngestWorker stopped")

    async def _run(self):
        """Main loop — sleep after each tick. Must never die on a doc error."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — worker must not die
                logger.error(
                    f"❌ KnowledgeIngestWorker tick error: {exc}", exc_info=True
                )
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> int:
        """One tick: claim ONE pending doc → parse/chunk/embed/store. Returns 1 if a
        doc was processed (indexed OR failed), 0 if none pending.

        Concurrency: ``FOR UPDATE SKIP LOCKED`` + an immediate flip to
        ``processing`` (committed) means a parallel worker / second tick skips a
        doc already being worked. A worker crash mid-parse leaves the doc stuck in
        ``processing`` (re-index = flip back to pending manually) — acceptable for
        v1; the delete-then-insert keeps a manual re-run idempotent.
        """
        settings = get_settings()

        # 1) Claim ONE pending doc and flip it to processing in its own committed
        #    transaction, so a UI poll sees `processing` while we parse/embed.
        async with AsyncSessionLocal() as db:
            async with db.begin():
                row = (await db.execute(
                    text(
                        """
                        SELECT id, kb_id, workspace_id, source_kind, raw_content
                        FROM kb_documents
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """
                    )
                )).first()

                if row is None:
                    return 0

                doc_id = str(row.id)
                kb_id = str(row.kb_id)
                workspace_id = str(row.workspace_id)
                source_kind = row.source_kind
                raw_content = row.raw_content

                await db.execute(
                    text(
                        "UPDATE kb_documents "
                        "SET status = 'processing', updated_at = NOW() "
                        "WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
            # committed → UI sees `processing`

        # 2) Parse → chunk → embed → store. Any failure flips the doc to `failed`
        #    in a fresh transaction; the worker loop continues (never dies).
        try:
            # raw_content may arrive as memoryview from the driver — normalise to bytes.
            blob = bytes(raw_content) if raw_content is not None else b""
            doc_text = await kb_ingest.extract_text_async(blob, source_kind)
            chunks = await asyncio.to_thread(
                kb_ingest.chunk_text,
                doc_text,
                settings.kb_chunk_max_tokens,
                settings.kb_chunk_overlap,
            )

            async with AsyncSessionLocal() as db:
                async with db.begin():
                    # Re-index idempotency (Pitfall 8): always clear existing chunks
                    # BEFORE inserting, so a re-run never doubles chunk_count.
                    await db.execute(
                        text("DELETE FROM kb_chunks WHERE document_id = :doc_id"),
                        {"doc_id": doc_id},
                    )

                    if not chunks:
                        # Empty / whitespace doc → indexed with 0 chunks (NOT failed).
                        await db.execute(
                            text(
                                "UPDATE kb_documents "
                                "SET status = 'indexed', chunk_count = 0, "
                                "    error = NULL, updated_at = NOW() "
                                "WHERE id = :id"
                            ),
                            {"id": doc_id},
                        )
                        logger.info(f"📚 KB doc {doc_id} indexed (empty, 0 chunks)")
                        return 1

                    vectors = await kb_ingest.embed_texts(
                        chunks, settings.openai_embedding_model
                    )

                    for idx, (content, embedding) in enumerate(zip(chunks, vectors)):
                        await db.execute(
                            text(
                                """
                                INSERT INTO kb_chunks
                                    (workspace_id, kb_id, document_id,
                                     chunk_index, content, embedding)
                                VALUES
                                    (:ws, :kb, :doc, :idx, :content, :embedding)
                                """
                            ),
                            {
                                "ws": workspace_id,
                                "kb": kb_id,
                                "doc": doc_id,
                                "idx": idx,
                                "content": content,
                                # pgvector accepts a list of floats via its type adapter,
                                # but raw-SQL bind needs the string form '[..]'.
                                "embedding": "[" + ",".join(repr(float(x)) for x in embedding) + "]",
                            },
                        )

                    await db.execute(
                        text(
                            "UPDATE kb_documents "
                            "SET status = 'indexed', chunk_count = :n, "
                            "    error = NULL, updated_at = NOW() "
                            "WHERE id = :id"
                        ),
                        {"id": doc_id, "n": len(chunks)},
                    )
                logger.info(f"📚 KB doc {doc_id} indexed ({len(chunks)} chunks)")
            return 1

        except Exception as exc:  # noqa: BLE001 — mark failed, keep the worker alive
            logger.error(
                f"❌ KB ingest failed for doc {doc_id}: {exc}", exc_info=True
            )
            try:
                async with AsyncSessionLocal() as db:
                    async with db.begin():
                        await db.execute(
                            text(
                                "UPDATE kb_documents "
                                "SET status = 'failed', error = :err, updated_at = NOW() "
                                "WHERE id = :id"
                            ),
                            {"id": doc_id, "err": str(exc)[:1000]},
                        )
            except Exception as exc2:  # noqa: BLE001
                logger.error(
                    f"❌ KB ingest could not record failure for doc {doc_id}: {exc2}"
                )
            return 0


# Module-scope singleton (mirrors contact_check_worker) — started in app/main.py lifespan.
kb_ingest_worker = KnowledgeIngestWorker()
