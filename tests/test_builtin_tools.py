"""Phase 4 Plan 04-05 Task 2 — GREEN tests for built-in tools (mark_as_lead,
transfer_to_manager, finish_conversation).

Closes CAMP-11/CAMP-12/CAMP-13/CAMP-16 unit-test surface.
"""

import json
import uuid

import pytest
from sqlalchemy import text as _t

from app.services.ai_engine import (
    BUILT_IN_TOOL_NAMES,
    _BUILTIN_PRIORITY,
    _handle_builtin_signal,
    ai_engine,
    build_builtin_tools,
    get_context_for_conversation,
)
from tests.utils.openai_mocks import make_openai_response, patched_openai_client

pytestmark = pytest.mark.asyncio


# ─── build_builtin_tools shape ─────────────────────────────────────────────────


async def test_build_builtin_tools_returns_3_tools():
    """build_builtin_tools(campaign) returns list of 3 OpenAI function specs."""
    tools = build_builtin_tools({})
    assert isinstance(tools, list)
    assert len(tools) == 3
    for t in tools:
        assert t["type"] == "function"
        assert "name" in t["function"]
        assert "description" in t["function"]
        assert t["function"]["parameters"]["type"] == "object"
        assert "reason" in t["function"]["parameters"]["properties"]
        assert t["function"]["parameters"]["required"] == ["reason"]


async def test_built_in_tool_names_are_mark_lead_transfer_finish():
    """Names: mark_as_lead, transfer_to_manager, finish_conversation (C-04)."""
    tools = build_builtin_tools({})
    names = {t["function"]["name"] for t in tools}
    assert names == BUILT_IN_TOOL_NAMES
    assert names == {"mark_as_lead", "transfer_to_manager", "finish_conversation"}


async def test_built_in_description_uses_trigger_hint():
    """If campaign.lead_trigger_hint='X' — tool description contains 'X'."""
    hint = "Когда клиент чётко сказал что готов купить тонну гречки"
    tools = build_builtin_tools({"lead_trigger_hint": hint})
    lead_tool = next(t for t in tools if t["function"]["name"] == "mark_as_lead")
    assert hint in lead_tool["function"]["description"]


async def test_built_in_description_fallback_when_hint_null():
    """Pitfall 7: restrictive default description when hint is None."""
    tools = build_builtin_tools({"lead_trigger_hint": None})
    lead = next(t for t in tools if t["function"]["name"] == "mark_as_lead")
    # Default has restrictive language ("ONLY", "Do not mark") — verify presence.
    assert "ONLY" in lead["function"]["description"]
    assert "Do not mark" in lead["function"]["description"]


# ─── _handle_builtin_signal status updates ────────────────────────────────────


async def test_mark_as_lead_updates_conversation_status(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """LLM calls mark_as_lead → UPDATE conversations.status='lead', ai_enabled stays true."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79110000001",
        },
    )
    await async_db_session.commit()

    campaign_dict = {
        "id": camp["id"],
        "name": camp["name"],
        "workspace_id": camp["workspace_id"],
        "lead_webhook_url": None,
    }
    status = await _handle_builtin_signal(
        db=async_db_session,
        conversation_id=conv_id,
        campaign=campaign_dict,
        contact={"phone": "+79110000001", "full_name": "Test"},
        signal_name="mark_as_lead",
        reason="Клиент сказал что готов купить",
    )
    assert status == "lead"

    row = (
        await async_db_session.execute(
            _t("SELECT status, ai_enabled FROM conversations WHERE id = :id"),
            {"id": str(conv_id)},
        )
    ).first()
    assert row.status == "lead"
    assert row.ai_enabled is True  # lead не закрывает диалог


async def test_transfer_to_manager_disables_ai(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """LLM calls transfer_to_manager → UPDATE status='handoff', ai_enabled=false, paused_at, paused_reason."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79110000002",
        },
    )
    await async_db_session.commit()

    status = await _handle_builtin_signal(
        db=async_db_session,
        conversation_id=conv_id,
        campaign={
            "id": camp["id"],
            "name": camp["name"],
            "workspace_id": camp["workspace_id"],
            "handoff_webhook_url": None,
        },
        contact={"phone": "+79110000002"},
        signal_name="transfer_to_manager",
        reason="Клиент попросил человека",
    )
    assert status == "handoff"

    row = (
        await async_db_session.execute(
            _t(
                "SELECT status, ai_enabled, paused_at, paused_reason "
                "FROM conversations WHERE id = :id"
            ),
            {"id": str(conv_id)},
        )
    ).first()
    assert row.status == "handoff"
    assert row.ai_enabled is False
    assert row.paused_at is not None
    assert row.paused_reason == "Клиент попросил человека"


async def test_finish_conversation_disables_ai_and_marks_finished(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """LLM calls finish_conversation → UPDATE status='finished', ai_enabled=false."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79110000003",
        },
    )
    await async_db_session.commit()

    status = await _handle_builtin_signal(
        db=async_db_session,
        conversation_id=conv_id,
        campaign={
            "id": camp["id"],
            "name": camp["name"],
            "workspace_id": camp["workspace_id"],
            "finish_webhook_url": None,
        },
        contact={"phone": "+79110000003"},
        signal_name="finish_conversation",
        reason="Контакт сказал спасибо и до свидания",
    )
    assert status == "finished"

    row = (
        await async_db_session.execute(
            _t("SELECT status, ai_enabled FROM conversations WHERE id = :id"),
            {"id": str(conv_id)},
        )
    ).first()
    assert row.status == "finished"
    assert row.ai_enabled is False


# ─── Pitfall 1: parallel tool calls priority ──────────────────────────────────


async def test_parallel_tool_calls_priority_finish_wins_over_lead(
    async_db_session, monkeypatch, test_workspace, test_sender_factory, test_campaign_factory
):
    """Pitfall 1: LLM returns finish + lead — final_status='finished'."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79110000004",
        },
    )
    await async_db_session.commit()

    response = make_openai_response(
        text_content="Спасибо за обращение!",
        tool_calls=[
            {"name": "mark_as_lead", "arguments": json.dumps({"reason": "interest"})},
            {
                "name": "finish_conversation",
                "arguments": json.dumps({"reason": "said goodbye"}),
            },
        ],
        finish_reason="tool_calls",
    )
    patched_openai_client(monkeypatch, response)

    reply = await ai_engine.generate_response(
        session=async_db_session,
        conversation_id=str(conv_id),
        context_id=None,
        contact_name="Test Contact",
        new_message="Спасибо большое, до свидания!",
        conversation_context={"contact_phone": "+79110000004"},
    )

    # Q3 farewell — text_content returned even with finish signal.
    assert reply == "Спасибо за обращение!"

    # finish wins → final state 'finished' + ai_enabled false.
    row = (
        await async_db_session.execute(
            _t("SELECT status, ai_enabled FROM conversations WHERE id = :id"),
            {"id": str(conv_id)},
        )
    ).first()
    assert row.status == "finished"
    assert row.ai_enabled is False


async def test_parallel_tool_calls_priority_handoff_wins_over_lead(
    async_db_session, monkeypatch, test_workspace, test_sender_factory, test_campaign_factory
):
    """LLM returns handoff + lead — final_status='handoff'."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79110000005",
        },
    )
    await async_db_session.commit()

    response = make_openai_response(
        text_content=None,
        tool_calls=[
            {"name": "mark_as_lead", "arguments": json.dumps({"reason": "interest"})},
            {
                "name": "transfer_to_manager",
                "arguments": json.dumps({"reason": "complex pricing"}),
            },
        ],
        finish_reason="tool_calls",
    )
    patched_openai_client(monkeypatch, response)

    await ai_engine.generate_response(
        session=async_db_session,
        conversation_id=str(conv_id),
        context_id=None,
        contact_name="Test",
        new_message="Хочу скидку и обсудить с менеджером",
        conversation_context={"contact_phone": "+79110000005"},
    )

    row = (
        await async_db_session.execute(
            _t("SELECT status, ai_enabled FROM conversations WHERE id = :id"),
            {"id": str(conv_id)},
        )
    ).first()
    assert row.status == "handoff"
    assert row.ai_enabled is False


# ─── Q3: farewell text + tool_call ────────────────────────────────────────────


async def test_q3_text_plus_tool_call_sends_farewell_before_flip(
    async_db_session, monkeypatch, test_workspace, test_sender_factory, test_campaign_factory
):
    """Q3: if LLM returns text_content + finish_conversation — text возвращается
    перед status flip (т.е. возвращается строка, которую listener отправит контакту)."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79110000006",
        },
    )
    await async_db_session.commit()

    farewell = "Спасибо за обращение! Хорошего дня!"
    response = make_openai_response(
        text_content=farewell,
        tool_calls=[
            {
                "name": "finish_conversation",
                "arguments": json.dumps({"reason": "contact said goodbye"}),
            }
        ],
        finish_reason="tool_calls",
    )
    patched_openai_client(monkeypatch, response)

    reply = await ai_engine.generate_response(
        session=async_db_session,
        conversation_id=str(conv_id),
        context_id=None,
        contact_name="Test",
        new_message="Спасибо, до свидания",
        conversation_context={"contact_phone": "+79110000006"},
    )

    # Farewell returned — listener will deliver it before AI closes.
    assert reply == farewell

    # Status flipped to finished.
    row = (
        await async_db_session.execute(
            _t("SELECT status, ai_enabled FROM conversations WHERE id = :id"),
            {"id": str(conv_id)},
        )
    ).first()
    assert row.status == "finished"
    assert row.ai_enabled is False


# ─── CAMP-16: built-in + custom merged ────────────────────────────────────────


async def test_builtin_and_custom_tools_merged_into_one_request(
    async_db_session, monkeypatch, test_workspace, test_sender_factory, test_campaign_factory
):
    """CAMP-16: tools=build_builtin_tools(campaign) + build_tools(campaign.tools) — single OpenAI call."""
    sender = await test_sender_factory()
    custom_tool = {
        "name": "save_to_crm",
        "description": "Save lead info",
        "parameters": [{"name": "name", "type": "string", "required": True}],
        "webhook_url": "https://example.com/crm",
    }
    camp = await test_campaign_factory(tools=[custom_tool])
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79110000007",
        },
    )
    await async_db_session.commit()

    # Intercept OpenAI call to capture tools argument.
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["tools"] = kwargs.get("tools")
        return make_openai_response(text_content="hi", tool_calls=None)

    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.services.ai_engine.client.chat.completions.create",
        AsyncMock(side_effect=fake_create),
    )

    await ai_engine.generate_response(
        session=async_db_session,
        conversation_id=str(conv_id),
        context_id=None,
        contact_name="Test",
        new_message="hi",
        conversation_context={"contact_phone": "+79110000007"},
    )

    tools = captured["tools"]
    assert tools is not None
    names = [t["function"]["name"] for t in tools]
    # 3 built-in + 1 custom = 4 in total
    assert len(tools) == 4
    assert "mark_as_lead" in names
    assert "transfer_to_manager" in names
    assert "finish_conversation" in names
    assert "save_to_crm" in names
