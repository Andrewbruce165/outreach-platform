"""Regression tests for the 2026-07-27 poisoned-cache incident
(.planning/debug/cnc-followup-campaign-100pct-not-registered.md).

Four throttle-degraded checkers wrote durable `is_registered=false` rows into
`contacts_cache`; a later HEALTHY re-check stamped the `contacts` verdict
registered/high/clean, which CLOSED the D-12 suspect gate and made the stale
falses authoritative — every send for those phones short-circuited in
`_get_cached_contact` with ZERO Telegram calls, on every sender.

Fixes under test:
  1. `_get_cached_contact` — newest-row + verdict-recency semantics: a cached
     false older than the newest checker verdict (`contacts.tg_checked_at`)
     is never served; the newest cache row (not "any false row") decides.
  2. `ContactCheckWorker._flag_checker_degraded` — purges the degraded
     checker's fresh false cache rows (mirrors send_suspect._rollback step 4).
  3. `send_message` propagates `from_cache` on RECIPIENT_NOT_IN_TELEGRAM so
     the queue can skip the pointless account re-rotation.
"""
import pytest
from uuid import uuid4
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _mk_sender(db, workspace_id, *, role="checker"):
    from app.models import Sender
    suffix = uuid4().hex[:8]
    s = Sender(
        workspace_id=workspace_id, slug=f"poison-{role}-{suffix}", name=f"poison {role}",
        phone=f"+7999{suffix[:7]}", session_string="enc", role=role,
        auth_status="ok", lifecycle_status="active",
        rate_per_min=4, rate_per_hour=20,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _seed_cache_row(db, workspace_id, sender_id, phone, *,
                          is_registered, age_sql="NOW()",
                          telegram_id=None, access_hash=None):
    await db.execute(text(f"""
        INSERT INTO contacts_cache
            (id, workspace_id, sender_id, phone, telegram_id, access_hash,
             is_registered, updated_at)
        VALUES (:id, :wid, :sid, :phone, :tg, :ah, :reg, {age_sql})
    """), {
        "id": str(uuid4()), "wid": str(workspace_id), "sid": str(sender_id),
        "phone": phone, "tg": telegram_id, "ah": access_hash, "reg": is_registered,
    })
    await db.commit()


async def _seed_contact(db, workspace_id, *, phone, tg_status,
                        tg_username_resolved=None, tg_probe_state=None,
                        tg_confidence=None, checked_at_sql="NULL"):
    suffix = uuid4().hex[:8]
    folder_id = str(uuid4())
    await db.execute(text("""
        INSERT INTO folders (id, workspace_id, name) VALUES (:id, :wid, :name)
    """), {"id": folder_id, "wid": str(workspace_id), "name": f"poison-folder-{suffix}"})
    await db.execute(text(f"""
        INSERT INTO contacts (id, workspace_id, folder_id, phone, full_name, tg_status,
                              tg_username_resolved, tg_probe_state, tg_confidence,
                              tg_checked_at)
        VALUES (:id, :wid, :fid, :phone, 'poison contact', :st, :uname, :probe, :conf,
                {checked_at_sql})
    """), {
        "id": str(uuid4()), "wid": str(workspace_id), "fid": folder_id,
        "phone": phone, "st": tg_status, "uname": tg_username_resolved,
        "probe": tg_probe_state, "conf": tg_confidence,
    })
    await db.commit()


def _resolved(*, telegram_id=999, access_hash=555, username=None):
    class _User:
        id = telegram_id

        def __init__(self):
            self.access_hash = access_hash
            self.first_name = "Test"
            self.last_name = None
            self.username = username

    class _Resolved:
        users = [_User()]

    return _Resolved()


async def test_stale_false_older_than_verdict_forces_live_resolve(
    async_db_session, test_workspace, mock_telethon_client,
):
    """The exact incident shape: poison false written by a (later degraded)
    checker, then a HEALTHY checker re-verifies registered/high/clean — the
    verdict is NEWER than the false. The stale false must NOT short-circuit;
    the sender must run a LIVE resolve (tier-2 on the captured handle)."""
    from app.services.telegram import TelegramService

    phone = "+79990070001"
    poison_checker = await _mk_sender(async_db_session, test_workspace.id)
    # Poison false at T-2h (throttled-checker batch).
    await _seed_cache_row(
        async_db_session, test_workspace.id, poison_checker.id, phone,
        is_registered=False, age_sql="NOW() - INTERVAL '2 hours'",
    )
    # Healthy re-check at T-1h: registered/high/clean — this CLOSED the old
    # D-12 gate and re-enabled the poison before the fix.
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone,
        tg_status="registered", tg_username_resolved="live_handle",
        tg_probe_state="clean", tg_confidence="high",
        checked_at_sql="NOW() - INTERVAL '1 hour'",
    )

    client = mock_telethon_client
    client.set_response(
        "ResolveUsernameRequest",
        _resolved(telegram_id=777, username="live_handle"),
    )

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), str(uuid4()), phone,
    )
    names = [c[0] for c in client.calls]
    assert "ResolveUsernameRequest" in names, (
        "a cached false OLDER than the newest checker verdict must not "
        f"short-circuit — expected a live tier-2 resolve; calls={names}"
    )
    assert res.get("is_registered") is True


async def test_false_newer_than_verdict_still_served_from_cache(
    async_db_session, test_workspace, mock_telethon_client,
):
    """Normal (non-incident) flow is preserved: a false written AFTER the
    checker verdict, with the suspect gate closed, is served from cache with
    zero Telegram calls."""
    from app.services.telegram import TelegramService

    phone = "+79990070002"
    checker = await _mk_sender(async_db_session, test_workspace.id)
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone,
        tg_status="not_registered",
        tg_probe_state="clean", tg_confidence="high",
        checked_at_sql="NOW() - INTERVAL '1 hour'",
    )
    # Cache false NEWER than the verdict (written by the verdict's own batch).
    await _seed_cache_row(
        async_db_session, test_workspace.id, checker.id, phone,
        is_registered=False, age_sql="NOW() - INTERVAL '30 minutes'",
    )

    client = mock_telethon_client
    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), str(uuid4()), phone,
    )
    assert res.get("is_registered") is False
    assert res.get("from_cache") is True
    assert client.calls == [], (
        f"a trusted fresh false must short-circuit with zero Telegram calls; calls={client.calls}"
    )


async def test_newest_true_row_blocks_cross_sender_false(
    async_db_session, test_workspace, mock_telethon_client,
):
    """Newest-row semantics: an older cross-sender false is superseded by a
    NEWER true row from another account — the blind false must not be served
    (`from_cache` short-circuit impossible)."""
    from app.services.telegram import TelegramService

    phone = "+79990070003"
    checker_a = await _mk_sender(async_db_session, test_workspace.id)
    checker_b = await _mk_sender(async_db_session, test_workspace.id)
    await _seed_cache_row(
        async_db_session, test_workspace.id, checker_a.id, phone,
        is_registered=False, age_sql="NOW() - INTERVAL '2 hours'",
    )
    await _seed_cache_row(
        async_db_session, test_workspace.id, checker_b.id, phone,
        is_registered=True, age_sql="NOW() - INTERVAL '1 hour'",
        telegram_id=12345, access_hash=678,
    )

    res = await TelegramService().resolve_contact(
        mock_telethon_client, str(test_workspace.id), str(uuid4()), phone,
    )
    assert res.get("from_cache") is not True, (
        "the newest cache row is TRUE — the older cross-sender false must not "
        f"short-circuit; got {res}"
    )


async def test_flag_checker_degraded_purges_fresh_false_rows(
    async_db_session, test_workspace,
):
    """Fix 2: degrading a checker purges its FRESH false cache rows (the batch
    it burned sliding into the throttle) while keeping old falses and fresh
    trues."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker = await _mk_sender(async_db_session, test_workspace.id)
    fresh_false = "+79990070010"
    old_false = "+79990070011"
    fresh_true = "+79990070012"
    await _seed_cache_row(
        async_db_session, test_workspace.id, checker.id, fresh_false,
        is_registered=False, age_sql="NOW() - INTERVAL '2 minutes'",
    )
    await _seed_cache_row(
        async_db_session, test_workspace.id, checker.id, old_false,
        is_registered=False, age_sql="NOW() - INTERVAL '2 hours'",
    )
    await _seed_cache_row(
        async_db_session, test_workspace.id, checker.id, fresh_true,
        is_registered=True, age_sql="NOW() - INTERVAL '2 minutes'",
        telegram_id=555, access_hash=666,
    )

    await ContactCheckWorker()._flag_checker_degraded(str(checker.id), 2)

    rows = (await async_db_session.execute(text("""
        SELECT phone FROM contacts_cache WHERE sender_id = :sid ORDER BY phone
    """), {"sid": str(checker.id)})).fetchall()
    phones = [r[0] for r in rows]
    assert fresh_false not in phones, (
        "the degraded checker's FRESH false row must be purged (it is the "
        f"poison the throttled batch wrote); remaining={phones}"
    )
    assert old_false in phones, "falses outside the suspect window are kept"
    assert fresh_true in phones, "true rows are never purged"

    status = (await async_db_session.execute(text(
        "SELECT restriction_status FROM senders WHERE id = :sid"
    ), {"sid": str(checker.id)})).scalar()
    assert status == "spam_limited"


async def test_send_error_carries_from_cache_flag(
    async_db_session, test_workspace, mock_telethon_client,
):
    """Fix 3: a cache-sourced RECIPIENT_NOT_IN_TELEGRAM carries
    `from_cache: True` so the queue skips `_reroute_resolve_fail` (rotating
    accounts cannot change a workspace-scoped DB read)."""
    from app.services.telegram import TelegramService

    phone = "+79990070020"
    checker = await _mk_sender(async_db_session, test_workspace.id)
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone,
        tg_status="not_registered",
        tg_probe_state="clean", tg_confidence="high",
        checked_at_sql="NOW() - INTERVAL '1 hour'",
    )
    await _seed_cache_row(
        async_db_session, test_workspace.id, checker.id, phone,
        is_registered=False, age_sql="NOW() - INTERVAL '30 minutes'",
    )

    res = await TelegramService().send_message(
        mock_telethon_client, phone, "Name", "hello",
        sender_id=str(uuid4()), workspace_id=str(test_workspace.id),
    )
    assert res["success"] is False
    assert res["error"]["code"] == "RECIPIENT_NOT_IN_TELEGRAM"
    assert res["error"].get("from_cache") is True, (
        f"cache-sourced resolve fail must be flagged from_cache; got {res['error']}"
    )
