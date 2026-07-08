"""Phase 23 — edit / delete / send-file from the inbox UI + incoming media.

Wave-0 RED scaffold (Plan 23-01). Covers the six capability clusters from
23-VALIDATION.md (INBM-01..08):

  - schema      : migration 053 columns exist, NULL text + default 'text' work
  - messages GET: GET /messages returns the new media fields (23-03)
  - save_message: listener persists message_type + media metadata (23-04)
  - edit        : PATCH /messages/{id} (edit-for-everyone) (23-02)
  - delete      : DELETE /messages/{id} (revoke=True hard delete) (23-02)
  - send-file   : POST /send-file multipart takeover + guards (23-02/23-05)
  - incoming    : listener writes media type/metadata, voice transcribed (23-04)
  - download    : GET /messages/{id}/download bytes + mime (23-05)

Design (RESEARCH): imports of not-yet-existent symbols live INSIDE test bodies and
endpoint calls go through async_client so the file COLLECTS with 0 errors while the
endpoints/service-methods do not yet exist. The behavioural clusters are marked
xfail(strict=False) — Wave-2..5 plans implement them and flip each to green (drop the
marker). Only `test_schema_new_columns_present` is expected GREEN now (it validates
migration 053).

The four NEW TelegramService methods (land in plan 23-02) are patched at the boundary
with raising=False so the monkeypatch itself does not error before the method exists:
edit_message_by_telegram_id / delete_message_by_telegram_id /
send_file_by_telegram_id / download_media_by_telegram_id.
"""

import uuid as _uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

_WAVE2 = pytest.mark.xfail(
    reason="Wave 2..5 — endpoint/service lands in plans 23-02..23-05",
    strict=False,
)


def _auth_headers(jwt_factory, sub: str = "inbm-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _seed_message(db, conv, *, direction="outbound", sent_by="human",
                        message_text="hello", telegram_message_id=None,
                        message_type="text", file_name=None, mime_type=None,
                        size_bytes=None):
    """Insert a messages row inline (test_conversation_factory does NOT create them)."""
    row = (await db.execute(text("""
        INSERT INTO messages (
            workspace_id, conversation_id, direction, message_text, sent_by,
            telegram_message_id, message_type, file_name, mime_type, size_bytes
        ) VALUES (
            :wid, :cid, :dir, :txt, :by, :tmid, :mtype, :fname, :mime, :size
        ) RETURNING id
    """), {
        "wid": str(conv["workspace_id"]),
        "cid": str(conv["id"]),
        "dir": direction,
        "txt": message_text,
        "by": sent_by,
        "tmid": telegram_message_id,
        "mtype": message_type,
        "fname": file_name,
        "mime": mime_type,
        "size": size_bytes,
    })).first()
    await db.commit()
    return row.id


# ── schema cluster (INBM-08) — GREEN once migration 053 is applied ───────────


async def test_schema_new_columns_present(async_db_session, test_conversation_factory):
    """Migration 053: a file bubble (message_text=NULL, message_type='photo' + media
    metadata) inserts cleanly, and a row that OMITS message_type defaults to 'text'."""
    conv = await test_conversation_factory()

    # File bubble — NULL text, non-text type, media metadata.
    await async_db_session.execute(text("""
        INSERT INTO messages (workspace_id, conversation_id, direction, message_text,
                              sent_by, message_type, file_name, mime_type, size_bytes)
        VALUES (:wid, :cid, 'outbound', NULL, 'human',
                'photo', 'pic.jpg', 'image/jpeg', 12345)
    """), {"wid": str(conv["workspace_id"]), "cid": str(conv["id"])})
    await async_db_session.commit()

    # Row that OMITS message_type — DB DEFAULT must backfill 'text'.
    await async_db_session.execute(text("""
        INSERT INTO messages (workspace_id, conversation_id, direction, message_text,
                              sent_by, telegram_message_id)
        VALUES (:wid, :cid, 'inbound', 'plain', 'contact', 424242)
    """), {"wid": str(conv["workspace_id"]), "cid": str(conv["id"])})
    await async_db_session.commit()

    rows = (await async_db_session.execute(text("""
        SELECT message_type, message_text, file_name, mime_type, size_bytes, edited_at
        FROM messages WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).fetchall()

    by_type = {r.message_type: r for r in rows}
    assert "photo" in by_type, "file bubble row missing"
    assert "text" in by_type, "omitted message_type did not default to 'text'"

    photo = by_type["photo"]
    assert photo.message_text is None          # NOT NULL relaxed (D-20)
    assert photo.file_name == "pic.jpg"
    assert photo.mime_type == "image/jpeg"
    assert photo.size_bytes == 12345
    assert photo.edited_at is None             # never edited


async def test_schema_message_type_check_rejects_unknown(
    async_db_session, test_conversation_factory
):
    """CHECK constraint rejects a message_type outside the locked set."""
    conv = await test_conversation_factory()
    with pytest.raises(Exception):
        await async_db_session.execute(text("""
            INSERT INTO messages (workspace_id, conversation_id, direction,
                                  message_text, sent_by, message_type)
            VALUES (:wid, :cid, 'outbound', 'x', 'human', 'sticker')
        """), {"wid": str(conv["workspace_id"]), "cid": str(conv["id"])})
        await async_db_session.commit()
    await async_db_session.rollback()


# ── GET /messages media fields (INBM-06, 23-03) ──────────────────────────────


@_WAVE2
async def test_messages_select_includes_media_fields(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """GET /conversations/{id}/messages returns message_type + media metadata."""
    conv = await test_conversation_factory(workspace_id=test_workspace.id)
    await _seed_message(
        async_db_session, conv, direction="outbound", sent_by="human",
        message_text=None, message_type="photo", file_name="pic.jpg",
        mime_type="image/jpeg", size_bytes=999, telegram_message_id=1001,
    )
    await _bind(async_db_session, test_workspace.id, "u-inbm-select")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/messages",
        headers=_auth_headers(valid_supabase_jwt, "u-inbm-select"),
    )
    assert r.status_code == 200, r.text
    items = r.json()["messages"]
    assert items, "no messages returned"
    photo = items[0]
    assert photo["message_type"] == "photo"
    assert photo["file_name"] == "pic.jpg"
    assert photo["mime_type"] == "image/jpeg"
    assert photo["size_bytes"] == 999


# ── listener save_message persists media (INBM-05, 23-04) ─────────────────────


@_WAVE2
async def test_save_message_persists_media_fields(
    async_db_session, test_conversation_factory,
):
    """listener.save_message(..., message_type=, file_name=, mime_type=, size_bytes=)
    persists the media columns (RED until 23-04 widens the signature/INSERT)."""
    from app.services.listener import TelegramListener

    conv = await test_conversation_factory()
    listener = TelegramListener()
    saved = await listener.save_message(
        conversation_id=str(conv["id"]),
        direction="inbound",
        message_text=None,
        sent_by="contact",
        telegram_message_id=7777,
        message_type="document",
        file_name="report.pdf",
        mime_type="application/pdf",
        size_bytes=2048,
    )
    assert saved is True

    row = (await async_db_session.execute(text("""
        SELECT message_type, file_name, mime_type, size_bytes
        FROM messages WHERE conversation_id = :cid AND telegram_message_id = 7777
    """), {"cid": str(conv["id"])})).first()
    assert row.message_type == "document"
    assert row.file_name == "report.pdf"
    assert row.mime_type == "application/pdf"
    assert row.size_bytes == 2048


# ── edit-for-everyone (INBM-01, 23-02) ───────────────────────────────────────


@_WAVE2
async def test_edit_success_updates_text_and_edited_at(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, monkeypatch,
):
    """PATCH /messages/{id} edits an outbound message in Telegram + sets edited_at."""
    monkeypatch.setattr(
        "app.services.telegram.telegram_service.edit_message_by_telegram_id",
        AsyncMock(return_value={"success": True}), raising=False,
    )
    conv = await test_conversation_factory(
        workspace_id=test_workspace.id, contact_telegram_id=555, status="manual",
    )
    mid = await _seed_message(
        async_db_session, conv, direction="outbound", sent_by="human",
        message_text="old", telegram_message_id=42,
    )
    await _bind(async_db_session, test_workspace.id, "u-edit-ok")

    r = await async_client.patch(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        json={"message": "new text"},
        headers=_auth_headers(valid_supabase_jwt, "u-edit-ok"),
    )
    assert r.status_code == 200, r.text
    row = (await async_db_session.execute(text("""
        SELECT message_text, edited_at FROM messages WHERE id = :id
    """), {"id": str(mid)})).first()
    assert row.message_text == "new text"
    assert row.edited_at is not None


@_WAVE2
async def test_edit_too_old_returns_error(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, monkeypatch,
):
    """Telegram MessageEditTimeExpired → MESSAGE_EDIT_TOO_OLD error surface."""
    from telethon.errors import MessageEditTimeExpiredError

    monkeypatch.setattr(
        "app.services.telegram.telegram_service.edit_message_by_telegram_id",
        AsyncMock(side_effect=MessageEditTimeExpiredError(request=None)),
        raising=False,
    )
    conv = await test_conversation_factory(
        workspace_id=test_workspace.id, contact_telegram_id=556, status="manual",
    )
    mid = await _seed_message(
        async_db_session, conv, direction="outbound", sent_by="human",
        message_text="old", telegram_message_id=43,
    )
    await _bind(async_db_session, test_workspace.id, "u-edit-old")

    r = await async_client.patch(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        json={"message": "new"},
        headers=_auth_headers(valid_supabase_jwt, "u-edit-old"),
    )
    assert r.status_code in (409, 422)
    assert "MESSAGE_EDIT_TOO_OLD" in r.text


@_WAVE2
async def test_edit_not_modified_is_success_noop(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, monkeypatch,
):
    """Telegram MessageNotModified → treated as a success no-op (D-07)."""
    from telethon.errors import MessageNotModifiedError

    monkeypatch.setattr(
        "app.services.telegram.telegram_service.edit_message_by_telegram_id",
        AsyncMock(side_effect=MessageNotModifiedError(request=None)),
        raising=False,
    )
    conv = await test_conversation_factory(
        workspace_id=test_workspace.id, contact_telegram_id=557, status="manual",
    )
    mid = await _seed_message(
        async_db_session, conv, direction="outbound", sent_by="human",
        message_text="same", telegram_message_id=44,
    )
    await _bind(async_db_session, test_workspace.id, "u-edit-nm")

    r = await async_client.patch(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        json={"message": "same"},
        headers=_auth_headers(valid_supabase_jwt, "u-edit-nm"),
    )
    assert r.status_code == 200, r.text


@_WAVE2
async def test_edit_inbound_message_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Editing an inbound (contact) message is not allowed → 404."""
    conv = await test_conversation_factory(workspace_id=test_workspace.id)
    mid = await _seed_message(
        async_db_session, conv, direction="inbound", sent_by="contact",
        message_text="theirs", telegram_message_id=45,
    )
    await _bind(async_db_session, test_workspace.id, "u-edit-in")

    r = await async_client.patch(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        json={"message": "hijack"},
        headers=_auth_headers(valid_supabase_jwt, "u-edit-in"),
    )
    assert r.status_code == 404


@_WAVE2
async def test_edit_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """A message in another workspace is invisible → 404 (no cross-tenant leak)."""
    conv = await test_conversation_factory()  # its own (other) workspace
    mid = await _seed_message(
        async_db_session, conv, direction="outbound", sent_by="human",
        message_text="secret", telegram_message_id=46,
    )
    await _bind(async_db_session, test_workspace.id, "u-edit-x")

    r = await async_client.patch(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        json={"message": "leak"},
        headers=_auth_headers(valid_supabase_jwt, "u-edit-x"),
    )
    assert r.status_code == 404


# ── delete-for-everyone (INBM-02, 23-02) ─────────────────────────────────────


@_WAVE2
async def test_delete_success_hard_deletes_row(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, monkeypatch,
):
    """DELETE /messages/{id} revokes in Telegram (revoke=True) + hard-deletes the row."""
    del_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        "app.services.telegram.telegram_service.delete_message_by_telegram_id",
        del_mock, raising=False,
    )
    conv = await test_conversation_factory(
        workspace_id=test_workspace.id, contact_telegram_id=560, status="manual",
    )
    mid = await _seed_message(
        async_db_session, conv, direction="outbound", sent_by="human",
        message_text="delete me", telegram_message_id=50,
    )
    await _bind(async_db_session, test_workspace.id, "u-del-ok")

    r = await async_client.delete(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        headers=_auth_headers(valid_supabase_jwt, "u-del-ok"),
    )
    assert r.status_code in (200, 204), r.text
    del_mock.assert_awaited()
    # revoke=True passed to the service (delete-for-everyone, not just-for-me)
    _, kwargs = del_mock.await_args
    assert kwargs.get("revoke", True) is True

    left = (await async_db_session.execute(text("""
        SELECT COUNT(*) AS n FROM messages WHERE id = :id
    """), {"id": str(mid)})).first()
    assert left.n == 0


@_WAVE2
async def test_delete_inbound_message_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Deleting an inbound (contact) message is not allowed → 404."""
    conv = await test_conversation_factory(workspace_id=test_workspace.id)
    mid = await _seed_message(
        async_db_session, conv, direction="inbound", sent_by="contact",
        message_text="theirs", telegram_message_id=51,
    )
    await _bind(async_db_session, test_workspace.id, "u-del-in")

    r = await async_client.delete(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        headers=_auth_headers(valid_supabase_jwt, "u-del-in"),
    )
    assert r.status_code == 404


@_WAVE2
async def test_delete_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Deleting a message in another workspace → 404 (no cross-tenant leak)."""
    conv = await test_conversation_factory()
    mid = await _seed_message(
        async_db_session, conv, direction="outbound", sent_by="human",
        message_text="secret", telegram_message_id=52,
    )
    await _bind(async_db_session, test_workspace.id, "u-del-x")

    r = await async_client.delete(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}",
        headers=_auth_headers(valid_supabase_jwt, "u-del-x"),
    )
    assert r.status_code == 404


# ── send file from inbox (INBM-03, 23-02/23-05) ──────────────────────────────


@_WAVE2
async def test_send_file_takeover_and_persists_row(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, monkeypatch,
):
    """POST /send-file: manager takeover (status='manual', ai_enabled=false, pending
    queue failed) + a new messages row carrying message_type."""
    monkeypatch.setattr(
        "app.services.telegram.telegram_service.send_file_by_telegram_id",
        AsyncMock(return_value={
            "success": True, "telegram_message_id": 8001, "message_type": "document",
        }),
        raising=False,
    )
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_telegram_id=600, status="active", ai_enabled=True,
    )
    await _bind(async_db_session, conv["workspace_id"], "u-file-ok")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send-file",
        files={"file": ("report.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"caption": "смотри вложение"},
        headers=_auth_headers(valid_supabase_jwt, "u-file-ok"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    conv_row = (await async_db_session.execute(text("""
        SELECT status, ai_enabled FROM conversations WHERE id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert conv_row.status == "manual"
    assert conv_row.ai_enabled is False

    msg = (await async_db_session.execute(text("""
        SELECT message_type FROM messages
        WHERE conversation_id = :cid ORDER BY created_at DESC LIMIT 1
    """), {"cid": str(conv["id"])})).first()
    assert msg.message_type in ("document", "photo", "video", "voice")


@_WAVE2
async def test_send_file_too_large_returns_413(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory,
):
    """A file over the size cap → 413 FILE_TOO_LARGE (guard runs before any send)."""
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_telegram_id=601, status="active",
    )
    await _bind(async_db_session, conv["workspace_id"], "u-file-big")

    big = b"x" * (51 * 1024 * 1024)  # > 50MB
    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send-file",
        files={"file": ("big.bin", big, "application/octet-stream")},
        headers=_auth_headers(valid_supabase_jwt, "u-file-big"),
    )
    assert r.status_code == 413
    assert "FILE_TOO_LARGE" in r.text


@_WAVE2
async def test_send_file_no_telegram_id_returns_400(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory,
):
    """A conversation without a resolved contact_telegram_id → 400 NO_TELEGRAM_ID."""
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_telegram_id=None, status="active",
    )
    await _bind(async_db_session, conv["workspace_id"], "u-file-noid")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send-file",
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
        headers=_auth_headers(valid_supabase_jwt, "u-file-noid"),
    )
    assert r.status_code == 400
    assert "NO_TELEGRAM_ID" in r.text


@_WAVE2
async def test_send_file_inactive_sender_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory,
):
    """An inactive/paused sender → 404, Telethon never touched."""
    sender = await test_sender_factory(lifecycle_status="paused")
    conv = await test_conversation_factory(
        sender=sender, contact_telegram_id=602, status="active",
    )
    await _bind(async_db_session, conv["workspace_id"], "u-file-inact")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send-file",
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
        headers=_auth_headers(valid_supabase_jwt, "u-file-inact"),
    )
    assert r.status_code == 404


# ── incoming media (INBM-04, 23-04) ──────────────────────────────────────────


@_WAVE2
async def test_incoming_media_writes_type_and_metadata(
    async_db_session, test_conversation_factory,
):
    """listener persists an incoming photo with message_type + name/mime/size."""
    from app.services.listener import TelegramListener

    conv = await test_conversation_factory()
    listener = TelegramListener()
    await listener.save_message(
        conversation_id=str(conv["id"]),
        direction="inbound",
        message_text=None,
        sent_by="contact",
        telegram_message_id=9001,
        message_type="photo",
        file_name="in.jpg",
        mime_type="image/jpeg",
        size_bytes=333,
    )
    row = (await async_db_session.execute(text("""
        SELECT message_type, file_name, mime_type, size_bytes
        FROM messages WHERE telegram_message_id = 9001
    """))).first()
    assert row.message_type == "photo"
    assert row.file_name == "in.jpg"
    assert row.mime_type == "image/jpeg"
    assert row.size_bytes == 333


@_WAVE2
async def test_incoming_media_voice_still_transcribed(
    async_db_session, test_conversation_factory,
):
    """A voice note keeps message_type='voice' AND stores the transcript as text."""
    from app.services.listener import TelegramListener

    conv = await test_conversation_factory()
    listener = TelegramListener()
    await listener.save_message(
        conversation_id=str(conv["id"]),
        direction="inbound",
        message_text="[расшифровка] привет",
        sent_by="contact",
        telegram_message_id=9002,
        message_type="voice",
        file_name="voice.ogg",
        mime_type="audio/ogg",
        size_bytes=1024,
    )
    row = (await async_db_session.execute(text("""
        SELECT message_type, message_text FROM messages WHERE telegram_message_id = 9002
    """))).first()
    assert row.message_type == "voice"
    assert row.message_text and "привет" in row.message_text


@_WAVE2
async def test_incoming_media_idempotent_on_duplicate(
    async_db_session, test_conversation_factory,
):
    """A duplicate telegram_message_id is a no-op (second save returns False)."""
    from app.services.listener import TelegramListener

    conv = await test_conversation_factory()
    listener = TelegramListener()
    first = await listener.save_message(
        conversation_id=str(conv["id"]), direction="inbound", message_text=None,
        sent_by="contact", telegram_message_id=9003, message_type="document",
        file_name="d.pdf", mime_type="application/pdf", size_bytes=10,
    )
    second = await listener.save_message(
        conversation_id=str(conv["id"]), direction="inbound", message_text=None,
        sent_by="contact", telegram_message_id=9003, message_type="document",
        file_name="d.pdf", mime_type="application/pdf", size_bytes=10,
    )
    assert first is True
    assert second is False


# ── download media (INBM-07, 23-05) ──────────────────────────────────────────


@_WAVE2
async def test_download_returns_bytes_and_mime(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, monkeypatch,
):
    """GET /messages/{id}/download streams bytes + mime + Content-Disposition."""
    monkeypatch.setattr(
        "app.services.telegram.telegram_service.download_media_by_telegram_id",
        AsyncMock(return_value=b"%PDF-1.4 real bytes"), raising=False,
    )
    conv = await test_conversation_factory(
        workspace_id=test_workspace.id, contact_telegram_id=700, status="manual",
    )
    mid = await _seed_message(
        async_db_session, conv, direction="inbound", sent_by="contact",
        message_text=None, message_type="document", file_name="doc.pdf",
        mime_type="application/pdf", size_bytes=19, telegram_message_id=800,
    )
    await _bind(async_db_session, test_workspace.id, "u-dl-ok")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}/download",
        headers=_auth_headers(valid_supabase_jwt, "u-dl-ok"),
    )
    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.4 real bytes"
    assert r.headers.get("content-type", "").startswith("application/pdf")
    assert "attachment" in r.headers.get("content-disposition", "").lower()


@_WAVE2
async def test_download_unavailable_returns_error(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, monkeypatch,
):
    """Telegram returns no media (None) → 404/410 MEDIA_UNAVAILABLE."""
    monkeypatch.setattr(
        "app.services.telegram.telegram_service.download_media_by_telegram_id",
        AsyncMock(return_value=None), raising=False,
    )
    conv = await test_conversation_factory(
        workspace_id=test_workspace.id, contact_telegram_id=701, status="manual",
    )
    mid = await _seed_message(
        async_db_session, conv, direction="inbound", sent_by="contact",
        message_text=None, message_type="photo", file_name="gone.jpg",
        mime_type="image/jpeg", size_bytes=5, telegram_message_id=801,
    )
    await _bind(async_db_session, test_workspace.id, "u-dl-gone")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}/download",
        headers=_auth_headers(valid_supabase_jwt, "u-dl-gone"),
    )
    assert r.status_code in (404, 410)
    assert "MEDIA_UNAVAILABLE" in r.text


@_WAVE2
async def test_download_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Downloading media from another workspace → 404 (no cross-tenant leak)."""
    conv = await test_conversation_factory()
    mid = await _seed_message(
        async_db_session, conv, direction="inbound", sent_by="contact",
        message_text=None, message_type="document", file_name="secret.pdf",
        mime_type="application/pdf", size_bytes=9, telegram_message_id=802,
    )
    await _bind(async_db_session, test_workspace.id, "u-dl-x")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/messages/{mid}/download",
        headers=_auth_headers(valid_supabase_jwt, "u-dl-x"),
    )
    assert r.status_code == 404
