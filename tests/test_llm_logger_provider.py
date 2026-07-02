"""Wave-0 RED scaffold — llm_logger records provider + key_source (LLMP-07).

Targets the extended `log_llm_call(..., provider=, key_source=)` signature (NOT yet built —
current signature has neither param). Deferred imports keep --collect-only clean. This is
RED now: calling log_llm_call with provider=/key_source= raises TypeError (unexpected kwarg)
until plan 18-04 threads the two columns through. Once landed, it writes an llm_calls row
with provider/key_source populated.

D-07: on every logged call, llm_logger persists the actual provider ('openai'|'anthropic')
and the key_source ('platform'|'byok'|'fallback') for analytics + future cost-billing.
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_log_records_provider_and_key_source(
    async_db_session, test_workspace, test_conversation_factory,
):
    """log_llm_call(..., provider='anthropic', key_source='byok') writes an llm_calls row
    with provider=='anthropic' and key_source=='byok'."""
    from app.services.llm_logger import log_llm_call

    conv = await test_conversation_factory(workspace_id=test_workspace.id)
    conv_id = conv["id"]

    # RED: provider/key_source are net-new kwargs (18-04). This raises TypeError today.
    await log_llm_call(
        workspace_id=test_workspace.id,
        conversation_id=conv_id,
        model="claude-sonnet-4-5",
        prompt={"messages": [{"role": "user", "content": "hi"}]},
        response=None,
        latency_ms=42,
        provider="anthropic",
        key_source="byok",
    )

    row = (await async_db_session.execute(text("""
        SELECT provider, key_source
        FROM llm_calls
        WHERE conversation_id = :cid
        ORDER BY created_at DESC
        LIMIT 1
    """), {"cid": str(conv_id)})).first()

    assert row is not None, "log_llm_call did not persist an llm_calls row"
    assert row.provider == "anthropic"
    assert row.key_source == "byok"
