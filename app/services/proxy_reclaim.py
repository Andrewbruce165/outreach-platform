"""Proxy-port reclaim sweep worker.

Frees ``proxy_pool`` rows held by DEAD senders (``auth_status`` in
``banned``/``session_expired``) back into the free pool, and clears those
senders' ``proxy`` JSON so their config matches reality.

**Why this exists.** ``proxy_pool.assigned_to_sender_id`` was only ever *set*,
never released. A banned/expired account held its port forever, so the free pool
silently drained. When it hit zero, ``resolve_import_proxy`` activated freshly
imported accounts WITHOUT a proxy (direct from the server IP) — the confirmed
root cause of the C&C-campaign mass ban (2026-08): a whole vendor batch went out
proxy-less, clustered on one datacenter IP, and died together.

A single set-based sweep (modelled on GradeProgressionWorker) is used instead of
event hooks in every auth-death writer: ``auth_status`` is flipped to a dead
value in THREE places (``telegram._set_auth_status``, ``checker._flag_checker_auth``,
``listener._set_auth_status``); a periodic reconcile covers all three — and every
future writer — without drift, and also frees ports that died before this code
existed.

**Re-auth safety.** Clearing ``sender.proxy`` here is safe: re-authenticating an
existing sender overwrites ``sender.proxy`` from the onboarding session's own
proxy selection (``onboarding._refresh_sender_session``), so a re-authed account
picks up a fresh assignment. A dead sender (``auth_status != 'ok'``) is never
connected by the listener, so freeing its port can never cause a live double-IP.
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import text

from app.config import get_settings
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


# Atomic reclaim: free every pool row whose assigned sender is dead AND quarantine
# it (a burned static IP rests :qdays before reissue — H4), then clear ``proxy`` on
# exactly those senders (the CTE scopes the second UPDATE to senders whose port we
# just freed — an inline-only proxy on a dead account is left untouched; this sweep
# only reclaims POOL ports). Bind param :qdays only / no user input.
_RECLAIM_SQL = text(
    """
    WITH freed AS (
        UPDATE proxy_pool p
           SET assigned_to_sender_id = NULL,
               quarantined_until = NOW() + make_interval(days => :qdays)
          FROM senders s
         WHERE p.assigned_to_sender_id = s.id
           AND s.auth_status IN ('banned', 'session_expired')
        RETURNING s.id AS sender_id
    )
    UPDATE senders
       SET proxy = NULL
      FROM freed
     WHERE senders.id = freed.sender_id
       AND senders.proxy IS NOT NULL
    """
)


class ProxyReclaimWorker:
    """Periodic sweep that returns dead senders' proxy ports to the free pool.

    Mirrors GradeProgressionWorker's start()/stop()/_run() asyncio-task lifecycle.
    Dead accounts accrue slowly, so a 15-minute cadence is ample; the pool
    self-heals without a redeploy.
    """

    TICK_INTERVAL = 900  # seconds — 15 min; dead accounts accrue slowly

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        """Запустить воркер как фоновый asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._run(), name="proxy_reclaim_worker")
        logger.info("🔌 Proxy reclaim worker запущен")

    async def stop(self):
        """Graceful shutdown."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Proxy reclaim worker остановлен")

    # ─── Main loop ────────────────────────────────────────────────────────────

    async def _run(self):
        """Главный цикл: тик раз в 15 минут."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в proxy reclaim tick: {e}", exc_info=True)
            await asyncio.sleep(self.TICK_INTERVAL)

    async def _tick(self) -> int:
        """Один тик: единичный set-based sweep, освобождающий порты мёртвых аккаунтов.

        Returns the number of senders un-proxied this tick (== ports reclaimed;
        useful for tests and startup logging).
        """
        qdays = get_settings().proxy_quarantine_days
        async with AsyncSessionLocal() as db:
            result = await db.execute(_RECLAIM_SQL, {"qdays": qdays})
            reclaimed = result.rowcount or 0
            await db.commit()

        if reclaimed:
            logger.info(f"🔌 Proxy reclaim: освобождено портов у мёртвых аккаунтов — {reclaimed}")
        return reclaimed


# Module-level singleton wired into app.main lifespan.
proxy_reclaim_worker = ProxyReclaimWorker()
