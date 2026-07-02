"""Phase 5 analytics endpoints (ANLX-01..04) — schemas + smoke isolation + 4-endpoint parity.

Covers schema validation (Task 1) and auth/isolation/schema-parity smoke
(Task 2). Correctness with seeded fixtures lives in
``tests/test_phase5_analytics_correctness.py``.
"""

import uuid as _uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.schemas import AnalyticsCards, AnalyticsReplied

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(jwt_factory, sub: str = "analytics-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── Task 1: Pydantic schema validation ───────────────────────────────────────


def test_analytics_cards_valid():
    """Schema accepts a fully-populated AnalyticsCards instance."""
    obj = AnalyticsCards(
        sent=10,
        replied=AnalyticsReplied(conversation_count=3, message_count=5),
        leads=2,
        finishes=1,
    )
    assert obj.sent == 10
    assert obj.replied.conversation_count == 3
    assert obj.replied.message_count == 5
    assert obj.leads == 2
    assert obj.finishes == 1


def test_analytics_cards_missing_required():
    """Missing required field 'replied' raises ValidationError."""
    with pytest.raises(ValidationError):
        AnalyticsCards(sent=10, leads=2, finishes=1)  # missing replied


def test_analytics_replied_zeros():
    """AnalyticsReplied accepts (0, 0) — no inbound messages yet."""
    obj = AnalyticsReplied(conversation_count=0, message_count=0)
    assert obj.conversation_count == 0
    assert obj.message_count == 0


def test_analytics_cards_dump_to_dict():
    """model_dump() shape matches JSON-serialisable nested dict."""
    obj = AnalyticsCards(
        sent=10,
        replied=AnalyticsReplied(conversation_count=3, message_count=5),
        leads=2,
        finishes=1,
    )
    d = obj.model_dump()
    assert d["sent"] == 10
    assert d["leads"] == 2
    assert d["finishes"] == 1
    assert d["replied"]["conversation_count"] == 3
    assert d["replied"]["message_count"] == 5


# ── Task 2: Auth + workspace isolation + schema parity (4 endpoints) ─────────


async def test_workspace_endpoint_401_without_auth(async_client):
    """ANLX-01 auth gate: GET /workspace without credentials → 401."""
    r = await async_client.get("/api/v1/analytics/workspace")
    assert r.status_code == 401


async def test_campaign_endpoint_401_without_auth(async_client):
    r = await async_client.get(f"/api/v1/analytics/campaigns/{_uuid.uuid4()}")
    assert r.status_code == 401


async def test_agent_endpoint_401_without_auth(async_client):
    r = await async_client.get(f"/api/v1/analytics/agents/{_uuid.uuid4()}")
    assert r.status_code == 401


async def test_sender_endpoint_401_without_auth(async_client):
    r = await async_client.get(f"/api/v1/analytics/senders/{_uuid.uuid4()}")
    assert r.status_code == 401


async def test_workspace_endpoint_returns_4_metrics(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """ANLX-01: empty workspace returns all four metric fields with zero counts."""
    await _bind(async_db_session, test_workspace.id, "u-ws-empty")

    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-ws-empty"),
    )
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {
        "sent", "replied", "leads", "finishes",
        "contacts_messaged", "registered_contacts", "llm_spend_usd_cents",
    }
    assert set(data["replied"].keys()) == {"conversation_count", "message_count"}
    assert data["sent"] == 0
    assert data["replied"]["conversation_count"] == 0
    assert data["replied"]["message_count"] == 0
    assert data["leads"] == 0
    assert data["finishes"] == 0
    # Progress fields are campaign-scoped only → 0 for workspace scope.
    assert data["contacts_messaged"] == 0
    assert data["registered_contacts"] == 0
    # Additive LLM spend field (ILX-LLM-SPEND) — 0 with no priced calls.
    assert data["llm_spend_usd_cents"] == 0


async def test_workspace_endpoint_workspace_isolation(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """T-05-02-WS-ISOLATION smoke: workspace A counts exclude workspace B data."""
    from app.models import Sender, Workspace

    # Workspace A: one conversation with 1 outbound message.
    conv_a = await test_conversation_factory(status="active")
    await test_message_factory(
        conv_a["id"], count=1, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await _bind(async_db_session, test_workspace.id, "u-iso-ws")

    # Workspace B: separate workspace with 5 outbound messages.
    other = Workspace(name="OtherWS-analytics")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    other_sender = Sender(
        workspace_id=other.id, slug="other-sender-analytics", name="Other",
        phone="+79002222301", session_string="x", role="sender",
        lifecycle_status="active", auth_status="ok",
    )
    async_db_session.add(other_sender)
    await async_db_session.commit()
    await async_db_session.refresh(other_sender)
    other_conv_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
        VALUES (:cid, :wid, :sid, '+79992223331', 'active')
    """), {"cid": str(other_conv_id), "wid": str(other.id), "sid": str(other_sender.id)})
    await async_db_session.commit()
    for i in range(5):
        await async_db_session.execute(text("""
            INSERT INTO messages
                (workspace_id, conversation_id, direction, message_text, sent_by,
                 telegram_message_id)
            VALUES (:wid, :cid, 'outbound', 'foreign', 'ai', :tmid)
        """), {"wid": str(other.id), "cid": str(other_conv_id), "tmid": 900_000 + i})
    await async_db_session.commit()

    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-iso-ws"),
    )
    assert r.status_code == 200
    # Workspace A user sees only its own one outbound.
    assert r.json()["sent"] == 1


async def test_campaign_endpoint_404_cross_workspace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """ANLX-02: campaign owned by another workspace → 404 (not 403)."""
    from app.models import Workspace

    other = Workspace(name="OtherWS-camp-analytics")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)

    # Foreign campaign — store its id, but don't bind user to other workspace.
    foreign_camp_id = _uuid.uuid4()
    # We need a basic foreign agent+folder for the FK to make sense.
    await async_db_session.execute(text("""
        INSERT INTO ai_contexts (id, workspace_id, name)
        VALUES (:aid, :wid, 'foreign-agent-analytics')
    """), {"aid": str(_uuid.uuid4()), "wid": str(other.id)})
    foreign_agent_row = (await async_db_session.execute(text(
        "SELECT id FROM ai_contexts WHERE workspace_id=:wid LIMIT 1"
    ), {"wid": str(other.id)})).first()
    foreign_folder_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO folders (id, workspace_id, name)
        VALUES (:fid, :wid, 'foreign-folder-analytics')
    """), {"fid": str(foreign_folder_id), "wid": str(other.id)})
    await async_db_session.execute(text("""
        INSERT INTO campaigns (id, workspace_id, agent_id, folder_id, name, status,
                               message_template)
        VALUES (:cid, :wid, :aid, :fid, 'foreign-camp', 'draft', 'hi')
    """), {
        "cid": str(foreign_camp_id), "wid": str(other.id),
        "aid": str(foreign_agent_row.id), "fid": str(foreign_folder_id),
    })
    await async_db_session.commit()

    await _bind(async_db_session, test_workspace.id, "u-ws-camp-404")
    r = await async_client.get(
        f"/api/v1/analytics/campaigns/{foreign_camp_id}",
        headers=_auth_headers(valid_supabase_jwt, "u-ws-camp-404"),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"


async def test_agent_endpoint_404_cross_workspace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """ANLX-04: agent owned by another workspace → 404."""
    from app.models import Workspace

    other = Workspace(name="OtherWS-agent-analytics")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)

    foreign_agent_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO ai_contexts (id, workspace_id, name)
        VALUES (:aid, :wid, 'foreign-agent')
    """), {"aid": str(foreign_agent_id), "wid": str(other.id)})
    await async_db_session.commit()

    await _bind(async_db_session, test_workspace.id, "u-ws-agent-404")
    r = await async_client.get(
        f"/api/v1/analytics/agents/{foreign_agent_id}",
        headers=_auth_headers(valid_supabase_jwt, "u-ws-agent-404"),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "AGENT_NOT_FOUND"


async def test_sender_endpoint_404_cross_workspace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """ANLX-03: sender owned by another workspace → 404."""
    from app.models import Sender, Workspace

    other = Workspace(name="OtherWS-sender-analytics")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)

    foreign_sender = Sender(
        workspace_id=other.id, slug="foreign-sender-analytics", name="Foreign",
        phone="+79008008008", session_string="x", role="sender",
        lifecycle_status="active", auth_status="ok",
    )
    async_db_session.add(foreign_sender)
    await async_db_session.commit()
    await async_db_session.refresh(foreign_sender)

    await _bind(async_db_session, test_workspace.id, "u-ws-sender-404")
    r = await async_client.get(
        f"/api/v1/analytics/senders/{foreign_sender.id}",
        headers=_auth_headers(valid_supabase_jwt, "u-ws-sender-404"),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SENDER_NOT_FOUND"


async def test_all_4_endpoints_same_schema(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_agent_factory, test_sender_factory,
):
    """D-16 schema parity: all four levels return identical AnalyticsCards shape."""
    await _bind(async_db_session, test_workspace.id, "u-schema-parity")
    h = _auth_headers(valid_supabase_jwt, "u-schema-parity")

    camp = await test_campaign_factory()
    agent = await test_agent_factory()
    sender = await test_sender_factory()

    expected_top = {
        "sent", "replied", "leads", "finishes",
        "contacts_messaged", "registered_contacts", "llm_spend_usd_cents",
    }
    expected_replied = {"conversation_count", "message_count"}

    paths = [
        "/api/v1/analytics/workspace",
        f"/api/v1/analytics/campaigns/{camp['id']}",
        f"/api/v1/analytics/agents/{agent.id}",
        f"/api/v1/analytics/senders/{sender.id}",
    ]
    for p in paths:
        r = await async_client.get(p, headers=h)
        assert r.status_code == 200, f"{p} status={r.status_code} body={r.text}"
        data = r.json()
        assert set(data.keys()) == expected_top, f"{p} top-level keys mismatch"
        assert set(data["replied"].keys()) == expected_replied, \
            f"{p} replied keys mismatch"
