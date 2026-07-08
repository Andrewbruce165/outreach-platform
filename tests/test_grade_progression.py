"""Phase 22 Plan 02 — grade auto-progression sweep (D-14 advance / D-17 stop-at-3).

Runs the worker's exact set-based UPDATE (`_SWEEP_SQL`, imported from
app.services.grade_progression) against the isolated test session so seed → sweep
→ assertions all live in one rolled-back transaction (no cross-connection
visibility or global side-effects).

Covered behaviours:
- A level-1 sender past its step_days advances to level 2, level_updated_at reset. (D-14)
- A level-1 sender inside its window does NOT advance. (D-14)
- A level-3 sender NEVER advances, even far past any interval. (D-17)
- step_days are read from the PER-WORKSPACE ladder, not a code constant: a
  workspace with a shortened level1_step_days advances a sender that the default
  30-day step would have left in place. (key_link)
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

from app.services.grade_progression import _SWEEP_SQL

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _make_workspace(db) -> str:
    wid = str(_uuid.uuid4())
    await db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, :n)"),
        {"id": wid, "n": f"WS {wid[:8]}"},
    )
    return wid


async def _make_sender(db, wid: str, slug: str, level: int, age_days: int) -> str:
    """Seed a sender at `level` whose level_updated_at is `age_days` in the past."""
    sid = str(_uuid.uuid4())
    await db.execute(
        text(
            """
            INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                                 role, auth_status, lifecycle_status,
                                 rate_per_min, rate_per_hour, rate_per_day,
                                 current_level, level_updated_at)
            VALUES (:id, :wid, :slug, :name, :phone, 'stub',
                    'sender', 'ok', 'active', 4, 20, 150,
                    :lvl, NOW() - make_interval(days => :age))
            """
        ),
        {
            "id": sid, "wid": wid, "slug": slug, "name": slug,
            "phone": f"+790{abs(hash(sid)) % 10_000_000:07d}",
            "lvl": level, "age": age_days,
        },
    )
    return sid


async def _read_level(db, sid: str) -> tuple[int, object]:
    row = (
        await db.execute(
            text("SELECT current_level, level_updated_at FROM senders WHERE id = :id"),
            {"id": sid},
        )
    ).first()
    return row[0], row[1]


# ── D-14: advance / hold ─────────────────────────────────────────────────────


async def test_due_level1_advances_to_level2(async_db_session):
    """A level-1 sender past the 30-day default step advances to level 2 (D-14)."""
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "due-l1", level=1, age_days=31)

    before_level, before_ts = await _read_level(db, sid)
    await db.execute(_SWEEP_SQL)

    after_level, after_ts = await _read_level(db, sid)
    assert after_level == 2, "due level-1 sender must advance to level 2 (D-14)"
    assert after_ts > before_ts, "level_updated_at must be reset to NOW() on advance"


async def test_in_window_sender_does_not_advance(async_db_session):
    """A level-1 sender inside its 30-day window stays at level 1 (D-14)."""
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "in-window", level=1, age_days=5)

    await db.execute(_SWEEP_SQL)

    after_level, _ = await _read_level(db, sid)
    assert after_level == 1, "sender still inside its step window must not advance"


async def test_due_level2_advances_to_level3(async_db_session):
    """A level-2 sender past its step advances to the permanent top level 3 (D-14)."""
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "due-l2", level=2, age_days=40)

    await db.execute(_SWEEP_SQL)

    after_level, _ = await _read_level(db, sid)
    assert after_level == 3, "due level-2 sender must advance to level 3"


# ── D-17: stop-at-3 (permanent top level) ────────────────────────────────────


async def test_level3_sender_never_advances(async_db_session):
    """A level-3 sender never advances, even 999 days past any interval (D-17)."""
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "top", level=3, age_days=999)

    await db.execute(_SWEEP_SQL)

    after_level, _ = await _read_level(db, sid)
    assert after_level == 3, "level 3 is permanent — must never advance past it (D-17)"


# ── key_link: step_days come from the per-workspace ladder, not a constant ────


async def test_step_days_read_from_workspace_ladder(async_db_session):
    """A workspace with a shortened level1_step_days advances a sender that the
    default 30-day step would have left in place (key_link)."""
    db = async_db_session
    wid = await _make_workspace(db)
    # Shorten level-1 step to 10 days for THIS workspace only.
    await db.execute(
        text(
            """
            INSERT INTO sender_grade_settings
                (workspace_id, level1_chats_per_day, level1_step_days,
                 level2_chats_per_day, level2_step_days, level3_chats_per_day)
            VALUES (:wid, 5, 10, 9, 30, 13)
            """
        ),
        {"wid": wid},
    )
    # 15 days old: past the workspace's 10-day step, but WITHIN the default 30.
    sid = await _make_sender(db, wid, "custom-ladder", level=1, age_days=15)

    await db.execute(_SWEEP_SQL)

    after_level, _ = await _read_level(db, sid)
    assert after_level == 2, (
        "sweep must read step_days from sender_grade_settings (10d) — a code "
        "constant of 30d would have left this sender at level 1 (key_link)"
    )
