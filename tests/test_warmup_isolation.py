"""Phase 15 — Warmup isolation RED guards (WARM-01 / WARM-02 / WARM-04).

These tests are intentionally RED at the end of Plan 15-01. They assert the
deterministic internal-traffic short-circuit (D-01/D-02) that Plans 02/03
implement in ``app/services/listener.py``:

- WARM-01: a message is *internal* iff its counterparty ``telegram_id`` belongs
  to another sender of the *same workspace* as the listening sender — NOT keyed
  on phone (closes the ``phone="unknown"`` leak) and NOT keyed on
  ``warmup_pool`` membership. Probed via the new listener helper
  ``_get_workspace_sender_tg_ids(workspace_id)`` (does not exist yet → RED).
- WARM-02: internal inbound traffic is dropped *before* any AI scheduling and
  *before* any ``conversations``/``messages`` write — warmup lives only in the
  ``warmup_*`` tables.
- WARM-04: source-introspection guard — the short-circuit marker token must be
  wired into BOTH ``handle_incoming_message`` and ``handle_outgoing_message``
  so a future refactor can't silently drop it (Phase 13 ``getsource`` pattern).

RED rationale: the helper / short-circuit do not exist yet, so the
behavioural assertions fail and the introspection guard fails on a missing
marker token — for the right reason (missing behaviour), not import errors.
Imports of not-yet-existing symbols are deferred into the test bodies so
``pytest --collect-only`` stays clean.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── Local fake-inbound-event helper (no real Telegram) ───────────────────────


def _make_fake_inbound_event(counterparty_tg_id: int, my_tg_id: int = 999_999):
    """Fabricate a Telethon-ish NewMessage event whose sender is
    ``counterparty_tg_id``. ``event.client.get_me()`` returns a different id so
    the existing 'skip self' check does NOT swallow the message — we want the
    NEW internal short-circuit to be what drops it.
    """
    fake_sender = MagicMock()
    fake_sender.id = counterparty_tg_id
    fake_sender.first_name = "Internal"
    fake_sender.last_name = "Sender"
    fake_sender.phone = "unknown"           # the historical leak vector
    fake_sender.bot = False
    fake_sender.title = None

    me = MagicMock()
    me.id = my_tg_id

    event = MagicMock()
    event.chat_id = counterparty_tg_id
    event.sender_id = counterparty_tg_id
    event.is_group = False
    event.is_channel = False
    event.text = "привет, как дела?"
    event.message = MagicMock()
    event.message.photo = None
    event.message.video = None
    event.message.document = None
    event.message.voice = None
    event.message.id = 1
    event.get_sender = AsyncMock(return_value=fake_sender)
    event.get_chat = AsyncMock(return_value=fake_sender)
    event.client = MagicMock()
    event.client.get_me = AsyncMock(return_value=me)
    event.client.send_read_acknowledge = AsyncMock()
    return event


# ── WARM-01: internal detected from workspace senders (not pool, not phone) ───


async def test_internal_detected_by_workspace_telegram_id(
    async_db_session, test_workspace, test_sender_factory,
):
    """WARM-01: two senders in the same workspace — the listener's workspace
    internal-sender set for that workspace contains the *other* sender's
    telegram_id, independent of warmup_pool membership and of phone.
    """
    sender_a = await test_sender_factory(slug="ws-a-1", telegram_id=111_111)
    sender_b = await test_sender_factory(slug="ws-a-2", telegram_id=222_222)
    assert sender_a.workspace_id == sender_b.workspace_id

    # Deferred import: the helper lands in Plan 02 → AttributeError now (RED).
    from app.services.listener import TelegramListener

    listener = TelegramListener()
    helper = getattr(listener, "_get_workspace_sender_tg_ids", None)
    assert helper is not None, (
        "TelegramListener._get_workspace_sender_tg_ids missing — WARM-01 "
        "deterministic internal-sender set not implemented yet"
    )

    internal_ids = await helper(str(test_workspace.id))
    assert 222_222 in internal_ids, (
        "sender_b's telegram_id must be in the workspace internal set "
        "(detection must not depend on warmup_pool membership or phone)"
    )
    assert 111_111 in internal_ids


# ── WARM-02: internal inbound → no DB write, no AI ───────────────────────────


async def test_internal_inbound_no_dbwrite_no_ai(
    async_db_session, test_workspace, test_sender_factory,
):
    """WARM-02: an inbound message from a known workspace-sender telegram_id is
    dropped before any conversations/messages row is created and before
    schedule_ai_response is called.
    """
    listening = await test_sender_factory(slug="listening", telegram_id=333_333)
    internal = await test_sender_factory(slug="internal", telegram_id=444_444)

    from app.services.listener import TelegramListener

    listener = TelegramListener()
    listener.schedule_ai_response = AsyncMock()

    sender_info = {
        "id": str(listening.id),
        "slug": listening.slug,
        "phone": listening.phone,
        "workspace_id": str(test_workspace.id),
    }

    before = (await async_db_session.execute(
        text("SELECT COUNT(*) FROM messages")
    )).scalar()

    event = _make_fake_inbound_event(counterparty_tg_id=444_444)
    # The handler must short-circuit internal traffic; if it raises on a missing
    # helper / dependency that is the RED signal we want.
    await listener.handle_incoming_message(event, sender_info)

    listener.schedule_ai_response.assert_not_called()

    after = (await async_db_session.execute(
        text("SELECT COUNT(*) FROM messages")
    )).scalar()
    assert after == before, (
        "internal warmup traffic must NOT create rows in messages (WARM-02)"
    )


# ── WARM-04: source-introspection guard — short-circuit wired in both ────────


def test_shortcircuit_wired():
    """WARM-04: the internal short-circuit marker must be present in BOTH
    handlers (so a refactor can't silently remove it). RED until Plan 02 wires
    ``_get_workspace_sender_tg_ids`` into both handler sources.
    """
    from app.services.listener import TelegramListener

    inc = inspect.getsource(TelegramListener.handle_incoming_message)
    out = inspect.getsource(TelegramListener.handle_outgoing_message)
    assert "_get_workspace_sender_tg_ids" in inc, (
        "inbound internal short-circuit not wired into handle_incoming_message"
    )
    assert "_get_workspace_sender_tg_ids" in out, (
        "outgoing internal short-circuit not wired into handle_outgoing_message"
    )
