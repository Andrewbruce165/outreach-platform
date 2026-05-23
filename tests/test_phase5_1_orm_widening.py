"""ORM smoke tests for Phase 05.1 widening — pure import + select-compile
(no DB hit). Validates that the SQLAlchemy ORM mirrors migration 018.
"""

from sqlalchemy import select

from app.models import AIContext, Campaign, TelemetryEvent


def test_aicontext_v2_columns_present():
    cols = {c.name for c in AIContext.__table__.columns}
    expected = {
        "who_is_agent", "company_knowledge", "knowledge_base",
        "voice_baseline", "tone", "max_message_length",
        "mirror_language", "allow_emoji", "banlist", "qa_pairs",
        "auto_pause_triggers", "auto_pause_scope",
    }
    missing = expected - cols
    assert not missing, f"AIContext missing v2 columns: {missing}"


def test_campaign_v2_columns_present():
    cols = {c.name for c in Campaign.__table__.columns}
    expected = {"audience_hints", "primary_goal", "success_criteria", "webhook_url"}
    missing = expected - cols
    assert not missing, f"Campaign missing v2 columns: {missing}"


def test_telemetry_event_table_name():
    assert TelemetryEvent.__table__.name == "telemetry_events"
    cols = {c.name for c in TelemetryEvent.__table__.columns}
    assert cols == {"event_id", "workspace_id", "user_id", "event", "props",
                    "client_ts", "server_ts"}, (
        f"TelemetryEvent column set mismatch: got {cols}"
    )


def test_aicontext_select_compiles():
    """No DB hit — just ensures column refs are syntactically valid."""
    stmt = select(AIContext).where(AIContext.voice_baseline == "Professional")
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "voice_baseline" in compiled


def test_legacy_columns_still_present():
    """Acceptance criterion: no legacy fields removed by 05.1 widening."""
    ai_cols = {c.name for c in AIContext.__table__.columns}
    # Legacy AIContext fields kept (Phase 3 D-02 + back-compat for ai_engine).
    for legacy in ("system_prompt", "tone_of_voice", "rules", "faq",
                   "company_info", "product_info"):
        assert legacy in ai_cols, f"AIContext legacy field removed: {legacy}"

    camp_cols = {c.name for c in Campaign.__table__.columns}
    # Legacy Campaign fields kept (Pitfall 6 — 3 split webhook URLs).
    for legacy in ("lead_webhook_url", "handoff_webhook_url", "finish_webhook_url",
                   "lead_trigger_hint", "handoff_trigger_hint", "finish_trigger_hint",
                   "tools"):
        assert legacy in camp_cols, f"Campaign legacy field removed: {legacy}"
