---
phase: 10-pool-visibility
plan: 03
type: execute
wave: 3
depends_on: [01, 02]
files_modified:
  - app/schemas/__init__.py
  - app/routers/campaigns.py
  - app/routers/senders.py
autonomous: true
requirements: [HLTH-03, POOLV-01, POOLV-02]
must_haves:
  truths:
    - "CampaignResponse exposes pool_health {active, paused, total, earliest_resume_at} computed in one pass in _campaign_to_response"
    - "Each attached_senders[] entry carries restriction_status + restricted_until"
    - "GET /senders/{slug}/restriction-events returns the workspace's events newest-first and never returns another workspace's events"
  artifacts:
    - path: "app/schemas/__init__.py"
      provides: "PoolHealth model, CampaignSenderAttach +2 fields, CampaignResponse +pool_health, RestrictionEventResponse"
      contains: "class PoolHealth"
    - path: "app/routers/campaigns.py"
      provides: "pool_health aggregate + per-sender enrichment in _campaign_to_response/_build_attached_senders"
      contains: "pool_health"
    - path: "app/routers/senders.py"
      provides: "GET /senders/{slug}/restriction-events endpoint"
      contains: "restriction-events"
  key_links:
    - from: "app/routers/campaigns.py::_campaign_to_response"
      to: "CampaignResponse.pool_health"
      via: "aggregate SELECT over campaign_senders JOIN senders, mapped to PoolHealth"
      pattern: "pool_health\\s*="
    - from: "app/routers/senders.py endpoint"
      to: "sender_restriction_events"
      via: "_load_sender_by_slug (workspace-scoped) then SELECT events WHERE sender_id ORDER BY created_at DESC"
      pattern: "_load_sender_by_slug"
---

<objective>
Завершить бэкенд фазы: расширить ответ кампании агрегатом `pool_health` (POOLV-01) и пер-sender restriction-полями (POOLV-02), и добавить read-эндпоинт истории restriction-событий по аккаунту (HLTH-03). Всё на готовых паттернах computed-полей и workspace-scoped slug-lookup — pure additive.

Purpose: Сделать «частичную паузу» кампании видимой (главный UX-сигнал фазы — K из N аккаунтов на паузе до T) и дать команде историю ограничений по конкретному аккаунту. Бейдж green/yellow/red выводится НА ФРОНТЕ из числового pool_health (API остаётся presentation-free).
Output: схемы (PoolHealth, RestrictionEventResponse, +2 поля в CampaignSenderAttach, +pool_health в CampaignResponse), врезка в campaigns.py, новый эндпоинт в senders.py.

OQ#4 decision adopted: earliest_resume_at = MIN(restricted_until) среди ограниченных senders (recheck-горизонт). Для frozen это «до проверки в T», не «возобновится в T» — словесная деталь живёт во фронте (Plan 04).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/10-pool-visibility/10-RESEARCH.md
@.planning/phases/10-pool-visibility/10-PATTERNS.md

<interfaces>
<!-- Contracts produced by this plan (consumed by Wave 4 frontend + Wave 1 tests). -->

PoolHealth: {active: int, paused: int, total: int, earliest_resume_at: datetime | None}
CampaignSenderAttach gains: restriction_status: Literal["none","spam_limited","frozen"]="none"; restricted_until: Optional[datetime]=None
CampaignResponse gains: pool_health: PoolHealth
RestrictionEventResponse (from_attributes): id, event_type, source, category, restricted_until, raw_text, activity_slice, proxy, created_at
GET /senders/{slug}/restriction-events → list[RestrictionEventResponse] newest-first, workspace-scoped, auth_dep required.

Available from Plan 02: SenderRestrictionEvent ORM model; table sender_restriction_events.
Badge mapping (frontend, NOT API): paused==0→green; 0<paused<total→yellow; paused==total && total>0→red.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Schemas — PoolHealth, RestrictionEventResponse, CampaignSenderAttach +2 fields, CampaignResponse +pool_health</name>
  <read_first>
    - app/schemas/__init__.py L133-134 (SenderResponse restriction fields — COPY VERBATIM for name/type consistency), L574-591 (CampaignSenderAttach current shape + @computed_field id), L685-729 (CampaignResponse tail + model_config ConfigDict from_attributes L687, attached_senders/is_exhausted L726-727)
    - .planning/phases/10-pool-visibility/10-PATTERNS.md §MOD schemas (exact field placements) + §Pydantic response sub-model
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Pydantic schema additions
  </read_first>
  <action>
    In app/schemas/__init__.py: (1) Add `class PoolHealth(BaseModel)` with fields active:int, paused:int, total:int, earliest_resume_at:Optional[datetime]=None (exact D-08 names). (2) In CampaignSenderAttach (L574) add the two restriction fields by COPYING VERBATIM from SenderResponse L133-134: `restriction_status: Literal["none","spam_limited","frozen"] = "none"` and `restricted_until: Optional[datetime] = None`; keep the existing @computed_field id property. (3) In CampaignResponse (L685) add `pool_health: PoolHealth` near attached_senders/is_exhausted (L726-727). (4) Add `class RestrictionEventResponse(BaseModel)` with `model_config = ConfigDict(from_attributes=True)` mirroring the table columns: id:UUID, event_type:str, source:str, category:str, restricted_until:Optional[datetime], raw_text:Optional[str], activity_slice:Optional[dict], proxy:Optional[dict], created_at:datetime. Use the existing imports (Literal, Optional, datetime, UUID, ConfigDict, computed_field already present in the module).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.schemas import PoolHealth, RestrictionEventResponse, CampaignResponse, CampaignSenderAttach; assert 'restriction_status' in CampaignSenderAttach.model_fields and 'pool_health' in CampaignResponse.model_fields; print('ok')"</automated>
  </verify>
  <acceptance_criteria>
    - Import of PoolHealth, RestrictionEventResponse, CampaignResponse, CampaignSenderAttach from app.schemas succeeds.
    - `CampaignSenderAttach.model_fields` contains restriction_status and restricted_until; `CampaignResponse.model_fields` contains pool_health.
    - restriction_status field def in CampaignSenderAttach is byte-identical (Literal values, default "none") to SenderResponse — `grep -A1 'restriction_status: Literal' app/schemas/__init__.py` shows the same Literal in both definitions.
    - RestrictionEventResponse has `model_config = ConfigDict(from_attributes=True)`.
  </acceptance_criteria>
  <done>All four schema additions present and importable; restriction field defs reuse SenderResponse names/types verbatim.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: campaigns.py — pool_health aggregate + per-sender enrichment</name>
  <behavior>
    - _build_attached_senders returns each CampaignSenderAttach enriched with restriction_status + restricted_until from the joined senders row.
    - _campaign_to_response computes pool_health in one aggregate pass: total = COUNT(*), active = COUNT FILTER restriction_status='none', paused = COUNT FILTER restriction_status<>'none', earliest_resume_at = MIN(restricted_until) FILTER restriction_status<>'none'.
    - all-active pool → {active:N, paused:0, total:N, earliest_resume_at:None}; partial → paused=K, earliest_resume_at=MIN; all-paused → active:0, paused=total.
  </behavior>
  <read_first>
    - app/routers/campaigns.py L196-227 (_build_attached_senders current SELECT + CampaignSenderAttach construction), L230-277 (_campaign_to_response: calls _build_attached_senders L233, _compute_is_exhausted L237-239 precedent, CampaignResponse constructor L273)
    - .planning/phases/10-pool-visibility/10-PATTERNS.md §MOD campaigns.py (exact insertion points) + §Computed/derived value in _campaign_to_response
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Pattern 3 (aggregate SQL L170-180 + 3-state mapping)
    - tests/test_pool_health.py (Wave 1 RED stubs this task turns GREEN)
  </read_first>
  <action>
    In _build_attached_senders (campaigns.py:196): extend the SELECT to JOIN senders s ON s.id = cs.sender_id and add s.restriction_status, s.restricted_until to the projection; pass them into each CampaignSenderAttach(... restriction_status=row[...], restricted_until=row[...]). Preserve the existing locked_by subqueries and ORDER BY cs.added_at.

    In _campaign_to_response (campaigns.py:230): after building `attached`, add a sibling aggregate query (RESEARCH §Pattern 3 SQL) — `SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE s.restriction_status='none') AS active, COUNT(*) FILTER (WHERE s.restriction_status<>'none') AS paused, MIN(s.restricted_until) FILTER (WHERE s.restriction_status<>'none') AS earliest_resume_at FROM campaign_senders cs JOIN senders s ON s.id=cs.sender_id WHERE cs.campaign_id=:cid`. Map the row into `pool_health = PoolHealth(active=..., paused=..., total=..., earliest_resume_at=...)` (mirror the _compute_is_exhausted "compute then pass to constructor" shape) and pass `pool_health=pool_health` into the CampaignResponse(...) constructor alongside attached_senders/is_exhausted. Empty pool (total=0) → PoolHealth(active=0, paused=0, total=0, earliest_resume_at=None). No fenced body in PLAN — copy SQL from RESEARCH per read_first.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_health.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_pool_health.py::test_pool_health_states -x` (test-overlay) exits 0 — all-active / partial / all-paused arithmetic correct, earliest_resume_at = MIN(restricted_until) among paused.
    - `pytest tests/test_pool_health.py::test_attached_senders_enriched -x` exits 0 — frozen sender's attached entry carries its restriction_status + restricted_until; active senders carry 'none'/None.
    - `grep -q "pool_health" app/routers/campaigns.py` succeeds; aggregate is one SELECT (no per-sender N+1 — `grep -c "restriction_status" app/routers/campaigns.py` reflects the join projection + aggregate, not a loop query).
    - No badge_state/color field added to the API response (presentation stays on the frontend).
  </acceptance_criteria>
  <done>CampaignResponse carries pool_health + enriched attached_senders; both pool-health tests GREEN.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: senders.py — GET /senders/{slug}/restriction-events</name>
  <behavior>
    - GET /senders/{slug}/restriction-events resolves the sender workspace-scoped via _load_sender_by_slug (404 SENDER_NOT_FOUND if not in this workspace), then returns its events ORDER BY created_at DESC as list[RestrictionEventResponse].
    - A sender belonging to another workspace is never returned (cross-tenant isolation).
    - Unauthenticated request is rejected (auth_dep, 403).
  </behavior>
  <read_first>
    - app/routers/senders.py L209-234 (_load_sender_by_slug — workspace-scoped, opaque 404 — REUSE directly), L400-408 (get_sender endpoint signature: auth_dep + get_db pattern), L626-631 (/senders/{slug}/spambot-check — slug-keyed read analog), L53 (AuthCtx, auth_dep import)
    - .planning/phases/10-pool-visibility/10-PATTERNS.md §NEW endpoint (exact signature + workspace-scoped lookup + ORDER BY created_at DESC matching idx_sre_sender_created)
    - app/models/__init__.py (SenderRestrictionEvent from Plan 02 — for ORM read with from_attributes)
    - app/schemas/__init__.py (RestrictionEventResponse from Task 1)
    - tests/test_restriction_audit.py::test_history_endpoint (Wave 1 RED stub this task turns GREEN)
  </read_first>
  <action>
    In app/routers/senders.py add `@router.get("/senders/{slug}/restriction-events", response_model=list[RestrictionEventResponse])` with signature `(slug: str, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db))`. Body: `sender = await _load_sender_by_slug(db, ctx, slug)` (reuses the workspace-scoped 404), then SELECT events for that sender newest-first — either `select(SenderRestrictionEvent).where(SenderRestrictionEvent.sender_id == sender.id).order_by(SenderRestrictionEvent.created_at.desc())` (ORM, from_attributes) or equivalent `text()` matching idx_sre_sender_created. Return the rows as RestrictionEventResponse list. Add a sane default LIMIT (e.g. 200, newest-first) to bound the response. Import RestrictionEventResponse from app.schemas and SenderRestrictionEvent from app.models. The workspace filter is enforced by _load_sender_by_slug (sender must belong to ctx.workspace_id) — defence-in-depth: also scope the event SELECT by workspace_id is optional but recommended.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_restriction_audit.py::test_history_endpoint -x</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_restriction_audit.py::test_history_endpoint -x` (test-overlay) exits 0 — returns the workspace's events newest-first AND a foreign-workspace sender's events are NOT visible (404 or empty, never leaked).
    - `grep -q "_load_sender_by_slug" app/routers/senders.py` in the new endpoint body (reuses the workspace-scoped lookup, not a bare SELECT by slug).
    - Endpoint declares `Depends(auth_dep)` (workspace-scoped; unauthenticated → 403).
    - Full suite green under test-overlay (all 12 Phase-10 tests + no regressions).
  </acceptance_criteria>
  <done>GET /senders/{slug}/restriction-events returns workspace-scoped, newest-first history; history test GREEN; full suite green.</done>
</task>

</tasks>

<threat_model>
ASVS L1 (block_on=high). Focus areas for this plan:
- **New read endpoint MUST be workspace-scoped (no cross-tenant leakage):** `GET /senders/{slug}/restriction-events` uses the SAME auth_dep + workspace filter as existing senders endpoints — it resolves the sender via `_load_sender_by_slug(db, ctx, slug)` which filters `Sender.workspace_id == ctx.workspace_id` and returns opaque 404 for foreign/unknown slugs. `test_history_endpoint` asserts a foreign-workspace sender's events are never returned. Recommended defence-in-depth: also constrain the event SELECT by workspace_id.
- **No write paths exposed:** the endpoint is read-only (GET); no UPDATE/DELETE on the append-only log via the API.
- **raw_text / proxy in the response:** RestrictionEventResponse returns raw_text and the proxy JSONB. These are workspace-internal (only the owning workspace can reach the endpoint). raw_text is human-facing error/bot text (no secrets — guaranteed by Plan 02). The proxy field reveals the workspace's own proxy config to the workspace owner only — acceptable (same-tenant).
- **Response bounded:** default LIMIT on the history query prevents unbounded payloads.
- **pool_health is numeric/presentation-free:** no server-side badge color; UI derives green/yellow/red. Keeps the API surface minimal.
- **Prod safety:** tests via test-overlay only; rebuild api after code change (restart does not pick up code); NEVER `down -v`.
</threat_model>

<verification>
- Schemas import cleanly; CampaignResponse/CampaignSenderAttach carry the new fields.
- pool_health correct for all-active/partial/all-paused; attached_senders enriched.
- History endpoint workspace-scoped, newest-first, auth-gated.
- Full Phase-10 test suite GREEN under test-overlay (all 12 rows from VALIDATION.md); no regressions in the broader suite.
</verification>

<success_criteria>
- POOLV-01: pool_health {active, paused, total, earliest_resume_at} computed in one pass.
- POOLV-02: attached_senders[] enriched with restriction_status/restricted_until (verbatim SenderResponse names).
- HLTH-03: per-account restriction-events endpoint, workspace-scoped, newest-first.
- API presentation-free (badge logic on frontend); endpoint read-only over the append-only log.
</success_criteria>

<output>
After completion, create `.planning/phases/10-pool-visibility/10-03-SUMMARY.md`
</output>
