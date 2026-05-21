"""Unit tests for ``app/services/onboarding_state.py`` (Phase 2 — D-16).

Covers:
* ``save_state`` inserts an encrypted row with status='code_sent'.
* ``load_state`` returns row only for matching workspace, ``None`` otherwise.
* ``load_state`` returns ``None`` when ``expires_at`` is past.
* ``update_status`` flips status (and optionally session_string).
* ``OnboardingCleanupWorker`` start/stop/tick deletes expired rows.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models import OnboardingSession
from app.services.encryption import decrypt_session, encrypt_session
from app.services.onboarding_state import (
    OnboardingCleanupWorker,
    delete_session,
    load_state,
    onboarding_cleanup_worker,
    save_state,
    update_status,
)


# ─── save_state ──────────────────────────────────────────────────────────────


async def test_save_state_inserts_row_encrypted(async_db_session, test_workspace):
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="hash-abc",
        session_string="raw-session-string",
        role="sender",
        proxy={"type": "socks5", "host": "1.2.3.4", "port": 1080},
    )

    assert isinstance(sid, uuid.UUID)

    row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == sid)
        )
    ).scalar_one()
    assert row.workspace_id == test_workspace.id
    assert row.phone == "+79001234567"
    assert row.phone_code_hash == "hash-abc"
    assert row.role == "sender"
    assert row.status == "code_sent"
    assert row.expires_at > datetime.now(timezone.utc)
    # Session string is encrypted, not raw
    assert row.encrypted_session_string != "raw-session-string"
    assert decrypt_session(row.encrypted_session_string) == "raw-session-string"


async def test_save_state_invalid_role_raises(async_db_session, test_workspace):
    with pytest.raises(ValueError, match="Invalid role"):
        await save_state(
            db=async_db_session,
            workspace_id=test_workspace.id,
            phone="+79001234567",
            phone_code_hash="hash",
            session_string="ss",
            role="not-a-role",
        )


async def test_save_state_checker_role(async_db_session, test_workspace):
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="hash",
        session_string="ss",
        role="checker",
    )
    row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == sid)
        )
    ).scalar_one()
    assert row.role == "checker"


# ─── load_state ──────────────────────────────────────────────────────────────


async def test_load_state_returns_row_for_correct_workspace(
    async_db_session, test_workspace
):
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="h",
        session_string="s",
    )
    row = await load_state(async_db_session, sid, test_workspace.id)
    assert row is not None
    assert row.id == sid


async def test_load_state_cross_tenant_returns_none(
    async_db_session, test_workspace
):
    """Session in workspace A — request from workspace B → None (D-16 isolation)."""
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="h",
        session_string="s",
    )
    other_ws = uuid.uuid4()
    row = await load_state(async_db_session, sid, other_ws)
    assert row is None


async def test_load_state_expired_returns_none(async_db_session, test_workspace):
    """Row with expires_at in the past → None (caller maps to 404)."""
    sid = uuid.uuid4()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await async_db_session.execute(
        text(
            """
            INSERT INTO onboarding_sessions
                (id, workspace_id, phone, phone_code_hash,
                 encrypted_session_string, role, status, expires_at)
            VALUES
                (:id, :wid, :ph, :hash, :ess, 'sender', 'code_sent', :exp)
            """
        ),
        {
            "id": str(sid),
            "wid": str(test_workspace.id),
            "ph": "+79001234567",
            "hash": "h",
            "ess": encrypt_session("s"),
            "exp": past,
        },
    )
    await async_db_session.commit()

    row = await load_state(async_db_session, sid, test_workspace.id)
    assert row is None


# ─── update_status ───────────────────────────────────────────────────────────


async def test_update_status_changes_status(async_db_session, test_workspace):
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="h",
        session_string="s",
    )
    await update_status(async_db_session, sid, "awaiting_2fa")

    row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == sid)
        )
    ).scalar_one()
    assert row.status == "awaiting_2fa"


async def test_update_status_with_new_session_string(
    async_db_session, test_workspace
):
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="h",
        session_string="initial",
    )
    new_encrypted = encrypt_session("refreshed-session")
    await update_status(
        async_db_session,
        sid,
        "awaiting_2fa",
        encrypted_session_string=new_encrypted,
    )
    row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == sid)
        )
    ).scalar_one()
    assert row.status == "awaiting_2fa"
    assert decrypt_session(row.encrypted_session_string) == "refreshed-session"


async def test_update_status_invalid_raises(async_db_session, test_workspace):
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="h",
        session_string="s",
    )
    with pytest.raises(ValueError, match="Invalid status"):
        await update_status(async_db_session, sid, "garbage")


async def test_delete_session_removes_row(async_db_session, test_workspace):
    sid = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="h",
        session_string="s",
    )
    await delete_session(async_db_session, sid)
    row = (
        await async_db_session.execute(
            select(OnboardingSession).where(OnboardingSession.id == sid)
        )
    ).scalar_one_or_none()
    assert row is None


# ─── OnboardingCleanupWorker ─────────────────────────────────────────────────


async def test_worker_start_creates_task():
    worker = OnboardingCleanupWorker()
    worker.interval = 3600  # don't actually tick during the test
    worker.start()
    assert worker._task is not None
    assert not worker._task.done()
    await worker.stop()
    assert worker._task.done() or worker._task.cancelled()


async def test_worker_stop_is_idempotent():
    worker = OnboardingCleanupWorker()
    worker.interval = 3600
    worker.start()
    await worker.stop()
    # Second stop must not raise
    await worker.stop()


async def test_worker_tick_deletes_expired(async_db_session, test_workspace):
    """One tick deletes rows with expires_at < NOW(), keeps fresh ones."""
    # Expired row
    expired_id = uuid.uuid4()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await async_db_session.execute(
        text(
            """
            INSERT INTO onboarding_sessions
                (id, workspace_id, phone, phone_code_hash,
                 encrypted_session_string, role, status, expires_at)
            VALUES
                (:id, :wid, '+7', 'h', :ess, 'sender', 'code_sent', :exp)
            """
        ),
        {
            "id": str(expired_id),
            "wid": str(test_workspace.id),
            "ess": encrypt_session("s"),
            "exp": past,
        },
    )
    await async_db_session.commit()

    # Fresh row (TTL still in the future)
    fresh_id = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="h",
        session_string="s",
    )

    worker = OnboardingCleanupWorker()
    deleted = await worker._tick()
    assert deleted >= 1

    rows = (
        await async_db_session.execute(
            select(OnboardingSession.id).where(
                OnboardingSession.workspace_id == test_workspace.id
            )
        )
    ).scalars().all()
    assert expired_id not in rows
    assert fresh_id in rows


def test_module_singleton_exists():
    """The module-level singleton is the one main.py imports."""
    assert isinstance(onboarding_cleanup_worker, OnboardingCleanupWorker)
