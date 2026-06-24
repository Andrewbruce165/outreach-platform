---
phase: 11-agent-campaign-field-split-and-prompt-assembly
plan: 03
type: execute
wave: 3
depends_on: ["11-02"]
files_modified:
  - app/services/ai_engine.py
  - app/services/listener.py
autonomous: true
requirements: [PMT-01, PMT-02, PMT-03, PMT-04, PMT-05, PMT-06, PMT-07, RT-01, D-03, D-06, D-12, D-14, D-15]
must_haves:
  truths:
    - "The system prompt renders blocks in the fixed BRIEF §7 order with exactly one source per block"
    - "Tone appears only in the [ТОН] block, derived solely from tone_preset"
    - "A rule written on both agent and campaign appears once (deduped)"
    - "Dialogue stages come from the campaign dialogue_flow, not a static hardcoded goal"
    - "AI reply delay honours the agent response_speed (manual uses response_delay_seconds)"
  artifacts:
    - path: "app/services/ai_engine.py"
      provides: "build_system_prompt §7 rewrite + get_context_for_conversation/get_context SELECT updates + _PROMPT_FACTS_GUARD"
    - path: "app/services/listener.py"
      provides: "response_speed-aware delay in schedule_ai_response + context-dict augmentation"
  key_links:
    - from: "app/services/ai_engine.py::get_context_for_conversation"
      to: "ai_contexts.tone_preset / campaigns.dialogue_flow"
      via: "SELECT into context dict"
      pattern: "tone_preset"
    - from: "app/services/listener.py::schedule_ai_response"
      to: "context.response_speed"
      via: "delay branch"
      pattern: "response_speed"
---

<objective>
Rewrite the system-prompt assembly to the fixed block order (BRIEF §7) with exactly one source per block, eliminating the duplicate/contradictory instructions that make GPT-5 mini drift. Collapse the 3-source tone block to a single tone_preset line, replace the static dialogue goal with per-campaign dialogue_flow, add an [АРГУМЕНТЫ И ФАКТЫ] block with an anti-hallucination guard, and dedup [ПРАВИЛА] across agent+campaign. Then wire response_speed into the listener debounce so the AI reply delay is configurable.

Purpose: This is the behavioral core of the phase — "no duplicate instructions in the prompt" proven by golden-prompt tests.
Output: rewritten build_system_prompt + updated context SELECTs + response_speed runtime. Flips PMT-01..07 and RT-01 green.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-CONTEXT.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-RESEARCH.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-PATTERNS.md
@app/services/ai_engine.py
@app/services/listener.py

<interfaces>
<!-- Grounded from live code 2026-06-24. -->

app/services/ai_engine.py:
  _PROMPT_PRODUCT_GUARD = (...)        # :397  (reuse phrasing for new _PROMPT_FACTS_GUARD)
  _PROMPT_DIALOGUE_GOAL = """..."""    # :402  (static 3-step goal -> REMOVE, replace with dialogue_flow)
  get_context_for_conversation         # :147  (SELECT a.tone_of_voice :185, a.voice_baseline :189; mapped tone_of_voice :227, voice_baseline :230)
  get_context (cached, TTL 60s)        # :466  (SELECT tone_of_voice :490, mapped :504; _context_cache :463; invalidate_context :521)
  build_system_prompt                  # :559  (tone_of_voice :577, voice_baseline :578; <tone> assembly :614-635; <product>+guard :611; <dialogue_goal> append :650)

Target block order (BRIEF §7 -> tags):
  <role>(ИДЕНТИЧНОСТЬ) -> <company> -> <product> -> <tone> -> <task_audience>(ЗАДАЧА+КОМУ ПИШЕМ)
  -> <dialogue_flow>(ХОД РАЗГОВОРА) -> <arguments_facts> -> [БАЗА ЗНАНИЙ: deferred, skip]
  -> <rules>(ПРАВИЛА, deduped) -> <signals>/<tools> -> <message_style>(ФОРМАТ ОТВЕТА)

_TONE_LINES dict (RESEARCH code example, Claude's discretion on exact wording):
  Friendly/Professional/Direct/Casual -> one-line tone instruction each.

app/services/listener.py:
  DEBOUNCE_MIN=20.0 :134 ; DEBOUNCE_MAX=180.0 :135 ; MAX_BUFFER_TIME=300.0 :136 (do NOT touch values)
  schedule_ai_response :209 ; MAX_BUFFER_TIME guard :224 ; delay calc :230:
    delay = min(random.uniform(self.DEBOUNCE_MIN, self.DEBOUNCE_MAX), self.MAX_BUFFER_TIME - buffer_age)
  context dict built in handle_incoming_message :848 (ai_context_id in scope at :826)

Campaign fields now in dict (post 11-02): dialogue_flow (list[dict]), arguments_facts, campaign_rules,
lead_trigger_hint (now carries migrated success_criteria), audience_hints, primary_goal.
Agent fields now in dict: tone_preset, response_speed, response_delay_seconds.
</interfaces>
</context>

<threat_model>
ASVS L1 surface for this plan:
- T1 Prompt-injection via user-supplied campaign facts/rules (arguments_facts, campaign_rules) feeding the system prompt: mitigated by (a) the [АРГУМЕНТЫ И ФАКТЫ] anti-hallucination guard _PROMPT_FACTS_GUARD ("use only these facts, do not invent"), (b) rendering user content inside a clearly-delimited block (<arguments_facts>...</arguments_facts>) so injected "ignore previous instructions" text sits as data within a labelled section rather than as top-level instruction, (c) the fixed block order keeps signals/tools/format blocks after user content so behavioral guardrails are not overridden by earlier free-text. No change to tool dispatch trust boundary (built-in tools still injected by ai_engine, not from user text). Task 1.
- T2 dialogue_flow content rendering: each stage's title/instruction is already length-capped by DialogueStage (11-02); render escapes nothing special (plain text into a labelled block) — acceptable for v1, content is workspace-owner authored, not third-party.
- T3 response_delay_seconds DoS (huge delay stalls replies): bounded at schema layer (le=3600, 11-02) AND the MAX_BUFFER_TIME cap is preserved in schedule_ai_response so no mode can exceed the existing buffer ceiling. Task 2.
- T4 Tone/instruction duplication regression (the original drift bug): mitigated by golden-prompt dedup tests (PMT-02/05) gating this plan. Task 1.
No new endpoints, no auth changes, no migration in this plan.
</threat_model>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Rewrite build_system_prompt to §7 order + single-source tone + dialogue_flow + facts guard + rules dedup</name>
  <read_first>
    - app/services/ai_engine.py:559-713 (full build_system_prompt: role_lines, <company>, <product>+guard, <tone> 3-source assembly :614-635, <rules> :637-638, <dialogue_goal> :649-651, signals/tools, <message_style>)
    - app/services/ai_engine.py:385-457 (module-level _PROMPT_* constants — where _PROMPT_FACTS_GUARD goes; _PROMPT_PRODUCT_GUARD phrasing to mirror)
    - BRIEF.md §7 (fixed block order) and 11-CONTEXT.md D-03/D-06/D-12/D-14
    - 11-PATTERNS.md §"build_system_prompt" (block-conditional skeleton to preserve; what to DELETE)
    - tests/test_ai_engine.py (PMT-01..07 xfail tests to flip green)
  </read_first>
  <behavior>
    - Blocks appended in order: <role> -> <company> -> <product> -> <tone> -> <task_audience> -> <dialogue_flow> -> <arguments_facts> -> <rules> -> signals/tools -> <message_style>. Each block conditional on its field being non-empty.
    - <tone>: derived ONLY from context["tone_preset"] via _TONE_LINES dict (1-2 lines); no voice_baseline/tone-slider/tone_of_voice text anywhere; tone text never appears inside <rules>.
    - <task_audience>: rendered from campaign primary_goal + audience_hints; identity (who_is_agent) carries no task/goal sentence.
    - <dialogue_flow>: numbered stages from context["campaign"]["dialogue_flow"] (skip stages with empty instruction); the old _PROMPT_DIALOGUE_GOAL static text is gone.
    - <arguments_facts>: arguments_facts content + _PROMPT_FACTS_GUARD anti-hallucination line.
    - <rules>: agent rules + campaign_rules through line-level dedup (strip+lower compare, dict.fromkeys order preserve, agent first); a rule on both sides appears once.
    - brief raw text is never an input (signature takes structured context only).
  </behavior>
  <action>
    Keep the blocks: list[str] + conditional-append + "\n\n".join(blocks) skeleton. Reorder appends to BRIEF §7. DELETE the entire 3-source <tone> assembly (:614-635) and replace with a single-source render: read tone_preset from context, look up _TONE_LINES (add this module-level dict with Friendly/Professional/Direct/Casual entries), append <tone> only if a preset is set. Remove the now-dead local reads of tone_of_voice/voice_baseline (:577-578). Add a module-level _PROMPT_FACTS_GUARD constant in the same style as _PROMPT_PRODUCT_GUARD (:397) using the proven "strictly from this block, don't invent" phrasing; append an <arguments_facts> block (content + guard) right after <dialogue_flow>. Add a numbered-stage render of campaign dialogue_flow as the <dialogue_flow> block and DELETE _PROMPT_DIALOGUE_GOAL (:402) plus its <dialogue_goal> append (:650). Add a module-level helper _dedup_rules(*texts) (split lines, strip, skip blanks, dedup by lowercased key, preserve order) and build <rules> from _dedup_rules(agent_rules, campaign_rules) joined by newline — agent rules first. Add the <task_audience> block from campaign primary_goal + audience_hints. Do NOT render any [БАЗА ЗНАНИЙ] block (deferred). All new guard/text constants go module-level as _PROMPT_*, not inline.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_ai_engine.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - build_system_prompt output orders blocks <role> < <company> < <product> < <tone> < <task_audience> < <dialogue_flow> < <arguments_facts> < <rules> < <message_style> (PMT-01)
    - Prompt contains the tone_preset line and NOT "Baseline persona" / "Tone calibration" / slider text (PMT-02)
    - _PROMPT_DIALOGUE_GOAL removed; dialogue_flow renders numbered "1." "2." stages (PMT-03)
    - _PROMPT_FACTS_GUARD exists and appears inside <arguments_facts> (PMT-04)
    - A rule present in both agent.rules and campaign_rules appears exactly once; test asserts prompt.count("Не давить") == 1 (PMT-05)
    - tests/test_ai_engine.py PMT-01..07 pass (xfail markers removed/flipped to green)
    - grep of ai_engine.py shows no remaining voice_baseline/tone_of_voice/_PROMPT_DIALOGUE_GOAL references
  </acceptance_criteria>
  <done>Single-source, fixed-order, deduped system prompt; the drift bug is structurally impossible per golden tests.</done>
</task>

<task type="auto">
  <name>Task 2: Update context SELECTs to feed new fields + remove tone COALESCE</name>
  <read_first>
    - app/services/ai_engine.py:147-268 (get_context_for_conversation SELECT + context dict + context["campaign"] sub-dict)
    - app/services/ai_engine.py:466-519 (get_context cached SELECT + ordinal-position warning comment :483-485)
    - 11-PATTERNS.md §"get_context_for_conversation / get_context" (add columns, remove tone COALESCE)
  </read_first>
  <action>
    In get_context_for_conversation SELECT: add a.tone_preset, a.response_speed, a.response_delay_seconds (agent) and c.dialogue_flow, c.arguments_facts, c.campaign_rules (campaign). Remove the tone columns from the SELECT (a.tone_of_voice, a.voice_baseline, and the tone JSONB) and the corresponding dict keys (:227-231) — they no longer exist after 11-02. Map the new agent fields into the top-level context dict ("tone_preset", "response_speed", "response_delay_seconds") and the new campaign fields into context["campaign"] ("dialogue_flow", "arguments_facts", "campaign_rules") following the one-line-per-field style; default response_speed to "human" when NULL and dialogue_flow to [] when NULL. In get_context (cached path) widen the SELECT to include tone_preset, response_speed, response_delay_seconds (the listener pulls speed from here per RESEARCH Open-Q1) and remove the dead tone_of_voice read; mind the ordinal-position warning comment when changing the column list. Ensure both functions return dicts whose keys exactly match what build_system_prompt and listener now read.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_ai_engine.py -q</automated>
  </verify>
  <acceptance_criteria>
    - get_context_for_conversation SELECT lists a.tone_preset, a.response_speed, a.response_delay_seconds, c.dialogue_flow, c.arguments_facts, c.campaign_rules
    - No SELECT or dict reference to voice_baseline / tone_of_voice / tone JSONB remains in ai_engine.py (grep 0)
    - context["response_speed"] defaults to "human" when DB value NULL; context["campaign"]["dialogue_flow"] defaults to []
    - get_context cached SELECT includes tone_preset/response_speed/response_delay_seconds
    - test_ai_engine.py suite green (prompt tests now read real dict keys)
  </acceptance_criteria>
  <done>Both context loaders feed the new single-source fields; no dead tone reads.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Wire response_speed into listener debounce (RT-01)</name>
  <read_first>
    - app/services/listener.py:134-136 (DEBOUNCE_MIN/MAX/MAX_BUFFER_TIME constants — do NOT change values), :209-233 (schedule_ai_response, buffer_age guard :224, delay calc :230)
    - app/services/listener.py:826 (ai_context_id in scope), :848-860 (context dict build + schedule_ai_response call)
    - 11-RESEARCH.md §"Open Questions" Q1 + Q3 (defaults: instant~0-2s, human=current range, slow larger, manual=response_delay_seconds), §"Common Pitfalls" Pitfall 1 (debounce != queue rate-limit)
    - tests/test_listener_response_speed.py (RT-01 test to flip green)
  </read_first>
  <behavior>
    - schedule_ai_response computes delay branched on context["response_speed"]: instant -> ~0-2s, human (or missing/NULL, default) -> existing random.uniform(DEBOUNCE_MIN, DEBOUNCE_MAX), slow -> a larger range (Claude's discretion, e.g. 3x or fixed 300-600), manual -> context["response_delay_seconds"].
    - The MAX_BUFFER_TIME - buffer_age cap is applied to EVERY mode (no mode exceeds the buffer ceiling).
    - DEBOUNCE_MIN/MAX/MAX_BUFFER_TIME class constants are unchanged; queue.py is untouched.
  </behavior>
  <action>
    In handle_incoming_message add "response_speed" and "response_delay_seconds" to the context dict built at :848, sourced from the agent via AIEngine.get_context (cached, widened in Task 2) keyed on ai_context_id — no new raw SELECT. In schedule_ai_response replace the single delay line (:230) with a branch on context.get("response_speed") or "human": compute a base delay per mode (instant ~uniform(0,2); human current DEBOUNCE_MIN..MAX; slow larger range; manual = float(context.get("response_delay_seconds") or DEBOUNCE_MIN)), then apply the existing cap delay = min(base, self.MAX_BUFFER_TIME - buffer_age). Keep the early-return MAX_BUFFER_TIME guard (:224). Do NOT touch DEBOUNCE_* constant values and do NOT touch queue.py (Pitfall 1 — different subsystem).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_listener_response_speed.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - schedule_ai_response branches on response_speed; manual mode -> delay == response_delay_seconds (capped by MAX_BUFFER_TIME)
    - instant -> delay <= ~2s; human/missing -> delay within DEBOUNCE_MIN..DEBOUNCE_MAX
    - The min(..., MAX_BUFFER_TIME - buffer_age) cap is applied in every branch
    - context dict in handle_incoming_message includes response_speed + response_delay_seconds
    - DEBOUNCE_MIN/MAX/MAX_BUFFER_TIME values unchanged; no diff in queue.py
    - tests/test_listener_response_speed.py passes
  </acceptance_criteria>
  <done>AI reply delay is configurable per agent without touching queue rate-limit constants.</done>
</task>

</tasks>

<verification>
- Full suite via test-overlay green: PMT-01..07 (test_ai_engine.py) + RT-01 (test_listener_response_speed.py) now pass; migration + router suites still green.
- grep ai_engine.py + listener.py: no voice_baseline / tone_of_voice / _PROMPT_DIALOGUE_GOAL references; queue.py unchanged.
- Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` exits 0.
</verification>

<success_criteria>
- System prompt blocks render in BRIEF §7 order, one source per block (PMT-01).
- Tone single-source from tone_preset; absent from rules (PMT-02, D-03).
- dialogue_flow replaces static goal (PMT-03, D-06).
- arguments_facts block carries anti-hallucination guard (PMT-04, D-12).
- Duplicate rule appears once (PMT-05, D-14).
- response_speed controls AI reply delay; manual honours response_delay_seconds (RT-01, D-11).
</success_criteria>

<output>
After completion, create `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-03-SUMMARY.md`
</output>
