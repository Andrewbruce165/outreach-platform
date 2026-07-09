"""Pydantic schema tests for Agent v2 + Phase 11 field split (UI-AGNT-01, schema layer).

Pure schema validation — no DB hit. Covers AgentCreate, AgentUpdate,
AgentResponse, DialogueStage, QAPair payloads.

Phase 11 D-01: tone_preset replaces voice_baseline/tone/ToneSpec/tone_of_voice.
Phase 11 D-11: response_speed + response_delay_seconds added.
"""

import pytest
from pydantic import ValidationError

from app.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    DialogueStage,
    QAPair,
)


def test_agent_create_minimal_legacy_still_works():
    """Phase 3 payload (legacy fields only) must still validate."""
    a = AgentCreate(name="LegacyAgent")
    assert a.name == "LegacyAgent"
    # Phase 11 D-01: tone_preset replaces voice_baseline
    assert a.tone_preset is None
    assert a.response_speed is None
    assert a.who_is_agent is None


def test_agent_create_full_v2_payload():
    """Full v2 + Phase 11 payload validates and exposes typed fields."""
    a = AgentCreate(
        name="V2Agent",
        who_is_agent="Senior SDR at aimly",
        company_knowledge="aimly is a Telegram outreach platform",
        knowledge_base="Pricing: $99/mo",
        # Phase 11 D-01: tone_preset replaces voice_baseline
        tone_preset="Professional",
        response_speed="human",
        response_delay_seconds=None,
        max_message_length=280,
        mirror_language=True,
        allow_emoji=False,
        banlist=["revolutionary", "synergy"],
        qa_pairs=[{"q": "What's pricing?", "a": "$99/mo"}],
        auto_pause_triggers=["unsubscribe"],
        auto_pause_scope="conversation",
    )
    assert a.tone_preset == "Professional"
    assert a.response_speed == "human"
    assert a.qa_pairs is not None
    assert a.qa_pairs[0].q == "What's pricing?"
    assert a.auto_pause_scope == "conversation"


def test_agent_create_rejects_invalid_tone_preset():
    """Phase 11 D-01: only Friendly/Professional/Direct/Casual allowed."""
    with pytest.raises(ValidationError):
        AgentCreate(name="X", tone_preset="Sarcastic")


def test_agent_create_rejects_invalid_response_speed():
    """Phase 11 D-11: only instant/human/slow/manual allowed."""
    with pytest.raises(ValidationError):
        AgentCreate(name="X", response_speed="turbo")


def test_agent_create_rejects_invalid_auto_pause_scope():
    with pytest.raises(ValidationError):
        AgentCreate(name="X", auto_pause_scope="bogus")


def test_agent_create_rejects_response_delay_out_of_range():
    """Phase 11 T3: response_delay_seconds must be 0..3600."""
    with pytest.raises(ValidationError):
        AgentCreate(name="X", response_delay_seconds=-1)
    with pytest.raises(ValidationError):
        AgentCreate(name="X", response_delay_seconds=3601)
    # Boundary values must be valid
    a = AgentCreate(name="X", response_delay_seconds=0)
    assert a.response_delay_seconds == 0
    b = AgentCreate(name="X", response_delay_seconds=3600)
    assert b.response_delay_seconds == 3600


def test_agent_update_full_v2_partial_patch():
    """AgentUpdate accepts any subset of v2/Phase-11 fields (Partial PATCH semantics)."""
    # Phase 11 D-01: tone_preset replaces voice_baseline
    u = AgentUpdate(tone_preset="Friendly", allow_emoji=True)
    assert u.tone_preset == "Friendly"
    assert u.allow_emoji is True
    # All other fields default None — back-compat with legacy partial PATCH.
    assert u.name is None
    assert u.who_is_agent is None


def test_qa_pair_length_constraints():
    """QAPair: q max 2000 chars, a max 4000 chars, both min 1."""
    ok = QAPair(q="Q?", a="A.")
    assert ok.q == "Q?"
    with pytest.raises(ValidationError):
        QAPair(q="", a="a")
    with pytest.raises(ValidationError):
        QAPair(q="q", a="")


def test_agent_response_accepts_legacy_only():
    """AgentResponse must accept a pre-Phase-11 row dict (no v2/Phase-11 fields)."""
    resp = AgentResponse(
        id="00000000-0000-0000-0000-000000000000",
        name="Legacy",
        system_prompt=None,
        rules=None,
        faq=[],
        company_info=None,
        product_info=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert resp.who_is_agent is None
    assert resp.tone_preset is None
    assert resp.response_speed is None
    assert resp.banlist is None


def test_agent_response_accepts_phase11_payload():
    """AgentResponse must accept the Phase 11 payload (tone_preset as str)."""
    resp = AgentResponse(
        id="00000000-0000-0000-0000-000000000000",
        name="V2",
        faq=[],
        who_is_agent="SDR",
        # Phase 11 D-01: tone_preset replaces voice_baseline/tone
        tone_preset="Friendly",
        response_speed="human",
        response_delay_seconds=30,
        max_message_length=280,
        mirror_language=True,
        allow_emoji=False,
        banlist=["x"],
        qa_pairs=[{"q": "q?", "a": "a."}],
        auto_pause_triggers=["unsubscribe"],
        auto_pause_scope="contact",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert resp.tone_preset == "Friendly"
    assert resp.response_speed == "human"
    assert resp.response_delay_seconds == 30
    assert resp.qa_pairs == [{"q": "q?", "a": "a."}]
    assert resp.auto_pause_scope == "contact"


def test_dialogue_stage_validation():
    """Phase 11 D-04: DialogueStage — instruction required, title optional, size limits."""
    # Minimal (no title)
    s = DialogueStage(instruction="Ask about their budget")
    assert s.instruction == "Ask about their budget"
    assert s.title is None

    # With title
    s2 = DialogueStage(title="Discovery", instruction="Find pain points")
    assert s2.title == "Discovery"

    # Empty instruction must fail
    with pytest.raises(ValidationError):
        DialogueStage(instruction="")

    # Instruction too long (> 3000, raised from 2000 on 2026-07-09 —
    # debug/campaign-draft-save-validation-failed.md)
    with pytest.raises(ValidationError):
        DialogueStage(instruction="x" * 3001)

    # Title too long (> 120)
    with pytest.raises(ValidationError):
        DialogueStage(title="t" * 121, instruction="ok")
