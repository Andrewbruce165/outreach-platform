"""Migration 018 (Phase 05.1) — telemetry_events + ai_contexts v2 columns
+ campaigns v2 columns + backfill of unified webhook_url.

Tests cover (UI-MIG-018):
- Idempotency (apply twice → no error)
- telemetry_events table shape + composite index
- ai_contexts 12 new columns (incl. resurrected auto_pause_triggers)
- voice_baseline CHECK ('Professional','Friendly','Playful')
- auto_pause_scope CHECK ('conversation','contact','campaign')
- campaigns primary_goal CHECK ('book_meeting','qualify','click','engage')
- defaults: tone={"formal":0,"warm":0,"brief":0}, max_message_length=280,
  mirror_language=true, allow_emoji=false, auto_pause_scope='conversation'
- webhook_url backfill from lead/handoff/finish legacy URLs (Pitfall 6 keeps
  the 3 legacy cols, but unifies webhook_url where it was NULL).
"""

import pathlib
import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIG_018 = (PROJECT_ROOT / "migrations" / "018_phase5_1.sql").read_text()
_MIG_032_PATH = PROJECT_ROOT / "migrations" / "032_phase11_field_split.sql"
MIG_032 = _MIG_032_PATH.read_text() if _MIG_032_PATH.exists() else None


# ─── 1. Idempotency ───────────────────────────────────────────────────────────


async def test_migration_018_idempotent(async_db_session):
    """Migration 018 was applied once by conftest. Apply it again — must not fail
    (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS).

    Phase 11 note: migration 032 dropped voice_baseline/tone/tone_of_voice.
    Re-applying 018 re-adds them. We re-apply 032 immediately after to restore
    the Phase-11 schema state so subsequent tests see the correct column set.
    """
    conn = await async_db_session.connection()
    raw = await conn.get_raw_connection()
    # asyncpg driver handles the BEGIN/COMMIT inside the migration body.
    await raw.driver_connection.execute(MIG_018)
    # Re-apply Phase 11 migration to drop the columns 018 just re-added.
    if MIG_032:
        await raw.driver_connection.execute(MIG_032)


# ─── 2. telemetry_events shape ────────────────────────────────────────────────


async def test_telemetry_events_table_shape(async_db_session):
    cols = (await async_db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'telemetry_events'
    """))).scalars().all()
    expected = {"event_id", "workspace_id", "user_id", "event", "props",
                "client_ts", "server_ts"}
    missing = expected - set(cols)
    assert not missing, f"telemetry_events missing columns: {missing}"

    # Composite index for KPI queries.
    idx_rows = (await async_db_session.execute(text("""
        SELECT indexname FROM pg_indexes WHERE tablename = 'telemetry_events'
    """))).scalars().all()
    assert "idx_telemetry_workspace_event_server" in set(idx_rows), \
        "Missing composite index idx_telemetry_workspace_event_server"


# ─── 3. ai_contexts v2 columns present ────────────────────────────────────────


async def test_ai_contexts_v2_columns_exist(async_db_session):
    """Phase 11 D-01: after migration 032, voice_baseline/tone replaced by tone_preset/
    response_speed/response_delay_seconds. Other Phase 05.1 columns still present."""
    cols = (await async_db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_contexts'
    """))).scalars().all()
    col_set = set(cols)
    # Phase 05.1 columns that were NOT dropped by Phase 11:
    expected_present = {
        "who_is_agent", "company_knowledge", "knowledge_base",
        "max_message_length", "mirror_language", "allow_emoji",
        "banlist", "qa_pairs", "auto_pause_triggers", "auto_pause_scope",
    }
    # Phase 11 D-01 new columns (replaces voice_baseline/tone):
    expected_present |= {"tone_preset", "response_speed", "response_delay_seconds"}
    missing = expected_present - col_set
    assert not missing, f"ai_contexts missing v2/Phase-11 columns: {missing}"

    # Phase 11 D-01: voice_baseline and tone must be DROPPED (migration 032)
    for dropped in ("voice_baseline", "tone", "tone_of_voice"):
        assert dropped not in col_set, \
            f"ai_contexts Phase-11 dropped column still present: {dropped}"


# ─── 4. tone_preset CHECK (Phase 11 replaces voice_baseline CHECK) ────────────


async def test_tone_preset_check_rejects_invalid(
    async_db_session, test_workspace
):
    """Invalid tone_preset string must violate ai_contexts_tone_preset_check (Phase 11 D-01)."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as exc_info:
        await async_db_session.execute(text("""
            INSERT INTO ai_contexts (workspace_id, name, tone_preset)
            VALUES (:wid, :name, 'Aggressive')
        """), {
            "wid": str(test_workspace.id),
            "name": f"Bad tone {_uuid.uuid4()}",
        })
        await async_db_session.commit()
    await async_db_session.rollback()
    err = str(exc_info.value).lower()
    assert "tone_preset" in err or "check constraint" in err


async def test_tone_preset_check_accepts_four_values(
    async_db_session, test_workspace
):
    """All four Phase 11 tone_preset values must INSERT cleanly."""
    for v in ("Friendly", "Professional", "Direct", "Casual"):
        await async_db_session.execute(text("""
            INSERT INTO ai_contexts (workspace_id, name, tone_preset)
            VALUES (:wid, :name, :v)
        """), {
            "wid": str(test_workspace.id),
            "name": f"tone-{v}-{_uuid.uuid4()}",
            "v": v,
        })
    await async_db_session.commit()


# ─── 5. primary_goal CHECK ────────────────────────────────────────────────────


async def test_primary_goal_check_rejects_invalid(
    async_db_session, test_workspace, test_agent_factory, test_folder,
):
    """Invalid primary_goal violates campaigns_primary_goal_check."""
    from sqlalchemy.exc import IntegrityError

    agent = await test_agent_factory()
    with pytest.raises(IntegrityError) as exc_info:
        await async_db_session.execute(text("""
            INSERT INTO campaigns (workspace_id, agent_id, folder_id, name,
                                   message_template, primary_goal)
            VALUES (:wid, :aid, :fid, :name, 'hi', 'ascend_to_godhood')
        """), {
            "wid": str(test_workspace.id),
            "aid": str(agent.id),
            "fid": str(test_folder.id),
            "name": f"bad-goal-{_uuid.uuid4()}",
        })
        await async_db_session.commit()
    await async_db_session.rollback()
    err = str(exc_info.value).lower()
    assert "primary_goal" in err or "check constraint" in err


async def test_primary_goal_check_accepts_four_values(
    async_db_session, test_workspace, test_agent_factory, test_folder,
):
    """All four documented primary_goal values must INSERT cleanly."""
    agent = await test_agent_factory()
    for g in ("book_meeting", "qualify", "click", "engage"):
        await async_db_session.execute(text("""
            INSERT INTO campaigns (workspace_id, agent_id, folder_id, name,
                                   message_template, primary_goal)
            VALUES (:wid, :aid, :fid, :name, 'hi', :g)
        """), {
            "wid": str(test_workspace.id),
            "aid": str(agent.id),
            "fid": str(test_folder.id),
            "name": f"goal-{g}-{_uuid.uuid4()}",
            "g": g,
        })
    await async_db_session.commit()


# ─── 6. auto_pause_scope CHECK ────────────────────────────────────────────────


async def test_auto_pause_scope_check(async_db_session, test_workspace):
    """ai_contexts.auto_pause_scope: rejects 'invalid', accepts conversation/contact/campaign."""
    from sqlalchemy.exc import IntegrityError

    # Accepts the 3 valid values.
    for scope in ("conversation", "contact", "campaign"):
        await async_db_session.execute(text("""
            INSERT INTO ai_contexts (workspace_id, name, auto_pause_scope)
            VALUES (:wid, :name, :scope)
        """), {
            "wid": str(test_workspace.id),
            "name": f"scope-{scope}-{_uuid.uuid4()}",
            "scope": scope,
        })
    await async_db_session.commit()

    # Rejects invalid.
    with pytest.raises(IntegrityError) as exc_info:
        await async_db_session.execute(text("""
            INSERT INTO ai_contexts (workspace_id, name, auto_pause_scope)
            VALUES (:wid, :name, 'bogus_scope')
        """), {
            "wid": str(test_workspace.id),
            "name": f"bad-scope-{_uuid.uuid4()}",
        })
        await async_db_session.commit()
    await async_db_session.rollback()
    err = str(exc_info.value).lower()
    assert "auto_pause_scope" in err or "check constraint" in err


# ─── 7. Defaults applied at INSERT time ───────────────────────────────────────


async def test_defaults_applied(async_db_session, test_workspace):
    """INSERT ai_contexts with only required cols — defaults must materialise.

    Phase 11 note: `tone` (JSONB slider) was dropped by migration 032.
    We only check the columns still present after Phase 11.
    """
    name = f"defaults-{_uuid.uuid4()}"
    await async_db_session.execute(text("""
        INSERT INTO ai_contexts (workspace_id, name)
        VALUES (:wid, :name)
    """), {"wid": str(test_workspace.id), "name": name})
    await async_db_session.commit()

    row = (await async_db_session.execute(text("""
        SELECT max_message_length, mirror_language, allow_emoji,
               auto_pause_scope
        FROM ai_contexts WHERE name = :name
    """), {"name": name})).first()

    assert row is not None, "Inserted ai_contexts row not found"
    max_len, mirror, emoji, scope = row
    # Phase 11 D-02: tone (JSONB slider) dropped — no longer tested here.
    assert max_len == 280
    assert mirror is True
    assert emoji is False
    assert scope == "conversation"


# ─── 8. webhook_url backfill ──────────────────────────────────────────────────


async def test_webhook_url_backfill(
    async_db_session, test_workspace, test_agent_factory, test_folder,
):
    """Insert a campaign with lead_webhook_url set and webhook_url NULL, then
    re-apply migration 018 — webhook_url must get backfilled from lead_webhook_url
    via the COALESCE UPDATE inside the migration.
    """
    agent = await test_agent_factory()
    cid = str(_uuid.uuid4())

    # Insert with lead_webhook_url populated, unified webhook_url explicitly NULL.
    await async_db_session.execute(text("""
        INSERT INTO campaigns (
            id, workspace_id, agent_id, folder_id, name,
            message_template, lead_webhook_url, webhook_url
        ) VALUES (
            :cid, :wid, :aid, :fid, :name,
            'hi', 'https://lead.test/hook', NULL
        )
    """), {
        "cid": cid,
        "wid": str(test_workspace.id),
        "aid": str(agent.id),
        "fid": str(test_folder.id),
        "name": f"backfill-{_uuid.uuid4()}",
    })
    await async_db_session.commit()

    # Sanity precondition.
    pre = (await async_db_session.execute(text("""
        SELECT webhook_url FROM campaigns WHERE id = :cid
    """), {"cid": cid})).scalar()
    assert pre is None, f"Setup precondition failed; webhook_url already set: {pre!r}"

    # Re-apply migration 018 — UPDATE inside it should backfill webhook_url.
    conn = await async_db_session.connection()
    raw = await conn.get_raw_connection()
    await raw.driver_connection.execute(MIG_018)

    # Phase 11 note: 018 re-adds voice_baseline/tone/tone_of_voice. Re-apply 032
    # immediately to restore Phase-11 schema so later tests see the correct state.
    if MIG_032:
        await raw.driver_connection.execute(MIG_032)

    # asyncpg ran on a different connection; commit visibility check.
    await async_db_session.commit()

    post = (await async_db_session.execute(text("""
        SELECT webhook_url FROM campaigns WHERE id = :cid
    """), {"cid": cid})).scalar()
    assert post == "https://lead.test/hook", (
        f"webhook_url backfill from lead_webhook_url failed; got {post!r}"
    )
