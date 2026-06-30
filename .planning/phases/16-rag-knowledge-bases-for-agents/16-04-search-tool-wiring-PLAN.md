---
phase: 16-rag-knowledge-bases-for-agents
plan: 04
type: execute
wave: 3
depends_on: ["16-01", "16-02"]
files_modified:
  - app/services/kb_search.py
  - app/services/ai_engine.py
  - tests/test_kb_search.py
  - tests/test_ai_engine_kb_tool.py
autonomous: true
requirements: [KB-05, KB-06]
must_haves:
  truths:
    - "kb_search returns the nearest chunks across all KBs attached to the agent, ordered by cosine distance, top-K + threshold applied"
    - "Search is workspace-isolated — a workspace never sees another workspace's chunks"
    - "The search_knowledge_base tool is offered to the model ONLY when the agent has ≥1 attached KB (D-04)"
    - "On a search_knowledge_base tool_call the engine appends a role:tool message with the chunks and runs a SECOND completion (data-tool, model continues) — it does NOT change conversation.status"
    - "An empty search result returns an explicit no-passages note so the model falls back to existing off-topic behavior instead of hallucinating"
  artifacts:
    - path: "app/services/kb_search.py"
      provides: "async kb_search() cosine query over attached KBs, workspace-filtered"
      contains: "cosine_distance"
    - path: "app/services/ai_engine.py"
      provides: "search_knowledge_base tool spec + gated registration + data-tool dispatch branch"
      contains: "search_knowledge_base"
  key_links:
    - from: "app/services/ai_engine.py generate_response (first OpenAI call)"
      to: "search_knowledge_base tool in request_params['tools']"
      via: "appended only when agent has ≥1 attached KB (D-04)"
      pattern: "search_knowledge_base"
    - from: "app/services/ai_engine.py dispatch loop"
      to: "two-pass response2 (role:tool message + second completion)"
      via: "search_knowledge_base call routed through the custom-call tool_results path, not _handle_builtin_signal"
      pattern: "tool_results|role.*tool"
    - from: "app/services/kb_search.py"
      to: "kb_chunks filtered by workspace_id + attached kb_ids"
      via: "KbChunk.embedding.cosine_distance(query_vec) ORDER BY ... LIMIT top_k"
      pattern: "workspace_id"
---

<objective>
Wire retrieval. Build `app/services/kb_search.py` (the cosine-distance query over
the union of KBs attached to the agent, workspace-filtered) and add the
`search_knowledge_base` data-tool to `ai_engine.generate_response`: register it
ONLY when the agent has ≥1 attached KB (D-04), and on a tool_call run the vector
search, append the chunks as a `role:"tool"` message, and let the existing
two-pass flow produce the final reply.

Purpose: this is the single genuinely-new wiring of the phase (RESEARCH "focus
review here"). KB-05 (retrieval visibly influences answers) and KB-06 (search
workspace isolation). The tool is a DATA tool — unlike the signal tools
(mark_as_lead / transfer_to_manager / finish_conversation) it must NOT terminate
the loop or touch `conversation.status`; it returns data and the model continues.

Output: `app/services/kb_search.py`, the gated tool registration + dispatch branch
in `ai_engine.py`, and the search + ai-engine-tool tests turn GREEN.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-CONTEXT.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-01-SUMMARY.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-02-SUMMARY.md

<interfaces>
<!-- The EXACT integration points. Read these before editing. -->

Cosine query pattern (RESEARCH Pattern 2):
```python
from sqlalchemy import select
from app.models import KbChunk
stmt = (
    select(KbChunk.content, KbChunk.document_id,
           KbChunk.embedding.cosine_distance(query_vec).label("distance"))
    .where(KbChunk.workspace_id == workspace_id, KbChunk.kb_id.in_(attached_kb_ids))
    .order_by(KbChunk.embedding.cosine_distance(query_vec))
    .limit(top_k)
)
rows = (await db.execute(stmt)).all()
hits = [r for r in rows if r.distance <= max_distance]   # distance, lower = better (Pitfall 4)
```

ai_engine generate_response — the EXACT lines to edit:
- Line 39: `client = AsyncOpenAI(...)` (reuse for embeddings via kb_ingest.embed_texts).
- Line 45: `BUILT_IN_TOOL_NAMES = {"mark_as_lead", "transfer_to_manager", "finish_conversation"}`
  — search_knowledge_base is NOT a built-in signal; it must NOT be added here.
- Lines 221-250: the `context` dict carries `context["workspace_id"]` and `context["agent_id"]`
  (both populated by get_context_for_conversation) — kb_search needs both.
- Lines 1185-1205: tools assembled as `all_tools = build_builtin_tools(campaign) + self.build_tools(custom_tools_spec)`;
  passed to the first OpenAI call as `request_params["tools"]`. ADD the gated KB tool here.
- Lines 1255-1270: dispatch loop splits tool_calls into `builtin_signals` vs `custom_calls`
  by `func_name in BUILT_IN_TOOL_NAMES`. search_knowledge_base falls into the `else` (custom_calls)
  bucket — but it must be RESOLVED LOCALLY (vector search), not via execute_webhook.
- Lines 1310-1314: the two-pass flow is gated `if not custom_calls: return text_content`.
  A search_knowledge_base call MUST land in custom_calls so this gate lets it through to
  the two-pass block.
- Lines 1316-1339: per-custom-call, `func_config` is looked up in `custom_tools_spec`;
  if found → `execute_webhook`. ADD a branch: if `func_name == "search_knowledge_base"`,
  resolve via kb_search instead of execute_webhook.
- Lines 1342-1383: the two-pass block appends `response_message` + one `role:"tool"` message
  per custom_call (`content = tool_results[tool_call.id]`) and runs `response2`. This runs
  UNCHANGED for the KB tool — the model continues with the chunks in context.

Empty-result fallback (Pitfall 5): on zero hits return
`json.dumps({"results": [], "note": "no relevant passages found"}, ensure_ascii=False)`
so the model inherits the existing off-topic behavior (ai_engine _PROMPT_OUT_OF_SCOPE).

Tool spec (RESEARCH Example 3) — register only when agent has ≥1 attached KB:
```python
SEARCH_KB_TOOL = {"type": "function", "function": {
    "name": "search_knowledge_base",
    "description": "Search the agent's attached knowledge bases for relevant reference material. "
                   "Call this when the contact asks something that may be answered by stored "
                   "documents/facts. Returns relevant passages; use them to answer.",
    "parameters": {"type": "object",
        "properties": {"query": {"type": "string", "description": "Natural-language search query."}},
        "required": ["query"]}}}
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: kb_search.py — cosine query over attached KBs, workspace-filtered</name>
  <read_first>
    - app/models/__init__.py (KbChunk, AgentKnowledgeBase definitions from 16-01)
    - app/services/kb_ingest.py (embed_texts — used to embed the query)
    - app/config.py (kb_search_top_k, kb_search_max_distance from 16-02)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (Pattern 2 cosine query, Pitfall 4 threshold, §Index Choice)
    - tests/test_kb_search.py (RED: test_cosine_search_orders_by_distance, test_search_workspace_isolated)
  </read_first>
  <files>app/services/kb_search.py, tests/test_kb_search.py</files>
  <behavior>
    - kb_search returns chunks ordered ascending by cosine distance (nearest first), capped at top_k, dropping any with distance > max_distance.
    - Searching with workspace A's id never returns workspace B's chunks even if kb_ids overlap by accident (defense-in-depth: filter BOTH workspace_id AND kb_id).
    - When the agent has zero attached KBs (no kb_ids) → returns [] without querying.
    - The agent's attached kb_ids are resolved from agent_knowledge_bases scoped to the workspace.
  </behavior>
  <action>
    Create `app/services/kb_search.py`:
    - `async def attached_kb_ids(db, workspace_id, agent_id) -> list[UUID]` — SELECT kb_id
      FROM agent_knowledge_bases WHERE agent_id=:aid AND workspace_id=:wid.
    - `async def kb_search(db, workspace_id, agent_id, query, top_k=None, max_distance=None) -> list[dict]`:
      1. Resolve `kb_ids = await attached_kb_ids(...)`. If empty → return `[]` (no query).
      2. Embed the query: `vecs = await kb_ingest.embed_texts([query], settings.openai_embedding_model); query_vec = vecs[0]`.
      3. Run the RESEARCH Pattern 2 cosine query — `select(KbChunk.content, KbChunk.document_id,
         KbChunk.embedding.cosine_distance(query_vec).label("distance"))
         .where(KbChunk.workspace_id == workspace_id, KbChunk.kb_id.in_(kb_ids))
         .order_by(KbChunk.embedding.cosine_distance(query_vec)).limit(top_k or settings.kb_search_top_k)`.
         JOIN kb_documents to fetch `document_name` for the hit (LEFT JOIN on document_id).
      4. Filter `distance <= (max_distance or settings.kb_search_max_distance)` (Pitfall 4 —
         distance, lower = better; do NOT invert).
      5. Return `[{"content": ..., "document_id": str(...), "document_name": ..., "distance": float(...)}]`.
    - The function must call `kb_ingest.embed_texts` by module reference so tests can
      monkeypatch the embedder; OR accept an injectable embed function. Document the contract.
    Flesh out `test_cosine_search_orders_by_distance` (hand-crafted unit vectors, monkeypatched
    embedder, known ordering) and `test_search_workspace_isolated` (two workspaces, search A,
    assert zero B rows) to GREEN.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_kb_search.py -x -q 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/kb_search.py` defines `async def kb_search(...)` and `async def attached_kb_ids(...)`
    - The query uses `KbChunk.embedding.cosine_distance(...)` in both ORDER BY and the distance label
    - The WHERE clause filters BOTH `workspace_id` and `kb_id.in_(...)`
    - Zero attached KBs → returns `[]` without an OpenAI/embed call
    - Distance filter is `distance <= max_distance` (not `>=`, not similarity-inverted)
    - `pytest tests/test_kb_search.py` exits 0 (KB-05 ordering + KB-06 isolation GREEN)
  </acceptance_criteria>
  <done>kb_search returns workspace-isolated nearest chunks across attached KBs, ordered by cosine distance with top-K + threshold; KB-05/KB-06 search tests GREEN.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: search_knowledge_base data-tool in ai_engine.generate_response</name>
  <read_first>
    - app/services/ai_engine.py (lines 39, 45-67 tool-name constants; 1140-1205 context resolve + tool assembly; 1232-1383 dispatch loop + two-pass flow — read this whole span carefully)
    - app/services/kb_search.py (kb_search from Task 1)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-RESEARCH.md (§Code Examples Example 3, Anti-Patterns "Treating search_knowledge_base like a signal-tool", Pitfall 5)
    - tests/test_ai_engine_kb_tool.py (RED: test_tool_gated_on_attached_kb, test_search_kb_continues_conversation)
  </read_first>
  <files>app/services/ai_engine.py, tests/test_ai_engine_kb_tool.py</files>
  <behavior>
    - When the conversation's agent has ≥1 attached KB, the first OpenAI request's `tools` array includes the `search_knowledge_base` function spec; when it has 0, the spec is absent.
    - When the model returns a `search_knowledge_base` tool_call, the engine: (a) runs kb_search with the conversation's workspace_id + agent_id + the tool's `query` arg, (b) appends the model message + a `role:"tool"` message whose content is the JSON of the hits, (c) issues a SECOND completion and returns its content. conversation.status is NOT changed.
    - Zero hits → the role:tool content is the explicit no-passages note; the model still gets a second pass.
    - search_knowledge_base in parallel with a signal tool: the signal-tool priority dispatch is unaffected (KB tool is a data path); if finish/handoff fires, that terminating behavior still wins per existing logic.
  </behavior>
  <action>
    Edit `app/services/ai_engine.py` (do NOT add `search_knowledge_base` to BUILT_IN_TOOL_NAMES):
    1. Add the `SEARCH_KB_TOOL` spec constant near the other tool constants (after line ~82),
       using the EXACT spec from the interfaces block above. Also define
       `SEARCH_KB_TOOL_NAME = "search_knowledge_base"`.
    2. In `generate_response`, after `context`/`campaign` are resolved (the context dict carries
       `context["workspace_id"]` and `context["agent_id"]`, lines ~1144-1158) and BEFORE the first
       OpenAI call, resolve whether to offer the tool:
       - `agent_id = context.get("agent_id"); workspace_id = context.get("workspace_id")`
       - `kb_ids = await kb_search.attached_kb_ids(session, workspace_id, agent_id) if agent_id else []`
       - `has_kb = bool(kb_ids)`
    3. Where `all_tools = builtin_tools + custom_tools` is built (line ~1188), append the KB tool
       ONLY when `has_kb` (D-04): `if has_kb: all_tools = all_tools + [SEARCH_KB_TOOL]`.
       (Anti-pattern: never register it unconditionally.)
    4. In the dispatch loop (lines 1255-1270): `search_knowledge_base` is NOT in
       BUILT_IN_TOOL_NAMES, so it already lands in `custom_calls`. Good — leave the split as-is.
    5. In the per-custom-call resolution (lines 1316-1339), add a branch BEFORE the
       `func_config in custom_tools_spec` lookup:
       ```python
       if func_name == SEARCH_KB_TOOL_NAME:
           hits = await kb_search.kb_search(
               db=session, workspace_id=workspace_id, agent_id=agent_id,
               query=func_args.get("query", ""),
           )
           tool_results[tool_call.id] = json.dumps(
               {"results": hits} if hits else {"results": [], "note": "no relevant passages found"},
               ensure_ascii=False,
           )
           continue   # do NOT fall through to the webhook lookup
       ```
       This guarantees the call's tool_call.id is in `tool_results`, so the existing two-pass
       block (lines 1342-1383) appends its `role:"tool"` message and the `response2` flow runs
       UNCHANGED → the model continues with the chunks in context.
    6. CRITICAL — the two-pass gate at line ~1310 is `if not custom_calls: return text_content`.
       Because search_knowledge_base is in custom_calls, this gate already passes through to the
       two-pass block. Confirm no other early-return short-circuits a KB-only tool_call (e.g.
       the `final_status in ("handoff","finished")` return at ~1299 only fires for signal tools —
       a KB-only response has `final_status=None`, so it proceeds correctly).
    7. Wrap the kb_search call defensively — on exception, log and set the tool_result to the
       no-passages note (never crash the reply; mirror the engine's existing resilient style).
    Flesh out the two RED tests to GREEN with a mocked OpenAI client:
    `test_tool_gated_on_attached_kb` (assert SEARCH_KB_TOOL present iff ≥1 KB attached) and
    `test_search_kb_continues_conversation` (mock first completion returns a
    search_knowledge_base tool_call with a seeded query → assert a role:"tool" message with the
    chunk text is appended AND a second completion is requested AND conversation.status unchanged).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_ai_engine_kb_tool.py -x -q 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/ai_engine.py` defines `SEARCH_KB_TOOL` and references `search_knowledge_base`; the name is NOT added to `BUILT_IN_TOOL_NAMES`
    - The KB tool is appended to `all_tools` only inside an `if has_kb` / `if kb_ids` gate (grep the conditional)
    - The dispatch loop has a `func_name == "search_knowledge_base"` branch that calls `kb_search.kb_search` and writes `tool_results[tool_call.id]` then `continue`s (does NOT call execute_webhook for it)
    - Empty hits produce the `{"results": [], "note": "no relevant passages found"}` tool_result
    - No code path sets `conversation.status` for a KB-only tool_call
    - `pytest tests/test_ai_engine_kb_tool.py` exits 0 (both tests GREEN)
  </acceptance_criteria>
  <done>search_knowledge_base is gated on ≥1 attached KB, resolves via kb_search, returns chunks as a role:tool message, and the model continues via the two-pass flow without touching conversation.status; KB-05 engine tests GREEN.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_kb_search.py tests/test_ai_engine_kb_tool.py` GREEN.
- Full suite GREEN after the wave: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` (the existing ai_engine signal-tool tests must NOT regress — the KB branch is additive).
- grep confirms the tool is gated and routed as a data-tool, not a signal-tool.
</verification>

<success_criteria>
- kb_search: workspace-isolated nearest chunks across attached KBs, top-K + distance threshold (KB-06).
- search_knowledge_base offered only when ≥1 KB attached (D-04); resolved locally; two-pass continuation; no conversation.status change (KB-05).
- Empty result → explicit no-passages note (Pitfall 5).
- KB-05/KB-06 tests GREEN; no regression in existing signal-tool tests.
</success_criteria>

<output>
After completion, create `.planning/phases/16-rag-knowledge-bases-for-agents/16-04-SUMMARY.md`
</output>
