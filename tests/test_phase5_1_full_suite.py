"""Phase 05.1 sentinel — every 05.1-added module/class importable.

This is the first test to run in the phase 05.1 suite. If it fails, all
the per-feature tests below it will also fail with confusing errors; this
test surfaces import-level regressions (typos in class names, missing
__all__ exports, circular imports, dropped migration that breaks ORM
mapper config) at the top of the suite output.

Pure-import test; no DB hit. Runs in ~1 second.
"""
from __future__ import annotations


def test_all_05_1_modules_importable():
    """Every 05.1-added file imports cleanly + endpoint surface preserved."""
    # Routers
    from app.routers import telemetry as _telemetry  # noqa: F401
    from app.routers import analytics as _analytics  # noqa: F401
    from app.routers import campaigns as _campaigns  # noqa: F401
    from app.routers import senders as _senders      # noqa: F401
    from app.routers import agents as _agents        # noqa: F401

    # New endpoint markers on existing routers (catches accidental removal):
    assert any(
        getattr(r, "path", "").endswith("/funnel") for r in _analytics.router.routes
    ), "GET /analytics/funnel not registered"
    assert any(
        getattr(r, "path", "").endswith("/llm") for r in _analytics.router.routes
    ), "GET /analytics/llm not registered"
    assert any(
        getattr(r, "path", "").endswith("/events") for r in _telemetry.router.routes
    ), "POST /telemetry/events not registered"
    assert any(
        getattr(r, "path", "").endswith("/core-value") for r in _telemetry.router.routes
    ), "GET /telemetry/core-value not registered"
    assert any(
        getattr(r, "path", "").endswith("/stop") for r in _campaigns.router.routes
    ), "POST /campaigns/{id}/stop not registered"
    assert any(
        getattr(r, "path", "").endswith("/auto-fill") for r in _campaigns.router.routes
    ), "POST /campaigns/auto-fill not registered"
    assert any(
        getattr(r, "path", "").endswith("/pause") for r in _senders.router.routes
    ), "POST /senders/{slug}/pause not registered"
    assert any(
        getattr(r, "path", "").endswith("/resume") for r in _senders.router.routes
    ), "POST /senders/{slug}/resume not registered"


def test_05_1_schemas_importable():
    """Every Pydantic schema added in 05.1-01 / 05.1-04 is importable + has expected fields."""
    from app.schemas import (
        # ToneSpec removed in Phase 11 D-01 (replaced by tone_preset Literal field)
        DialogueStage,
        QAPair,
        TelemetryEventIn,
        CoreValueResponse,
        FunnelResponse,
        LLMAggregatesResponse,
    )

    # Spot-check field names (catches accidental removal).
    assert set(TelemetryEventIn.model_fields.keys()) >= {"event_id", "event", "props"}
    assert set(CoreValueResponse.model_fields.keys()) == {
        "time_to_first_campaign_seconds", "signup_at", "first_launch_at",
    }
    assert set(FunnelResponse.model_fields.keys()) == {
        "sent", "replied", "engaged", "lead", "handoff",
    }
    assert set(LLMAggregatesResponse.model_fields.keys()) == {
        "total_calls", "avg_latency_ms", "prompt_tokens",
        "completion_tokens", "total_tokens", "spend_usd_cents",
    }
    # Phase 11 D-01: DialogueStage replaces ToneSpec
    assert set(DialogueStage.model_fields.keys()) == {"title", "instruction"}
    # QA pair v2 (UI-SPEC §5.8 FAQ tab)
    assert set(QAPair.model_fields.keys()) == {"q", "a"}


def test_05_1_orm_models_importable():
    """TelemetryEvent ORM class + 05.1 widening on AIContext + Campaign."""
    from app.models import TelemetryEvent, AIContext, Campaign

    assert TelemetryEvent.__table__.name == "telemetry_events"

    ai_cols = {c.name for c in AIContext.__table__.columns}
    # Phase 11 D-01: tone_preset replaces voice_baseline/tone/tone_of_voice;
    # response_speed/response_delay_seconds added.
    expected_ai_v2 = {
        "who_is_agent", "company_knowledge", "knowledge_base",
        "tone_preset", "response_speed", "response_delay_seconds",
        "max_message_length",
        "mirror_language", "allow_emoji", "banlist", "qa_pairs",
        "auto_pause_triggers", "auto_pause_scope",
    }
    missing_ai = expected_ai_v2 - ai_cols
    assert not missing_ai, f"AIContext missing v2/Phase-11 columns: {missing_ai}"

    camp_cols = {c.name for c in Campaign.__table__.columns}
    # Phase 11 D-04/D-12/D-14: dialogue_flow/arguments_facts/campaign_rules added.
    # Phase 11 D-13: success_criteria removed.
    expected_camp_v2 = {"audience_hints", "primary_goal", "webhook_url",
                        "dialogue_flow", "arguments_facts", "campaign_rules"}
    missing_camp = expected_camp_v2 - camp_cols
    assert not missing_camp, f"Campaign missing v2/Phase-11 columns: {missing_camp}"


def test_app_main_includes_telemetry_router():
    """05.1-04 registered telemetry router in app/main.py."""
    from app.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/v1/telemetry/events" in paths, (
        f"Telemetry router not registered in app.main. "
        f"Got paths: {sorted(p for p in paths if p)[:20]}..."
    )
    assert "/api/v1/telemetry/core-value" in paths
