"""Phase 22 (D-14/D-17) — grade auto-progression sweep worker.

A durable background worker (modelled on WarmupWorker's asyncio-task lifecycle)
that, once per hour, advances every sender that has spent its configured
step-days at its current level: `current_level += 1` and `level_updated_at = NOW()`
when `NOW() - level_updated_at >= step_days(current_level)` (D-14).

Step-days come from each sender's PER-WORKSPACE ladder (sender_grade_settings),
NOT a code constant — an absent row falls back to the code-default 30/30 via
COALESCE so an unconfigured workspace behaves identically to the platform default
(grade_ladder.LADDER_DEFAULTS). A sender at level 3 NEVER advances (D-17): the
`s.current_level < 3` guard excludes the permanent top level.

Catch-up policy (RESEARCH Pattern 3 — one level per tick, eventual catch-up): the
sweep runs a SINGLE set-based UPDATE per tick. Postgres `NOW()` is the
transaction-start timestamp (constant for the whole statement), so a sender
advanced in this tick gets `level_updated_at = NOW()` and its time-at-level
delta becomes 0 — it cannot advance again until a later tick. A long-stalled
account therefore climbs at most one level per hourly tick (acceptable eventual
catch-up); step_days >= 1 in the schema guarantees it never double-steps.
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import text

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


# Set-based sweep: advance one level for every sender whose time-at-level has
# elapsed, reading step-days from the sender's workspace ladder (COALESCE to the
# code-default 30 when the workspace has no sender_grade_settings row). Level 3 is
# excluded by `s.current_level < 3` (D-17 permanent). Bind params only / no user
# input reaches the sweep (T-22-06).
_SWEEP_SQL = text(
    """
    UPDATE senders s
       SET current_level = s.current_level + 1,
           level_updated_at = NOW()
      FROM (
            SELECT w.id AS wid,
                   COALESCE(g.level1_step_days, 30) AS s1,
                   COALESCE(g.level2_step_days, 30) AS s2
              FROM workspaces w
              LEFT JOIN sender_grade_settings g ON g.workspace_id = w.id
           ) L
     WHERE s.workspace_id = L.wid
       AND s.current_level < 3
       AND NOW() - s.level_updated_at >= make_interval(
             days => CASE s.current_level
                       WHEN 1 THEN L.s1
                       WHEN 2 THEN L.s2
                     END)
    """
)


class GradeProgressionWorker:
    """Hourly sweep that auto-advances sender grade levels per the workspace ladder.

    Mirrors WarmupWorker's start()/stop()/_run() asyncio-task lifecycle. A
    day-scale ladder does not need sub-hour resolution, so TICK_INTERVAL is 1h.
    """

    TICK_INTERVAL = 3600  # seconds — hourly is ample for a day-scale ladder

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        """Запустить воркер как фоновый asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._run(), name="grade_progression_worker")
        logger.info("📈 Grade progression worker запущен")

    async def stop(self):
        """Graceful shutdown."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Grade progression worker остановлен")

    # ─── Main loop ────────────────────────────────────────────────────────────

    async def _run(self):
        """Главный цикл: тик раз в час."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в grade progression tick: {e}", exc_info=True)
            await asyncio.sleep(self.TICK_INTERVAL)

    async def _tick(self) -> int:
        """Один тик: единичный set-based sweep, повышающий уровень на 1 у всех due.

        Returns the number of level-advancements applied this tick (useful for
        tests). One level per tick — see the module docstring (frozen transaction
        NOW() means a just-advanced sender can't double-step within a tick).
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(_SWEEP_SQL)
            advanced = result.rowcount or 0
            await db.commit()

        if advanced:
            logger.info(f"📈 Grade progression: повышено уровней — {advanced}")
        return advanced


# Module-level singleton wired into app.main lifespan.
grade_progression_worker = GradeProgressionWorker()
