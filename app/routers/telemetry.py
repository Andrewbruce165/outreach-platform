"""Telemetry router (Phase 05.1 — UI-SPEC §9).

Ingest UI-fired events for Core Value KPI tracking. JWT-only (no API-key path —
events are user-context, not integration-context). Storage = local Postgres
table ``telemetry_events`` (migration 018). NO PostHog/Segment in v1.

Whitelist enforced at ingest time so unknown events get caught at the edge
(prevents schema drift from typos / agent hallucinations downstream).

Idempotency: ``event_id`` is client-supplied UUID PK (decision logged in
05.1-01-SUMMARY.md). ``ON CONFLICT (event_id) DO NOTHING`` dedupes
navigator.sendBeacon retries on flaky networks.

Security: ``props`` blob is never logged — may contain user PII or webhook
tokens. Only ``workspace_id / event / event_id`` are emitted to the logger.
"""

import json
import logging
import uuid as _uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import CoreValueResponse, TelemetryEventIn
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


# UI-SPEC §9 — canonical event whitelist.
#
# This set MUST stay in sync with:
#   - .planning/phases/05.1-lovable-ui-v1/05.1-UI-SPEC.md §9 (15 numbered events)
#   - Lovable handoff bundle telemetry-events.md (plan 05.1-05)
# Plus 2 derived events (`dashboard_viewed`, `custom_tool_added`) used by the
# UI for engagement tracking that did not get a numbered slot in UI-SPEC §9.
#
# Unknown events at /events are 400 — schema-drift contract error, not transient.
_EVENT_WHITELIST = {
    "magic_link_requested",
    "signup_completed",
    "sender_added",
    "contacts_imported",
    "csv_import_completed",
    "agent_created",
    "campaign_created",
    "campaign_launched",
    "campaign_paused",
    "campaign_resumed",
    "conversation_taken_over_by_human",
    "llm_trace_opened",
    "workspace_api_key_created",
    "settings_changed",
    "agent_voice_changed",
    "custom_tool_added",
    "dashboard_viewed",
}


@router.post("/events", status_code=202)
async def ingest_event(
    body: TelemetryEventIn,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ingest a single telemetry event. Idempotent on event_id.

    - 202 Accepted on success (matches sendBeacon semantics — fire-and-forget).
    - 202 Accepted on duplicate event_id (ON CONFLICT DO NOTHING — sendBeacon
      retries on flaky networks must not surface as errors to the client).
    - 400 UNKNOWN_EVENT when the event name is not in ``_EVENT_WHITELIST``
      (contract error — typos / hallucinations should not silently pollute
      the table).

    Returns ``{accepted: True, event_id: "<uuid>"}`` for client-side dedup.
    """
    if body.event not in _EVENT_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNKNOWN_EVENT",
                    "message": f"Event '{body.event}' not in whitelist (UI-SPEC §9)"},
        )

    # Generate event_id on the server if the client omitted it; clients should
    # supply one for sendBeacon idempotency but the field is Optional in the
    # Pydantic schema (TelemetryEventIn.event_id: Optional[UUID]).
    event_id = body.event_id or _uuid.uuid4()

    # SECURITY: never log the props blob — may contain user PII or webhook
    # tokens that should not appear in plaintext logs.
    logger.info(
        "[telemetry] ingest workspace=%s event=%s event_id=%s",
        ctx.workspace_id, body.event, event_id,
    )

    # JSONB cast via :props is dialect-portable; ON CONFLICT DO NOTHING uses
    # the event_id PK to dedup sendBeacon retries (decision 05.1-01).
    await db.execute(text("""
        INSERT INTO telemetry_events
            (event_id, workspace_id, user_id, event, props, client_ts, server_ts)
        VALUES (:eid, :wid, :uid, :event, CAST(:props AS JSONB), :cts, NOW())
        ON CONFLICT (event_id) DO NOTHING
    """), {
        "eid": str(event_id),
        "wid": str(ctx.workspace_id),
        "uid": ctx.user_id,
        "event": body.event,
        "props": json.dumps(body.props or {}, ensure_ascii=False, default=str),
        "cts": body.client_timestamp,
    })
    await db.commit()
    return {"accepted": True, "event_id": str(event_id)}


@router.get("/core-value", response_model=CoreValueResponse)
async def core_value(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> CoreValueResponse:
    """UI-SPEC §9 KPI #9 — Core Value: time_to_first_campaign_seconds.

    Computes::

        delta = MIN(campaign_launched.server_ts) - MIN(signup_completed.server_ts)

    per workspace. Returns ``None`` fields when the workspace has not yet
    signed up or has not launched a campaign yet (UI renders "—" in that case).

    Target: < 600 seconds (10 minutes) for 80% of new users (UI-SPEC §9).

    Workspace isolation is enforced inside the SQL — both subqueries filter
    ``WHERE workspace_id = :wid``. No JOIN to a global table, so cross-workspace
    leakage is structurally impossible.
    """
    row = (await db.execute(text("""
        WITH t AS (
          SELECT
            (SELECT MIN(server_ts) FROM telemetry_events
              WHERE workspace_id = :wid AND event = 'signup_completed') AS signup_at,
            (SELECT MIN(server_ts) FROM telemetry_events
              WHERE workspace_id = :wid AND event = 'campaign_launched') AS first_launch_at
        )
        SELECT signup_at, first_launch_at,
               CASE WHEN signup_at IS NOT NULL AND first_launch_at IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (first_launch_at - signup_at))::INT
                    ELSE NULL END AS delta_seconds
        FROM t
    """), {"wid": str(ctx.workspace_id)})).first()

    return CoreValueResponse(
        time_to_first_campaign_seconds=row.delta_seconds if row else None,
        signup_at=row.signup_at if row else None,
        first_launch_at=row.first_launch_at if row else None,
    )
