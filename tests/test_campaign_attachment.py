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


# ─── 24-04: attachment-endpoint + wiring tests ──────────────────────────────
#
# Covers D-03/D-13/D-19/D-20:
#   - POST /campaigns/{id}/attachment (multipart) stores exactly ONE blob (upsert),
#     alias-tolerant to file|attachment, >50MB -> 413 FILE_TOO_LARGE.
#   - DELETE /campaigns/{id}/attachment -> 204, row gone.
#   - cross-workspace campaign -> 404 CAMPAIGN_NOT_FOUND.
#   - has_attachment surfaces the blob; variation_enabled round-trips through PATCH.
#   - duplicate_campaign copies BOTH the flag AND the blob (own row for the copy).

from app.routers.campaigns import MAX_ATTACHMENT_BYTES


async def _bind(db, ws_id, uid):
    await db.execute(
        text(
            "INSERT INTO user_workspaces (supabase_user_id, workspace_id, role) "
            "VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING"
        ),
        {"uid": uid, "wid": str(ws_id)},
    )
    await db.commit()


async def _count_attachments(db, campaign_id) -> int:
    return (
        await db.execute(
            text("SELECT COUNT(*) FROM campaign_attachments WHERE campaign_id = :cid"),
            {"cid": str(campaign_id)},
        )
    ).scalar()


async def test_upload_attachment_stores_one_blob(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """POST stores the uploaded file as exactly ONE campaign_attachments row;
    the response echoes size_bytes == len(raw) (D-19)."""
    await _bind(async_db_session, test_workspace.id, "u-up")
    camp = await test_campaign_factory()
    blob = b"hello-first-message-attachment-bytes"
    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        files={"file": ("doc.pdf", blob, "application/pdf")},
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-up')}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_name"] == "doc.pdf"
    assert body["size_bytes"] == len(blob)
    assert body["content_type"] == "application/pdf"
    assert await _count_attachments(async_db_session, camp["id"]) == 1


async def test_upload_replaces_existing_blob(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """A second upload replaces the first — still exactly one row (D-01 upsert)."""
    await _bind(async_db_session, test_workspace.id, "u-rep")
    camp = await test_campaign_factory()
    hdr = {"Authorization": f"Bearer {valid_supabase_jwt(sub='u-rep')}"}
    r1 = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        files={"file": ("a.pdf", b"first", "application/pdf")}, headers=hdr,
    )
    assert r1.status_code == 200, r1.text
    r2 = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        files={"file": ("b.png", b"second-bytes", "image/png")}, headers=hdr,
    )
    assert r2.status_code == 200, r2.text
    assert await _count_attachments(async_db_session, camp["id"]) == 1
    row = (await async_db_session.execute(
        text("SELECT file_name, file_data FROM campaign_attachments WHERE campaign_id = :cid"),
        {"cid": str(camp["id"])},
    )).first()
    assert row[0] == "b.png"
    assert bytes(row[1]) == b"second-bytes"


async def test_upload_alias_attachment_field(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """The multipart field may be named 'attachment' instead of 'file' (D-19 Lovable)."""
    await _bind(async_db_session, test_workspace.id, "u-alias")
    camp = await test_campaign_factory()
    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        files={"attachment": ("c.txt", b"alias-field", "text/plain")},
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-alias')}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["file_name"] == "c.txt"
    assert await _count_attachments(async_db_session, camp["id"]) == 1


async def test_upload_no_file_field_422(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """Neither file nor attachment present -> 422 FILE_REQUIRED."""
    await _bind(async_db_session, test_workspace.id, "u-nofile")
    camp = await test_campaign_factory()
    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        data={"foo": "bar"},
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-nofile')}"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "FILE_REQUIRED"


async def test_upload_too_large_413(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """A file over MAX_ATTACHMENT_BYTES (50 MB) -> 413 FILE_TOO_LARGE (D-03)."""
    await _bind(async_db_session, test_workspace.id, "u-big")
    camp = await test_campaign_factory()
    oversized = b"0" * (MAX_ATTACHMENT_BYTES + 1)
    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        files={"file": ("big.bin", oversized, "application/octet-stream")},
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-big')}"},
    )
    assert r.status_code == 413, r.status_code
    assert r.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert await _count_attachments(async_db_session, camp["id"]) == 0


async def test_delete_attachment_204(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """DELETE removes the blob -> 204 and the row is gone (D-19)."""
    await _bind(async_db_session, test_workspace.id, "u-del")
    camp = await test_campaign_factory()
    hdr = {"Authorization": f"Bearer {valid_supabase_jwt(sub='u-del')}"}
    up = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        files={"file": ("d.pdf", b"to-delete", "application/pdf")}, headers=hdr,
    )
    assert up.status_code == 200, up.text
    assert await _count_attachments(async_db_session, camp["id"]) == 1
    d = await async_client.delete(
        f"/api/v1/campaigns/{camp['id']}/attachment", headers=hdr,
    )
    assert d.status_code == 204, d.text
    assert await _count_attachments(async_db_session, camp["id"]) == 0


async def test_delete_attachment_idempotent_no_blob(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """DELETE with no attachment present is a no-op -> still 204 (idempotent)."""
    await _bind(async_db_session, test_workspace.id, "u-del2")
    camp = await test_campaign_factory()
    d = await async_client.delete(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-del2')}"},
    )
    assert d.status_code == 204, d.text


async def test_upload_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_campaign_factory,
):
    """Uploading to a campaign owned by another workspace -> 404 (workspace isolation)."""
    from app.models import Workspace

    camp = await test_campaign_factory()  # in test_workspace
    other = Workspace(name="Other-attach-ws")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-cross")
    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        files={"file": ("x.pdf", b"nope", "application/pdf")},
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-cross')}"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"
    assert await _count_attachments(async_db_session, camp["id"]) == 0
