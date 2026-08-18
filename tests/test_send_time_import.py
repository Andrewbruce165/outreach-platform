"""Send-time tier-3 ImportContacts — mandatory cleanup + kill-switch.

Regression guard for the C&C mass-ban (2026-08): both frozen accounts died on the
send-time `ImportContactsRequest`, and the sender HOARDED every imported contact
(D-04) — a growing saved-contacts list that accelerates the shadow-ban. These
tests pin the two remediations in `TelegramService.resolve_contact`:

  1. After the import, the sender ALWAYS cleans up (mirror the checker's WR-07):
     `DeleteContactsRequest` for a surfaced user, `DeleteByPhonesRequest` for an
     empty import that still saved the phone.
  2. `SEND_TIME_IMPORT_ENABLED=False` stops the tier-3 import entirely (kill-switch).

Reaching tier-3 requires: tier-1 cache miss (no cache row), tier-2 skipped
(verdict registered but NO captured/declared @username), and the checker verdict
`tg_status='registered'`.
"""
import pytest
from uuid import uuid4
from sqlalchemy import text

import app.services.telegram as telegram_module
from app.services.telegram import TelegramService

pytestmark = pytest.mark.asyncio


async def _make_sender(db, workspace_id, *, proxied=True, restriction="none"):
    """A real sender row so the H3 import-health gate has something to read."""
    sid = str(uuid4())
    proxy = '{"type":"socks5","host":"h","port":1080,"username":"u","password":"p"}' if proxied else None
    await db.execute(
        text(
            """
            INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                                 role, auth_status, lifecycle_status,
                                 rate_per_min, rate_per_hour, proxy, restriction_status)
            VALUES (:id, :wid, :slug, :slug, :phone, 'stub',
                    'sender', 'ok', 'active', 4, 20, CAST(:proxy AS jsonb), :restr)
            """
        ),
        {"id": sid, "wid": str(workspace_id), "slug": f"t3-s-{uuid4().hex[:8]}",
         "phone": f"+790{abs(hash(sid)) % 10_000_000:07d}", "proxy": proxy, "restr": restriction},
    )
    await db.commit()
    return sid


async def _seed_registered_no_username(db, workspace_id, phone):
    """A contacts row the verdict loader reads as registered with no @username,
    so resolve_contact skips tier-2 and reaches tier-3."""
    folder_id = str(uuid4())
    await db.execute(
        text("INSERT INTO folders (id, workspace_id, name) VALUES (:id, :wid, :name)"),
        {"id": folder_id, "wid": str(workspace_id), "name": f"t3-folder-{uuid4().hex[:8]}"},
    )
    await db.execute(
        text(
            """
            INSERT INTO contacts (id, workspace_id, folder_id, phone, full_name,
                                  tg_status, tg_username_resolved, tg_probe_state,
                                  tg_confidence, tg_checked_at)
            VALUES (:id, :wid, :fid, :phone, 'tier3 contact',
                    'registered', NULL, 'clean', 'high', NOW())
            """
        ),
        {"id": str(uuid4()), "wid": str(workspace_id), "fid": folder_id, "phone": phone},
    )
    await db.commit()


def _imported(telegram_id=123, access_hash=456, username=None):
    class _User:
        id = telegram_id

        def __init__(self):
            self.access_hash = access_hash
            self.first_name = "T3"
            self.last_name = None
            self.username = username

    class _Resp:
        users = [_User()]

    return _Resp()


def _empty_import():
    class _Resp:
        users = []

    return _Resp()


async def test_tier3_import_then_cleanup_on_surfaced_user(
    async_db_session, test_workspace, mock_telethon_client
):
    phone = "+79990080001"
    await _seed_registered_no_username(async_db_session, test_workspace.id, phone)
    sid = await _make_sender(async_db_session, test_workspace.id)
    client = mock_telethon_client
    client.set_response("ImportContactsRequest", _imported(telegram_id=555))

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), sid, phone, "Recipient"
    )
    names = [c[0] for c in client.calls]

    assert "ImportContactsRequest" in names
    assert "DeleteContactsRequest" in names, f"surfaced user must be cleaned up; calls={names}"
    assert names.index("DeleteContactsRequest") > names.index("ImportContactsRequest")
    assert res.get("is_registered") is True


async def test_tier3_empty_import_cleans_by_phone(
    async_db_session, test_workspace, mock_telethon_client
):
    phone = "+79990080002"
    await _seed_registered_no_username(async_db_session, test_workspace.id, phone)
    sid = await _make_sender(async_db_session, test_workspace.id)
    client = mock_telethon_client
    client.set_response("ImportContactsRequest", _empty_import())

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), sid, phone, "Recipient"
    )
    names = [c[0] for c in client.calls]

    assert "ImportContactsRequest" in names
    assert "DeleteByPhonesRequest" in names, f"empty import must be cleaned by phone; calls={names}"
    assert res.get("is_registered") is False


async def test_kill_switch_skips_tier3_import(
    async_db_session, test_workspace, mock_telethon_client, monkeypatch
):
    phone = "+79990080003"
    await _seed_registered_no_username(async_db_session, test_workspace.id, phone)
    client = mock_telethon_client
    client.set_response("ImportContactsRequest", _imported(telegram_id=777))
    monkeypatch.setattr(telegram_module.settings, "send_time_import_enabled", False)

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), str(uuid4()), phone, "Recipient"
    )
    names = [c[0] for c in client.calls]

    assert "ImportContactsRequest" not in names, f"kill-switch must skip import; calls={names}"
    assert res.get("is_registered") is False


async def test_h3_unhealthy_sender_skips_import(
    async_db_session, test_workspace, mock_telethon_client
):
    """H3: a restricted (or unproxied) sender never fires the send-time import even
    with a registered verdict + kill-switch ON — the freeze vector is not spent from
    a vulnerable account."""
    phone = "+79990080004"
    await _seed_registered_no_username(async_db_session, test_workspace.id, phone)
    sid = await _make_sender(async_db_session, test_workspace.id, restriction="spam_limited")
    client = mock_telethon_client
    client.set_response("ImportContactsRequest", _imported(telegram_id=888))

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), sid, phone, "Recipient"
    )
    names = [c[0] for c in client.calls]

    assert "ImportContactsRequest" not in names, f"unhealthy sender must skip import; calls={names}"
    assert res.get("is_registered") is False


async def test_h3_unproxied_sender_skips_import(
    async_db_session, test_workspace, mock_telethon_client
):
    """H3: an unproxied sender also skips the send-time import."""
    phone = "+79990080005"
    await _seed_registered_no_username(async_db_session, test_workspace.id, phone)
    sid = await _make_sender(async_db_session, test_workspace.id, proxied=False)
    client = mock_telethon_client
    client.set_response("ImportContactsRequest", _imported(telegram_id=999))

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), sid, phone, "Recipient"
    )
    names = [c[0] for c in client.calls]

    assert "ImportContactsRequest" not in names, f"unproxied sender must skip import; calls={names}"
    assert res.get("is_registered") is False
