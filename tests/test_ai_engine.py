"""Phase 3 — ai_engine.get_context adapter (RESEARCH Pitfall 1).

После миграции 015 в ai_contexts больше нет is_active / max_message_length /
webhook_functions — get_context() не должен их SELECTить и проставляет
max_message_length дефолтом, чтобы build_system_prompt работал без правок.

Phase 5 (SaaS-чистка): убран DEFAULT_SYSTEM_PROMPT-fallback и поле
webhook_functions из возвращаемого dict-а. Несуществующий context_id и
пустой context_id теперь возвращают None — workspace обязан настроить агента.

Phase 11 (PMT-01..07): golden-prompt assertions for the Phase 11 prompt rewrite.
New tests are xfail(strict=False) on behavior not yet implemented. They activate
when Plan 11-03 rewrites build_system_prompt.
"""
import inspect
import pytest

pytestmark = pytest.mark.asyncio


async def test_get_context_phase3_schema(async_db_session, test_agent_factory):
    """После миграции 015 get_context работает без is_active/max_message_length/webhook_functions."""
    from app.services.ai_engine import ai_engine

    agent = await test_agent_factory(
        name="Phase 3 Agent",
        system_prompt="test prompt",
        tone_of_voice="friendly",
        rules="rule 1",
        company_info="Test Co.",
    )
    # Clear cache to force DB hit
    ai_engine._context_cache.clear()

    ctx = await ai_engine.get_context(async_db_session, str(agent.id))

    assert ctx is not None
    assert ctx["system_prompt"] == "test prompt"
    assert ctx["tone_of_voice"] == "friendly"
    assert ctx["rules"] == "rule 1"
    assert ctx["company_info"] == "Test Co."
    # Phase 05.1: колонка вернулась миграцией 018 (default 280) — get_context
    # теперь читает её из БД и прокидывает в build_system_prompt → <message_style>.
    assert ctx["max_message_length"] == 280
    # webhook_functions выпилен из возвращаемого dict-а
    assert "webhook_functions" not in ctx


async def test_get_context_returns_none_for_missing(async_db_session):
    """Несуществующий context_id → None (брендового fallback больше нет)."""
    from app.services.ai_engine import ai_engine

    ai_engine._context_cache.clear()
    ctx = await ai_engine.get_context(async_db_session, "00000000-0000-0000-0000-000000000000")

    assert ctx is None


async def test_get_context_returns_none_for_empty_id(async_db_session):
    """Пустой context_id → None, без обращения к БД."""
    from app.services.ai_engine import ai_engine

    ctx = await ai_engine.get_context(async_db_session, None)
    assert ctx is None

    ctx = await ai_engine.get_context(async_db_session, "")
    assert ctx is None


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 11 — PMT-01..07: golden-prompt assertions
#
# These tests pin the target behavior of `build_system_prompt` after Phase 11
# rewrites it. All behavior-dependent assertions are xfail(strict=False) so the
# suite stays green now and tests flip PASSING in Wave 3 (Plan 11-03).
#
# Skip/xfail strategy by test:
#   PMT-01 (block order)         → xfail: new blocks <task_audience>/<dialogue_flow>/
#                                  <arguments_facts> not yet in build_system_prompt
#   PMT-02 (tone single source)  → xfail: tone_preset field not yet read
#   PMT-03 (dialogue_flow render)→ xfail: dialogue_flow field not yet read
#   PMT-04 (arguments_facts)     → xfail: arguments_facts field not yet read
#   PMT-05 (rules dedup)         → xfail: dedup logic not yet implemented
#   PMT-06 (task from campaign)  → xfail: primary_goal/task_audience blocks not yet in
#   PMT-07 (brief excluded)      → NOT xfail: structural/contract assertion, passes today
# ═══════════════════════════════════════════════════════════════════════════════

# Detect which Phase 11 features are implemented to drive xfail conditions.
# We use source inspection to avoid importing non-existent symbols.
from app.services.ai_engine import AIEngine as _AIEngine
_BUILD_SRC = inspect.getsource(_AIEngine.build_system_prompt)
_PHASE11_PROMPT_IMPLEMENTED = (
    "tone_preset" in _BUILD_SRC
    and "dialogue_flow" in _BUILD_SRC
    and "arguments_facts" in _BUILD_SRC
    and "task_audience" in _BUILD_SRC
)
_DEDUP_IMPLEMENTED = "dedup" in _BUILD_SRC or "seen_rules" in _BUILD_SRC


def _make_full_context(
    *,
    tone_preset: str | None = "Friendly",
    response_speed: str | None = "human",
    primary_goal: str | None = "Qualify grain sellers",
    audience_hints: str | None = "Farmers in Krasnodar region",
    dialogue_flow: list | None = None,
    arguments_facts: str | None = "We pay in 2 days.",
    campaign_rules: str | None = "Never pressure. Reply briefly.",
    agent_rules: str | None = "Не давить.",
) -> dict:
    """Build a fully-populated agent+campaign context for PMT tests.

    Uses the current context schema expected by build_system_prompt: a flat dict
    with agent fields at the top level and campaign sub-dict under 'campaign'.
    Phase 11 fields are included even if build_system_prompt ignores them today
    (they'll be picked up after Plan 11-03).
    """
    if dialogue_flow is None:
        dialogue_flow = [
            {"title": "Open", "instruction": "Respond to first reaction, no pressure."},
            {"title": "Qualify", "instruction": "Ask one question at a time."},
        ]

    return {
        # Agent-level fields (existing)
        "system_prompt": "You are a sales agent for AGS Foods.",
        "company_info": "AGS Foods — grain trading company.",
        "product_info": "We buy sunflower and wheat.",
        "rules": agent_rules or "",
        "tone_of_voice": "professional",
        "voice_baseline": "Professional",  # legacy, should be overridden by tone_preset
        "tone_spec": {"formal": 3, "warm": 2, "brief": 4},  # legacy sliders
        "mirror_language": True,
        "allow_emoji": False,
        "max_message_length": 0,
        "banlist": [],
        # Phase 11 agent fields (new — may be ignored until Plan 11-03)
        "tone_preset": tone_preset,
        "response_speed": response_speed,
        # Campaign sub-dict (existing fields)
        "campaign": {
            "lead_trigger_hint": "Contact agrees to sell.",
            "handoff_trigger_hint": "Contact asks for a human.",
            "finish_trigger_hint": "Contact says goodbye.",
            # Phase 11 campaign fields (new)
            "primary_goal": primary_goal,
            "audience_hints": audience_hints,
            "dialogue_flow": dialogue_flow,
            "arguments_facts": arguments_facts,
            "campaign_rules": campaign_rules,
        },
    }


# ── PMT-01: block order ────────────────────────────────────────────────────────

@pytest.mark.xfail(
    not _PHASE11_PROMPT_IMPLEMENTED,
    reason="Phase 11 pending: build_system_prompt does not yet include Phase 11 blocks",
    strict=False,
)
async def test_prompt_block_order():
    """PMT-01: Full context → Phase 11 block order per BRIEF §7.

    Expected order: <role> < <company> < <product> < <tone> < <task_audience>
                    < <dialogue_flow> < <arguments_facts> < <rules> < <message_style>
    """
    from app.services.ai_engine import ai_engine

    ctx = _make_full_context()
    prompt = ai_engine.build_system_prompt(ctx, "Иван")

    def idx(tag: str) -> int:
        pos = prompt.find(tag)
        assert pos >= 0, f"PMT-01: tag '{tag}' not found in prompt"
        return pos

    assert idx("<role>") < idx("<company>"), "PMT-01: <role> must precede <company>"
    assert idx("<company>") < idx("<product>"), "PMT-01: <company> must precede <product>"
    assert idx("<product>") < idx("<tone>"), "PMT-01: <product> must precede <tone>"
    assert idx("<tone>") < idx("<task_audience>"), "PMT-01: <tone> must precede <task_audience>"
    assert idx("<task_audience>") < idx("<dialogue_flow>"), \
        "PMT-01: <task_audience> must precede <dialogue_flow>"
    assert idx("<dialogue_flow>") < idx("<arguments_facts>"), \
        "PMT-01: <dialogue_flow> must precede <arguments_facts>"
    assert idx("<arguments_facts>") < idx("<rules>"), \
        "PMT-01: <arguments_facts> must precede <rules>"
    assert idx("<rules>") < idx("<message_style>"), \
        "PMT-01: <rules> must precede <message_style>"


# ── PMT-02: tone single source ────────────────────────────────────────────────

@pytest.mark.xfail(
    not _PHASE11_PROMPT_IMPLEMENTED,
    reason="Phase 11 pending: build_system_prompt does not yet use tone_preset as sole tone source",
    strict=False,
)
async def test_tone_single_source():
    """PMT-02: tone_preset='Friendly' + residual legacy voice_baseline → ONLY preset rendered.

    The prompt MUST NOT contain 'Baseline persona' or 'Tone calibration' (legacy sources).
    """
    from app.services.ai_engine import ai_engine

    ctx = _make_full_context(tone_preset="Friendly")
    # Ensure legacy fields are populated (they should be suppressed)
    ctx["voice_baseline"] = "Professional"
    ctx["tone_spec"] = {"formal": 4, "warm": 1, "brief": 5}
    ctx["tone_of_voice"] = "Keep it brief and formal."

    prompt = ai_engine.build_system_prompt(ctx, "Иван")

    assert "Friendly" in prompt, \
        "PMT-02: tone_preset='Friendly' line not found in prompt"
    assert "Baseline persona" not in prompt, \
        "PMT-02: legacy 'Baseline persona' text must not appear when tone_preset is set"
    assert "Tone calibration" not in prompt, \
        "PMT-02: legacy 'Tone calibration' sliders must not appear when tone_preset is set"


# ── PMT-03: dialogue_flow render ──────────────────────────────────────────────

@pytest.mark.xfail(
    not _PHASE11_PROMPT_IMPLEMENTED,
    reason="Phase 11 pending: build_system_prompt does not yet render dialogue_flow",
    strict=False,
)
async def test_dialogue_flow_render():
    """PMT-03: dialogue_flow stages render as numbered list; old static goal absent.

    Given dialogue_flow=[{title:'Open', instruction:...}, {title:'Qualify', instruction:...}],
    the prompt must contain '1.' and '2.' stage markers and must NOT contain the old
    static _PROMPT_DIALOGUE_GOAL text ('move through three steps').
    """
    from app.services.ai_engine import ai_engine

    ctx = _make_full_context(
        dialogue_flow=[
            {"title": "Open", "instruction": "Short greeting, no pressure."},
            {"title": "Qualify", "instruction": "One question at a time."},
        ]
    )

    prompt = ai_engine.build_system_prompt(ctx, "Иван")

    assert "1." in prompt, \
        "PMT-03: numbered stage '1.' not found in rendered dialogue_flow"
    assert "2." in prompt, \
        "PMT-03: numbered stage '2.' not found in rendered dialogue_flow"
    assert "move through three steps" not in prompt, \
        "PMT-03: old static _PROMPT_DIALOGUE_GOAL ('move through three steps') " \
        "must not appear when dialogue_flow is provided"


# ── PMT-04: arguments_facts + anti-hallucination guard ───────────────────────

@pytest.mark.xfail(
    not _PHASE11_PROMPT_IMPLEMENTED,
    reason="Phase 11 pending: build_system_prompt does not yet render arguments_facts",
    strict=False,
)
async def test_arguments_facts_guard():
    """PMT-04: arguments_facts renders in prompt + anti-hallucination guard present.

    When arguments_facts is set, the rendered prompt must include the fact text
    AND an anti-hallucination instruction (e.g. 'не выдумывай' / 'stick to facts').
    """
    from app.services.ai_engine import ai_engine

    ctx = _make_full_context(arguments_facts="We pay in 2 working days. No hidden fees.")

    prompt = ai_engine.build_system_prompt(ctx, "Иван")

    assert "We pay in 2 working days" in prompt, \
        "PMT-04: arguments_facts text not rendered in prompt"
    # Anti-hallucination guard must appear near the arguments_facts block
    antihall_present = any(
        phrase in prompt
        for phrase in (
            "не выдумывай", "stick to facts", "don't make up", "не придумывай",
            "only from this block", "strictly from", "don't fill in details",
        )
    )
    assert antihall_present, \
        "PMT-04: anti-hallucination guard not found in prompt after arguments_facts block"


# ── PMT-05: rules dedup — behavioral core ─────────────────────────────────────

@pytest.mark.xfail(
    not _DEDUP_IMPLEMENTED,
    reason="Phase 11 pending: build_system_prompt does not yet deduplicate agent+campaign rules",
    strict=False,
)
async def test_rules_dedup_no_duplicate():
    """PMT-05 (behavioral core): agent.rules 'Не давить.' + campaign_rules 'Не давить.\\nОтвечать кратко.'
    → prompt.count('Не давить') == 1 AND 'Отвечать кратко' present.

    This is the behavioral core of Phase 11: no rule appears twice in the prompt
    regardless of whether it comes from agent-level or campaign-level rules.
    """
    from app.services.ai_engine import ai_engine

    ctx = _make_full_context(
        agent_rules="Не давить.",
        campaign_rules="Не давить.\nОтвечать кратко.",
    )

    prompt = ai_engine.build_system_prompt(ctx, "Иван")

    count = prompt.count("Не давить")
    assert count == 1, (
        f"PMT-05: 'Не давить' appears {count} times in prompt, expected exactly 1 "
        f"(dedup must merge agent+campaign rules, removing exact duplicates)"
    )
    assert "Отвечать кратко" in prompt, \
        "PMT-05: 'Отвечать кратко' (non-duplicate campaign rule) not found in prompt"


# ── PMT-06: task/audience from campaign ──────────────────────────────────────

@pytest.mark.xfail(
    not _PHASE11_PROMPT_IMPLEMENTED,
    reason="Phase 11 pending: build_system_prompt does not yet render task_audience from campaign",
    strict=False,
)
async def test_task_source_campaign():
    """PMT-06: primary_goal + audience_hints render in <task_audience>; who_is_agent stays identity-only.

    After Phase 11: campaign.primary_goal and campaign.audience_hints must appear
    in a <task_audience> block. The <role> block (who_is_agent / system_prompt) must
    NOT contain task or goal sentences — it should only contain identity description.
    """
    from app.services.ai_engine import ai_engine

    ctx = _make_full_context(
        primary_goal="Qualify grain sellers for purchase",
        audience_hints="Farmers in southern Russia with 200+ tonnes",
    )

    prompt = ai_engine.build_system_prompt(ctx, "Иван")

    assert "<task_audience>" in prompt, \
        "PMT-06: <task_audience> block not found in prompt"
    assert "Qualify grain sellers" in prompt, \
        "PMT-06: primary_goal text not rendered in <task_audience>"
    assert "southern Russia" in prompt, \
        "PMT-06: audience_hints text not rendered in <task_audience>"

    # who_is_agent (system_prompt) text should be in <role>, not carry the task description
    # The goal text should NOT be duplicated inside <role>
    role_start = prompt.find("<role>")
    role_end = prompt.find("</role>")
    if role_start >= 0 and role_end > role_start:
        role_block = prompt[role_start:role_end]
        assert "Qualify grain sellers" not in role_block, \
            "PMT-06: primary_goal text leaked into <role> block (should be in <task_audience> only)"


# ── PMT-07: brief excluded (structural/contract) ─────────────────────────────

async def test_brief_excluded():
    """PMT-07: a raw 'brief' string is NEVER an input to build_system_prompt.

    Structural assertion: build_system_prompt must NOT accept a positional 'brief'
    argument — the function signature takes only (self, context, contact_name).
    The brief (raw campaign description) must be decomposed into structured fields
    (primary_goal, audience_hints, etc.) before being passed to build_system_prompt.

    This test passes TODAY (no Phase 11 changes needed) — it pins the contract.
    """
    from app.services.ai_engine import AIEngine

    sig = inspect.signature(AIEngine.build_system_prompt)
    param_names = list(sig.parameters.keys())

    # 'self' + 'context' + 'contact_name' — that's all
    assert "brief" not in param_names, (
        f"PMT-07: 'brief' is a parameter of build_system_prompt — raw brief text "
        f"must NOT be a direct input. Decompose into structured context fields. "
        f"Current params: {param_names}"
    )
    assert "context" in param_names, \
        "PMT-07: build_system_prompt must accept a 'context' dict parameter"
    assert "contact_name" in param_names, \
        "PMT-07: build_system_prompt must accept 'contact_name' parameter"
