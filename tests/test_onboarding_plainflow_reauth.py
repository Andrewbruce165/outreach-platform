"""Regression — plain-flow re-auth must UPSERT, not INSERT a duplicate slug.

Bug repro (reauth-verify-2fa-500, 2026-07-02):
- The documented re-auth contract (lovable-handoff/reconciliation.md L44-46) is
  that re-auth reuses the PLAIN onboarding flow (start → verify-code → verify-2fa)
  against the same slug — there is no dedicated /reauth endpoint the UI must use.
  A plain-flow onboarding session has original_sender_id = NULL.
- Before the fix, _finalize_onboarding_or_reauth only routed to the UPDATE branch
  when original_sender_id was NOT NULL. With NULL it fell through to
  _create_sender_from_session, which computes the deterministic slug
  `sender-<telegram_id>` and unconditionally INSERTed. Re-onboarding the same
  physical Telegram account → same telegram_id → same slug → collision on the
  UNIQUE (workspace_id, slug) index idx_senders_workspace_slug → 500
  UniqueViolationError (observed twice in prod on verify-2fa).
- After the fix, _create_sender_from_session is idempotent on (workspace_id, slug):
  if a sender with that slug already exists in the workspace it UPDATEs the
  session/auth_status in place; otherwise it INSERTs a new row.

Tests:
1. test_plainflow_reauth_upserts_existing_sender — NULL original_sender_id + existing
   slug → UPDATE in place, no IntegrityError, exactly 1 row.
2. test_plainflow_reauth_populates_telegram_id_on_new_sender — first-time onboarding
   sets telegram_id (previously omitted).
3. test_plainflow_first_time_still_inserts — no existing slug → normal INSERT.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select, text

from app.models import OnboardingSession, Sender
from app.routers.onboarding import (
    _create_sender_from_session,
    _finalize_onboarding_or_reauth,
)

pytestmark = pytest.mark.asyncio


class _MockCtx:
    """Минимальный stand-in для AuthCtx (нужен только workspace_id)."""

    def __init__(self, wid: uuid.UUID):
        self.workspace_id = wid


def _make_mock_client(telegram_id: int, new_session: str, first_name: str = "Reauthed") -> MagicMock:
    """Telethon-like mock: session.save() → new_session; get_me().id → telegram_id."""
    client = MagicMock(name="MockTelethonClient")
    session = MagicMock()
    session.save = MagicMock(return_value=new_session)
    client.session = session
    client.get_me = AsyncMock(
        return_value=MagicMock(id=telegram_id, first_name=first_name)
    )
    return client


# ─── 1. Main repro: plain-flow re-auth upserts ──────────────────────────────


async def test_plainflow_reauth_upserts_existing_sender(
    async_db_session, test_workspace, test_sender_factory
):
    """original_sender_id NULL + existing slug=sender-<tg_id> → UPDATE, not INSERT.

    This is the exact prod failure: re-auth via the plain onboarding flow whose
    session has no original_sender_id, for an account already onboarded.
    """
    tg_id = 8218483045
    slug = f"sender-{tg_id}"
    existing = await test_sender_factory(
        slug=slug,
        telegram_id=tg_id,
        auth_status="session_expired",
        session_string="encrypted_old_session",
    )
    original_id = existing.id

    mock_client = _make_mock_client(telegram_id=tg_id, new_session="brand_new_session")

    # Plain-flow onboarding session — no original_sender_id (NULL).
    row = OnboardingSession(
        workspace_id=test_workspace.id,
        phone="+79587869196",
        phone_code_hash="hash-plainflow",
        encrypted_session_string="dummy",
        role="sender",
        proxy=None,
        status="awaiting_2fa",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    async_db_session.add(row)
    await async_db_session.commit()
    assert row.original_sender_id is None  # confirms we exercise the plain path

    # Must NOT raise IntegrityError.
    result = await _finalize_onboarding_or_reauth(
        async_db_session, _MockCtx(test_workspace.id), row, mock_client
    )

    # Same row updated in place.
    assert result.id == original_id
    assert result.slug == slug
    assert result.auth_status == "ok"
    assert result.session_string != "encrypted_old_session"

    # Exactly one sender with this (workspace, slug).
    count = (
        await async_db_session.execute(
            text(
                "SELECT COUNT(*) FROM senders WHERE slug = :s AND workspace_id = :w"
            ),
            {"s": slug, "w": str(test_workspace.id)},
        )
    ).scalar()
    assert count == 1, (
        f"regression: expected 1 sender, found {count} "
        "(INSERTed a duplicate instead of upserting)"
    )


# ─── 2. telegram_id populated on first-time onboarding ───────────────────────


async def test_plainflow_first_time_populates_telegram_id(
    async_db_session, test_workspace
):
    """First-time onboarding writes telegram_id (previously omitted from INSERT)."""
    tg_id = 5550001111
    mock_client = _make_mock_client(telegram_id=tg_id, new_session="first_session")

    row = OnboardingSession(
        workspace_id=test_workspace.id,
        phone="+79001112233",
        phone_code_hash="hash-firsttime",
        encrypted_session_string="dummy",
        role="sender",
        proxy=None,
        status="awaiting_2fa",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    async_db_session.add(row)
    await async_db_session.commit()

    result = await _create_sender_from_session(
        async_db_session, _MockCtx(test_workspace.id), row, mock_client
    )

    assert result.slug == f"sender-{tg_id}"
    assert result.telegram_id == tg_id


# ─── 3. first-time onboarding still INSERTs ──────────────────────────────────


async def test_plainflow_first_time_still_inserts(
    async_db_session, test_workspace
):
    """No pre-existing slug → normal INSERT of a new sender."""
    tg_id = 6660002222
    slug = f"sender-{tg_id}"

    before = (
        await async_db_session.execute(
            text("SELECT COUNT(*) FROM senders WHERE slug = :s AND workspace_id = :w"),
            {"s": slug, "w": str(test_workspace.id)},
        )
    ).scalar()
    assert before == 0

    mock_client = _make_mock_client(telegram_id=tg_id, new_session="new_first_session")
    row = OnboardingSession(
        workspace_id=test_workspace.id,
        phone="+79004445566",
        phone_code_hash="hash-new",
        encrypted_session_string="dummy",
        role="sender",
        proxy=None,
        status="awaiting_2fa",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    async_db_session.add(row)
    await async_db_session.commit()

    result = await _finalize_onboarding_or_reauth(
        async_db_session, _MockCtx(test_workspace.id), row, mock_client
    )

    assert result.slug == slug
    assert result.auth_status == "ok"
    after = (
        await async_db_session.execute(
            text("SELECT COUNT(*) FROM senders WHERE slug = :s AND workspace_id = :w"),
            {"s": slug, "w": str(test_workspace.id)},
        )
    ).scalar()
    assert after == 1
