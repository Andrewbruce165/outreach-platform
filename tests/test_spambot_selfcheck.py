"""SpamBot self-check antispam guard (quick task 260622-gxt).

When we ping @SpamBot ourselves (reconcile sweep or manual check), its reply
lands in the listener's update stream and would normally trigger
`_handle_antispam_signal` — cancelling the sender's own queue + disabling AI.
The self-check registry marks a short solicited window so the handler skips the
auto-cancel for *our* ping, while genuine unsolicited warnings still cancel.

Covers:
  1. mark/is registry + TTL expiry (unit, fake clock).
  2. _handle_antispam_signal skips cancellation when the marker is active.
  3. _handle_antispam_signal still cancels when no marker is set.
"""

import types

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── 1: registry mark/is + TTL expiry ─────────────────────────────────────────


async def test_selfcheck_mark_is_and_ttl_expiry(monkeypatch):
    import app.services.telegram as tg

    clock = [1000.0]
    monkeypatch.setattr(
        "app.services.telegram.time",
        types.SimpleNamespace(monotonic=lambda: clock[0]),
    )

    svc = tg.TelegramService()
    assert svc.is_spambot_selfcheck("acc") is False

    svc.mark_spambot_selfcheck("acc", ttl=30)
    assert svc.is_spambot_selfcheck("acc") is True       # t=1000, exp=1030

    clock[0] = 1029.0
    assert svc.is_spambot_selfcheck("acc") is True        # still inside window

    clock[0] = 1031.0
    assert svc.is_spambot_selfcheck("acc") is False       # expired
    # Expired entry must be pruned, not linger in the dict.
    assert "acc" not in svc._spambot_selfcheck


async def test_selfcheck_prunes_unrelated_expired_entries(monkeypatch):
    import app.services.telegram as tg

    clock = [500.0]
    monkeypatch.setattr(
        "app.services.telegram.time",
        types.SimpleNamespace(monotonic=lambda: clock[0]),
    )
    svc = tg.TelegramService()
    svc.mark_spambot_selfcheck("old", ttl=10)   # exp=510
    svc.mark_spambot_selfcheck("live", ttl=60)  # exp=560

    clock[0] = 520.0
    # Querying any key prunes all expired entries.
    assert svc.is_spambot_selfcheck("live") is True
    assert "old" not in svc._spambot_selfcheck


# ── helpers for the listener guard tests ─────────────────────────────────────


def _sender_info(sender) -> dict:
    return {
        "id": str(sender.id),
        "workspace_id": str(sender.workspace_id),
        "slug": sender.slug,
        "phone": sender.phone,
    }


async def _seed_queue_and_conversation(async_db_session, sender, workspace):
    """One pending queue item + one ai_enabled conversation for `sender`."""
    qid = (await async_db_session.execute(text("""
        INSERT INTO message_queue
            (workspace_id, sender_id, item_type, status, recipient_phone, message_text)
        VALUES (:wid, :sid, 'message', 'pending', '+79990001112', 'Hi')
        RETURNING id
    """), {"wid": str(workspace.id), "sid": str(sender.id)})).scalar()
    cid = (await async_db_session.execute(text("""
        INSERT INTO conversations
            (workspace_id, sender_id, contact_phone, contact_telegram_id, ai_enabled, status)
        VALUES (:wid, :sid, '+79990001112', 555001, true, 'active')
        RETURNING id
    """), {"wid": str(workspace.id), "sid": str(sender.id)})).scalar()
    await async_db_session.commit()
    return qid, cid


# ── 2: guard active → no cancellation ────────────────────────────────────────


async def test_antispam_guard_skips_when_selfcheck_active(
    async_db_session, test_sender_factory, test_workspace,
):
    from app.services.listener import TelegramListener
    from app.services.telegram import telegram_service

    sender = await test_sender_factory()
    qid, cid = await _seed_queue_and_conversation(async_db_session, sender, test_workspace)

    telegram_service.mark_spambot_selfcheck(sender.slug, ttl=30)
    try:
        listener_obj = TelegramListener()
        await listener_obj._handle_antispam_signal(
            _sender_info(sender), "SpamBot", 178220800, "Good news, no limits!"
        )
    finally:
        telegram_service._spambot_selfcheck.clear()

    q_status = (await async_db_session.execute(
        text("SELECT status FROM message_queue WHERE id = :id"), {"id": str(qid)}
    )).scalar()
    ai_enabled = (await async_db_session.execute(
        text("SELECT ai_enabled FROM conversations WHERE id = :id"), {"id": str(cid)}
    )).scalar()

    assert q_status == "pending"      # solicited reply → queue untouched
    assert ai_enabled is True         # AI not disabled


# ── 3: no marker → pause + flag (unified freeze policy, Phase 07) ─────────────


async def test_antispam_guard_pauses_and_flags_when_no_selfcheck(
    async_db_session, test_sender_factory, test_workspace,
):
    """Unsolicited antispam warning → pause pending (status stays 'pending',
    scheduled_at +24h), flag sender spam_limited, leave ai_enabled untouched.

    Mirrors the PEER_FLOOD soft-restriction contract so the reconcile sweep
    (status='pending' AND scheduled_at > NOW()) can auto-resume the queue.
    """
    from app.services.listener import TelegramListener
    from app.services.telegram import telegram_service

    sender = await test_sender_factory()
    qid, cid = await _seed_queue_and_conversation(async_db_session, sender, test_workspace)

    # Ensure no stray marker from another test.
    telegram_service._spambot_selfcheck.pop(sender.slug, None)

    listener_obj = TelegramListener()
    await listener_obj._handle_antispam_signal(
        _sender_info(sender), "SpamBot", 178220800, "Your account is limited"
    )

    q_status, scheduled_future = (await async_db_session.execute(
        text("SELECT status, scheduled_at > NOW() FROM message_queue WHERE id = :id"),
        {"id": str(qid)},
    )).one()
    ai_enabled = (await async_db_session.execute(
        text("SELECT ai_enabled FROM conversations WHERE id = :id"), {"id": str(cid)}
    )).scalar()
    restriction = (await async_db_session.execute(
        text("SELECT restriction_status FROM senders WHERE id = :id"),
        {"id": str(sender.id)},
    )).scalar()

    assert q_status == "pending"          # paused, not failed → reconcile can resume
    assert scheduled_future is True       # scheduled_at pushed into the future (+24h)
    assert ai_enabled is True             # AI left on — replies keep flowing (FRZ-03)
    assert restriction == "spam_limited"  # sender flagged (FRZ-01)


# ── 4: unsolicited CLEAN SpamBot reply → must NOT flag (regression 2026-06-29) ──


async def test_antispam_guard_skips_clean_spambot_body_even_without_marker(
    async_db_session, test_sender_factory, test_workspace,
):
    """A @SpamBot reply that says the account is CLEAN ("Good news, no limits …
    free as a bird!") must NOT flag spam_limited, even with no self-check marker.

    Regression for the 2026-06-29 false-positive: _handle_antispam_signal flagged
    on SpamBot *sender id* alone and ignored the body, so a clean reply pinned a
    checker spam_limited for 6h while @SpamBot reported no restriction. The body is
    now classified (classify_spambot_text) and only 'limited'/'suspended' flags.
    See .planning/debug/checker-false-spam-limited.md.
    """
    from app.services.listener import TelegramListener
    from app.services.telegram import telegram_service

    sender = await test_sender_factory()
    qid, cid = await _seed_queue_and_conversation(async_db_session, sender, test_workspace)

    # No self-check marker — exercise the body classifier, not the solicited guard.
    telegram_service._spambot_selfcheck.pop(sender.slug, None)

    listener_obj = TelegramListener()
    await listener_obj._handle_antispam_signal(
        _sender_info(sender), "SpamBot", 178220800,
        "Good news, no limits are currently applied to your account. "
        "You’re free as a bird!",
    )

    q_status = (await async_db_session.execute(
        text("SELECT status FROM message_queue WHERE id = :id"), {"id": str(qid)}
    )).scalar()
    restriction = (await async_db_session.execute(
        text("SELECT restriction_status FROM senders WHERE id = :id"),
        {"id": str(sender.id)},
    )).scalar()

    assert q_status == "pending"     # clean reply → queue untouched (not paused)
    assert restriction == "none"     # sender NOT flagged (the bug fix)
