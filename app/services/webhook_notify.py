"""Phase 4 D-13: Fire-and-forget webhook helper for built-in campaign signals
(lead / handoff / finish).

Uniform payload shape per C-01 / AUDIT.md Section 6 Q3. No HMAC (deferred to v2).
Pattern: queue.py:_fire_callback async fire-and-forget — never blocks AI response.

Used by app/services/ai_engine.py:_handle_builtin_signal.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 30
MESSAGE_HISTORY_LIMIT = 20  # C-01 — last 20 messages в excerpt


async def _fetch_history_excerpt(db: AsyncSession, conversation_id: UUID) -> list[dict]:
    """SELECT last 20 messages of conversation (chronologically ascending in output)."""
    rows = await db.execute(
        text("""
            SELECT direction, message_text, created_at
            FROM messages
            WHERE conversation_id = :cid
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        {"cid": str(conversation_id), "limit": MESSAGE_HISTORY_LIMIT},
    )
    messages: list[dict] = []
    for r in rows.fetchall():
        # MessageLog/messages.direction is 'inbound' (contact -> us) / 'outbound' (us -> contact).
        role = "assistant" if r.direction == "outbound" else "user"
        messages.append(
            {
                "role": role,
                "content": r.message_text or "",
                "timestamp": r.created_at.isoformat() if r.created_at else None,
            }
        )
    # Reverse — return chronologically ascending (oldest first) which is
    # downstream's natural expectation.
    return list(reversed(messages))


async def _fire(url: str, payload: dict) -> None:
    """Fire-and-forget POST. Catches all exceptions — never raises."""
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            r = await client.post(url, json=payload)
            logger.info(
                "Webhook fired event=%s url=%s status=%s",
                payload.get("event_type"),
                url,
                r.status_code,
            )
    except Exception as e:
        # Never fail the originating flow because of webhook errors.
        logger.warning(
            "Webhook failed event=%s url=%s err=%s",
            payload.get("event_type"),
            url,
            e,
        )


async def notify_signal(
    *,
    event_type: str,
    campaign: dict[str, Any],
    conversation_id: UUID,
    contact: dict[str, Any],
    reason: str,
    db: AsyncSession,
) -> None:
    """Build payload and fire webhook (asyncio.create_task — non-blocking).

    Args:
        event_type: 'lead' | 'handoff' | 'finish'.
        campaign: dict with id, name, workspace_id, {event_type}_webhook_url.
        conversation_id: UUID of the conversation that triggered the signal.
        contact: dict with phone, telegram_id, full_name (or name), username,
                 source, custom.
        reason: LLM-supplied reason string (truncated).
        db: AsyncSession (for message history excerpt).

    Semantics:
        - If event_type not in known set → log error and return.
        - If `campaign[{event_type}_webhook_url]` is None/empty → log info and
          return. Status update has already been done by the caller — webhook
          is the *optional* notification side.
        - Otherwise: spawn fire-and-forget task and return immediately. AI
          response path is NOT delayed by webhook latency.
    """
    if event_type not in ("lead", "handoff", "finish"):
        logger.error("notify_signal: invalid event_type=%s", event_type)
        return

    # Phase 5.1 unification: prefer event-specific URL, fall back to unified
    # `campaign.webhook_url` (UI-only field, populated by Lovable). Closes the
    # backwards-compat gap between Phase 4 (per-event URL) and Phase 5.1 UI
    # (single URL for all events).
    url_key = f"{event_type}_webhook_url"
    url = campaign.get(url_key) or campaign.get("webhook_url")
    if not url:
        logger.info(
            "notify_signal: no webhook URL (%s + webhook_url both NULL) for campaign %s — skip",
            url_key,
            campaign.get("id"),
        )
        return

    excerpt = await _fetch_history_excerpt(db, conversation_id)

    payload = {
        "event_type": event_type,
        "campaign_id": str(campaign["id"]) if campaign.get("id") else None,
        "campaign_name": campaign.get("name"),
        "conversation_id": str(conversation_id),
        "workspace_id": str(campaign["workspace_id"])
        if campaign.get("workspace_id")
        else None,
        "contact": {
            "phone": contact.get("phone"),
            "telegram_id": contact.get("telegram_id"),
            "name": contact.get("full_name") or contact.get("name"),
            "username": contact.get("username"),
            "source": contact.get("source"),
            "custom": contact.get("custom") or {},
        },
        "reason": reason or "",
        "message_history_excerpt": excerpt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Fire-and-forget (Pitfall: NEVER await inside DB transaction).
    asyncio.create_task(_fire(url, payload))
