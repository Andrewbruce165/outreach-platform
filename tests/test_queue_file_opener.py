"""Plan 24-06 Task 2 — campaign file-opener blob delivery + media-typed inbox row.

Integration tests for the queue worker file branch (D-05/D-06/D-08 + Phase 23
mig-053 inbox fidelity, bridged by migration 055):

- ATT-DELIVER: an item_type='file' opener with campaign_id set + file_url NULL
  loads the blob from campaign_attachments by campaign_id and calls
  telegram_service.send_file(file_bytes=<blob>, file_name=<name>,
  caption=<varied caption>, force_document=False). The varied caption is
  strip_invisible-identical to the clean caption but byte-different.
- INBOX-MEDIA: the messages row written for that opener carries message_type
  derived from the attachment extension (jpg->photo, mp4->video, pdf->document)
  plus non-null file_name/mime_type/size_bytes, and message_text = the CLEAN
  (unvaried) caption (D-14). A text opener keeps message_type='text'.
- FALLBACK: item_type='file' with a file_url (no blob) → the legacy URL path is
  used (send_file with file_url=...), no crash, and the messages row is
  plain-text (message_type='text').

telegram_service.get_client + send_file are mocked (AsyncMock); the real
Postgres test DB stores the queue/attachment/messages rows.
"""

import json as _json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.services import queue as queue_module
from app.services.queue import QueueWorker
from app.services.variation import strip_invisible

pytestmark = pytest.mark.asyncio


_CLEAN_CAPTION = "Смотрите наше коммерческое предложение по подсолнечнику подробно"


def _ok_file_result(message_id: str = "2001") -> dict:
    return {
        "success": True,
        "action": "file_sent",
        "message_id": message_id,
        "recipient": {"telegram_id": 777, "name": "Тест", "username": None},
    }


async def _insert_attachment(
    db, *, workspace_id, campaign_id, file_name, content_type, size_bytes,
    file_data: bytes = b"\xff\xd8\xffblobbytes", position: int = 0,
    replace: bool = True,
):
    # 260709-dbl: campaign_attachments is 1-to-N now (no UNIQUE(campaign_id)), so the
    # old ON CONFLICT (campaign_id) upsert no longer has a matching constraint.
    # `replace=True` clears the set first (single-file semantics); pass replace=False
    # + distinct positions to build a multi-file album.
    if replace:
        await db.execute(
            text("DELETE FROM campaign_attachments WHERE campaign_id = :cid"),
            {"cid": str(campaign_id)},
        )
    await db.execute(text("""
        INSERT INTO campaign_attachments
            (campaign_id, workspace_id, file_data, file_name, content_type, size_bytes, position)
        VALUES (:cid, :wid, :data, :fn, :ct, :sz, :pos)
    """), {
        "cid": str(campaign_id), "wid": str(workspace_id), "data": file_data,
        "fn": file_name, "ct": content_type, "sz": size_bytes, "pos": position,
    })
    await db.commit()


async def _insert_file_item(
    db, *, workspace_id, sender_id, campaign_id,
    recipient_phone: str = "+79997778899",
    caption: str = _CLEAN_CAPTION, file_url=None, file_name=None,
) -> str:
    qid = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, caption, file_url, file_name,
            scheduled_at, extra_data
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'file', 'processing', :rp, :cap, :furl, :fn, :sa, '{}'::jsonb
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id) if campaign_id else None,
        "rp": recipient_phone, "cap": caption, "furl": file_url, "fn": file_name,
        "sa": datetime.now(timezone.utc) - timedelta(minutes=1),
    })
    await db.commit()
    return qid


async def _insert_message_item(
    db, *, workspace_id, sender_id, campaign_id,
    recipient_phone: str = "+79995550000", message_text: str = "Просто текст",
) -> str:
    qid = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text, scheduled_at, extra_data
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'processing', :rp, :txt, :sa, '{}'::jsonb
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id) if campaign_id else None,
        "rp": recipient_phone, "txt": message_text,
        "sa": datetime.now(timezone.utc) - timedelta(minutes=1),
    })
    await db.commit()
    return qid


def _patch_file_send(capture: dict):
    async def _fake_send_file(**kwargs):
        capture.setdefault("calls", []).append(kwargs)
        return _ok_file_result(str(2000 + len(capture["calls"])))

    async def _fake_send_message(**kwargs):
        capture.setdefault("msgs", []).append(kwargs.get("message"))
        return {"success": True, "message_id": "3001",
                "recipient": {"telegram_id": 777, "name": "Тест", "username": None}}

    return (
        patch.object(queue_module.telegram_service, "get_client",
                     new=AsyncMock(return_value=MagicMock())),
        patch.object(queue_module.telegram_service, "send_file",
                     new=AsyncMock(side_effect=_fake_send_file)),
        patch.object(queue_module.telegram_service, "send_message",
                     new=AsyncMock(side_effect=_fake_send_message)),
    )


async def _inbox_row(db, phone: str) -> dict:
    row = (await db.execute(text("""
        SELECT m.message_text, m.message_type, m.file_name, m.mime_type, m.size_bytes
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.contact_phone = :p AND m.direction = 'outbound'
        ORDER BY m.created_at DESC LIMIT 1
    """), {"p": phone})).first()
    return dict(row._mapping) if row else {}


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_file_opener_blob_automedia_photo(
    async_db_session, test_running_campaign_factory
):
    """ATT-DELIVER + INBOX-MEDIA: jpg attachment → send_file(file_bytes, force_document=False)
    with varied caption; inbox row is message_type='photo' + media metadata; clean text."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    # variation on by default (server_default true); assert explicitly for clarity.
    await async_db_session.execute(
        text("UPDATE campaigns SET variation_enabled = true WHERE id = :c"),
        {"c": str(camp["id"])})
    await async_db_session.commit()
    await _insert_attachment(
        async_db_session, workspace_id=camp["workspace_id"], campaign_id=camp["id"],
        file_name="promo.jpg", content_type="image/jpeg", size_bytes=1234,
    )
    phone = "+79997770001"
    qid = await _insert_file_item(
        async_db_session, workspace_id=camp["workspace_id"],
        sender_id=senders[0].id, campaign_id=camp["id"], recipient_phone=phone,
    )

    cap: dict = {}
    cm_c, cm_f, cm_m = _patch_file_send(cap)
    with cm_c, cm_f, cm_m:
        await QueueWorker()._send_item(qid)

    assert len(cap["calls"]) == 1
    kw = cap["calls"][0]
    assert kw.get("force_document") is False
    assert kw.get("file_bytes") == b"\xff\xd8\xffblobbytes"
    assert kw.get("file_name") == "promo.jpg"
    sent_caption = kw.get("caption")
    assert sent_caption != _CLEAN_CAPTION, "caption must be varied (flag on)"
    assert strip_invisible(sent_caption) == _CLEAN_CAPTION

    row = await _inbox_row(async_db_session, phone)
    assert row["message_type"] == "photo"
    assert row["file_name"] == "promo.jpg"
    assert row["mime_type"] == "image/jpeg"
    assert row["size_bytes"] == 1234
    assert row["message_text"] == _CLEAN_CAPTION, "inbox text must be the CLEAN caption (D-14)"


async def test_file_opener_multiple_attachments_album(
    async_db_session, test_running_campaign_factory
):
    """260709-dbl: 2 attachment rows (ORDER BY position) → the worker delivers an
    album: send_file receives an `attachments` list of len 2 (ordered), the single
    file_bytes path is NOT used, and the inbox row records the FIRST file."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    await _insert_attachment(
        async_db_session, workspace_id=camp["workspace_id"], campaign_id=camp["id"],
        file_name="deck.pdf", content_type="application/pdf", size_bytes=11,
        file_data=b"deckbytes01", position=0, replace=True,
    )
    await _insert_attachment(
        async_db_session, workspace_id=camp["workspace_id"], campaign_id=camp["id"],
        file_name="photo.jpg", content_type="image/jpeg", size_bytes=9,
        file_data=b"jpgbytes1", position=1, replace=False,
    )
    phone = "+79997773344"
    qid = await _insert_file_item(
        async_db_session, workspace_id=camp["workspace_id"],
        sender_id=senders[0].id, campaign_id=camp["id"], recipient_phone=phone,
    )

    cap: dict = {}
    cm_c, cm_f, cm_m = _patch_file_send(cap)
    with cm_c, cm_f, cm_m:
        await QueueWorker()._send_item(qid)

    assert len(cap["calls"]) == 1
    kw = cap["calls"][0]
    atts = kw.get("attachments")
    assert isinstance(atts, list) and len(atts) == 2, "album must pass a 2-item attachments list"
    assert [a["file_name"] for a in atts] == ["deck.pdf", "photo.jpg"]
    assert [a["file_bytes"] for a in atts] == [b"deckbytes01", b"jpgbytes1"]
    assert kw.get("force_document") is False
    # The single-blob path must NOT be used for an album.
    assert kw.get("file_bytes") is None
    sent_caption = kw.get("caption")
    assert strip_invisible(sent_caption) == _CLEAN_CAPTION

    # Inbox records the primary (first) file (v1 limitation — all ARE delivered).
    row = await _inbox_row(async_db_session, phone)
    assert row["message_type"] == "document"   # deck.pdf → document
    assert row["file_name"] == "deck.pdf"
    assert row["message_text"] == _CLEAN_CAPTION


async def test_file_opener_video_and_document_classification(
    async_db_session, test_running_campaign_factory
):
    """INBOX-MEDIA: mp4 → 'video', pdf → 'document' (extension auto-classification)."""
    for ext_name, ctype, expected in (
        ("clip.mp4", "video/mp4", "video"),
        ("offer.pdf", "application/pdf", "document"),
    ):
        camp, senders = await test_running_campaign_factory(sender_count=1)
        await _insert_attachment(
            async_db_session, workspace_id=camp["workspace_id"], campaign_id=camp["id"],
            file_name=ext_name, content_type=ctype, size_bytes=50,
        )
        phone = f"+7999777{hash(ext_name) % 10000:04d}"
        qid = await _insert_file_item(
            async_db_session, workspace_id=camp["workspace_id"],
            sender_id=senders[0].id, campaign_id=camp["id"], recipient_phone=phone,
        )
        cap: dict = {}
        cm_c, cm_f, cm_m = _patch_file_send(cap)
        with cm_c, cm_f, cm_m:
            await QueueWorker()._send_item(qid)
        row = await _inbox_row(async_db_session, phone)
        assert row["message_type"] == expected, f"{ext_name} → {expected}"


async def test_text_opener_row_stays_text(
    async_db_session, test_running_campaign_factory
):
    """A non-file (text) opener keeps message_type='text' (DB DEFAULT), no media."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    phone = "+79995550101"
    qid = await _insert_message_item(
        async_db_session, workspace_id=camp["workspace_id"],
        sender_id=senders[0].id, campaign_id=camp["id"], recipient_phone=phone,
    )
    cap: dict = {}
    cm_c, cm_f, cm_m = _patch_file_send(cap)
    with cm_c, cm_f, cm_m:
        await QueueWorker()._send_item(qid)

    row = await _inbox_row(async_db_session, phone)
    assert row["message_type"] == "text"
    assert row["file_name"] is None and row["mime_type"] is None and row["size_bytes"] is None


async def test_missing_attachment_falls_back_to_url_plaintext(
    async_db_session, test_running_campaign_factory
):
    """FALLBACK: file item with a file_url (no blob row) → legacy URL send_file,
    no crash, plain-text messages row (message_type='text')."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    phone = "+79997770202"
    # file_url set → the blob branch is skipped; no attachment row inserted.
    qid = await _insert_file_item(
        async_db_session, workspace_id=camp["workspace_id"],
        sender_id=senders[0].id, campaign_id=camp["id"], recipient_phone=phone,
        caption="short", file_url="http://example.com/legacy.pdf", file_name="legacy.pdf",
    )
    cap: dict = {}
    cm_c, cm_f, cm_m = _patch_file_send(cap)
    with cm_c, cm_f, cm_m:
        await QueueWorker()._send_item(qid)

    assert len(cap["calls"]) == 1
    kw = cap["calls"][0]
    assert kw.get("file_url") == "http://example.com/legacy.pdf"
    assert kw.get("file_bytes") is None
    row = await _inbox_row(async_db_session, phone)
    assert row["message_type"] == "text", "legacy URL path leaves the row plain-text"
