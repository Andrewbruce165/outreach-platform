"""Plan 24-03 — send_file blob source + auto-media + force_document passthrough.

RED-first Telethon-mocked unit tests for the delivery primitive extension:

- D-08: `file_bytes` blob source writes straight to a temp file, skipping the
  httpx URL download; the existing `file_url` path stays intact.
- D-06: `force_document` (default True = today's behaviour). `force_document=False`
  yields Telethon auto-media because the temp file keeps the ORIGINAL extension
  (suffix = os.path.splitext(file_name)[1]).
- D-07: caption >1024 chars reuses the EXISTING overflow branch — file sent
  without caption + full text as a separate follow-up message.
- Backwards-compat: the current worker call (file_url=..., no file_bytes, no
  force_document) is byte-identical to today.

The Telethon client is fully mocked (AsyncMock). resolve_contact/check_contact
are patched to a registered contact. httpx.AsyncClient is replaced with a guard
that raises if the blob path ever touches the network.
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.telegram import TelegramService

pytestmark = pytest.mark.asyncio


_REGISTERED = {
    "is_registered": True,
    "telegram_id": 123,
    "access_hash": 456,
    "from_cache": False,
    "first_name": "Тест",
    "username": None,
}


def _mock_client():
    """Telethon client mock: send_file returns an object with .id; send_message AsyncMock."""
    client = MagicMock()
    sent = MagicMock()
    sent.id = 999
    client.send_file = AsyncMock(return_value=sent)
    client.send_message = AsyncMock()
    return client


def _svc():
    svc = TelegramService()
    # Never actually disconnect a MagicMock client in unit tests.
    svc.disconnect_client = AsyncMock()
    return svc


def _httpx_guard():
    """A stand-in for httpx.AsyncClient that fails loudly if constructed/used.

    On the blob path the code must NOT touch the network, so httpx.AsyncClient
    should never be instantiated.
    """
    guard = MagicMock(side_effect=AssertionError("httpx.AsyncClient must NOT be used on the blob path"))
    return guard


async def test_blob_source_auto_media():
    """D-08 + D-06: file_bytes writes a temp file with the original suffix and
    passes force_document=False through to client.send_file, with NO httpx GET."""
    svc = _svc()
    client = _mock_client()

    with patch.object(TelegramService, "check_contact", new=AsyncMock(return_value=_REGISTERED)), \
         patch.object(TelegramService, "resolve_contact", new=AsyncMock(return_value=_REGISTERED)), \
         patch("app.services.telegram.httpx.AsyncClient", new=_httpx_guard()):
        result = await svc.send_file(
            client,
            "+79991234567",
            "Тест",
            file_bytes=b"\xff\xd8\xff\xe0jpgbytes",
            file_name="promo.jpg",
            caption="Привет",
            force_document=False,
        )

    assert result["success"] is True
    client.send_file.assert_awaited_once()
    args, kwargs = client.send_file.call_args
    # Second positional arg is the temp path.
    tmp_path = args[1]
    assert str(tmp_path).endswith(".jpg"), f"temp file must keep .jpg suffix, got {tmp_path}"
    assert kwargs.get("force_document") is False
    assert kwargs.get("caption") == "Привет"
    # send_message (overflow follow-up) must NOT fire for a short caption.
    client.send_message.assert_not_awaited()


async def test_url_default_force_document_true():
    """Backwards-compat: file_url with NO force_document arg → force_document=True
    (today's behaviour) AND the httpx download path is used."""
    svc = _svc()
    client = _mock_client()

    download_mock = MagicMock()
    resp = MagicMock()
    resp.content = b"pdfbytes"
    resp.raise_for_status = MagicMock()
    download_mock.get = AsyncMock(return_value=resp)
    http_cm = MagicMock()
    http_cm.__aenter__ = AsyncMock(return_value=download_mock)
    http_cm.__aexit__ = AsyncMock(return_value=False)

    with patch.object(TelegramService, "check_contact", new=AsyncMock(return_value=_REGISTERED)), \
         patch.object(TelegramService, "resolve_contact", new=AsyncMock(return_value=_REGISTERED)), \
         patch("app.services.telegram.httpx.AsyncClient", return_value=http_cm):
        result = await svc.send_file(
            client,
            "+79991234567",
            "Тест",
            file_url="http://example.com/y.pdf",
            caption="short",
        )

    assert result["success"] is True
    download_mock.get.assert_awaited_once()  # URL download path used
    args, kwargs = client.send_file.call_args
    assert kwargs.get("force_document") is True  # default preserved
    tmp_path = args[1]
    assert str(tmp_path).endswith(".pdf")


async def test_caption_overflow_followup():
    """D-07: caption of length 1500 with file_bytes → send_file caption=None +
    a SECOND client.send_message with the full 1500-char text."""
    svc = _svc()
    client = _mock_client()
    long_caption = "я" * 1500

    with patch.object(TelegramService, "check_contact", new=AsyncMock(return_value=_REGISTERED)), \
         patch.object(TelegramService, "resolve_contact", new=AsyncMock(return_value=_REGISTERED)), \
         patch("app.services.telegram.httpx.AsyncClient", new=_httpx_guard()):
        result = await svc.send_file(
            client,
            "+79991234567",
            "Тест",
            file_bytes=b"jpgbytes",
            file_name="promo.jpg",
            caption=long_caption,
            force_document=False,
        )

    assert result["success"] is True
    _, kwargs = client.send_file.call_args
    assert kwargs.get("caption") is None  # overflow → file sent without caption
    client.send_message.assert_awaited_once()
    msg_args, _ = client.send_message.call_args
    assert msg_args[1] == long_caption  # full text delivered as follow-up


async def test_no_source_returns_error():
    """Guard: both file_url and file_bytes None → structured error, no send_file."""
    svc = _svc()
    client = _mock_client()

    with patch.object(TelegramService, "check_contact", new=AsyncMock(return_value=_REGISTERED)), \
         patch.object(TelegramService, "resolve_contact", new=AsyncMock(return_value=_REGISTERED)):
        result = await svc.send_file(
            client,
            "+79991234567",
            "Тест",
            file_url=None,
            file_bytes=None,
            file_name="promo.jpg",
        )

    assert result["success"] is False
    assert "error" in result
    client.send_file.assert_not_awaited()


async def test_not_registered_returns_recipient_not_in_telegram():
    """Existing behaviour preserved: unregistered contact → RECIPIENT_NOT_IN_TELEGRAM."""
    svc = _svc()
    client = _mock_client()
    not_reg = {"is_registered": False}

    with patch.object(TelegramService, "check_contact", new=AsyncMock(return_value=not_reg)), \
         patch.object(TelegramService, "resolve_contact", new=AsyncMock(return_value=not_reg)), \
         patch("app.services.telegram.httpx.AsyncClient", new=_httpx_guard()):
        result = await svc.send_file(
            client,
            "+79991234567",
            "Тест",
            file_bytes=b"jpgbytes",
            file_name="promo.jpg",
            force_document=False,
        )

    assert result["success"] is False
    assert result["error"]["code"] == "RECIPIENT_NOT_IN_TELEGRAM"
    client.send_file.assert_not_awaited()
