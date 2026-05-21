"""Phase 3 — rotation._pick_best_sender adapter (Phase 3 D-04).

After migration 015 dropped senders.ai_context_id, _pick_best_sender selects
from the workspace sender pool (without ai_context_id filter). context_id
parameter remains in signature for backward compat with get_or_assign_sender
which still writes context_contact_assignments per D-05.
"""
import pytest
from uuid import uuid4

pytestmark = pytest.mark.asyncio


async def test_pick_best_sender_workspace_only(
    async_db_session, test_sender_factory, test_workspace
):
    """Phase 3: _pick_best_sender выбирает из workspace pool (не фильтрует по ai_context_id)."""
    from app.services.rotation import _pick_best_sender

    # Create active sender in test_workspace
    s_active = await test_sender_factory(slug="active-1", lifecycle_status="active", auth_status="ok")

    # Dummy context_id (no longer relevant for SQL, but parameter remains)
    dummy_ctx = uuid4()

    winner = await _pick_best_sender(async_db_session, dummy_ctx, test_workspace.id)
    assert winner is not None
    assert winner.id == s_active.id


async def test_pick_best_sender_workspace_isolated(
    async_db_session, test_sender_factory, test_workspace
):
    """Phase 3: senders из другого workspace не выбираются."""
    from app.services.rotation import _pick_best_sender
    from app.models import Workspace, Sender

    # Other workspace + sender there
    other_ws = Workspace(name="Other WS")
    async_db_session.add(other_ws)
    await async_db_session.commit()
    await async_db_session.refresh(other_ws)

    # No sender in test_workspace at all — only in other_ws
    other_sender = Sender(
        workspace_id=other_ws.id,
        slug="other-sender",
        name="Other",
        phone="+79999999999",
        session_string="stub",
        role="sender",
        auth_status="ok",
        lifecycle_status="active",
    )
    async_db_session.add(other_sender)
    await async_db_session.commit()

    dummy_ctx = uuid4()
    # Should return None — no senders in test_workspace
    result = await _pick_best_sender(async_db_session, dummy_ctx, test_workspace.id)
    assert result is None, "should not pick sender from other workspace"
