"""Phase 4 Plan 04-05 Task 1 — Wave 0 stubs for custom tools переезд
from dropped ai_contexts.webhook_functions → campaigns.tools JSONB.

Closes CAMP-15 (custom tools работают) test surface +
get_context_for_conversation legacy fallback (M3 revision).
Implemented in Task 2.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_custom_tools_source_is_campaigns_tools_not_ai_contexts():
    """CAMP-15: build_tools читает campaign.tools JSONB (NOT ai_contexts.webhook_functions — поле дропнуто)."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_custom_tool_call_invokes_execute_webhook():
    """LLM calls custom function 'save_to_crm' → execute_webhook(tool_call, context) called."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_custom_tools_workspace_isolated_via_campaign():
    """Custom tools кампании workspace A не подмешиваются в LLM-prompt кампании workspace B."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_empty_campaign_tools_still_has_3_builtin():
    """campaign.tools=[] — LLM получает только 3 built-in tools, не падает."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_get_context_for_conversation_resolves_via_campaign():
    """ai_engine.get_context_for_conversation(conv_id) → resolves campaign through
    conversations.campaign_id JOIN, returns dict with tools/hints/webhook_urls."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_legacy_conversation_without_campaign_id_handled():
    """M3 (revision): conversation.campaign_id IS NULL (legacy pre-Phase-4 conversation) →
    get_context_for_conversation корректно резолвит agent через fallback path
    (LEFT JOIN ai_contexts ON conv.ai_context_id, без campaign-level fields).

    Setup: use raw SQL `INSERT INTO conversations (...) VALUES (..., campaign_id=NULL, ai_context_id=<existing>)`
    чтобы имитировать pre-Phase-4 conversation (factory `test_campaign_factory` создаёт
    conversation с campaign_id NOT NULL — здесь нужен legacy-shaped row).

    Assertions:
      - returned context['campaign'] is None
      - context['agent_id'] == existing ai_context_id
      - context['system_prompt'], rules, etc. — заполнены из ai_contexts
      - функция НЕ raises, gracefully вернуть partial context.
    """
    pytest.skip("Wave 0 stub — implemented in Task 2")
