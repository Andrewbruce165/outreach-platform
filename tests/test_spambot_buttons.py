"""Integration tests for @SpamBot inline/reply-keyboard button capture + click
(quick task 260713-jmp).

Covers:
  1. listener._persist_spambot_message serializes event.message.reply_markup into
     messages.buttons (2D {text} array, row/col = Telethon indexing).
  2. reply_markup=None (plain text) → messages.buttons stays NULL (no behavior
     change from 260713-hiw).
  3. GET /conversations/{id}/messages returns each row's `buttons`.
  4. POST /conversations/{id}/messages/{message_id}/click on a foreign-workspace
     message → 404 MESSAGE_NOT_FOUND (tenant isolation, no existence leak).
  5. Click on a valid @SpamBot message → calls
     telegram_service.click_message_button_by_telegram_id with the resolved
     sender session + (row, col); a mocked success returns {"success": true}.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.listener import TelegramListener

pytestmark = pytest.mark.asyncio

SPAMBOT_ID = 178220800


# ─── Helpers (mirror tests/test_spambot_conversation.py) ──────────────────────


async def _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub: str):
    """Bootstrap a workspace via JWT POST /auth/me. Returns (token, workspace_id)."""
    token = valid_supabase_jwt(sub=sub, email=f"{sub}@test.com")
    r = await async_client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return token, r.json()["workspace_id"]


async def _insert_sender_raw(db: AsyncSession, workspace_id: str, slug: str) -> str:
    """Direct INSERT into senders (active + ok). Returns sender_id."""
    sid = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO senders
                (id, workspace_id, slug, name, phone, session_string, role,
                 lifecycle_status, auth_status, rate_per_min, rate_per_hour)
            VALUES
                (:id, :wid, :slug, :name, :phone, 'encrypted_stub', 'sender',
                 'active', 'ok', 4, 20)
        """),
        {"id": sid, "wid": workspace_id, "slug": slug, "name": slug,
         "phone": f"+7900{sid[:7]}"},
    )
    await db.commit()
    return sid


async def _insert_spambot_conversation(
    db: AsyncSession, workspace_id: str, sender_id: str
) -> str:
    """Direct INSERT of a status='spambot' conversation. Returns conversation_id."""
    cid = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO conversations (
                id, workspace_id, sender_id, contact_phone, contact_name,
                contact_telegram_id, ai_enabled, status
            )
            VALUES (:id, :wid, :sid, 'spambot:178220800', '@SpamBot',
                    :tid, false, 'spambot')
        """),
        {"id": cid, "wid": workspace_id, "sid": sender_id, "tid": SPAMBOT_ID},
    )
    await db.commit()
    return cid


async def _insert_inbound_message(
    db: AsyncSession, workspace_id: str, conversation_id: str,
    telegram_message_id: int, buttons: list | None,
) -> str:
    """Direct INSERT of an inbound message with optional buttons. Returns id."""
    mid = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO messages
                (id, workspace_id, conversation_id, direction, message_text,
                 sent_by, telegram_message_id, buttons)
            VALUES (:id, :wid, :cid, 'inbound', 'Menu', 'contact', :tmid,
                    CAST(:buttons AS JSONB))
        """),
        {"id": mid, "wid": workspace_id, "cid": conversation_id,
         "tmid": telegram_message_id,
         "buttons": json.dumps(buttons) if buttons else None},
    )
    await db.commit()
    return mid


def _fake_event(message_id: int, textbody: str, reply_markup) -> SimpleNamespace:
    """Telethon NewMessage stand-in: exposes .id, .text, .message.reply_markup."""
    return SimpleNamespace(
        id=message_id,
        text=textbody,
        message=SimpleNamespace(reply_markup=reply_markup),
    )


def _fake_reply_markup(rows: list[list[str]]) -> SimpleNamespace:
    """Build a Telethon-like reply_markup: .rows[].buttons[].text."""
    return SimpleNamespace(rows=[
        SimpleNamespace(buttons=[SimpleNamespace(text=label) for label in row])
        for row in rows
    ])


# ─── 1: listener capture serializes reply_markup ─────────────────────────────


async def test_persist_spambot_message_captures_button_layout(
    async_client, async_db_session, valid_supabase_jwt
):
    _token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="btn-cap"
    )
    sid = await _insert_sender_raw(async_db_session, ws, "btn-cap-sender")

    listener = TelegramListener()
    sender_info = {"id": sid, "workspace_id": ws, "slug": "btn-cap-sender"}
    spambot = SimpleNamespace(id=SPAMBOT_ID)
    event = _fake_event(
        901, "Choose", _fake_reply_markup([["A", "B"], ["C"]])
    )

    await listener._persist_spambot_message(
        sender_info, spambot, event, "@SpamBot", "unknown"
    )

    row = (await async_db_session.execute(text("""
        SELECT buttons FROM messages WHERE telegram_message_id = 901
    """))).fetchone()
    assert row is not None
    assert row.buttons == [[{"text": "A"}, {"text": "B"}], [{"text": "C"}]]


async def test_persist_spambot_message_plain_text_buttons_null(
    async_client, async_db_session, valid_supabase_jwt
):
    _token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="btn-plain"
    )
    sid = await _insert_sender_raw(async_db_session, ws, "btn-plain-sender")

    listener = TelegramListener()
    sender_info = {"id": sid, "workspace_id": ws, "slug": "btn-plain-sender"}
    spambot = SimpleNamespace(id=SPAMBOT_ID)
    event = _fake_event(902, "Your account is free", reply_markup=None)

    await listener._persist_spambot_message(
        sender_info, spambot, event, "@SpamBot", "unknown"
    )

    row = (await async_db_session.execute(text("""
        SELECT buttons FROM messages WHERE telegram_message_id = 902
    """))).fetchone()
    assert row is not None
    assert row.buttons is None


# ─── 2: GET /messages returns buttons ────────────────────────────────────────


async def test_get_messages_returns_buttons(
    async_client, async_db_session, valid_supabase_jwt
):
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="btn-get"
    )
    sid = await _insert_sender_raw(async_db_session, ws, "btn-get-sender")
    cid = await _insert_spambot_conversation(async_db_session, ws, sid)
    await _insert_inbound_message(
        async_db_session, ws, cid, 903, [[{"text": "Appeal"}]]
    )

    r = await async_client.get(
        f"/api/v1/conversations/{cid}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["buttons"] == [[{"text": "Appeal"}]]


# ─── 3: click endpoint — cross-tenant → 404 ──────────────────────────────────


async def test_click_button_cross_tenant_returns_404(
    async_client, async_db_session, valid_supabase_jwt
):
    token_a, _ws_a = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="btn-cross-a"
    )
    _token_b, ws_b = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="btn-cross-b"
    )
    sid_b = await _insert_sender_raw(async_db_session, ws_b, "btn-cross-b-sender")
    cid_b = await _insert_spambot_conversation(async_db_session, ws_b, sid_b)
    mid_b = await _insert_inbound_message(
        async_db_session, ws_b, cid_b, 904, [[{"text": "X"}]]
    )

    # Workspace A cannot click a message living in workspace B.
    r = await async_client.post(
        f"/api/v1/conversations/{cid_b}/messages/{mid_b}/click",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"row": 0, "col": 0},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MESSAGE_NOT_FOUND"


# ─── 4: click endpoint — success (mocked Telethon) ───────────────────────────


async def test_click_button_success(
    async_client, async_db_session, valid_supabase_jwt, monkeypatch
):
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="btn-click"
    )
    sid = await _insert_sender_raw(async_db_session, ws, "btn-click-sender")
    cid = await _insert_spambot_conversation(async_db_session, ws, sid)
    mid = await _insert_inbound_message(
        async_db_session, ws, cid, 905, [[{"text": "Unfreeze"}]]
    )

    captured = {}

    async def _fake_click(**kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "app.routers.conversations.telegram_service.click_message_button_by_telegram_id",
        _fake_click,
    )

    r = await async_client.post(
        f"/api/v1/conversations/{cid}/messages/{mid}/click",
        headers={"Authorization": f"Bearer {token}"},
        json={"row": 0, "col": 0},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True}
    # Endpoint resolved the sender session + passed (row, col) through.
    assert captured["sender_slug"] == "btn-click-sender"
    assert captured["telegram_id"] == SPAMBOT_ID
    assert captured["telegram_message_id"] == 905
    assert captured["row"] == 0
    assert captured["col"] == 0
