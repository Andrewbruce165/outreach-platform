"""Pydantic schema tests for Agent v2 widening (UI-AGNT-01, schema layer).

Pure schema validation — no DB hit. Covers AgentCreate, AgentUpdate,
AgentResponse, ToneSpec, QAPair payloads from UI-SPEC §5.8.
"""

import pytest
from pydantic import ValidationError

from app.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    QAPair,
    ToneSpec,
)


def test_agent_create_minimal_legacy_still_works():
    """Phase 3 payload (legacy fields only) must still validate."""
    a = AgentCreate(name="LegacyAgent")
    assert a.name == "LegacyAgent"
    assert a.voice_baseline is None
    assert a.tone is None
    assert a.who_is_agent is None


def test_agent_create_full_v2_payload():
    """Full UI-SPEC §5.8 v2 payload validates and exposes typed fields."""
    a = AgentCreate(
        name="V2Agent",
        who_is_agent="Senior SDR at aimly",
        company_knowledge="aimly is a Telegram outreach platform",
        knowledge_base="Pricing: $99/mo",
        voice_baseline="Professional",
        tone={"formal": 10, "warm": -5, "brief": 20},
        max_message_length=280,
        mirror_language=True,
        allow_emoji=False,
        banlist=["revolutionary", "synergy"],
        qa_pairs=[{"q": "What's pricing?", "a": "$99/mo"}],
        auto_pause_triggers=["unsubscribe"],
        auto_pause_scope="conversation",
    )
    assert a.voice_baseline == "Professional"
    assert a.tone is not None
    assert a.tone.formal == 10
    assert a.tone.warm == -5
    assert a.tone.brief == 20
    assert a.qa_pairs is not None
    assert a.qa_pairs[0].q == "What's pricing?"
    assert a.auto_pause_scope == "conversation"


def test_agent_create_rejects_invalid_voice_baseline():
    with pytest.raises(ValidationError):
        AgentCreate(name="X", voice_baseline="Sarcastic")


def test_agent_create_rejects_invalid_auto_pause_scope():
    with pytest.raises(ValidationError):
        AgentCreate(name="X", auto_pause_scope="bogus")


def test_agent_update_full_v2_partial_patch():
    """AgentUpdate accepts any subset of v2 fields (Partial PATCH semantics)."""
    u = AgentUpdate(voice_baseline="Friendly", allow_emoji=True)
    assert u.voice_baseline == "Friendly"
    assert u.allow_emoji is True
    # All other fields default None — back-compat with legacy partial PATCH.
    assert u.name is None
    assert u.who_is_agent is None


def test_tone_spec_range_enforced():
    """formal/warm/brief must be in [-50, +50]."""
    with pytest.raises(ValidationError):
        ToneSpec(formal=100)
    with pytest.raises(ValidationError):
        ToneSpec(warm=-100)
    with pytest.raises(ValidationError):
        ToneSpec(brief=51)
    # Defaults are 0 — boundary 0 is valid.
    z = ToneSpec()
    assert z.formal == 0 and z.warm == 0 and z.brief == 0


def test_qa_pair_length_constraints():
    """QAPair: q max 2000 chars, a max 4000 chars, both min 1."""
    ok = QAPair(q="Q?", a="A.")
    assert ok.q == "Q?"
    with pytest.raises(ValidationError):
        QAPair(q="", a="a")
    with pytest.raises(ValidationError):
        QAPair(q="q", a="")


def test_agent_response_accepts_legacy_only():
    """AgentResponse must accept a pre-05.1 row dict (no v2 fields)."""
    resp = AgentResponse(
        id="00000000-0000-0000-0000-000000000000",
        name="Legacy",
        system_prompt=None,
        rules=None,
        tone_of_voice=None,
        faq=[],
        company_info=None,
        product_info=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert resp.who_is_agent is None
    assert resp.tone is None
    assert resp.banlist is None


def test_agent_response_accepts_v2_payload():
    """AgentResponse must accept the v2 payload (tone as dict, qa_pairs as list[dict])."""
    resp = AgentResponse(
        id="00000000-0000-0000-0000-000000000000",
        name="V2",
        faq=[],
        who_is_agent="SDR",
        voice_baseline="Friendly",
        tone={"formal": 1, "warm": 2, "brief": 3},
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
    assert resp.tone == {"formal": 1, "warm": 2, "brief": 3}
    assert resp.qa_pairs == [{"q": "q?", "a": "a."}]
    assert resp.auto_pause_scope == "contact"
