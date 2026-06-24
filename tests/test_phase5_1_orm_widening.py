"""ORM smoke tests for Phase 05.1 widening + Phase 11 field split — pure import + select-compile
(no DB hit). Validates that the SQLAlchemy ORM mirrors migration 018 / migration 032.

Phase 11 D-01: voice_baseline/tone/tone_of_voice removed; tone_preset/response_speed/
response_delay_seconds added. D-13: success_criteria removed; dialogue_flow/arguments_facts/
campaign_rules added.
"""

from sqlalchemy import select

from app.models import AIContext, Campaign, TelemetryEvent


def test_aicontext_v2_columns_present():
    cols = {c.name for c in AIContext.__table__.columns}
    # Phase 11 D-01: tone_preset replaces voice_baseline/tone; response_speed/response_delay_seconds added
    expected = {
        "who_is_agent", "company_knowledge", "knowledge_base",
        "tone_preset", "response_speed", "response_delay_seconds",
        "max_message_length",
        "mirror_language", "allow_emoji", "banlist", "qa_pairs",
        "auto_pause_triggers", "auto_pause_scope",
    }
    missing = expected - cols
    assert not missing, f"AIContext missing v2/Phase-11 columns: {missing}"


def test_campaign_v2_columns_present():
    cols = {c.name for c in Campaign.__table__.columns}
    # Phase 11 D-04/D-12/D-14: dialogue_flow/arguments_facts/campaign_rules added
    # Phase 11 D-13: success_criteria removed
    expected = {"audience_hints", "primary_goal", "webhook_url",
                "dialogue_flow", "arguments_facts", "campaign_rules"}
    missing = expected - cols
    assert not missing, f"Campaign missing v2/Phase-11 columns: {missing}"


def test_telemetry_event_table_name():
    assert TelemetryEvent.__table__.name == "telemetry_events"
    cols = {c.name for c in TelemetryEvent.__table__.columns}
    assert cols == {"event_id", "workspace_id", "user_id", "event", "props",
                    "client_ts", "server_ts"}, (
        f"TelemetryEvent column set mismatch: got {cols}"
    )


def test_aicontext_select_compiles():
    """No DB hit — just ensures column refs are syntactically valid."""
    # Phase 11 D-01: filter on tone_preset (replaces voice_baseline)
    stmt = select(AIContext).where(AIContext.tone_preset == "Professional")
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "tone_preset" in compiled


def test_phase11_fields_present_and_legacy_dropped():
    """Phase 11 field-split acceptance criterion."""
    ai_cols = {c.name for c in AIContext.__table__.columns}
    camp_cols = {c.name for c in Campaign.__table__.columns}

    # New fields must be present
    for new_col in ("tone_preset", "response_speed", "response_delay_seconds"):
        assert new_col in ai_cols, f"AIContext Phase-11 field missing: {new_col}"
    for new_col in ("dialogue_flow", "arguments_facts", "campaign_rules"):
        assert new_col in camp_cols, f"Campaign Phase-11 field missing: {new_col}"

    # Dropped fields must NOT be present (D-01, D-13)
    for dropped in ("voice_baseline", "tone", "tone_of_voice"):
        assert dropped not in ai_cols, f"AIContext Phase-11 dropped field still present: {dropped}"
    assert "success_criteria" not in camp_cols, \
        "Campaign Phase-11 dropped field success_criteria still present"


def test_legacy_columns_still_present():
    """Acceptance criterion: non-Phase-11 legacy fields NOT removed by widening."""
    ai_cols = {c.name for c in AIContext.__table__.columns}
    # Legacy AIContext fields kept (Phase 3 D-02 + back-compat for ai_engine).
    # Note: tone_of_voice IS dropped (Phase 11 D-01) so NOT in this list.
    for legacy in ("system_prompt", "rules", "faq",
                   "company_info", "product_info"):
        assert legacy in ai_cols, f"AIContext legacy field removed: {legacy}"

    camp_cols = {c.name for c in Campaign.__table__.columns}
    # Legacy Campaign fields kept (Pitfall 6 — 3 split webhook URLs).
    for legacy in ("lead_webhook_url", "handoff_webhook_url", "finish_webhook_url",
                   "lead_trigger_hint", "handoff_trigger_hint", "finish_trigger_hint",
                   "tools"):
        assert legacy in camp_cols, f"Campaign legacy field removed: {legacy}"
