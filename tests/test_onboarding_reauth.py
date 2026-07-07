"""Phase 02.1 (CR-05) — reauth flow: UPDATE existing sender, не INSERT нового.

Bug repro (closed by 02.1-02 Task 2):
- До фикса /reauth/{sender_slug} → verify-code → _create_sender_from_session
  INSERT'ил нового sender'а с slug=sender-{telegram_id}. Поскольку Sender.slug
  был globally UNIQUE — второй reauth того же slug падал с IntegrityError.
- После фикса (миграция 014 + Task 2): reauth-сессия маркируется
  original_sender_id в onboarding_sessions, и verify-code/verify-2fa/_wait_for_qr
  вызывают _refresh_sender_session (UPDATE), не _create_sender_from_session.

Tests:
1. test_save_state_persists_original_sender_id — save_state кладёт sender.id в колонку.
2. test_load_existing_sender_returns_sender — _load_existing_sender находит sender.
3. test_load_existing_sender_workspace_isolation — cross-workspace = None.
4. test_refresh_sender_session_updates_in_place — UPDATE not INSERT, slug preserved.
5. test_double_refresh_does_not_break — двойной reauth подряд без IntegrityError.
6. test_finalize_with_original_sender_id_takes_refresh_branch — helper выбирает UPDATE.
7. test_finalize_without_original_sender_id_takes_create_branch — обычный onboarding.
8. test_finalize_with_deleted_sender_falls_back_to_create — edge: sender удалён → INSERT.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text

# Гарантируем, что миграция 014 применена до запуска reauth-тестов.
# test_migration_014.py имеет module-scope autouse fixture, но если pytest
# выбирает только этот файл — нам нужен независимый apply.
import pathlib
import asyncpg

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION_014 = PROJECT_ROOT / "migrations" / "014_phase2_1_hardening.sql"

from app.models import OnboardingSession, Sender
from app.routers.onboarding import (
    _finalize_onboarding_or_reauth,
    _load_existing_sender,
    _refresh_sender_session,
)
from app.services.onboarding_state import load_state, save_state

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="module", autouse=True)
async def apply_migration_014(migrations_raw_dsn):
    """Re-apply 014 on the DEDICATED migrations DB, NOT the shared session DB.

    conftest._setup_database already applied 014 to the shared DB (which the reauth tests
    below use via async_db_session); this only proves 014 re-applies cleanly without
    committing DDL to the shared schema. Raw asyncpg (simple query protocol) — a
    multi-statement .sql file can't go through SQLAlchemy exec_driver_sql.
    """
    conn = await asyncpg.connect(dsn=migrations_raw_dsn)
    try:
        await conn.execute(MIGRATION_014.read_text())
    finally:
        await conn.close()
    yield


def _make_mock_client(new_session: str = "new_session_string_xyz") -> MagicMock:
    """Minimal Telethon-like mock that exposes session.save() → new_session."""
    client = MagicMock(name="MockTelethonClient")
    session = MagicMock()
    session.save = MagicMock(return_value=new_session)
    client.session = session
    return client


# ─── 1. save_state persists original_sender_id ──────────────────────────────


async def test_save_state_persists_original_sender_id(
    async_db_session, test_workspace, test_sender_factory
):
    """CR-05: save_state(..., original_sender_id=X) → row.original_sender_id == X."""
    sender = await test_sender_factory()
    session_id = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79001234567",
        phone_code_hash="hash-reauth-1",
        session_string="dummy-session-string",
        role="sender",
        proxy=None,
        original_sender_id=sender.id,
    )
    row = await load_state(async_db_session, session_id, test_workspace.id)
    assert row is not None
    assert row.original_sender_id == sender.id, (
        "save_state должен сохранять original_sender_id"
    )


async def test_save_state_without_original_sender_id_defaults_to_null(
    async_db_session, test_workspace
):
    """Backward compat: обычный onboarding (без original_sender_id) → колонка NULL."""
    session_id = await save_state(
        db=async_db_session,
        workspace_id=test_workspace.id,
        phone="+79002224466",
        phone_code_hash="hash-normal",
        session_string="dummy",
        role="sender",
    )
    row = await load_state(async_db_session, session_id, test_workspace.id)
    assert row is not None
    assert row.original_sender_id is None


# ─── 2. _load_existing_sender ───────────────────────────────────────────────


async def test_load_existing_sender_returns_sender(
    async_db_session, test_workspace, test_sender_factory
):
    sender = await test_sender_factory()
    found = await _load_existing_sender(
        async_db_session, sender.id, test_workspace.id
    )
    assert found is not None
    assert found.id == sender.id
    assert found.slug == sender.slug


async def test_load_existing_sender_workspace_isolation(
    async_db_session, test_workspace, test_sender_factory
):
    """Запрос _load_existing_sender с чужим workspace_id → None (cross-tenant защита)."""
    sender = await test_sender_factory()
    other_ws = uuid.uuid4()
    found = await _load_existing_sender(async_db_session, sender.id, other_ws)
    assert found is None, (
        "_load_existing_sender НЕ должен возвращать sender'а другого workspace"
    )


async def test_load_existing_sender_missing_returns_none(
    async_db_session, test_workspace
):
    """Запрос на несуществующий sender_id → None."""
    bogus = uuid.uuid4()
    found = await _load_existing_sender(async_db_session, bogus, test_workspace.id)
    assert found is None


# ─── 3. _refresh_sender_session ─────────────────────────────────────────────


async def test_refresh_sender_session_updates_in_place(
    async_db_session, test_workspace, test_sender_factory
):
    """CR-05 главный фикс: UPDATE существующего sender'а, не INSERT нового.

    Проверяем:
    - ID того же sender'а после refresh (UPDATE, не INSERT с новым ID)
    - session_string обновился
    - auth_status вернулся в 'ok'
    - В БД ровно 1 sender (не два)
    """
    sender = await test_sender_factory(auth_status="session_expired")
    original_id = sender.id
    original_session = sender.session_string
    original_slug = sender.slug

    mock_client = _make_mock_client(new_session="new_session_after_reauth")
    session_row = OnboardingSession(
        workspace_id=test_workspace.id,
        phone=sender.phone,
        phone_code_hash="hash-reauth-flow",
        encrypted_session_string="dummy",
        role=sender.role,
        proxy=None,
        status="code_sent",
        original_sender_id=sender.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    async_db_session.add(session_row)
    await async_db_session.commit()

    refreshed = await _refresh_sender_session(
        async_db_session, sender, mock_client, session_row
    )

    # Тот же sender (UPDATE)
    assert refreshed.id == original_id
    assert refreshed.slug == original_slug  # slug сохранён
    # session_string обновлён (encrypted версия new_session_after_reauth ≠ оригиналу)
    assert refreshed.session_string != original_session
    # auth_status вернулся в 'ok'
    assert refreshed.auth_status == "ok"

    # В БД ровно 1 sender с этим slug — CR-05 главное условие.
    count = (
        await async_db_session.execute(
            text(
                "SELECT COUNT(*) FROM senders WHERE slug = :s "
                "AND workspace_id = :w"
            ),
            {"s": original_slug, "w": str(test_workspace.id)},
        )
    ).scalar()
    assert count == 1, (
        f"CR-05 regression: ожидался 1 sender, найдено {count} (INSERT'ило нового вместо UPDATE)"
    )


async def test_double_refresh_does_not_break(
    async_db_session, test_workspace, test_sender_factory
):
    """CR-05 bug repro: два последовательных reauth — не падают с IntegrityError.

    До фикса второй reauth падал на INSERT (slug global unique).
    После фикса — UPDATE того же sender'а, идемпотентно.
    """
    sender = await test_sender_factory(auth_status="session_expired")
    original_id = sender.id
    original_slug = sender.slug

    for i in range(2):
        mock_client = _make_mock_client(new_session=f"session_v{i + 1}")
        row = OnboardingSession(
            workspace_id=test_workspace.id,
            phone=sender.phone,
            phone_code_hash=f"hash-v{i + 1}",
            encrypted_session_string="dummy",
            role=sender.role,
            proxy=None,
            status="code_sent",
            original_sender_id=sender.id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        async_db_session.add(row)
        await async_db_session.commit()

        existing = await _load_existing_sender(
            async_db_session, sender.id, test_workspace.id
        )
        assert existing is not None
        # Не должно поднимать IntegrityError.
        await _refresh_sender_session(async_db_session, existing, mock_client, row)

    # После двух reauth — всё ещё 1 sender.
    count = (
        await async_db_session.execute(
            text("SELECT COUNT(*) FROM senders WHERE id = :sid"),
            {"sid": str(original_id)},
        )
    ).scalar()
    assert count == 1, "Двойной reauth не должен создавать дубликат sender'а"

    # Slug не изменился.
    refreshed = (
        await async_db_session.execute(
            select(Sender).where(Sender.id == original_id)
        )
    ).scalars().first()
    assert refreshed.slug == original_slug


# ─── 4. _finalize_onboarding_or_reauth — branching ──────────────────────────


class _MockCtx:
    """Минимальный stand-in для AuthCtx (нужен только workspace_id)."""

    def __init__(self, wid: uuid.UUID):
        self.workspace_id = wid


async def test_finalize_with_original_sender_id_takes_refresh_branch(
    async_db_session, test_workspace, test_sender_factory
):
    """Если row.original_sender_id NOT NULL → UPDATE существующего, не INSERT нового."""
    sender = await test_sender_factory(auth_status="session_expired")
    original_id = sender.id

    mock_client = _make_mock_client(new_session="finalize_reauth_session")
    row = OnboardingSession(
        workspace_id=test_workspace.id,
        phone=sender.phone,
        phone_code_hash="hash-finalize",
        encrypted_session_string="dummy",
        role=sender.role,
        proxy=None,
        status="code_sent",
        original_sender_id=sender.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    async_db_session.add(row)
    await async_db_session.commit()

    result = await _finalize_onboarding_or_reauth(
        async_db_session, _MockCtx(test_workspace.id), row, mock_client
    )

    # Тот же sender (refresh-ветка).
    assert result.id == original_id
    assert result.auth_status == "ok"


async def test_finalize_with_deleted_sender_falls_back_to_create(
    async_db_session, test_workspace, test_sender_factory
):
    """Edge case: sender удалён пока шёл reauth → original_sender_id больше не
    резолвится → fallback на _create_sender_from_session, не блокирует юзера.

    onboarding_sessions.original_sender_id is a FK with ON DELETE CASCADE
    (migration 014), so a *dangling* FK can never exist in the DB. We reproduce
    the in-flight-delete race instead: seed a real sender, create the reauth
    onboarding row referencing it, commit, then delete the sender. The cascade
    removes the DB row, but the in-memory session_row object _finalize_*
    operates on still carries original_sender_id; the helper's SELECT now
    returns None → fallback create.
    """
    sender = await test_sender_factory(slug="reauth-doomed-sender")
    doomed_sender_id = sender.id

    mock_client = _make_mock_client(new_session="fallback_session")
    # get_me нужен _create_sender_from_session
    mock_client.get_me = AsyncMock(
        return_value=MagicMock(id=999111, first_name="Fallback", last_name=None, username=None, premium=False)
    )

    row = OnboardingSession(
        workspace_id=test_workspace.id,
        phone="+79001110000",
        phone_code_hash="hash-fallback",
        encrypted_session_string="dummy",
        role="sender",
        proxy=None,
        status="code_sent",
        original_sender_id=doomed_sender_id,  # references a real sender (FK ok)
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    async_db_session.add(row)
    await async_db_session.commit()

    # Simulate the sender being deleted mid-reauth. CASCADE also deletes the
    # onboarding row in the DB, but `row` (in memory) still holds the FK.
    async_db_session.expunge(row)
    await async_db_session.execute(
        text("DELETE FROM senders WHERE id = :sid"), {"sid": str(doomed_sender_id)}
    )
    await async_db_session.commit()

    result = await _finalize_onboarding_or_reauth(
        async_db_session, _MockCtx(test_workspace.id), row, mock_client
    )

    # Создан новый sender (fallback), не упало с 404.
    assert result.id != doomed_sender_id
    assert result.workspace_id == test_workspace.id
    assert result.slug == "sender-999111"


async def test_finalize_with_cross_workspace_original_sender_falls_back(
    async_db_session, test_workspace, test_sender_factory
):
    """Безопасность: original_sender_id из другого workspace → fallback на create,
    не возвращает чужого sender'а (workspace-guard в _load_existing_sender).
    """
    foreign_sender = await test_sender_factory()
    foreign_workspace_id = test_workspace.id

    other_workspace_id = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'Foreign-WS')"),
        {"wid": str(other_workspace_id)},
    )
    await async_db_session.commit()

    mock_client = _make_mock_client(new_session="cross_ws_session")
    mock_client.get_me = AsyncMock(
        return_value=MagicMock(id=999222, first_name="Cross", last_name=None, username=None, premium=False)
    )

    row = OnboardingSession(
        workspace_id=other_workspace_id,
        phone="+79001110001",
        phone_code_hash="hash-cross",
        encrypted_session_string="dummy",
        role="sender",
        proxy=None,
        status="code_sent",
        original_sender_id=foreign_sender.id,  # из другого workspace
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    async_db_session.add(row)
    await async_db_session.commit()

    result = await _finalize_onboarding_or_reauth(
        async_db_session, _MockCtx(other_workspace_id), row, mock_client
    )

    # Создан НОВЫЙ sender в other_workspace, foreign_sender остался невредим.
    assert result.id != foreign_sender.id
    assert result.workspace_id == other_workspace_id

    foreign_intact = (
        await async_db_session.execute(
            select(Sender).where(Sender.id == foreign_sender.id)
        )
    ).scalars().first()
    assert foreign_intact is not None
    assert foreign_intact.workspace_id == foreign_workspace_id
