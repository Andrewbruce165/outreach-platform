"""Pydantic schema tests for Campaign v2 widening (UI-CAMPB-01, schema layer).

Pure schema validation — no DB hit. Covers CampaignCreate/Update v2 fields
+ ToolSpec relaxation (Pitfall 6 — per-tool webhook_url is now Optional).
"""

import uuid

import pytest
from pydantic import ValidationError

from app.schemas import (
    CampaignCreate,
    CampaignUpdate,
    ToolParamSpec,
    ToolSpec,
)


def test_campaign_create_with_v2_columns():
    c = CampaignCreate(
        name="V2 Campaign",
        agent_id=uuid.uuid4(),
        folder_id=uuid.uuid4(),
        message_template="Hello {{name}}",
        audience_hints="Bay-Area SDRs",
        primary_goal="book_meeting",
        success_criteria="They confirm a calendar slot",
        webhook_url="https://hooks.zapier.test/123",
    )
    assert c.primary_goal == "book_meeting"
    assert c.audience_hints == "Bay-Area SDRs"
    assert str(c.webhook_url).startswith("https://hooks.zapier.test")


def test_campaign_create_rejects_invalid_primary_goal():
    with pytest.raises(ValidationError):
        CampaignCreate(
            name="X",
            agent_id=uuid.uuid4(),
            folder_id=uuid.uuid4(),
            message_template="hi",
            primary_goal="ascend_to_godhood",
        )


def test_campaign_create_accepts_all_four_goals():
    """All 4 enum values must validate."""
    for goal in ("book_meeting", "qualify", "click", "engage"):
        c = CampaignCreate(
            name=f"goal-{goal}",
            agent_id=uuid.uuid4(),
            folder_id=uuid.uuid4(),
            message_template="hi",
            primary_goal=goal,
        )
        assert c.primary_goal == goal


def test_campaign_update_with_v2_columns():
    """CampaignUpdate accepts v2 fields without other fields (Partial PATCH)."""
    u = CampaignUpdate(audience_hints="Eu fintech", primary_goal="qualify")
    assert u.audience_hints == "Eu fintech"
    assert u.primary_goal == "qualify"
    # Other fields default None — back-compat.
    assert u.name is None
    assert u.webhook_url is None


def test_tool_spec_without_webhook_url_validates():
    """05.1 shape — UI-SPEC §10: tool no longer requires per-tool webhook_url."""
    t = ToolSpec(
        id="t_1",
        name="book_demo",
        description="Books a calendar slot for the lead",
        parameters=[
            ToolParamSpec(
                name="slot_iso",
                type="string",
                description="ISO datetime",
                required=True,
            ),
        ],
    )
    assert t.webhook_url is None
    assert t.id == "t_1"
    assert t.name == "book_demo"


def test_tool_spec_with_webhook_url_still_validates_phase4_backcompat():
    """Pitfall 6: per-tool webhook_url stays Optional, NOT removed.

    Phase 4 tests (test_custom_tools_wiring.py) pass tool dicts with
    webhook_url + webhook_method — must remain accepted.
    """
    t = ToolSpec(
        name="legacy_tool",
        description="Phase 4 shape",
        parameters=[],
        webhook_url="https://hook.test",
        webhook_method="POST",
    )
    assert t.webhook_url is not None
    assert str(t.webhook_url).startswith("https://hook.test")
    assert t.webhook_method == "POST"


def test_campaign_create_legacy_tool_shape_still_works():
    """Phase 4 CampaignCreate(tools=[{...webhook_url...}]) payload still validates."""
    c = CampaignCreate(
        name="legacy-tools",
        agent_id=uuid.uuid4(),
        folder_id=uuid.uuid4(),
        message_template="hi",
        tools=[
            {
                "name": "save_to_crm",
                "description": "Save to CRM",
                "parameters": [],
                "webhook_url": "https://hook.test",
                "webhook_method": "POST",
            }
        ],
    )
    assert len(c.tools) == 1
    assert c.tools[0].name == "save_to_crm"
    assert c.tools[0].webhook_url is not None
