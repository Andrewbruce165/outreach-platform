"""Regression: rotation.py writes workspace_id and filters by it (CR-03, Phase 02.1-01).

After migration 012 the column context_contact_assignments.workspace_id is NOT NULL.
This file pins three invariants:

1. get_or_assign_sender requires workspace_id and persists it on the
   context_contact_assignments row.
2. _pick_best_sender filters by Sender.workspace_id — defence-in-depth against
   AIContext / Sender workspace_id divergence.
3. The same (context, phone) in two different workspaces is allowed and resolves
   to each workspace's own sender (no cross-tenant leak).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models import AIContext, Workspace
from app.services.rotation import _pick_best_sender, get_or_assign_sender

pytestmark = pytest.mark.asyncio


# ─── Local helpers (per parallel-safety rules: no conftest.py edits) ──────────


@pytest_asyncio.fixture
async def workspace_factory(async_db_session):
    counter = {"n": 0}

    async def _make(name: str | None = None) -> Workspace:
        counter["n"] += 1
        ws = Workspace(name=name or f"WS-rot-{counter['n']}")
        async_db_session.add(ws)
        await async_db_session.commit()
        await async_db_session.refresh(ws)
        return ws

    return _make


async def _add_sender(
    db,
    workspace_id,
    ai_context_id,
    slug,
    phone,
):
    row = (
        await db.execute(
            text(
                """
                INSERT INTO senders (
                    workspace_id, ai_context_id, slug, name, phone, session_string,
                    role, auth_status, lifecycle_status,
                    rate_per_min, rate_per_hour, rate_per_day
                )
                VALUES (
                    :wid, :ctx, :slug, :name, :phone, 'encrypted_stub',
                    'sender', 'ok', 'active',
                    4, 20, 150
                )
                RETURNING id
                """
            ),
            {
                "wid": str(workspace_id),
                "ctx": str(ai_context_id),
                "slug": slug,
                "name": slug,
                "phone": phone,
            },
        )
    ).fetchone()
    await db.commit()
    return uuid.UUID(str(row[0]))


async def _add_ai_context(db, workspace_id, name="rot-ctx"):
    ctx = AIContext(
        workspace_id=workspace_id,
        name=name,
        system_prompt="test",
        is_active=True,
    )
    db.add(ctx)
    await db.commit()
    await db.refresh(ctx)
    return ctx


# ─── Tests ────────────────────────────────────────────────────────────────────


async def test_get_or_assign_sender_requires_workspace_id():
    """get_or_assign_sender signature includes workspace_id (CR-03 contract)."""
    import inspect

    sig = inspect.signature(get_or_assign_sender)
    assert "workspace_id" in sig.parameters


async def test_assignment_row_persists_workspace_id(
    async_db_session, workspace_factory
):
    """First-time assignment INSERTs workspace_id into context_contact_assignments."""
    ws = await workspace_factory()
    ai_ctx = await _add_ai_context(async_db_session, ws.id)
    await _add_sender(
        async_db_session, ws.id, ai_ctx.id, "rot-a-1", "+79150000100"
    )

    sender = await get_or_assign_sender(
        db=async_db_session,
        context_id=ai_ctx.id,
        contact_phone="+79991111111",
        workspace_id=ws.id,
    )
    assert sender is not None

    row = (
        await async_db_session.execute(
            text(
                """
                SELECT workspace_id
                FROM context_contact_assignments
                WHERE context_id = :ctx AND contact_phone = :phone
                """
            ),
            {"ctx": str(ai_ctx.id), "phone": "+79991111111"},
        )
    ).fetchone()
    assert row is not None
    assert str(row[0]) == str(ws.id)


async def test_pick_best_sender_filters_by_workspace(
    async_db_session, workspace_factory
):
    """_pick_best_sender returns None when senders linked to context belong to a different workspace."""
    ws_a = await workspace_factory()
    ws_b = await workspace_factory()
    ai_ctx_a = await _add_ai_context(async_db_session, ws_a.id, name="ctx-a")
    await _add_sender(
        async_db_session, ws_a.id, ai_ctx_a.id, "rot-pick-a", "+79150000200"
    )

    # Same context_id should never resolve a sender when scanned in workspace B.
    result = await _pick_best_sender(
        db=async_db_session,
        context_id=ai_ctx_a.id,
        workspace_id=ws_b.id,
    )
    assert result is None


async def test_no_cross_tenant_leak_on_same_context_phone(
    async_db_session, workspace_factory
):
    """Two workspaces using the same (context_id, phone) pair don't collide.

    Each workspace gets its own sender + its own assignment row.
    """
    ws_a = await workspace_factory()
    ws_b = await workspace_factory()
    ctx_a = await _add_ai_context(async_db_session, ws_a.id, name="ctx-a")
    ctx_b = await _add_ai_context(async_db_session, ws_b.id, name="ctx-b")

    sa = await _add_sender(
        async_db_session, ws_a.id, ctx_a.id, "rot-iso-a", "+79150000300"
    )
    sb = await _add_sender(
        async_db_session, ws_b.id, ctx_b.id, "rot-iso-b", "+79150000301"
    )

    res_a = await get_or_assign_sender(
        db=async_db_session,
        context_id=ctx_a.id,
        contact_phone="+79991234567",
        workspace_id=ws_a.id,
    )
    res_b = await get_or_assign_sender(
        db=async_db_session,
        context_id=ctx_b.id,
        contact_phone="+79991234567",
        workspace_id=ws_b.id,
    )
    assert res_a.id == sa
    assert res_b.id == sb

    rows = (
        await async_db_session.execute(
            text(
                """
                SELECT workspace_id, sender_id
                FROM context_contact_assignments
                WHERE contact_phone = :phone
                """
            ),
            {"phone": "+79991234567"},
        )
    ).fetchall()
    # Both assignments persisted, each with its own workspace_id.
    assert len(rows) == 2
    by_ws = {str(r[0]): str(r[1]) for r in rows}
    assert by_ws[str(ws_a.id)] == str(sa)
    assert by_ws[str(ws_b.id)] == str(sb)


async def test_repeat_call_reuses_existing_assignment(
    async_db_session, workspace_factory
):
    """Second call returns the cached sender — no duplicate INSERT, idempotent."""
    ws = await workspace_factory()
    ai_ctx = await _add_ai_context(async_db_session, ws.id)
    sid = await _add_sender(
        async_db_session, ws.id, ai_ctx.id, "rot-reuse", "+79150000400"
    )

    first = await get_or_assign_sender(
        db=async_db_session,
        context_id=ai_ctx.id,
        contact_phone="+79992222222",
        workspace_id=ws.id,
    )
    second = await get_or_assign_sender(
        db=async_db_session,
        context_id=ai_ctx.id,
        contact_phone="+79992222222",
        workspace_id=ws.id,
    )
    assert first.id == sid
    assert second.id == sid

    count = (
        await async_db_session.execute(
            text(
                """
                SELECT COUNT(*) FROM context_contact_assignments
                WHERE context_id = :ctx AND contact_phone = :phone
                """
            ),
            {"ctx": str(ai_ctx.id), "phone": "+79992222222"},
        )
    ).scalar()
    assert count == 1
