"""Phase 24 (24-02) — data-model drift + idempotency tests for the campaign
first-message attachment (campaign_attachments) and the variation flag
(campaigns.variation_enabled).

RED-first: authored alongside migration 054 + the ORM mirrors. Extended later by
24-04 with the attachment-endpoint tests (same file).

Covers D-01/D-02/D-04/D-13:
  - variation_enabled defaults true when a raw INSERT omits it (server_default fires).
  - a raw INSERT into campaign_attachments omitting id/size_bytes/created_at succeeds
    (all defaults fire, no NotNullViolation) — the create_all/server_default drift guard.
  - migration 054 is idempotent (applying its DDL twice raises nothing).
"""
import pathlib
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

_MIG_054 = (
    pathlib.Path(__file__).resolve().parent.parent
    / "migrations"
    / "054_campaign_attachment_and_variation.sql"
)


async def test_variation_default_true(async_db_session, test_campaign_factory):
    """campaigns.variation_enabled defaults true — the factory INSERT omits it,
    so the row must read True (D-13 retro-enable-all)."""
    camp = await test_campaign_factory()
    val = (
        await async_db_session.execute(
            text("SELECT variation_enabled FROM campaigns WHERE id = :id"),
            {"id": str(camp["id"])},
        )
    ).scalar_one()
    assert val is True


async def test_attachment_raw_insert_omitting_defaults(
    async_db_session, test_workspace, test_campaign_factory
):
    """A raw INSERT into campaign_attachments that omits id, size_bytes and
    created_at must succeed (defaults fire — no NotNullViolation) and read back
    size_bytes == 0 with a non-null id (D-02/D-04 drift guard)."""
    camp = await test_campaign_factory()
    await async_db_session.execute(
        text(
            """
            INSERT INTO campaign_attachments
                (campaign_id, workspace_id, file_data, file_name)
            VALUES (:cid, :wid, :blob, :fname)
            """
        ),
        {
            "cid": str(camp["id"]),
            "wid": str(test_workspace.id),
            "blob": b"hello-attachment-bytes",
            "fname": "doc.pdf",
        },
    )
    await async_db_session.commit()

    row = (
        await async_db_session.execute(
            text(
                "SELECT id, size_bytes, created_at FROM campaign_attachments "
                "WHERE campaign_id = :cid"
            ),
            {"cid": str(camp["id"])},
        )
    ).first()
    assert row is not None
    assert row[0] is not None            # id default (gen_random_uuid) fired
    assert row[1] == 0                   # size_bytes default 0 fired
    assert row[2] is not None            # created_at default now() fired


async def test_attachment_campaign_id_unique(
    async_db_session, test_workspace, test_campaign_factory
):
    """campaign_id is UNIQUE — a second attachment for the same campaign must
    raise (D-01 exactly-one-attachment-per-campaign)."""
    camp = await test_campaign_factory()
    params = {
        "cid": str(camp["id"]),
        "wid": str(test_workspace.id),
        "blob": b"first",
        "fname": "a.pdf",
    }
    stmt = text(
        "INSERT INTO campaign_attachments (campaign_id, workspace_id, file_data, file_name) "
        "VALUES (:cid, :wid, :blob, :fname)"
    )
    await async_db_session.execute(stmt, params)
    await async_db_session.commit()

    with pytest.raises(Exception):
        params["blob"] = b"second"
        await async_db_session.execute(stmt, params)
        await async_db_session.commit()
    await async_db_session.rollback()


async def test_migration_054_idempotent(async_db_session):
    """Applying migration 054's DDL twice raises nothing — every statement is
    IF NOT EXISTS / ALTER SET DEFAULT (idempotent). The table/column already
    exist (create_all + conftest 054 apply), so both passes are no-ops."""
    assert _MIG_054.exists(), f"migration missing: {_MIG_054}"
    sql = _MIG_054.read_text()
    # asyncpg (SQLAlchemy AsyncSession) executes one statement per call. Strip
    # '--' comment lines FIRST (they may contain ';', which would break a naive
    # split), then split the remaining DDL on ';' and run each non-empty statement.
    code_only = "\n".join(
        ln for ln in sql.splitlines() if not ln.strip().startswith("--")
    )
    statements = [s.strip() for s in code_only.split(";") if s.strip()]
    assert statements, "no executable statements parsed from migration 054"
    for _ in range(2):
        for stmt in statements:
            await async_db_session.execute(text(stmt))
        await async_db_session.commit()
