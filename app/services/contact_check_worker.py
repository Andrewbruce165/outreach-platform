"""ContactCheckWorker (Phase 2 — CONT-04, D-19, D-20).

Background asyncio task в lifespan API-контейнера:

- SELECT pending contacts вместе с workspace's active checker через JOIN LATERAL
  (workspace-isolated: ``s.workspace_id = c.workspace_id`` AND ``role='checker'``
  AND ``auth_status='ok'``). Phase 14 (RESV-05/D-11) добавляет
  ``restriction_status='none'`` AND ``lifecycle_status <> 'paused'`` AND
  ``(restricted_until IS NULL OR restricted_until <= NOW())`` — degraded/paused/
  cooling-down checker НЕ выбирается, поэтому ``spam_limited``-флаг реально
  останавливает worker (закрытая дыра «checker keeps lying»). Mobiles (+79…)
  дренируются первыми (RESV-04/D-08), а per-checker daily-cap считается из
  durable источника (``contacts_cache`` writes today, RESV-02/D-10).
- Группируем по checker_id, батчем зовём
  ``checker_service.check_phones(...)`` — он уже умеет lock per checker_slug,
  FloodWait handling и polite delay 2–3.5s.
- По результатам UPDATE ``contacts.tg_status``
  (``'registered' | 'not_registered' | 'error'``) +
  ``tg_telegram_id`` / ``tg_username_resolved`` / ``tg_error`` /
  ``tg_checked_at``.

D-20: контакты в workspace без checker'а имеют ``tg_status='unchecked'``
(план 02-04 уже выставляет этот статус на импорте). JOIN LATERAL по
``role='checker'`` их пропускает — нечем резолвить. Когда checker появляется,
юзер вручную дёргает ``POST /api/v1/contacts/recheck`` (план 02-05 Task 2),
который переводит существующие контакты обратно в ``'pending'`` — этот worker
их подберёт на следующем тике.

Lifecycle: ``start()`` / ``stop()`` — registered in ``app/main.py`` lifespan.
"""

import asyncio
import logging
import os
import pathlib
import random
from datetime import datetime, timedelta, timezone
from itertools import groupby
from typing import Optional

from sqlalchemy import text

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.services.checker import checker_service
from app.services.restriction_audit import record_restriction_event

logger = logging.getLogger(__name__)


# RESV-01/D-05: control-set of known-live numbers used by the throttle-detector
# probe. Loaded ONCE from an app-readable file (NOT inline — the 49 numbers live
# in app/data/control_set_known_live.txt, shipped via `COPY app/ ./app/`). A probe
# resolves a small sample LIVE (bypassing cache); a control number coming back
# not_registered is a MISS.
_CONTROL_SET_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "control_set_known_live.txt"


def _load_control_set() -> list[str]:
    """Parse the control-set file into a list of phone numbers (skip comments)."""
    try:
        phones: list[str] = []
        for line in _CONTROL_SET_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            phone = line.split(",", 1)[0].strip()
            if phone:
                phones.append(phone)
        return phones
    except FileNotFoundError:
        logger.warning("control-set file missing at %s — probe disabled", _CONTROL_SET_PATH)
        return []


_CONTROL_SET: list[str] = _load_control_set()


# Env-overridable knobs (RESEARCH §"ContactCheckWorker — стратегия rate-limit"
# + CONTEXT C-06). Defaults: batch=5, poll=5s — ~30 phones/min per checker
# с учётом polite delay 2–3.5s внутри CheckerService.check_phones.
# CONTACT_CHECK_BATCH_SIZE is now an OPTIONAL back-compat override (sentinel None
# when unset) — the effective per-batch claim LIMIT defaults to
# settings.contact_check_burst_cap (RESV-02/D-10). An explicit env value can only
# LOWER the cap (it is min()'d with burst_cap); it can never uncap the worker.
_BATCH_SIZE_OVERRIDE = os.environ.get("CONTACT_CHECK_BATCH_SIZE")
CONTACT_CHECK_BATCH_SIZE = int(_BATCH_SIZE_OVERRIDE) if _BATCH_SIZE_OVERRIDE else None
CONTACT_CHECK_POLL_INTERVAL = int(os.environ.get("CONTACT_CHECK_POLL_INTERVAL", "5"))


# RESV-01/RESV-02/RESV-06 (Plan 14-05, Gap A): the minimum LIVE (non-cache) batch
# size for the all-empty anomaly branch of the inline throttle signal. The 14-04
# live-smoke observed the poisoned batches at checked=20..30 reg=0; we pick 8 —
# comfortably below that yet above stochastic noise (a healthy checker resolving a
# handful of genuinely-unregistered numbers in a row). Below this size an all-empty
# batch is treated as a normal (clean) result, NOT a throttle signal, to avoid
# false-positive degradation on small legitimately-empty batches. The flood branch
# (summary['flood_wait_hit']) fires regardless of batch size.
ANOMALY_MIN_BATCH = 8


def _is_throttle_signal(summary: dict) -> bool:
    """Inline flood/throttle detector for a resolve batch (Plan 14-05, Gap A).

    Returns True iff this batch must be treated as a throttled checker's poisoned
    output — so its not_registered results roll back to pending (suspect) and the
    checker is degraded INLINE, WITHOUT waiting for the decoupled ≥2-miss control
    probe (the 14-04 gap: the probe flagged a checker only AFTER an entire poisoned
    batch had already been finalized).

    Two signals, EITHER fires:
      1. FloodWait — ``summary['flood_wait_hit']`` is True (the checker hit a
         contacts-API FloodWait mid-batch; its partial results are untrustworthy).
      2. Anomalous all-empty — a LIVE (non-``from_cache``) batch of meaningful size
         (``>= ANOMALY_MIN_BATCH``) where EVERY live result came back not_registered.
         This is the 14-04 signature (checked=20..30 reg=0). Only LIVE results count
         toward the anomaly — an all-cache batch tests nothing about the checker's
         current health (Pitfall 1), and a tiny batch is plausibly legitimately empty.
         CR-02: the anomaly is judged from live results ONLY — NOT from
         ``summary['registered']`` (which counts cache hits too). A throttled batch of
         20+ false LIVE negatives plus a couple of cache-served positives would have
         ``registered > 0`` and so escaped the old gate, finalizing 20+ false
         negatives at high confidence.
    """
    if summary.get("flood_wait_hit"):
        return True
    results = summary.get("results", []) or []
    live = [r for r in results if not r.get("from_cache")]
    if len(live) < ANOMALY_MIN_BATCH:
        return False
    # Every LIVE result negative → anomalous empty rate (cache hits are irrelevant).
    if not any(r.get("is_registered") for r in live):
        return True
    return False


class ContactCheckWorker:
    """Background worker: poll pending contacts → batch resolve via checker.

    Singleton instance per process (создаётся в module scope ниже). Lifecycle
    повторяет паттерн ``OnboardingCleanupWorker`` / ``WarmupWorker`` — старт
    в ``app/main.py`` lifespan, остановка в shutdown.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # RESV-02/D-10: the per-batch claim LIMIT is the burst-cap (≤ the ~45–50
        # empirical throttle onset). An explicit CONTACT_CHECK_BATCH_SIZE env can
        # only LOWER it (min with burst_cap) — never uncap the worker.
        settings = get_settings()
        burst_cap = settings.contact_check_burst_cap
        if CONTACT_CHECK_BATCH_SIZE is not None:
            self.batch_size = min(CONTACT_CHECK_BATCH_SIZE, burst_cap)
        else:
            self.batch_size = burst_cap
        self.poll_interval = CONTACT_CHECK_POLL_INTERVAL
        # RESV-01/D-05: per-checker CONSECUTIVE control-miss counter. In-memory on
        # the singleton (mirrors CheckerService._locks singleton-state). A clean
        # probe RESETS to 0; >= 2 consecutive misses flag the checker spam_limited.
        # A single miss is stochastic noise (privacy false-negative) and never flags.
        self._consecutive_misses: dict[str, int] = {}
        # Checkers found DEGRADED by the probe this tick → their just-resolved
        # not_registered results roll back to pending in _apply_results (Task 3).
        self._degraded_this_tick: set[str] = set()
        # quick-260629-b7j (PROBE-02): per-checker last active-probe timestamp.
        # In-memory on the singleton (mirrors _consecutive_misses). The probe used
        # to fire every ~5s poll tick (~4267 probe-batches/account/day — the dominant
        # contacts-API burn); this throttles it to at most one probe per
        # contact_check_probe_interval_seconds per checker. A redeploy clears it →
        # one harmless re-probe, no state to persist.
        self._last_probe_at: dict[str, datetime] = {}

    def start(self):
        """Запустить background task. Идемпотентно (повторный start — no-op)."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="contact-check-worker")
            logger.info(
                f"📋 ContactCheckWorker started "
                f"(batch={self.batch_size}, poll={self.poll_interval}s)"
            )

    async def stop(self):
        """Остановить background task — gracefully (cancel + await)."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📋 ContactCheckWorker stopped")

    async def _run(self):
        """Главный цикл — sleep после tick, чтобы не лочить startup.

        Each cycle: (1) recover any checkers whose cooldown elapsed, (2) run the
        live control-probe over eligible checkers (RESV-01/D-05) — flagging degraded
        ones and populating ``_degraded_this_tick`` so the resolve step rolls back
        their suspect batches, then (3) the resolve tick. The probe runs in the
        loop (not inside ``_tick``) so a single ``_tick`` stays a predictable,
        single-batch operation for callers/tests.
        """
        while self._running:
            try:
                await self._recover_checkers()
                await self._probe_cycle()
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — worker must not die
                logger.error(
                    f"❌ ContactCheckWorker tick error: {exc}", exc_info=True
                )
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> int:
        """Один tick: подобрать pending → resolve → update. Returns processed count.

        Phase 02.1 (CR-08): двойная защита от race между двумя ContactCheckWorker
        экземплярами (горизонтальный масштаб или ошибочный запуск).

        1. ``FOR UPDATE OF c SKIP LOCKED`` — в открытой транзакции row-lock
           держится до commit'а; другой worker, выполняющий тот же SELECT
           параллельно, пропустит lock'нутые rows.
        2. ``tg_checked_at`` claim window — после SELECT'а мы UPDATE'им
           ``tg_checked_at = NOW()`` (без смены ``tg_status`` — CHECK constraint
           не разрешает 'processing'). Фильтр SELECT'а отсекает контакты,
           заклеймленные менее 5 минут назад. Это переживает commit и защищает
           от второго worker'а, пришедшего на **следующем тике**.

        Stale claim (worker упал между SELECT и _apply_results) автоматически
        восстанавливается: через 5 минут фильтр снова допустит contact.
        """
        async with AsyncSessionLocal() as db:
            # JOIN LATERAL: для каждого pending контакта подтягиваем checker
            # из ЕГО workspace (workspace isolation). Если в workspace нет
            # checker'а — контакт пропускается (JOIN LATERAL без match → строка
            # выпадает). D-20 ``unchecked`` контакты тут не выбираются по
            # ``tg_status='pending'`` фильтру.
            async with db.begin():
                result = await db.execute(
                    text(
                        """
                        SELECT c.id AS contact_id,
                               c.workspace_id,
                               c.phone,
                               c.username,
                               s.id AS checker_id,
                               s.slug AS checker_slug,
                               s.session_string,
                               s.proxy,
                               s.client_fingerprint
                        FROM contacts c
                        JOIN LATERAL (
                            SELECT id, slug, session_string, proxy, client_fingerprint
                            FROM senders
                            WHERE workspace_id = c.workspace_id
                              AND role = 'checker'
                              AND auth_status = 'ok'
                              -- RESV-05/D-11: a degraded/paused checker is NEVER
                              -- selected, so the spam_limited flag actually stops
                              -- the worker (the hole that let the broken checker lie).
                              AND restriction_status = 'none'
                              AND lifecycle_status <> 'paused'
                              -- RESV-02/D-10 cooldown gate: a checker resting on a
                              -- future restricted_until is skipped even if its status
                              -- was cleared early (durable — survives api restart).
                              AND (restricted_until IS NULL
                                   OR restricted_until <= NOW())
                              -- Plan 14-07 (Q3) benign post-batch REST gate: a checker
                              -- resting after its last batch is skipped until the rest
                              -- elapses, so the worker cannot chain batch-after-batch on
                              -- ONE account past the ~45-50 burst onset. SEPARATE from the
                              -- restriction cooldown above — keys on checker_rest_until,
                              -- never restricted_until, and carries NO restriction state.
                              AND (checker_rest_until IS NULL
                                   OR checker_rest_until <= NOW())
                              -- proxy-switch-listener-lag (mig 062): skip a checker whose
                              -- proxy switch is still pending confirmation, so it never
                              -- opens a resolve connection on the NEW IP while the OLD one
                              -- may still be live. TTL fallback lifts a stale flag (a
                              -- checker is not held by the listener, so only the TTL and
                              -- the reconcile-loop sweep clear it).
                              AND (proxy_switch_pending_at IS NULL
                                   OR proxy_switch_pending_at
                                      < NOW() - make_interval(secs => :proxy_switch_ttl))
                              -- RESV-02/D-10 durable daily-cap: count today's
                              -- contacts_cache writes by this checker (NOT an
                              -- in-memory counter, Pitfall 5). Over-quota → excluded.
                              AND (
                                  SELECT COUNT(*)
                                  FROM contacts_cache cc
                                  WHERE cc.sender_id = senders.id
                                    AND cc.updated_at >= date_trunc('day', now())
                              ) < :daily_cap
                            -- IN-03: deterministic rotation — prefer never-rested
                            -- (NULL) / longest-rested checkers, tie-break by id.
                            -- checker_rest_until is filtered in the WHERE so ORDER BY
                            -- may reference it though it is not in the SELECT list.
                            ORDER BY checker_rest_until NULLS FIRST, id
                            LIMIT 1
                        ) s ON TRUE
                        WHERE c.tg_status = 'pending'
                          AND (c.phone IS NOT NULL OR c.username IS NOT NULL)
                          AND (c.tg_checked_at IS NULL
                               OR c.tg_checked_at < NOW() - INTERVAL '5 minutes')
                        -- RESV-04/D-08: mobiles (+79…) ~50% live → drain first.
                        ORDER BY (c.phone LIKE '+79%') DESC,
                                 c.created_at ASC
                        LIMIT :n
                        FOR UPDATE OF c SKIP LOCKED
                        """
                    ),
                    {
                        "n": self.batch_size,
                        "daily_cap": get_settings().contact_check_daily_cap,
                        "proxy_switch_ttl": get_settings().proxy_switch_pending_ttl_seconds,
                    },
                )
                rows = result.fetchall()

                if rows:
                    # Claim: tg_checked_at = NOW() — другой worker увидит < 5min
                    # и пропустит на следующем тике. Без смены tg_status
                    # (CHECK constraint не позволяет 'processing').
                    contact_ids = [str(r.contact_id) for r in rows]
                    await db.execute(
                        text(
                            "UPDATE contacts SET tg_checked_at = NOW() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": contact_ids},
                    )
                # commit при выходе из async with db.begin()

        if not rows:
            return 0

        # Группируем по checker_id (обычно один checker per workspace в v1, но
        # SQL может вернуть несколько workspaces в одном tick'е).
        rows_sorted = sorted(rows, key=lambda r: str(r.checker_id))
        processed = 0
        for checker_id, items_iter in groupby(rows_sorted, key=lambda r: r.checker_id):
            items = list(items_iter)
            first = items[0]

            # RESV-01/D-05: the probe verdict for this checker. Probes run as a
            # separate step (run_control_probe / scheduled), accumulating per-checker
            # consecutive misses on the singleton; a checker flagged degraded this
            # cycle has its batch marked suspect so its not_registered results roll
            # back to pending instead of finalizing. We read the accumulated verdict
            # here rather than firing a fresh probe inline — keeping the batch resolve
            # (check_phones) a single, predictable call per checker.
            probe_state = "suspect" if str(checker_id) in self._degraded_this_tick else "clean"

            # Phone wins when present; username-only contacts resolve via username.
            phone_items = [r for r in items if r.phone]
            username_items = [r for r in items if not r.phone and r.username]

            common = dict(
                workspace_id=str(first.workspace_id),
                checker_id=str(checker_id),
                checker_slug=first.checker_slug,
                encrypted_session=first.session_string,
                proxy=first.proxy,
                # Phase 21 D-17/IMPT-04: imported checkers reconnect with THEIR
                # fingerprint; NULL for phone-onboarded checkers → strict global
                # fallback. Threads into check_phones(**common)/check_usernames(**common).
                fingerprint=first.client_fingerprint,
            )

            # Plan 14-07 (Q3): did THIS checker complete a batch without raising? Only
            # then do we put it on the benign post-batch rest. A raising branch is
            # handled by its own error/degrade path and must NOT also be rested.
            batch_applied = False

            if phone_items:
                try:
                    summary = await checker_service.check_phones(
                        phones=[r.phone for r in phone_items], **common
                    )
                    # Plan 14-05 (Gap A): an INLINE flood/throttle signal at the
                    # resolve tick degrades the checker immediately and marks this
                    # batch suspect — without waiting for the decoupled ≥2-miss probe.
                    # Recompute probe_state AFTER the signal so the just-flagged
                    # checker's own batch is finalized as suspect (rollback, no
                    # high-confidence). Reuses the D-07 suspect path + D-06 degrade.
                    probe_state = await self._maybe_degrade_on_signal(
                        str(checker_id), summary, probe_state
                    )
                    await self._apply_results(
                        phone_items, summary,
                        checker_id=str(checker_id), probe_state=probe_state,
                    )
                    batch_applied = True
                    processed += len(phone_items)
                    logger.info(
                        f"📋 ContactCheckWorker: checker={first.checker_slug} (phones) "
                        f"checked={summary.get('checked', 0)} "
                        f"reg={summary.get('registered', 0)} "
                        f"not_reg={summary.get('not_registered', 0)} "
                        f"flood={summary.get('flood_wait_hit', False)}"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"❌ ContactCheckWorker: checker={first.checker_slug} phones failed: {exc}",
                        exc_info=True,
                    )
                    # WR-06: back off a persistently-failing checker (auth-dead is
                    # flagged by _get_client + excluded by the LATERAL gate;
                    # network/frozen is covered here) via checker_rest_until so the
                    # worker does not re-claim the same contacts every ~5s poll tick.
                    await self._rest_checker(str(checker_id))

            if username_items:
                try:
                    summary = await checker_service.check_usernames(
                        usernames=[r.username for r in username_items], **common
                    )
                    # Plan 14-05 (Gap A): same inline flood/throttle degrade on the
                    # username path.
                    probe_state = await self._maybe_degrade_on_signal(
                        str(checker_id), summary, probe_state
                    )
                    await self._apply_results(
                        username_items, summary,
                        checker_id=str(checker_id), probe_state=probe_state,
                    )
                    batch_applied = True
                    processed += len(username_items)
                    logger.info(
                        f"📋 ContactCheckWorker: checker={first.checker_slug} (usernames) "
                        f"checked={summary.get('checked', 0)} "
                        f"reg={summary.get('registered', 0)} "
                        f"not_reg={summary.get('not_registered', 0)} "
                        f"flood={summary.get('flood_wait_hit', False)}"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"❌ ContactCheckWorker: checker={first.checker_slug} usernames failed: {exc}",
                        exc_info=True,
                    )
                    # WR-06: back off a persistently-failing checker (see the phones
                    # branch) via checker_rest_until. Resting twice if both branches
                    # fail just re-stamps the same value — harmless.
                    await self._rest_checker(str(checker_id))

            # Plan 14-07 (Q3): after a non-raising batch, put the checker on a benign
            # post-batch rest so the worker cannot chain batch-after-batch on this ONE
            # account past the ~45-50 burst onset. The existing rotation then alternates
            # to a second healthy checker meanwhile (≈2x throughput, no parallel exec).
            # A clean empty batch STILL rests; a raising branch is skipped (handled by
            # its own error/degrade path). This touches ONLY checker_rest_until — never
            # restriction_status/lifecycle_status/restricted_until — and writes NO
            # sender_restriction_events row, so a rested checker waking up is just
            # re-selected (it never goes through the restriction recovery control-probe).
            if batch_applied:
                await self._rest_checker(str(checker_id))
                # 2026-06-30 fix (companion to the _recover_checkers change): a clean
                # REAL batch is the genuine proof of health that resets the escalating-
                # backoff ladder. probe_state == 'clean' means the checker resolved a
                # full live batch without tripping the 14-05 throttle signal AND was not
                # flagged degraded by the decoupled ≥2-miss probe this cycle. The weak
                # ≤5-sample recovery probe must NOT reset the ladder (it falsely
                # "recovers" a still-throttled checker), so the reset lives here.
                if probe_state == "clean":
                    await self._reset_checker_trip(str(checker_id))

        return processed

    async def _rest_checker(self, checker_id: str, seconds: int | None = None) -> None:
        """Benign post-batch REST (Plan 14-07, Q3): stamp checker_rest_until = NOW() +
        rest for one checker, in its own short transaction.

        ``seconds`` overrides the default ``contact_check_rest_seconds`` — WR-08 uses
        it to apply the longer ``contact_check_cooldown_seconds`` as a rest-only
        degrade when the control set is empty (a spam_limited flag would be permanent
        without a control set to recover from). The default path is unchanged.

        SEPARATE from the restriction machinery (``_flag_checker_degraded`` /
        ``_recover_checkers``): this sets ONLY ``checker_rest_until`` and never touches
        ``restriction_status`` / ``lifecycle_status`` / ``restricted_until``, never
        writes a ``sender_restriction_events`` row. The LATERAL selection gate excludes
        the checker while ``checker_rest_until > NOW()``; once it elapses the checker is
        re-selected directly (no recovery control-probe — that path keys on
        ``restricted_until``, which this never sets).
        """
        rest_seconds = seconds if seconds is not None else get_settings().contact_check_rest_seconds
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    text(
                        "UPDATE senders "
                        "SET checker_rest_until = NOW() + make_interval(secs => :rest) "
                        "WHERE id = :id"
                    ),
                    {"rest": rest_seconds, "id": checker_id},
                )

    async def _reset_checker_trip(self, checker_id: str) -> None:
        """Reset the escalating-backoff ladder (checker_trip_count → 0) after a clean
        REAL resolve batch — the genuine proof of health.

        Companion to the 2026-06-30 ``_recover_checkers`` change: the ≤5-sample
        recovery probe is too weak to prove a checker is no longer throttled (it
        passes in a fresh burst window while real 30-resolve batches still trip), so
        resetting the ladder there let the pool flap forever at the base cooldown.
        The ladder is reset here instead — only when the checker completed a full
        live batch without a throttle signal (caller passes ``probe_state == 'clean'``).
        Touches ONLY ``checker_trip_count`` — no restriction/rest fields, no event row.
        Guarded (``checker_trip_count <> 0``) so an already-zero checker skips the write.
        """
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    text(
                        "UPDATE senders SET checker_trip_count = 0 "
                        "WHERE id = :id AND checker_trip_count <> 0"
                    ),
                    {"id": checker_id},
                )

    async def _probe_cycle(self) -> None:
        """Run the control-probe over every eligible checker once per loop cycle.

        Resets ``_degraded_this_tick`` (per-cycle scratch) then probes each eligible
        checker; a degraded checker is flagged + added to ``_degraded_this_tick`` so
        the subsequent resolve ``_tick`` rolls back its suspect batch. Probe/connection
        errors are swallowed inside ``probe_checker`` so a flaky probe never blocks
        resolution.
        """
        self._degraded_this_tick = set()
        if not _CONTROL_SET:
            return
        settings = get_settings()
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                text("""
                    SELECT id FROM senders
                    WHERE role = 'checker'
                      AND auth_status = 'ok'
                      AND restriction_status = 'none'
                      AND lifecycle_status <> 'paused'
                      AND (restricted_until IS NULL OR restricted_until <= NOW())
                      -- quick-260629-b7j (PROBE-01): honor the Plan-14-07 post-batch
                      -- rest in the PROBE path too. The 14-07 rest only excluded the
                      -- resolve _tick LATERAL; the probe ignored checker_rest_until and
                      -- hammered a "resting" checker every ~5s, defeating the rest and
                      -- burning the account. Mirror the _tick gate so a resting checker
                      -- is fully idle (probe AND resolve).
                      AND (checker_rest_until IS NULL OR checker_rest_until <= NOW())
                      -- quick-260629-b7j (PROBE-03): gate the probe on the same durable
                      -- daily_cap the resolve _tick uses (today's contacts_cache writes
                      -- by this checker). A probe DOES write contacts_cache, so an
                      -- ungated probe silently blew the per-account budget. Gating BEFORE
                      -- firing keeps probe + resolve load under one shared ceiling.
                      AND (
                          SELECT COUNT(*)
                          FROM contacts_cache cc
                          WHERE cc.sender_id = senders.id
                            AND cc.updated_at >= date_trunc('day', now())
                      ) < :daily_cap
                      -- proxy-switch-listener-lag (mig 062): a probe DOES open a resolve
                      -- connection, so skip a checker whose proxy switch is still pending
                      -- (never live on the NEW IP while the OLD may still be up). TTL
                      -- fallback lifts a stale flag.
                      AND (proxy_switch_pending_at IS NULL
                           OR proxy_switch_pending_at
                              < NOW() - make_interval(secs => :proxy_switch_ttl))
                """),
                {"daily_cap": settings.contact_check_daily_cap,
                 "proxy_switch_ttl": settings.proxy_switch_pending_ttl_seconds},
            )).fetchall()
        # quick-260629-b7j (PROBE-02): probe each eligible checker at most once per
        # contact_check_probe_interval_seconds. The inline 14-05 anomaly detector
        # already catches throttle for free on every real batch, so the active probe
        # is a rare backstop — it does NOT need to run every poll tick.
        now = datetime.now(timezone.utc)
        interval = timedelta(seconds=settings.contact_check_probe_interval_seconds)
        for r in rows:
            cid = str(r.id)
            last = self._last_probe_at.get(cid)
            if last is not None and (now - last) < interval:
                continue  # probed recently — skip this cycle
            self._last_probe_at[cid] = now
            await self.probe_checker(cid)

    def _probe_sample(self) -> list[str]:
        """A small random control-set sample (≤ ~5, ≤ burst_cap) — keeps the probe
        from eating the per-tick budget (RESV-02/D-10). Random so a stale-cache or
        targeted-number attack can't dodge the detector."""
        if not _CONTROL_SET:
            return []
        size = min(3, get_settings().contact_check_burst_cap, len(_CONTROL_SET))
        # Use a 3-5 window; 3 is enough for the consecutive-miss signal and is the
        # smallest burst the probe needs.
        return random.sample(_CONTROL_SET, size)

    async def probe_checker(self, checker_id: str) -> bool:
        """Run a live control-probe for one checker; track consecutive misses.

        RESV-01/D-05/CR-01/IN-01. Resolves a small control sample via
        ``checker_service.probe_control`` — the LIVE-ONLY primitive that hits
        Telegram on every control number and NEVER reads/writes ``contacts_cache``.
        (The old ``check_phones`` path consulted the cache, so a throttled checker
        "passed" the probe on stale cached hits and one bad live resolve of a control
        number could cache ``is_registered=false`` and cascade-park the whole pool.)
        A control number resolving ``not_registered`` — OR a flood-interrupted /
        truncated / empty probe — is a MISS. Misses are counted PER checker,
        CONSECUTIVE, on the singleton's
        ``_consecutive_misses`` dict; a clean probe RESETS the counter to 0.

        On ``>= 2`` consecutive misses (D-05 — a single miss is noise) the checker
        is flagged ``spam_limited`` via the Phase-10 restriction infra (event row +
        senders UPDATE in ONE transaction), paused, and put on a cooldown; the
        Plan-02 selection gate then excludes it on the next tick (Pitfall 2 — never
        via ``auth_status``).

        Returns ``True`` if the checker is degraded (flagged this call), else
        ``False``. The caller marks a degraded checker's batch suspect (Task 3).
        """
        sample = self._probe_sample()
        if not sample:
            return False

        # Read the checker's resolve credentials. A missing/ineligible row → no probe.
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                text("""
                    SELECT workspace_id, slug, session_string, proxy, client_fingerprint
                    FROM senders WHERE id = :id
                """),
                {"id": checker_id},
            )).fetchone()
        if row is None:
            return False

        try:
            summary = await checker_service.probe_control(
                checker_slug=row.slug,
                encrypted_session=row.session_string,
                phones=sample,
                proxy=row.proxy,
                checker_id=checker_id,  # WR-14: lock on id, not (non-unique) slug
                fingerprint=row.client_fingerprint,
            )
        except Exception as exc:  # noqa: BLE001 — a probe error must not kill the tick
            logger.warning("control-probe for checker %s raised: %s", checker_id, exc)
            return False

        results = summary.get("results", [])
        # MISS if the probe hit a FloodWait, was truncated (fewer results than the
        # sampled control numbers), came back empty, or ANY control number
        # (known-live) resolved not_registered. The control set is verified-live, so
        # a healthy checker resolves them ALL — a flood/truncated/empty probe proves
        # nothing about health and must NOT count as clean.
        miss = (
            bool(summary.get("flood_wait_hit"))
            or len(results) < len(sample)
            or any(not r.get("is_registered") for r in results)
            or not results
        )

        if not miss:
            self._consecutive_misses[checker_id] = 0
            return False

        self._consecutive_misses[checker_id] = self._consecutive_misses.get(checker_id, 0) + 1
        n = self._consecutive_misses[checker_id]
        if n < 2:
            # Single miss — stochastic noise (privacy false-negative). Do NOT flag.
            return False

        # >= 2 consecutive misses → degrade via Phase-10 infra.
        await self._flag_checker_degraded(checker_id, n)
        self._degraded_this_tick.add(checker_id)
        return True

    async def _flag_checker_degraded(
        self, checker_id: str, miss_count: int, raw_text: str | None = None
    ) -> None:
        """Mark a checker spam_limited + audit row in ONE transaction (D-06).

        Reuses the Phase-10 ``record_restriction_event`` (db= → caller commits) so
        the event row and the ``senders`` status UPDATE land atomically. Marks via
        ``restriction_status`` (NOT ``auth_status`` — Pitfall 2). Cooldown =
        ``settings.contact_check_cooldown_seconds``.

        ``raw_text`` lets the caller record the cause: the control-probe path passes
        ``None`` and gets the default "{miss_count} consecutive misses"; the inline
        resolve-tick path (Plan 14-05) passes "resolve-tick: FloodWait" /
        "resolve-tick: anomalous empty-rate N/N" so the audit row distinguishes the
        inline flood/throttle degrade from the decoupled ≥2-miss probe.
        """
        settings = get_settings()
        base_cooldown = settings.contact_check_cooldown_seconds
        max_backoff = settings.contact_check_max_backoff_seconds
        audit_text = raw_text or f"control-probe: {miss_count} consecutive misses"
        async with AsyncSessionLocal() as db:
            async with db.begin():
                # quick-260629-b7j (PROBE-04): escalating per-checker backoff. Bump the
                # persisted consecutive-trip counter and compute an exponential cooldown
                # base * 2^(trip-1) capped at max_backoff, so a checker that keeps
                # tripping rests for HOURS instead of auto-recovering every fixed ~15min
                # only to re-trip and burn the contacts-API. checker_trip_count is durable
                # (survives api restart) and is reset to 0 on a clean recovery
                # (_recover_checkers). RETURNING keeps the bump + cooldown computation in
                # ONE transaction with the event row and status UPDATE (atomic).
                new_trip = (await db.execute(
                    text("""
                        UPDATE senders
                        SET checker_trip_count = checker_trip_count + 1
                        WHERE id = :id
                        RETURNING checker_trip_count
                    """),
                    {"id": checker_id},
                )).scalar_one()
                cooldown = min(base_cooldown * (2 ** (new_trip - 1)), max_backoff)
                cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
                await record_restriction_event(
                    sender_id=checker_id,
                    event_type="spam_limited",
                    source="antispam_signal",
                    restricted_until=cooldown_until,
                    raw_text=audit_text,
                    db=db,  # same TX as the UPDATE below
                )
                await db.execute(
                    text("""
                        UPDATE senders
                        SET restriction_status = 'spam_limited',
                            restricted_until = :until,
                            lifecycle_status = 'paused'
                        WHERE id = :id
                    """),
                    {"until": cooldown_until, "id": checker_id},
                )
        logger.warning(
            "📋 control-probe flagged checker %s spam_limited (%d consecutive misses), "
            "trip #%d → cooldown %ss (base %ss, cap %ss)",
            checker_id, miss_count, new_trip, cooldown, base_cooldown, max_backoff,
        )

    async def _maybe_degrade_on_signal(
        self, checker_id: str, summary: dict, probe_state: str
    ) -> str:
        """Inline flood/throttle degrade at the resolve tick (Plan 14-05, Gap A).

        If ``summary`` carries an inline throttle signal (``flood_wait_hit`` or the
        anomalous all-empty rate, see ``_is_throttle_signal``), the checker is
        degraded INLINE — ``_flag_checker_degraded`` writes the
        ``sender_restriction_events`` row + sets ``restriction_status='spam_limited'``
        / ``lifecycle_status='paused'`` / cooldown (Pitfall 2 — never ``auth_status``),
        and the checker is added to ``_degraded_this_tick`` so the RESV-05 selection
        gate excludes it next tick. Returns ``'suspect'`` so THIS batch is finalized
        via the existing D-07 suspect rollback (not_registered → pending, no
        high-confidence). Without a signal, returns the incoming ``probe_state``
        unchanged (the decoupled ≥2-miss probe verdict still applies).

        Idempotent within a tick: if the checker was already flagged this tick
        (e.g. the phone batch already tripped it before the username batch), the
        degrade is not re-emitted but the suspect verdict is still returned.
        """
        if not _is_throttle_signal(summary):
            return probe_state
        if not _CONTROL_SET:
            # WR-08: with no control set, _recover_checkers can never clear a
            # spam_limited flag → it would be PERMANENT. Degrade REST-ONLY
            # (self-clearing via checker_rest_until) + ERROR log; the batch still
            # finalizes suspect (rollback), so no false negative is committed.
            if checker_id not in self._degraded_this_tick:
                logger.error(
                    "checker %s tripped the inline throttle signal but the CONTROL SET IS "
                    "EMPTY — recovery impossible; applying REST-ONLY degrade (NOT spam_limited) "
                    "so it self-clears. Ship app/data/control_set_known_live.txt.", checker_id,
                )
                await self._rest_checker(
                    checker_id, seconds=get_settings().contact_check_cooldown_seconds
                )
                self._degraded_this_tick.add(checker_id)
            return "suspect"
        if checker_id not in self._degraded_this_tick:
            checked = summary.get("checked", 0)
            raw_text = (
                "resolve-tick: FloodWait"
                if summary.get("flood_wait_hit")
                else f"resolve-tick: anomalous empty-rate {checked}/{checked}"
            )
            await self._flag_checker_degraded(checker_id, miss_count=0, raw_text=raw_text)
            self._degraded_this_tick.add(checker_id)
        return "suspect"

    async def _rearm_recovery_cooldown(self, checker_id: str) -> None:
        """CR-03: push ``restricted_until`` forward by the base cooldown WITHOUT
        touching the trip ladder or writing an audit row.

        Used when a recovery probe fails for a non-throttle reason (exception:
        network error, dead session). The recovery SELECT keys on
        ``restricted_until <= NOW()``, so an elapsed timestamp that is never
        re-armed re-selects the same checker on EVERY poll tick — the 2026-07-04
        prod hot loop. Throttle-evidenced failures go through
        ``_flag_checker_degraded`` instead (trip bump + audit + escalation).
        """
        cooldown = get_settings().contact_check_cooldown_seconds
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    text(
                        """
                        UPDATE senders
                        SET restricted_until = NOW() + make_interval(secs => :cooldown)
                        WHERE id = :id
                        """
                    ),
                    {"cooldown": cooldown, "id": checker_id},
                )

    async def _recover_checkers(self) -> None:
        """D-04 recovery: re-probe checkers whose cooldown elapsed; clear if clean.

        On a tick, any checker previously flagged ``spam_limited`` whose
        ``restricted_until <= NOW()`` gets a fresh live control-probe. If clean
        (no miss), write a ``cleared`` event + restore ``restriction_status='none'`` /
        ``lifecycle_status='active'`` / ``restricted_until=NULL`` and reset its miss
        counter — the Plan-02 selection gate then returns it to rotation. We do NOT
        reuse the sender SpamBot reconcile (it can't see a contacts-API throttle).
        """
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                text("""
                    SELECT id, workspace_id, slug, session_string, proxy, client_fingerprint
                    FROM senders
                    WHERE role = 'checker'
                      AND restriction_status = 'spam_limited'
                      AND restricted_until IS NOT NULL
                      AND restricted_until <= NOW()
                """)
            )).fetchall()

        # WR-08: nothing to recover.
        if not rows:
            return
        # WR-08: compute the control sample ONCE before the loop. On an empty/missing
        # control-set, WARN and early-return so recovery is DISABLED cleanly — never
        # abort mid-loop (the old per-iteration `return` skipped every checker AFTER
        # the first if the sample was empty). One sample for all checkers in a pass is
        # intended (FIXPLAN) — every control number is known-live.
        sample = self._probe_sample()
        if not sample:
            logger.warning(
                "checker recovery DISABLED: control-set empty/missing — cannot re-probe "
                "%d cooled-down checker(s)", len(rows),
            )
            return

        for r in rows:
            try:
                summary = await checker_service.probe_control(
                    checker_slug=r.slug,
                    encrypted_session=r.session_string,
                    phones=sample,
                    proxy=r.proxy,
                    checker_id=str(r.id),  # WR-14: lock on id, not (non-unique) slug
                    fingerprint=r.client_fingerprint,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("recovery probe for checker %s raised: %s", r.id, exc)
                # CR-03: re-arm the cooldown even on an infra error (network, dead
                # session). Without it the elapsed restricted_until re-selects this
                # checker EVERY tick → a ~12.5s connect/probe hot loop (prod incident
                # 2026-07-04..06, sender-8525079460, ~20k resolves/day). An exception
                # is not throttle evidence, so no trip bump / no audit row — just push
                # restricted_until forward by the base cooldown.
                await self._rearm_recovery_cooldown(str(r.id))
                continue
            results = summary.get("results", [])
            # CR-01: a recovery probe only counts as CLEAN if it hit Telegram live
            # for the FULL control sample with no flood and every number registered.
            # A flood-interrupted or truncated recovery probe must NOT fake-recover a
            # still-throttled checker back into rotation.
            clean = (
                not summary.get("flood_wait_hit")
                and len(results) == len(sample)
                and bool(results)
                and all(res.get("is_registered") for res in results)
            )
            if not clean:
                # CR-03: a failed recovery probe MUST re-arm the cooldown, or the
                # elapsed restricted_until keeps re-selecting this checker every
                # poll tick — a silent ~12.5s live-probe loop that burns the
                # already-throttled account. A live miss on known-live controls IS
                # throttle evidence → climb the escalating ladder (trip += 1,
                # exponential cooldown, audit row), same as any other trip.
                misses = sum(1 for res in results if not res.get("is_registered"))
                flood = ", flood_wait" if summary.get("flood_wait_hit") else ""
                await self._flag_checker_degraded(
                    str(r.id),
                    miss_count=misses,
                    raw_text=(
                        f"recovery-probe: still throttled "
                        f"({misses}/{len(sample)} live misses{flood})"
                    ),
                )
                continue
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    await record_restriction_event(
                        sender_id=str(r.id),
                        event_type="cleared",
                        source="antispam_signal",
                        restricted_until=None,
                        raw_text="control-probe recovery: clean after cooldown",
                        db=db,
                    )
                    await db.execute(
                        text("""
                            UPDATE senders
                            SET restriction_status = 'none',
                                restricted_until = NULL,
                                lifecycle_status = 'active'
                                -- 2026-06-30 fix: do NOT reset checker_trip_count here.
                                -- The recovery probe is only a ≤5-sample control burst
                                -- (_probe_sample) — far too weak to prove genuine health:
                                -- a soft-throttled checker passes 5 resolves in a fresh
                                -- burst window while real 30-resolve batches still trip.
                                -- Resetting the escalating-backoff ladder on every such
                                -- clear made the pool flap forever at the base 15min
                                -- cooldown (trip_count stuck at 0, ladder 30m/1h/…/6h
                                -- never reached). The ladder is now reset ONLY after a
                                -- clean REAL resolve batch (_reset_checker_trip in the
                                -- resolve tick). Recovery just returns the checker to
                                -- rotation with its trip history intact, so a checker that
                                -- keeps re-tripping climbs to hours of rest and stops
                                -- burning the contacts-API.
                            WHERE id = :id
                        """),
                        {"id": str(r.id)},
                    )
            self._consecutive_misses[str(r.id)] = 0
            logger.info("📋 control-probe recovered checker %s → active", r.id)

    async def _apply_results(
        self,
        items: list,
        summary: dict,
        checker_id: str | None = None,
        probe_state: str = "clean",
    ) -> None:
        """UPDATE contacts по результатам checker'а (RESV-06/D-07/D-09).

        ``summary['results']`` — список ``{phone|username, is_registered,
        telegram_id?, error?, from_cache?}``. Phone-контакты сматчиваем по phone
        (E.164 нормализован при импорте), username-контакты — по bare username.
        Если ключ отсутствует в ``results`` — не трогаем строку: для FloodWait
        partial run эти контакты останутся в ``'pending'`` и попадут в след. tick.

        Finalization rule (the core data-integrity fix):
        - ``probe_state='suspect'`` (degraded checker, >=2 control misses): a
          ``not_registered`` result is the prime suspect for a FALSE negative, so it
          is NOT finalized — it rolls back to ``tg_status='pending'`` with
          ``tg_checked_at=NULL`` (re-checkable by a healthy checker) and is stamped
          ``tg_probe_state='suspect'`` / ``tg_resolved_by``, ``tg_confidence`` left
          NULL. NEVER 'not_registered' (D-07/D-09 — this is the root-bug fix).
        - ``probe_state='clean'``: a ``not_registered`` result finalizes as today
          PLUS ``tg_confidence='high'`` / ``tg_resolved_by`` / ``tg_probe_state='clean'``.
        - ``registered`` is ALWAYS kept as 'registered' regardless of probe state
          (Pitfall 3 — a throttle yields false negatives only, never false positives),
          stamped with ``tg_resolved_by`` + ``tg_probe_state`` for provenance.
        """
        results_by_phone = {
            r.get("phone"): r for r in summary.get("results", []) if r.get("phone")
        }
        results_by_username = {
            r.get("username"): r for r in summary.get("results", []) if r.get("username")
        }
        if not results_by_phone and not results_by_username:
            return
        suspect = probe_state == "suspect"
        async with AsyncSessionLocal() as db:
            for item in items:
                if item.phone:
                    res = results_by_phone.get(item.phone)
                else:
                    res = results_by_username.get((item.username or "").lstrip("@"))
                if res is None:
                    # Не обработан (partial из-за FloodWait) — оставляем pending.
                    continue
                # IN-08 / D-09 forensics: a cache-served result was NOT resolved live
                # by THIS checker — it came from another sender's cache row. Stamp
                # resolver=NULL for cache hits so resolver-provenance is not corrupted
                # by a checker claiming a resolve it never performed. (tg_resolved_by
                # is UUID NULL — a string marker won't fit; NULL is the correct value.)
                resolver = None if res.get("from_cache") else checker_id
                if res.get("error"):
                    await db.execute(
                        text(
                            """
                            UPDATE contacts
                            SET tg_status = 'error',
                                tg_error = :err,
                                tg_checked_at = NOW(),
                                updated_at = NOW()
                            WHERE id = :cid
                            """
                        ),
                        {
                            "err": str(res["error"])[:500],
                            "cid": str(item.contact_id),
                        },
                    )
                elif res.get("is_registered"):
                    # True positive — kept regardless of probe state (Pitfall 3),
                    # stamped with resolver provenance. A clean probe stamps high
                    # confidence; a suspect probe leaves confidence NULL (kept, but
                    # not certified) so downstream cannot treat it as fully trusted.
                    await db.execute(
                        text(
                            """
                            UPDATE contacts
                            SET tg_status = 'registered',
                                tg_telegram_id = :tid,
                                tg_username_resolved = :uname,
                                tg_confidence = :confidence,
                                tg_resolved_by = :resolver,
                                tg_probe_state = :probe_state,
                                tg_checked_at = NOW(),
                                updated_at = NOW()
                            WHERE id = :cid
                            """
                        ),
                        {
                            "tid": res.get("telegram_id"),
                            "uname": res.get("username"),
                            "confidence": None if suspect else "high",
                            "resolver": resolver,
                            "probe_state": probe_state,
                            "cid": str(item.contact_id),
                        },
                    )
                elif suspect:
                    # Degraded checker — a not_registered is a likely FALSE negative.
                    # Roll back to pending (re-checkable); NEVER finalize (D-07/D-09).
                    await db.execute(
                        text(
                            """
                            UPDATE contacts
                            SET tg_status = 'pending',
                                tg_checked_at = NULL,
                                tg_probe_state = 'suspect',
                                tg_resolved_by = :resolver,
                                tg_confidence = NULL,
                                updated_at = NOW()
                            WHERE id = :cid
                            """
                        ),
                        {"resolver": resolver, "cid": str(item.contact_id)},
                    )
                else:
                    # Clean checker — finalize not_registered with high-confidence
                    # provenance (RESV-06/D-09).
                    await db.execute(
                        text(
                            """
                            UPDATE contacts
                            SET tg_status = 'not_registered',
                                tg_confidence = 'high',
                                tg_resolved_by = :resolver,
                                tg_probe_state = 'clean',
                                tg_checked_at = NOW(),
                                updated_at = NOW()
                            WHERE id = :cid
                            """
                        ),
                        {"resolver": resolver, "cid": str(item.contact_id)},
                    )
            await db.commit()


# Module-level singleton — register start/stop in app/main.py lifespan.
contact_check_worker = ContactCheckWorker()


async def apply_results_with_confidence(
    items: list,
    summary: dict,
    checker_id: str,
    probe_state: str = "clean",
) -> None:
    """Module-level entry point for the confidence/suspect-aware results write.

    RESV-06/D-07/D-09. Delegates to the singleton worker's ``_apply_results`` so the
    finalization rule (suspect → pending rollback, clean → high-confidence finalize,
    registered always kept) lives in one place.
    """
    await contact_check_worker._apply_results(
        items, summary, checker_id=checker_id, probe_state=probe_state
    )


async def run_control_probe(checker_id: str) -> bool:
    """Module-level entry point for a single checker's control-probe (RESV-01/D-05).

    Delegates to the singleton worker so the per-checker consecutive-miss counter
    is shared with ``_tick``'s probing. Returns True if the checker was flagged
    degraded by this probe.
    """
    return await contact_check_worker.probe_checker(checker_id)


async def select_eligible_checkers(workspace_id: str) -> list[str]:
    """Return the ids of all checkers in a workspace currently eligible to resolve.

    RESV-03/D-04. The pool-aware selection gate: a checker is eligible iff it is an
    authorized checker that is NOT restricted, NOT paused, and NOT resting on a
    future cooldown — the same disqualifiers the ``_tick`` JOIN LATERAL applies
    (Plan 02). With ≥2 eligible checkers the work can spread across them (rotation);
    at N=1 with the only checker resting, this returns ``[]`` so resolution pauses
    rather than lying (D-04).
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("""
                SELECT id
                FROM senders
                WHERE workspace_id = :wid
                  AND role = 'checker'
                  AND auth_status = 'ok'
                  AND restriction_status = 'none'
                  AND lifecycle_status <> 'paused'
                  AND (restricted_until IS NULL OR restricted_until <= NOW())
                  -- proxy-switch-listener-lag (mig 062): exclude a checker mid proxy
                  -- switch (pending listener/TTL confirmation) from the eligible count.
                  AND (proxy_switch_pending_at IS NULL
                       OR proxy_switch_pending_at
                          < NOW() - make_interval(secs => :proxy_switch_ttl))
            """),
            {"wid": workspace_id,
             "proxy_switch_ttl": get_settings().proxy_switch_pending_ttl_seconds},
        )).fetchall()
    return [str(r.id) for r in rows]
