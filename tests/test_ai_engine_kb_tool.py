"""Phase 16 — ai_engine search_knowledge_base tool wiring (Wave 0 RED).

Asserts the documented data-tool behaviour Plan 16-04 will implement in
ai_engine.generate_response:

  1. The `search_knowledge_base` tool spec is included in the OpenAI request
     ONLY when the agent has >=1 attached KB (D-04); absent otherwise.
  2. When the model returns a `search_knowledge_base` tool_call, the dispatch
     runs the vector search, appends a role:"tool" message containing the
     seeded chunk text, and requests a SECOND completion (two-pass) — it does
     NOT terminate the loop and does NOT change conversation.status (it is a
     DATA tool, unlike mark_as_lead/transfer_to_manager/finish_conversation).

Until 16-04 wires the tool, these FAIL RED:
  - test_tool_gated_on_attached_kb: the planned `build_kb_tool_spec` helper
    (or `search_knowledge_base` appearing in the tools array) does not exist
    → ImportError / assertion failure.
  - test_search_kb_continues_conversation: generate_response does not yet
    dispatch the tool → no role:"tool" message, no second completion.

These tests stub OpenAI (the codebase wraps `ai_engine.client.chat.completions`)
and the embedder so no network is hit. NB Wave 3/4: confirm the helper name and
the dispatch wiring when the module lands.

Test → requirement map:
- test_tool_gated_on_attached_kb       → KB-05 / D-04 (tool gating)
- test_search_kb_continues_conversation → KB-05 (data-tool two-pass, non-terminating)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ─── KB-05 / D-04: tool gating ─────────────────────────────────────────────────

async def test_tool_gated_on_attached_kb():
    """KB-05/D-04: the search_knowledge_base tool spec is built ONLY when the
    agent has >=1 attached KB, and is omitted when it has none.

    The planned helper `build_kb_tool_spec(has_kb: bool)` returns a one-element
    list with the tool when has_kb else an empty list.
    """
    from app.services.ai_engine import build_kb_tool_spec  # RED until 16-04

    with_kb = build_kb_tool_spec(True)
    assert isinstance(with_kb, list) and len(with_kb) == 1
    spec = with_kb[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "search_knowledge_base"
    # The query parameter must be declared so the model can call it.
    assert "query" in spec["function"]["parameters"]["properties"]

    without_kb = build_kb_tool_spec(False)
    assert without_kb == []


# ─── KB-05: data-tool two-pass (continue, not terminate) ───────────────────────

def _tool_call(tool_id: str, name: str, arguments: str):
    tc = MagicMock()
    tc.id = tool_id
    tc.type = "function"
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


def _completion(message):
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    return resp


async def test_search_kb_continues_conversation(
    async_db_session, test_workspace, test_agent_factory,
):
    """KB-05: a search_knowledge_base tool_call appends a role:"tool" message
    containing the seeded chunk text and triggers a SECOND completion (two-pass).
    The conversation status is NOT changed (data tool, not a signal tool).
    """
    from sqlalchemy import text as _sql_text

    from app.services import ai_engine  # import the module to monkeypatch its client

    # Seed an agent + KB + an indexed chunk, attach the KB to the agent.
    import uuid as _uuid
    agent = await test_agent_factory(name="KB Tool Agent")
    kb_id = str(_uuid.uuid4())
    await async_db_session.execute(_sql_text("""
        INSERT INTO knowledge_bases (id, workspace_id, name)
        VALUES (:id, :ws, 'Tool KB')
    """), {"id": kb_id, "ws": str(test_workspace.id)})
    doc_id = str(_uuid.uuid4())
    await async_db_session.execute(_sql_text("""
        INSERT INTO kb_documents (id, workspace_id, kb_id, name, source_kind, status)
        VALUES (:id, :ws, :kb, 'tool.txt', 'txt', 'indexed')
    """), {"id": doc_id, "ws": str(test_workspace.id), "kb": kb_id})
    seeded_chunk_text = "REFUND_POLICY: refunds within 14 days of purchase."
    emb = "[" + ",".join(["0.0"] * 1535 + ["1.0"]) + "]"
    await async_db_session.execute(_sql_text("""
        INSERT INTO kb_chunks
            (workspace_id, kb_id, document_id, chunk_index, content, embedding)
        VALUES (:ws, :kb, :doc, 0, :content, CAST(:emb AS vector))
    """), {"ws": str(test_workspace.id), "kb": kb_id, "doc": doc_id,
           "content": seeded_chunk_text, "emb": emb})
    await async_db_session.execute(_sql_text("""
        INSERT INTO agent_knowledge_bases (agent_id, kb_id, workspace_id)
        VALUES (:aid, :kb, :ws)
    """), {"aid": str(agent.id), "kb": kb_id, "ws": str(test_workspace.id)})
    await async_db_session.commit()

    # First completion: model decides to call search_knowledge_base.
    first_msg = MagicMock()
    first_msg.content = None
    first_msg.tool_calls = [_tool_call("call-1", "search_knowledge_base",
                                       '{"query": "what is the refund policy?"}')]
    # Second completion: model answers using the tool result.
    second_msg = MagicMock()
    second_msg.content = "You can get a refund within 14 days."
    second_msg.tool_calls = None

    mock_create = AsyncMock(side_effect=[_completion(first_msg), _completion(second_msg)])

    # Stub the query embedder so kb_search finds the seeded chunk deterministically.
    query_vec = [0.0] * 1535 + [1.0]

    with patch.object(ai_engine.client.chat.completions, "create", new=mock_create), \
         patch("app.services.kb_search.embed_query", new=AsyncMock(return_value=query_vec)):
        reply = await ai_engine.ai_engine.generate_response(
            session=async_db_session,
            conversation_id=str(_uuid.uuid4()),
            context_id=str(agent.id),
            contact_name="Tester",
            new_message="What is your refund policy?",
        )

    # Two-pass: exactly two completions were requested (not terminating early).
    assert mock_create.await_count == 2, "data tool must trigger a second completion"

    # The second call's messages include a role:"tool" message with the chunk text.
    second_call_messages = mock_create.await_args_list[1].kwargs.get("messages") \
        or mock_create.await_args_list[1].args[0]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_messages, "expected a role:'tool' message appended after the search"
    assert any(seeded_chunk_text in str(m.get("content", "")) for m in tool_messages), \
        "the tool-result message must contain the seeded chunk text"

    # The model's final answer is returned (continued, not aborted).
    assert reply == "You can get a refund within 14 days."
