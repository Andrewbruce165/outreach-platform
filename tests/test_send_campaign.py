"""Wave 0 stubs — Plan 04-04 Task 1 (send.py campaign_id-based rewrite).

Covers D-16 (POST /api/v1/send body has campaign_id, NOT ai_context_id).
Real test bodies в Task 5 (после send.py rewrite).
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_send_endpoint_accepts_campaign_id_not_ai_context_id():
    """D-16: POST /api/v1/send body has campaign_id, NOT ai_context_id."""
    pytest.skip("Wave 0 stub — Task 5 implements")


async def test_send_resolves_agent_from_campaign():
    """Agent = SELECT agent_id FROM campaigns WHERE id=:cid."""
    pytest.skip("Wave 0 stub — Task 5 implements")


async def test_send_with_other_workspace_campaign_404():
    """Workspace isolation: campaign из чужого workspace → 404."""
    pytest.skip("Wave 0 stub — Task 5 implements")


async def test_send_via_workspace_api_key():
    """n8n push через X-Workspace-Key — тот же endpoint."""
    pytest.skip("Wave 0 stub — Task 5 implements")


async def test_send_renders_template_when_text_not_provided():
    """Если в body нет text — render_template(campaign.message_template, contact)."""
    pytest.skip("Wave 0 stub — Task 5 implements")
