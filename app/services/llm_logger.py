"""LLM call audit logger — Phase 5 ANLX-05.

Wraps openai client.chat.completions.create result into an llm_calls INSERT.
Per D-09..D-12: only listener-driven generate_response logged; warmup-LLM
calls NOT logged. Critical contract: THIS FUNCTION MUST NEVER RAISE — failure
to log MUST NOT bubble up to ai_engine.generate_response caller (Pitfall 5 /
T-05-03-LOG-FAIL-DOS).

Security guard (T-05-03-PROMPT-LEAK): sensitive prompt content NEVER reaches
application logs. logger.warning calls take only conversation_id + exception
text — never the prompt dict itself. The full prompt JSONB lives ONLY in the
llm_calls.prompt column.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import AsyncSessionLocal
from app.models import LLMCall

logger = logging.getLogger(__name__)


async def log_llm_call(
    *,
    workspace_id: Optional[UUID | str],
    conversation_id: UUID | str,
    model: str,
    prompt: dict,
    response: Any,
    latency_ms: int,
    error: Optional[str] = None,
    provider: Optional[str] = None,
    key_source: Optional[str] = None,
) -> None:
    """Insert one llm_calls row. NEVER raises.

    Args:
        workspace_id: If None, resolved from conversations.workspace_id.
        conversation_id: FK NOT NULL — required.
        model: e.g. "gpt-5-mini-2025-08-07".
        prompt: Full request_params dict (messages + tools + temperature + model).
        response: OpenAI ChatCompletion object, a normalized LLMResult, OR None on error.
        latency_ms: Total round-trip time of the LLM call.
        error: Exception text if the LLM call failed (truncated to 500 chars by caller).
        provider: 'openai' | 'anthropic' — the provider that served the call (D-07).
        key_source: 'platform' | 'byok' | 'fallback' — which key was used (D-07).

    Returns:
        None. All exceptions are swallowed and warning-logged.
    """
    try:
        async with AsyncSessionLocal() as session:
            # 1. Resolve denormalised cols + workspace_id from conversations
            ws_id = workspace_id
            campaign_id = None
            agent_id = None
            sender_id = None

            row = (await session.execute(text("""
                SELECT workspace_id, campaign_id, ai_context_id, sender_id
                FROM conversations
                WHERE id = :cid
            """), {"cid": str(conversation_id)})).first()

            if row is not None:
                if ws_id is None:
                    ws_id = row.workspace_id
                campaign_id = row.campaign_id
                agent_id = row.ai_context_id
                sender_id = row.sender_id

            if ws_id is None:
                # Conversation deleted before log fired (Pitfall 5) — skip silently.
                # T-05-03-PROMPT-LEAK: do NOT include prompt in log.
                logger.warning(
                    "llm_calls: workspace_id unresolved for conv=%s — skipping",
                    conversation_id,
                )
                return

            # 2. Extract response fields (defensive — response может быть None на error)
            response_text: Optional[str] = None
            tool_calls_json: Optional[list[dict]] = None
            prompt_tokens: Optional[int] = None
            completion_tokens: Optional[int] = None
            total_tokens: Optional[int] = None

            if response is not None:
                try:
                    if _is_llm_result(response):
                        # Normalized LLMResult (provider adapter, Phase 18).
                        response_text = getattr(response, "text", None)
                        tcs = getattr(response, "tool_calls", None) or []
                        if tcs:
                            tool_calls_json = [
                                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                for tc in tcs
                            ]
                        usage = getattr(response, "usage", None) or {}
                        prompt_tokens = usage.get("prompt_tokens")
                        completion_tokens = usage.get("completion_tokens")
                        total_tokens = usage.get("total_tokens")
                    else:
                        # Legacy OpenAI ChatCompletion object graph.
                        msg = response.choices[0].message
                        response_text = getattr(msg, "content", None)
                        tcs = getattr(msg, "tool_calls", None)
                        if tcs:
                            tool_calls_json = []
                            for tc in tcs:
                                tool_calls_json.append({
                                    "id": getattr(tc, "id", None),
                                    "name": getattr(getattr(tc, "function", None), "name", None),
                                    "arguments": getattr(
                                        getattr(tc, "function", None), "arguments", None
                                    ),
                                })
                        usage = getattr(response, "usage", None)
                        if usage is not None:
                            prompt_tokens = getattr(usage, "prompt_tokens", None)
                            completion_tokens = getattr(usage, "completion_tokens", None)
                            total_tokens = getattr(usage, "total_tokens", None)
                except (AttributeError, IndexError, TypeError) as e:
                    # T-05-03-PROMPT-LEAK: do NOT log prompt or response content.
                    logger.warning(
                        "llm_calls: response extraction failed for conv=%s: %s",
                        conversation_id, e,
                    )

            # 3. INSERT one row via ORM — SQLAlchemy сам биндит JSONB-колонки
            # из dict, без raw `:param::jsonb` (asyncpg dialect ломается на
            # named bind перед `::` cast'ом). Сериализация через JSON
            # round-trip для UUID/datetime в prompt — JSONB ожидает простые типы.
            session.add(LLMCall(
                workspace_id=ws_id,
                conversation_id=conversation_id,
                campaign_id=campaign_id,
                agent_id=agent_id,
                sender_id=sender_id,
                model=model,
                prompt=_to_jsonb(prompt),
                response_text=response_text,
                tool_calls=_to_jsonb(tool_calls_json) if tool_calls_json else None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                error=error,
                provider=provider,
                key_source=key_source,
            ))
            await session.commit()

    except SQLAlchemyError as e:
        # T-05-03-PROMPT-LEAK: log only conversation_id + exception text.
        logger.warning(
            "llm_calls INSERT failed for conv=%s: %s",
            conversation_id, e,
        )
    except Exception as e:
        # Catch-all — log_llm_call MUST NEVER raise (Pitfall 5 / T-05-03-LOG-FAIL-DOS).
        logger.warning(
            "llm_calls log unexpected error for conv=%s: %s",
            conversation_id, e,
        )


def _is_llm_result(obj: Any) -> bool:
    """Duck-type a normalized LLMResult (Phase 18 provider adapter).

    An LLMResult exposes `.text` + `.tool_calls` + `.usage` and does NOT have the
    OpenAI `.choices` attribute. Duck-typing (not isinstance) keeps the logger
    import-light and tolerant of dataclass/subclass variations.
    """
    return (
        hasattr(obj, "text")
        and hasattr(obj, "tool_calls")
        and not hasattr(obj, "choices")
    )


def _to_jsonb(obj: Any) -> Any:
    """Round-trip через JSON чтобы UUID/datetime/прочие не-JSON типы стали str.

    SQLAlchemy JSONB-колонка биндится напрямую из dict/list. Но если внутри
    встретится UUID или datetime — встроенный JSON-encoder упадёт. Делаем
    round-trip json.dumps(default=str) → json.loads, получаем pure-json
    dict/list/scalar пригодный для прямого JSONB-bind'а без ::jsonb cast'а.

    `ensure_ascii=False` сохраняет Russian text в prompt (FAQ, persona) as-is.
    """
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))
