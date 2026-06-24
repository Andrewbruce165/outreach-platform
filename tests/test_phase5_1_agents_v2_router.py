"""Agent CRUD round-trip with 05.1 v2 columns + Phase 11 field split — UI-AGNT-01.

Plan 03 Task 3 (updated Phase 11):
- Router persists v2 + Phase 11 columns through POST / PATCH / GET / duplicate.
- Phase 11 D-01: voice_baseline/tone/tone_of_voice removed; tone_preset is single source.
- Phase 11 D-11: response_speed + response_delay_seconds added.
- ai_engine.get_context_for_conversation reads COALESCE(new, legacy) so legacy
  agents (only system_prompt set) keep producing identical LLM prompts.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "agent-v2-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ─── Router pass-through tests ───────────────────────────────────────────────


async def test_agent_create_with_full_v2_payload(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """All v2 + Phase 11 fields round-trip through POST + GET."""
    await _bind(async_db_session, test_workspace.id, "u-agent-create")
    body = {
        "name": "Voiced agent",
        "who_is_agent": "Senior SDR at aimly",
        "company_knowledge": "aimly is a Telegram outreach platform",
        "knowledge_base": "Pricing: $99/mo",
        # Phase 11 D-01: tone_preset replaces voice_baseline/tone/tone_of_voice
        "tone_preset": "Friendly",
        "response_speed": "human",
        "response_delay_seconds": None,
        "max_message_length": 200,
        "mirror_language": True,
        "allow_emoji": True,
        "banlist": ["synergy"],
        "qa_pairs": [{"q": "What's pricing?", "a": "$99/mo"}],
        "auto_pause_triggers": ["unsubscribe"],
        "auto_pause_scope": "contact",
    }
    resp = await async_client.post(
        "/api/v1/agents",
        json=body,
        headers=_auth_headers(valid_supabase_jwt, "u-agent-create"),
    )
    assert resp.status_code == 201, resp.text
    aid = resp.json()["id"]

    got = await async_client.get(
        f"/api/v1/agents/{aid}",
        headers=_auth_headers(valid_supabase_jwt, "u-agent-create"),
    )
    assert got.status_code == 200, got.text
    j = got.json()
    assert j["who_is_agent"] == "Senior SDR at aimly"
    assert j["company_knowledge"] == "aimly is a Telegram outreach platform"
    assert j["knowledge_base"] == "Pricing: $99/mo"
    # Phase 11 D-01 assertions:
    assert j["tone_preset"] == "Friendly"
    assert j["response_speed"] == "human"
    assert j["max_message_length"] == 200
    assert j["mirror_language"] is True
    assert j["allow_emoji"] is True
    assert j["banlist"] == ["synergy"]
    assert j["qa_pairs"] == [{"q": "What's pricing?", "a": "$99/mo"}]
    assert j["auto_pause_triggers"] == ["unsubscribe"]
    assert j["auto_pause_scope"] == "contact"
    # Removed fields must NOT be in the response
    assert "voice_baseline" not in j
    assert "tone" not in j
    assert "tone_of_voice" not in j


async def test_agent_patch_partial_v2_fields(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory,
):
    """PATCH updates only the fields in the body — others stay unchanged."""
    await _bind(async_db_session, test_workspace.id, "u-agent-patch")
    a = await test_agent_factory()
    # Phase 11 D-01: use tone_preset instead of voice_baseline
    patch = {"tone_preset": "Professional", "max_message_length": 320}
    resp = await async_client.patch(
        f"/api/v1/agents/{a.id}",
        json=patch,
        headers=_auth_headers(valid_supabase_jwt, "u-agent-patch"),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["tone_preset"] == "Professional"
    assert j["max_message_length"] == 320
    # Legacy system_prompt untouched (test_agent_factory default).
    assert j["system_prompt"] == "You are a helpful sales agent."


async def test_agent_create_legacy_only_payload_returns_v2_fields_null(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Phase 3 / Phase 4 client (no v2 fields) → response shows Phase 11 fields as None."""
    await _bind(async_db_session, test_workspace.id, "u-agent-legacy")
    resp = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Legacy shape",
            "system_prompt": "Hi there",
        },
        headers=_auth_headers(valid_supabase_jwt, "u-agent-legacy"),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    assert j["who_is_agent"] is None
    # Phase 11 D-01: tone_preset replaces voice_baseline
    assert j["tone_preset"] is None
    assert j["response_speed"] is None
    assert j["banlist"] is None
    # auto_pause_scope has DB default 'conversation' — not None on fresh insert
    assert j["auto_pause_scope"] == "conversation"


async def test_agent_duplicate_copies_v2_fields(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """POST /{id}/duplicate must copy Phase 11 fields for parity with source."""
    await _bind(async_db_session, test_workspace.id, "u-agent-dup")
    # Create source with Phase 11 fields.
    src_resp = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Dup source",
            "who_is_agent": "Source agent",
            # Phase 11 D-01: tone_preset replaces voice_baseline/tone
            "tone_preset": "Casual",
            "response_speed": "slow",
            "auto_pause_scope": "campaign",
        },
        headers=_auth_headers(valid_supabase_jwt, "u-agent-dup"),
    )
    assert src_resp.status_code == 201, src_resp.text
    src_id = src_resp.json()["id"]

    dup_resp = await async_client.post(
        f"/api/v1/agents/{src_id}/duplicate",
        headers=_auth_headers(valid_supabase_jwt, "u-agent-dup"),
    )
    assert dup_resp.status_code == 201, dup_resp.text
    dup = dup_resp.json()
    assert dup["who_is_agent"] == "Source agent"
    assert dup["tone_preset"] == "Casual"
    assert dup["response_speed"] == "slow"
    assert dup["auto_pause_scope"] == "campaign"
    assert dup["name"].startswith("Dup source (copy")


# ─── ai_engine COALESCE tests (raw SQL — mirrors the SELECT in the engine) ───


async def test_ai_engine_coalesce_legacy_only_agent(
    async_db_session, test_workspace, test_agent_factory,
):
    """Pre-05.1 agent (only system_prompt + company_info set) — COALESCE returns the
    legacy values so the in-memory `context` dict has the same shape as Phase 3.
    """
    a = await test_agent_factory(
        system_prompt="LEGACY_PROMPT_TEXT",
        company_info="LegacyCo",
        product_info="LegacyProduct",
    )
    row = (await async_db_session.execute(text("""
        SELECT
            COALESCE(who_is_agent, system_prompt) AS system_prompt,
            COALESCE(company_knowledge, company_info) AS company_info,
            COALESCE(knowledge_base, product_info) AS product_info
        FROM ai_contexts WHERE id = :id
    """), {"id": str(a.id)})).first()
    assert row.system_prompt == "LEGACY_PROMPT_TEXT"
    assert row.company_info == "LegacyCo"
    assert row.product_info == "LegacyProduct"


async def test_ai_engine_coalesce_new_wins_when_both_set(
    async_db_session, test_workspace, test_agent_factory,
):
    """Migrated agent — new col (who_is_agent / company_knowledge / knowledge_base)
    wins over legacy. Matches RESEARCH §"Backend Gap Map" — new takes precedence.
    """
    a = await test_agent_factory(
        system_prompt="OLD_PROMPT",
        company_info="OldCo",
        product_info="OldProduct",
    )
    await async_db_session.execute(text("""
        UPDATE ai_contexts SET
            who_is_agent='NEW_PROMPT',
            company_knowledge='NewCo',
            knowledge_base='NewProduct'
        WHERE id=:id
    """), {"id": str(a.id)})
    await async_db_session.commit()
    row = (await async_db_session.execute(text("""
        SELECT
            COALESCE(who_is_agent, system_prompt) AS system_prompt,
            COALESCE(company_knowledge, company_info) AS company_info,
            COALESCE(knowledge_base, product_info) AS product_info
        FROM ai_contexts WHERE id = :id
    """), {"id": str(a.id)})).first()
    assert row.system_prompt == "NEW_PROMPT"
    assert row.company_info == "NewCo"
    assert row.product_info == "NewProduct"


async def test_ai_engine_coalesce_new_only_no_legacy(
    async_db_session, test_workspace, test_agent_factory,
):
    """Newly created agent — only new cols set, legacy cols NULL. COALESCE picks new."""
    a = await test_agent_factory(
        system_prompt=None,
        company_info=None,
        product_info=None,
    )
    await async_db_session.execute(text("""
        UPDATE ai_contexts SET
            who_is_agent='NEW_ONLY_PROMPT',
            company_knowledge='NewOnlyCo',
            knowledge_base='NewOnlyProduct'
        WHERE id=:id
    """), {"id": str(a.id)})
    await async_db_session.commit()
    row = (await async_db_session.execute(text("""
        SELECT
            COALESCE(who_is_agent, system_prompt) AS system_prompt,
            COALESCE(company_knowledge, company_info) AS company_info,
            COALESCE(knowledge_base, product_info) AS product_info
        FROM ai_contexts WHERE id = :id
    """), {"id": str(a.id)})).first()
    assert row.system_prompt == "NEW_ONLY_PROMPT"
    assert row.company_info == "NewOnlyCo"
    assert row.product_info == "NewOnlyProduct"
