"""Phase 4 Plan 04-05 Task 1 — Wave 0 stubs for built-in tools (mark_as_lead,
transfer_to_manager, finish_conversation).

Closes CAMP-11/CAMP-12/CAMP-13/CAMP-16 unit-test surface.
Implemented in Task 2.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_build_builtin_tools_returns_3_tools():
    """build_builtin_tools(campaign) returns list of 3 OpenAI function specs."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_built_in_tool_names_are_mark_lead_transfer_finish():
    """Names: mark_as_lead, transfer_to_manager, finish_conversation (C-04)."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_built_in_description_uses_trigger_hint():
    """If campaign.lead_trigger_hint='X' — tool description contains 'X'."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_built_in_description_fallback_when_hint_null():
    """Pitfall 7: restrictive default description when hint is None."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_mark_as_lead_updates_conversation_status():
    """LLM calls mark_as_lead → UPDATE conversations.status='lead', ai_enabled stays true."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_transfer_to_manager_disables_ai():
    """LLM calls transfer_to_manager → UPDATE status='handoff', ai_enabled=false, paused_at, paused_reason."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_finish_conversation_disables_ai_and_marks_finished():
    """LLM calls finish_conversation → UPDATE status='finished', ai_enabled=false."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_parallel_tool_calls_priority_finish_wins_over_lead():
    """Pitfall 1: LLM returns finish + lead — final_status='finished'."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_parallel_tool_calls_priority_handoff_wins_over_lead():
    """LLM returns handoff + lead — final_status='handoff'."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_q3_text_plus_tool_call_sends_farewell_before_flip():
    """Q3: if LLM returns text_content + finish_conversation — text возвращается перед status flip."""
    pytest.skip("Wave 0 stub — implemented in Task 2")


async def test_builtin_and_custom_tools_merged_into_one_request():
    """CAMP-16: tools=build_builtin_tools(campaign) + build_tools(campaign.tools) — single OpenAI call."""
    pytest.skip("Wave 0 stub — implemented in Task 2")
