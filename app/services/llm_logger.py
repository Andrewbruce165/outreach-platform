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
) -> None:
    """Insert one llm_calls row. NEVER raises.

    Args:
        workspace_id: If None, resolved from conversations.workspace_id.
        conversation_id: FK NOT NULL — required.
        model: e.g. "gpt-5-mini-2025-08-07".
        prompt: Full request_params dict (messages + tools + temperature + model).
        response: OpenAI ChatCompletion object OR None on error.
        latency_ms: Total round-trip time of OpenAI call.
        error: Exception text if OpenAI call failed (truncated to 500 chars by caller).

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

            # 3. INSERT one row (15 columns, JSONB bind via ::jsonb cast)
            await session.execute(text("""
                INSERT INTO llm_calls (
                    workspace_id, conversation_id, campaign_id, agent_id, sender_id,
                    model, prompt, response_text, tool_calls,
                    prompt_tokens, completion_tokens, total_tokens, latency_ms, error
                )
                VALUES (
                    :wid, :cid, :camp, :agent, :sender,
                    :model, :prompt::jsonb, :response_text, :tool_calls::jsonb,
                    :pt, :ct, :tt, :latency, :error
                )
            """), {
                "wid": str(ws_id),
                "cid": str(conversation_id),
                "camp": str(campaign_id) if campaign_id else None,
                "agent": str(agent_id) if agent_id else None,
                "sender": str(sender_id) if sender_id else None,
                "model": model,
                "prompt": _safe_jsonify(prompt),
                "response_text": response_text,
                "tool_calls": _safe_jsonify(tool_calls_json) if tool_calls_json else None,
                "pt": prompt_tokens,
                "ct": completion_tokens,
                "tt": total_tokens,
                "latency": latency_ms,
                "error": error,
            })
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


def _safe_jsonify(obj: Any) -> str:
    """Serialize dict/list to JSON string for PG JSONB binding.

    `ensure_ascii=False` сохраняет Russian text в prompt (FAQ, persona) as-is.
    `default=str` для UUID/datetime objects, встречающихся в request_params.
    """
    return json.dumps(obj, ensure_ascii=False, default=str)
