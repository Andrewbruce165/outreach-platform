"""Phase 4 Plan 04-05 Task 2 — GREEN tests for custom tools переезд
from dropped ai_contexts.webhook_functions → campaigns.tools JSONB.

Closes CAMP-15 (custom tools работают) test surface +
get_context_for_conversation legacy fallback (M3 revision).
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text as _t

from app.services.ai_engine import ai_engine, get_context_for_conversation
from tests.utils.openai_mocks import make_openai_response

pytestmark = pytest.mark.asyncio


# ─── Helpers ───────────────────────────────────────────────────────────────────


async def _insert_conv(
    db, workspace_id, sender_id, campaign_id, phone, ai_context_id=None
):
    conv_id = uuid.uuid4()
    if campaign_id is not None:
        await db.execute(
            _t(
                """
                INSERT INTO conversations
                    (id, workspace_id, sender_id, campaign_id, contact_phone,
                     ai_enabled, status)
                VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
                """
            ),
            {
                "id": str(conv_id),
                "wid": str(workspace_id),
                "sid": str(sender_id),
                "cid": str(campaign_id),
                "phone": phone,
            },
        )
    else:
        # legacy shape — campaign_id NULL, ai_context_id used directly
        await db.execute(
            _t(
                """
                INSERT INTO conversations
                    (id, workspace_id, sender_id, ai_context_id, contact_phone,
                     ai_enabled, status)
                VALUES (:id, :wid, :sid, :aid, :phone, true, 'active')
                """
            ),
            {
                "id": str(conv_id),
                "wid": str(workspace_id),
                "sid": str(sender_id),
                "aid": str(ai_context_id) if ai_context_id else None,
                "phone": phone,
            },
        )
    await db.commit()
    return conv_id


# ─── CAMP-15: custom tools source = campaigns.tools ──────────────────────────


async def test_custom_tools_source_is_campaigns_tools_not_ai_contexts(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """CAMP-15: build_tools читает campaign.tools JSONB (NOT ai_contexts.webhook_functions —
    поле дропнуто в Phase 3 migration 015)."""
    sender = await test_sender_factory()
    custom = {
        "name": "custom_fn",
        "description": "test",
        "parameters": [{"name": "x", "type": "string", "required": True}],
        "webhook_url": "https://example.com/x",
    }
    camp = await test_campaign_factory(tools=[custom])
    conv_id = await _insert_conv(
        async_db_session, test_workspace.id, sender.id, camp["id"], "+79330000001"
    )

    ctx = await get_context_for_conversation(conv_id, async_db_session)
    assert ctx is not None
    assert ctx["campaign"] is not None
    assert ctx["campaign"]["tools"] == [custom]


async def test_custom_tool_call_invokes_execute_webhook(
    async_db_session,
    monkeypatch,
    test_workspace,
    test_sender_factory,
    test_campaign_factory,
):
    """LLM calls custom function 'save_to_crm' → execute_webhook called."""
    sender = await test_sender_factory()
    custom = {
        "name": "save_to_crm",
        "description": "Save to CRM",
        "parameters": [{"name": "volume", "type": "number", "required": False}],
        "webhook_url": "https://example.com/crm",
    }
    camp = await test_campaign_factory(tools=[custom])
    conv_id = await _insert_conv(
        async_db_session, test_workspace.id, sender.id, camp["id"], "+79330000002"
    )

    # Mock OpenAI: first call returns custom tool_call; second call returns text.
    first = make_openai_response(
        text_content=None,
        tool_calls=[
            {"name": "save_to_crm", "arguments": json.dumps({"volume": 5000})}
        ],
        finish_reason="tool_calls",
    )
    second = make_openai_response(text_content="Записано!", tool_calls=None)

    call_count = {"n": 0}

    async def fake_create(*args, **kwargs):
        call_count["n"] += 1
        return first if call_count["n"] == 1 else second

    monkeypatch.setattr(
        "app.services.ai_engine.client.chat.completions.create",
        AsyncMock(side_effect=fake_create),
    )

    execute_called = {"called": False, "func_config": None}

    async def fake_execute(self, *, func_config, func_args, conversation_context):
        execute_called["called"] = True
        execute_called["func_config"] = func_config
        return "ok"

    monkeypatch.setattr(
        "app.services.ai_engine.AIEngine.execute_webhook", fake_execute
    )

    reply = await ai_engine.generate_response(
        session=async_db_session,
        conversation_id=str(conv_id),
        context_id=None,
        contact_name="X",
        new_message="у нас 5 тонн",
        conversation_context={"contact_phone": "+79330000002"},
    )

    assert execute_called["called"] is True
    assert execute_called["func_config"]["name"] == "save_to_crm"
    assert reply == "Записано!"


async def test_custom_tools_workspace_isolated_via_campaign(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """Custom tools кампании A не появляются в context кампании B (через JOIN на campaign_id)."""
    sender = await test_sender_factory()
    tool_a = {
        "name": "tool_a",
        "description": "A",
        "parameters": [],
        "webhook_url": "https://example.com/a",
    }
    tool_b = {
        "name": "tool_b",
        "description": "B",
        "parameters": [],
        "webhook_url": "https://example.com/b",
    }
    camp_a = await test_campaign_factory(name="Camp A", tools=[tool_a])
    camp_b = await test_campaign_factory(name="Camp B", tools=[tool_b])
    conv_a = await _insert_conv(
        async_db_session, test_workspace.id, sender.id, camp_a["id"], "+79330000003"
    )
    conv_b = await _insert_conv(
        async_db_session, test_workspace.id, sender.id, camp_b["id"], "+79330000004"
    )

    ctx_a = await get_context_for_conversation(conv_a, async_db_session)
    ctx_b = await get_context_for_conversation(conv_b, async_db_session)

    names_a = {t["name"] for t in ctx_a["campaign"]["tools"]}
    names_b = {t["name"] for t in ctx_b["campaign"]["tools"]}
    assert names_a == {"tool_a"}
    assert names_b == {"tool_b"}


async def test_empty_campaign_tools_still_has_3_builtin(
    async_db_session,
    monkeypatch,
    test_workspace,
    test_sender_factory,
    test_campaign_factory,
):
    """campaign.tools=[] — LLM получает только 3 built-in tools, не падает."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(tools=[])
    conv_id = await _insert_conv(
        async_db_session, test_workspace.id, sender.id, camp["id"], "+79330000005"
    )

    captured = {}

    async def fake_create(*args, **kwargs):
        captured["tools"] = kwargs.get("tools")
        return make_openai_response(text_content="hi", tool_calls=None)

    monkeypatch.setattr(
        "app.services.ai_engine.client.chat.completions.create",
        AsyncMock(side_effect=fake_create),
    )

    await ai_engine.generate_response(
        session=async_db_session,
        conversation_id=str(conv_id),
        context_id=None,
        contact_name="X",
        new_message="hi",
        conversation_context={"contact_phone": "+79330000005"},
    )

    tools = captured["tools"]
    assert tools is not None
    names = [t["function"]["name"] for t in tools]
    assert len(tools) == 3
    assert set(names) == {"mark_as_lead", "transfer_to_manager", "finish_conversation"}


# ─── M3: get_context_for_conversation legacy fallback ─────────────────────────


async def test_get_context_for_conversation_resolves_via_campaign(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """ai_engine.get_context_for_conversation(conv_id) → resolves campaign through
    conversations.campaign_id JOIN, returns dict with tools/hints/webhook_urls."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(
        lead_trigger_hint="когда говорят покупаю",
        handoff_trigger_hint="когда просят человека",
        finish_trigger_hint="когда говорят до свидания",
        lead_webhook_url="https://example.com/l",
        handoff_webhook_url="https://example.com/h",
        finish_webhook_url="https://example.com/f",
        tools=[
            {
                "name": "x",
                "description": "x",
                "parameters": [],
                "webhook_url": "https://example.com/y",
            }
        ],
    )
    conv_id = await _insert_conv(
        async_db_session, test_workspace.id, sender.id, camp["id"], "+79330000006"
    )

    ctx = await get_context_for_conversation(conv_id, async_db_session)
    assert ctx is not None
    assert ctx["campaign"] is not None
    assert ctx["campaign"]["lead_trigger_hint"] == "когда говорят покупаю"
    assert ctx["campaign"]["handoff_trigger_hint"] == "когда просят человека"
    assert ctx["campaign"]["finish_trigger_hint"] == "когда говорят до свидания"
    assert ctx["campaign"]["lead_webhook_url"] == "https://example.com/l"
    assert ctx["campaign"]["handoff_webhook_url"] == "https://example.com/h"
    assert ctx["campaign"]["finish_webhook_url"] == "https://example.com/f"
    assert len(ctx["campaign"]["tools"]) == 1


async def test_legacy_conversation_without_campaign_id_handled(
    async_db_session, test_workspace, test_sender_factory, test_agent_factory
):
    """M3 (revision): conversation.campaign_id IS NULL (legacy pre-Phase-4 conversation) →
    get_context_for_conversation корректно резолвит agent через fallback path
    (LEFT JOIN ai_contexts ON conv.ai_context_id, без campaign-level fields).

    Setup: raw SQL INSERT with campaign_id NULL + ai_context_id = existing agent.
    Assertions:
      - returned context['campaign'] is None
      - context['agent_id'] == existing ai_context_id
      - context['system_prompt'], rules — заполнены из ai_contexts
      - функция НЕ raises, gracefully возвращает partial context.
    """
    sender = await test_sender_factory()
    agent = await test_agent_factory(
        system_prompt="Legacy prompt", rules="Legacy rules"
    )
    conv_id = await _insert_conv(
        async_db_session,
        test_workspace.id,
        sender.id,
        campaign_id=None,
        phone="+79330000007",
        ai_context_id=agent.id,
    )

    ctx = await get_context_for_conversation(conv_id, async_db_session)

    assert ctx is not None
    assert ctx["campaign"] is None
    assert ctx["agent_id"] == agent.id
    assert ctx["system_prompt"] == "Legacy prompt"
    assert ctx["rules"] == "Legacy rules"
