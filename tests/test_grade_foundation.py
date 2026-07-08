"""Phase 22 Plan 01 — grade-ladder foundation schema + backfill guards.

Proves the durable data-model foundation is correct against a fresh test-overlay
DB (build via conftest create_all + the SQL-only 056/057 blocks):

- grade columns exist and a freshly inserted sender defaults current_level=1 (D-14).
- the sender_first_contacts backfill is idempotent — an already-warmed pair is
  recorded exactly once no matter how many times the 057 backfill runs (D-08).
- load_ladder resolves the 5/9/13 code-default budgets when a workspace has no
  sender_grade_settings row (D-16).
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── Helpers (raw INSERT to avoid coupling to ORM defaults) ───────────────────


async def _make_workspace(db) -> str:
    wid = str(_uuid.uuid4())
    await db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, :n)"),
        {"id": wid, "n": f"WS {wid[:8]}"},
    )
    await db.commit()
    return wid


async def _make_sender(db, wid: str, slug: str) -> str:
    sid = str(_uuid.uuid4())
    await db.execute(
        text(
            """
        INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                             role, auth_status, lifecycle_status,
                             rate_per_min, rate_per_hour, rate_per_day)
        VALUES (:id, :wid, :slug, :name, :phone, 'stub',
                'sender', 'ok', 'active', 4, 20, 150)
    """
        ),
        {"id": sid, "wid": wid, "slug": slug, "name": slug,
         "phone": f"+790{abs(hash(sid)) % 10_000_000:07d}"},
    )
    await db.commit()
    return sid


# The 057 backfill from warmup_sessions, verbatim (canonical LEAST/GREATEST pair).
_BACKFILL_057 = """
INSERT INTO sender_first_contacts (sender_a_id, sender_b_id, first_contact_at)
SELECT LEAST(sender_a_id, sender_b_id),
       GREATEST(sender_a_id, sender_b_id),
       MIN(created_at)
  FROM warmup_sessions
 WHERE sender_a_id IS NOT NULL
   AND sender_b_id IS NOT NULL
   AND sender_a_id <> sender_b_id
 GROUP BY LEAST(sender_a_id, sender_b_id), GREATEST(sender_a_id, sender_b_id)
ON CONFLICT DO NOTHING
"""


async def test_grade_columns_exist_and_default(async_db_session):
    """senders has current_level / level_updated_at; a new sender defaults level 1."""
    db = async_db_session
    wid = await _make_workspace(db)
    sid = await _make_sender(db, wid, "grade-default")

    row = (
        await db.execute(
            text("SELECT current_level, level_updated_at FROM senders WHERE id = :id"),
            {"id": sid},
        )
    ).first()

    assert row is not None
    assert row[0] == 1              # current_level defaults to 1 (D-14)
    assert row[1] is not None       # level_updated_at is populated


async def test_first_contacts_backfill_idempotent(async_db_session):
    """Running the 057 backfill twice records an already-warmed pair exactly once."""
    db = async_db_session
    wid = await _make_workspace(db)
    a = await _make_sender(db, wid, "pair-a")
    b = await _make_sender(db, wid, "pair-b")

    # An existing warmup session between the pair (already-warmed before Phase 22).
    await db.execute(
        text(
            """
        INSERT INTO warmup_sessions (id, workspace_id, sender_a_id, sender_b_id,
                                     topic, status, messages_sent, target_messages,
                                     next_message_at, created_at, updated_at)
        VALUES (gen_random_uuid(), :wid, :a, :b, 'topic', 'completed', 6, 6,
                NOW(), NOW(), NOW())
    """
        ),
        {"wid": wid, "a": a, "b": b},
    )
    await db.commit()

    # Run the 057 backfill twice — must be idempotent.
    await db.execute(text(_BACKFILL_057))
    await db.commit()
    await db.execute(text(_BACKFILL_057))
    await db.commit()

    cnt = (
        await db.execute(
            text(
                """
        SELECT COUNT(*) FROM sender_first_contacts
         WHERE (sender_a_id = :a AND sender_b_id = :b)
            OR (sender_a_id = :b AND sender_b_id = :a)
    """
            ),
            {"a": a, "b": b},
        )
    ).scalar()

    assert cnt == 1  # already-warmed pair recorded exactly once (D-08)


async def test_load_ladder_code_defaults(async_db_session):
    """A workspace with no sender_grade_settings row resolves to code-defaults."""
    from app.services.grade_ladder import load_ladder

    db = async_db_session
    wid = await _make_workspace(db)  # deliberately no sender_grade_settings row

    ladder = await load_ladder(db, wid)
    budgets = [level[0] for level in ladder]

    assert budgets == [5, 9, 13]  # D-16 code-defaults
