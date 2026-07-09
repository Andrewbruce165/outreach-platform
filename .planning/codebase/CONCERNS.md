# Codebase Concerns

**Analysis Date:** 2026-07-09

## Tech Debt

**Multi-tenancy is app-level filtering only, no DB-level enforcement (RLS):**
- Issue: every query that must be workspace-scoped relies on a manually-added `WHERE workspace_id = :workspace_id` clause. There is no Postgres Row-Level Security policy backing this. 54 `# TODO(v2-rls)` markers are scattered across the codebase, each one a place where a missed/forgotten filter would leak one workspace's data (senders, contacts, conversations, campaigns...) into another's response.
- Files: `app/utils/auth.py:32,315,452`, `app/routers/workspace.py:115,137,166,203,287,322`, `app/routers/senders.py:448,473,904,1671,1732`, `app/routers/campaigns.py:92,130,148,169,589`, `app/routers/contacts.py:80,192,386,463,518,551,579`, `app/routers/conversations.py:92,175,564,702,911,1013,1072`, `app/routers/onboarding.py:147,219,346,826,867`, `app/routers/agents.py:123,174,198`, `app/routers/folders.py:93,150,184,231,284`, `app/routers/analytics.py:58,74,90`, `app/routers/account_import.py:246,335`, `app/services/onboarding_state.py:118`, `app/services/rotation.py:72`, `app/routers/send.py:85`
- Impact: this is a brownfield SaaS multi-tenancy project (`v1` goal is the first paying external customer) — a single missed filter on a new endpoint is a cross-tenant data leak, not just a bug. The blast radius grows with every new router added before RLS lands.
- Fix approach: implement Postgres RLS policies keyed on `app.workspace_id` session variable (already scoped/planned as "v2-rls" per the TODO convention) so the DB enforces isolation even if an app-level filter is forgotten; then the TODO comments can be deleted as each query proves redundant-but-safe under RLS.

**JWT library slated for deprecation (`python-jose`):**
- Issue: `app/utils/auth.py` uses `python-jose` for both ES256 (Supabase JWKS) and HS256 verification paths. Marked `TODO(v2): migrate from python-jose to PyJWT (deprecation — RESEARCH Pitfall 2)`.
- Files: `app/utils/auth.py:31-32,43` (`from jose import jwt`)
- Impact: `python-jose` is effectively unmaintained upstream; staying on it risks unpatched CVEs in a module that gates every authenticated request.
- Fix approach: swap to `PyJWT` preserving the dual ES256(JWKS)/HS256 branch and the 1h JWKS cache behavior; add regression tests for both algorithms before cutover (auth is the highest-blast-radius module to break).

**Hardcoded external alerting gap in the queue worker:**
- Issue: a detected failure condition in the send queue has no outbound notification — only a code comment.
- Files: `app/services/queue.py:1169` (`# TODO: add external alert (webhook/email) when monitoring infrastructure is available`)
- Impact: certain queue-level failures are silent until someone notices symptoms downstream (e.g. stalled sends) rather than being paged.
- Fix approach: wire into the existing Telegram alert bot pattern already used at the infra level (`/root/apps/monitoring/tg-notify.sh` equivalent) or a workspace-level webhook once workspace notification settings exist.

**CLAUDE.md documentation drift on working-hours/rate-limit hardcoding:**
- Issue: the root `CLAUDE.md` states rate limits and the 09:00–20:00 MSK working-hour window are "захардкожено в queue.py" — but `app/services/queue.py` now reads `work_hour_start`/`work_hour_end` and `campaign_tz` per-campaign from the `campaigns` table (`queue.py:91-101,124,177-186,261-262,412-413,500-501`), and per-sender `rate_per_min`/`rate_per_hour` from the `senders` table (`queue.py:610,646,674`). This is more configurable than the doc claims.
- Files: `app/services/queue.py`, root `CLAUDE.md` ("Чего нет — нужно построить" section)
- Impact: low risk technically, but stale docs can misdirect future planning/agent sessions into re-building something that already exists (or skipping verification of how configurable it actually is, e.g. is it exposed in the UI yet).
- Fix approach: verify current UI exposure of per-campaign work hours/per-sender rate limits, then update CLAUDE.md's "Чего нет" section to reflect actual state.

**Generic "too-frequent name change" advisory is not signal-driven:**
- Issue: the frontend profile-edit warning ("Слишком частая смена имени…") fires on every name/bio edit unconditionally — it is not driven by `profile_field_changed_at` or any real cooldown/rate-limit state.
- Files: `accounts.tsx:1263-1272` (frontend repo, `/root/apps/aimly/aimly-tg-outreach`)
- Impact: users get warned even when there's no real risk, and get no *extra* warning when risk is actually elevated (e.g. account <7 days old, per D-09) beyond the separate age-based advisory. Discovered as a contributing red herring during `.planning/debug/telegram-profile-update-not-applying.md`.
- Fix approach: drive the warning off `profile_field_changed_at` recency and/or account age, matching the same signal the backend eventually needs for real throttling decisions.

## Known Bugs

**No-reply / follow-up marking never fired for any conversation until 2026-07-08 (RESOLVED, but re-verify blast radius):**
- Symptoms: conversations waiting on a reply were never marked `no_reply`; 0 pings ever sent across the entire conversation history despite the FollowUpWorker running correctly since Phase 19.
- Files: `app/services/follow_up.py` (`_mark_no_reply_pass`, tick step 0)
- Trigger: root cause was `campaigns.follow_up_enabled` (server_default `false`) gating BOTH the no_reply status marking AND the ping/auto-finish sweep behind one flag; the flag was `false` on all 6 campaigns at investigation time, so marking never ran.
- Workaround: fixed and deployed in commit `65302c9` (2026-07-08) — marking is now decoupled from `follow_up_enabled` and widened to running+paused+no-campaign conversations (done excluded). Live deploy flipped 316 conversations to `no_reply` on first tick. Ping/auto-finish sweep is UNCHANGED and still gated by `follow_up_enabled`, which remains `false` on all but 1 of 6 campaigns — pinging is still essentially dormant platform-wide. See `.planning/debug/no-reply-followup-not-marked.md`.

**Telegram profile edits silently rejected or silently cleared the wrong field (AWAITING FINAL HUMAN VERIFICATION):**
- Symptoms: editing First/Last name via the UI either (a) appears to save successfully but Telegram silently rejects the change (server-side anti-abuse throttle on fresh accounts, no exception raised), or (b) clearing a name field sends `null` instead of `""`, which Telethon/Telegram interprets as "leave unchanged" — so the field is never actually cleared on Telegram, yet the local DB cache is wrongly overwritten to the empty value.
- Files: backend `app/routers/senders.py` (`update_sender_profile`, guard added), `app/services/telegram.py` (`update_profile`, `_verify_profile_applied`); frontend `accounts.tsx:1226-1227` (repo `aimly-tg-outreach`)
- Trigger: reproduced live on senders `polina_onoworksai` and `polinaworksai`.
- Workaround: both root causes have code fixes committed/deployed (backend commit `6705ac9`, frontend commit `fa8366e`) — post-write verification (`ProfileChangeRejectedError` → 409) and explicit `""` vs `null` handling — but the debug session status is still `awaiting_human_verify`. See `.planning/debug/telegram-profile-update-not-applying.md`.
- **Separate, larger, deferred issue found while investigating this:** a one-off bulk resync (2026-07-08) revealed that ~30+ of 63 sender accounts have garbled real Telegram last names (e.g. "Polina K Gudiyqnonx") because earlier rename attempts on fresh accounts were silently swallowed by Telegram's rate limit for a long time, and the local DB previously displayed the never-applied intended name as if it had succeeded. Retroactive bulk-rename-on-Telegram remediation is explicitly deferred/out of scope — tracked as separate follow-up work (`scripts/bulk_resync_profiles.py`, `scripts/bulk_clear_polina_lastnames.py` exist as untracked helper scripts for this).

**Checker (`is_registered` phone-resolve) false-negatives — multiple compounding failure modes, partially fixed:**
- Symptoms: `contacts_cache.is_registered=false` does not mean "no Telegram account" — it means "not resolvable by phone by this checker account right now." Historically this bucket has been polluted by (a) recipient privacy settings hiding phone-lookup (genuine, permanent), (b) checker-account throttle/shadow-ban (transient, was previously misdiagnosed as "checker healthy"), and (c) cache cross-contamination (a poisoned `contacts_cache` row is workspace-wide and consulted before ever calling Telegram again, so even a healthy checker inherits a prior checker's false negative).
- Files: `app/services/checker.py` (`_lookup_cache`, `checker.py:175,344`), `app/services/contact_check_worker.py` (health-probe, burst-cap, `checker_rest_until`, `checker_trip_count` escalation)
- Trigger: bulk resolve of purchased/bartered phone bases (e.g. "Barter_база Игоря" folder — see `.planning/debug/checker-fn-igor-base.md`, parked `parked_awaiting_healthy_pool` as of 2026-06-30) or any burst of >~45-50 resolves in a row on one checker.
- Workaround: Phase 14/17 landed a resolve ladder with confidence tracking (`tg_confidence`/`tg_resolved_by`/`tg_probe_state`), inline throttle-aware finalization, per-checker rest windows, and an escalating trip-count cooldown (fix deployed as quick task 260629-b7j, migration 036). Country-based gating was considered and explicitly rejected (D-10, `.planning/phases/17-.../17-CONTEXT.md`) after warmth/health, not country, was shown to be the real axis. **Open/parked:** the Igor-base folder itself remains parked with 176 contacts reset to `pending` because no verified-healthy RU checker pool existed at last check — re-verify pool health before resuming. See `.planning/notes/checker-false-negatives.md`, `.planning/notes/checker-pool-throttle-spike.md`, `.planning/notes/checker-strategy.md` for full history.

**Warmup head-of-line blocking (fixed) + separate open re-auth gap:**
- Symptoms: one ineligible warmup session at the front of the queue stalled the entire warmup pipeline because skipping it didn't advance `next_message_at`.
- Files: `app/services/warmup.py`
- Fix: deployed 2026-07-02 (quick 260702-c5k, commit `6dc751e`).
- **Still open:** Phase 17 removed the sender-side `ResolvePhone` capability, so warmup can no longer resolve its own-account peers when they aren't already in the Telethon entity cache — this degrades warmup throughput over time as cache entries age out. Additionally, as of the last check, 4 senders (including both working Canada-based checkers `ca-account-1`/`ca-account-2`) were in `session_expired` state, stalling checking as well as warmup for those accounts. A re-auth 500 bug (plain-flow INSERT vs. upsert conflict) was fixed (commit `3261529`, deployed 2026-07-02) so users can now re-authenticate expired senders through the UI — but the sessions still need to actually be re-authed by a human.

## Security Considerations

**Auth relies on unmaintained JWT library:**
- Risk: `python-jose` (see Tech Debt above) has known historical CVEs in the JWT ecosystem generally; an unmaintained crypto-adjacent dependency in the auth path is a standing risk even without a currently-known exploit.
- Files: `app/utils/auth.py`
- Current mitigation: none beyond pinning; ES256/JWKS path is the primary route (Supabase-issued tokens), HS256 is a legacy fallback.
- Recommendations: migrate to `PyJWT` (already tracked as `TODO(v2)`); audit whether the HS256 fallback path can be removed entirely if no legacy Supabase project still needs it.

**No DB-level tenant isolation (RLS) yet:**
- Risk: see Tech Debt — a single missing/incorrect `workspace_id` filter on any of dozens of endpoints is a full cross-tenant data read, which is a severe issue for a SaaS product onboarding external paying customers.
- Files: see the 54 `TODO(v2-rls)` sites listed above.
- Current mitigation: app-level `WHERE workspace_id = :workspace_id` on each query, code-reviewed by convention only.
- Recommendations: prioritize RLS before onboarding any customer whose data must not be visible to another tenant even in a worst-case app bug.

**Session strings for 13 shared Telegram accounts existed in two independent databases simultaneously (historical, now stopped but not cleaned up):**
- Risk: `/root/apps/telegram-api/` and `/root/apps/outreach-platform/` (old AGS internal tool + its predecessor) hold session strings for the exact same 13 physical Telegram accounts now live in this project's `outreach_platform` DB. Both are marked `restart: "no"` and are supposed to stay stopped, but they still exist on disk with live credentials and were confirmed (2026-06-24) to have been running concurrently with this project's listener at one point, causing message/AI cross-talk.
- Files: `/root/apps/telegram-api/`, `/root/apps/outreach-platform/` (outside this repo, on the same host)
- Current mitigation: both stopped; root `CLAUDE.md` documents the danger and forbids starting them.
- Recommendations: the deletion task documented in this repo's own `CLAUDE.md` ("Задача: удалить старые проекты") is still open — until the directories are actually deleted (or at minimum have credentials scrubbed), an accidental `docker compose up` on either old stack re-creates the session conflict. See `.planning/debug/service-conflict-investigation.md` for the full incident writeup.

**Secrets/tokens in shell scripts:**
- Risk: `backup.sh` and various one-off `scripts/*.py` (e.g. `scripts/bulk_resync_profiles.py`, `scripts/bulk_clear_polina_lastnames.py`) run with production DB/Telegram credentials via container exec; not inherently a vulnerability but worth confirming these scripts are never committed with inline secrets and are reviewed before ad-hoc production runs (per project convention, they already require explicit user confirmation).
- Files: `scripts/bulk_clear_polina_lastnames.py`, `scripts/bulk_resync_profiles.py` (both currently untracked in git per repo status)
- Recommendations: once verified safe, either commit with a clear "one-off/manual" header comment or delete after use — untracked scripts with prod access sitting in the working tree indefinitely is easy to lose track of.

## Performance Bottlenecks

**Checker pool throughput is fundamentally rate-limited by Telegram's own anti-abuse detection, not by code:**
- Problem: bulk phone-registration checking (`ImportContacts`/`ResolvePhone`) throughput is capped at roughly one checker's burst budget (~30 resolves) per rest window (default 300s, `CONTACT_CHECK_REST_SECONDS`), and adding more checkers only scales linearly (2 checkers ≈ 2x one checker) — there is no way to check faster without more warmed, healthy accounts.
- Files: `app/services/contact_check_worker.py` (`burst_cap`, `checker_rest_until`, `CONTACT_CHECK_BATCH_SIZE`)
- Cause: Telegram's own server-side throttling of phone-contact-resolution APIs; confirmed empirically in `.planning/notes/checker-pool-throttle-spike.md` — one batch ≤30 is safe, sequential batches without adequate rest collapse the whole pool.
- Improvement path: this is close to its practical ceiling given Telegram's constraints; the only lever is horizontal (more warmed checker accounts), not algorithmic. Do not attempt to "optimize" batch size/interval without re-reading the throttle-spike note first — several prior fixes were specifically to STOP over-eager batching.

**Large purchased/bartered contact bases (thousands of rows) drive full-folder background pagination on the frontend:**
- Problem: `/contacts` page stats briefly flash wrong numbers (e.g. `41/0/159` before settling to `126/4135/931`) while the frontend paginates through ~26 pages to fetch a 5192-contact folder before computing aggregate stats client-side.
- Files: `accounts.tsx`/`contacts.tsx` equivalent in the frontend repo (`contactsForStats = contactsStatsQ.data ?? contacts`, fallback to first-page-only `contacts`)
- Cause: stats were computed client-side over a full-folder fetch instead of server-side aggregation.
- Improvement path: already fixed for this specific case by moving the aggregate to a server-computed endpoint (see `.planning/debug/resolved/contacts-stats-flash-wrong.md`) — flag as a pattern to watch for: any other "client aggregates over all pages" UI component will hit the same flash-then-correct behavior at this data scale.

## Fragile Areas

**Restriction/recovery symmetry is easy to get wrong (PeerFlood ↔ SpamBot-clear):**
- Files: `app/services/listener.py` (`failover_cold_backlog`, `_restriction_reconcile_tick`)
- Why fragile: restricting a sender (PeerFlood) actively moves its cold-pending backlog to healthy senders (`failover_cold_backlog`), but until recently, clearing that restriction did NOT pull a fair share of backlog back — the recovered sender returned to `active` with zero assigned work, silently degrading campaign throughput. This is a case where "restrict" and "un-restrict" are not simple opposites — every future change to the restriction/recovery pipeline needs to be checked for this same one-way-door asymmetry.
- Safe modification: any change to sender restriction handling must be checked against BOTH directions (restrict → does it evacuate correctly; recover → does it re-balance work back) and against `rebalance_on_attach` needing ≥2 eligible senders to be a no-op-free operation (see memory note "rebalance_on_attach needs ≥2 eligible senders").
- Test coverage: `tests/test_sender_restriction.py`, `tests/test_rebalance.py`, `tests/test_failover.py`, `tests/test_spambot_selfcheck.py`, `tests/test_restriction_audit.py` — good coverage exists post-fix (see `.planning/debug/resolved/peerflood-return-no-queue.md`), but this class of bug (asymmetric state machine transitions) is exactly the kind that regresses silently when a new restriction category is added.

**AI reasoning-model token budget is a recurring source of silent empty responses:**
- Files: `app/services/ai_engine.py` (`_supports_reasoning_effort`-style gating around lines 64-130, retry-with-minimal-effort logic ~1559-1563)
- Why fragile: `gpt-5-mini` is a reasoning model that splits `max_completion_tokens` between hidden reasoning tokens and visible output. A tight cap causes the model to spend its entire budget on invisible reasoning and return `content=''` with `finish_reason='length'` — this already happened once in production (fixed 2026-07-02, see `.planning/debug/resolved/ai-empty-llm-response.md` and memory note "AI answerer runs gpt-5-mini"). Any future model swap or prompt-length increase (e.g. longer knowledge-base context, longer dialogue-stage instructions — already raised to a 3000-char cap per commit `2589585`) risks re-tripping this failure mode if the token budget isn't re-validated.
- Safe modification: never change `max_completion_tokens`/`reasoning_effort` constants without checking the retry-on-empty-content path still has headroom; add tests asserting non-empty content for near-budget-limit prompts when changing prompt-length caps elsewhere.
- Test coverage: retry-on-empty behavior is tested, but there's no automated guard tying "max allowed instruction/context length" to "token budget still sufficient" — that relationship is currently only verified by manual reasoning in comments.

**ORM `default=` vs `server_default=` drift causes NotNullViolation on raw-SQL inserts after fresh-DB recovery:**
- Files: `app/models/__init__.py` (multiple columns use Python-side `default=` without a matching `server_default=`), migrations `019`, `040`, `042`
- Why fragile: `Base.metadata.create_all()` (run at every API startup, `app/database.py::init_db`) recreates tables using ONLY the DB-level `server_default`, ignoring Python-side `default=`. On a fresh DB or disaster-recovery rebuild, any raw-SQL `INSERT` (in migrations or ad-hoc scripts) that omits a column relying on Python-side `default=` will hit `NotNullViolation`. This has already bitten `warmup_sessions.status/messages_sent`, `contacts`, and `kb_chunks`/`kb_documents`/`knowledge_bases.id` (pgvector Phase 16) — see memory note "ORM default= vs server_default= drift".
- Safe modification: whenever adding a new raw-SQL migration that inserts rows, explicitly provide every column's value in the `INSERT` rather than relying on either kind of default; when adding a new ORM column, add BOTH `default=` (Python-side convenience) AND `server_default=` (DB-level safety net) plus an explicit `ALTER COLUMN ... SET DEFAULT`.
- Test coverage: no automated check currently catches this class of drift — it has only ever been caught by live fresh-DB/recovery incidents.

**Full test suite is order-dependent and reports RED on a clean `main`:**
- Files: `tests/` (all), `tests/conftest.py`
- Why fragile: per project memory ("Test baseline RED again 2026-07-07"), running the FULL suite on a clean `main` produced 88 failed / 115 errors, but the same files pass when run in isolation or via targeted `-k` selection — meaning there is shared/leaking state between test modules (likely DB fixture teardown ordering or module-level singletons like `warmup_worker`/`FollowUpWorker`) rather than genuine regressions.
- Safe modification: do NOT trust a full-suite run's exit code as a correctness signal. Before merging a change, run the specific test file(s) touched plus a `git stash`-based clean-tree diff comparison, not `pytest` with no `-k` filter.
- Test coverage gap: the test suite needs an isolation audit (likely fixture scope/teardown in `tests/conftest.py`) before a full-suite green run can be trusted as a CI gate.

## Scaling Limits

**Checker/phone-resolve pool size is the hard ceiling on lead-list processing speed:**
- Current capacity: ~1 checker resolves ~30 numbers per ~5-minute rest window; 2 healthy checkers ≈ 2x throughput (verified empirically, not assumed).
- Limit: adding more RU-mobile-warmed accounts is the only lever; the two Canada-based accounts (`ca-account-1`/`ca-account-2`) are also viable checkers (country is not the gating factor — warmth/health is, per D-10) but both were `session_expired` as of the last check.
- Scaling path: re-authenticate expired checker accounts, warm additional accounts specifically for the checker role (not sender role — mixing roles caused the Igor-base false-negative incident when smoke-test checkers reverted to `role='sender'` mid-run without being removed from active resolve paths).

**Rate limits (4/min, 20/hour, 150/day per sender) are empirically tuned, not workspace-configurable yet:**
- Current capacity: fixed per-sender ceiling read from `senders.rate_per_min`/`rate_per_hour` (already per-sender, contra the stale CLAUDE.md claim — see Tech Debt), but there is no UI/API for a workspace to set its own "safe corridor" policy distinctly per workspace or campaign beyond what already exists on `campaigns.work_hour_start/work_hour_end`.
- Limit: this is explicitly listed in the project's own roadmap as unbuilt ("Зелёный коридор" — recommended safe values + warning on deviation) — not yet a bug, but a known gap for the multi-tenant v1 goal (an external customer cannot yet self-configure their own risk tolerance).
- Scaling path: tracked as planned future work in root `CLAUDE.md` ("Чего нет — нужно построить" → "Политика рассылки на уровне workspace").

## Dependencies at Risk

**`python-jose`:**
- Risk: deprecated/low-maintenance JWT library used for the primary auth verification path (both ES256/JWKS and HS256).
- Impact: security patches may lag; already flagged internally as a "RESEARCH Pitfall" in code comments.
- Migration plan: swap to `PyJWT`, preserving the dual-algorithm branch logic in `app/utils/auth.py` and the 1-hour JWKS cache; add regression tests for both ES256 and HS256 paths before cutover given how central this module is.

**Vendor-supplied residential proxies bundled with purchased Telegram account archives:**
- Risk: vendor proxies shipped inside account-import archives have repeatedly been dead/subscription-exhausted (`402: user reached limit`), causing import failures that looked randomly distributed across accounts.
- Impact: already worked around — `resolve_import_proxy` now ignores vendor-supplied proxies entirely and always assigns from the workspace's own `ProxyPool` (Decodo). See `.planning/debug/resolved/tg-import-vendor-proxy-dead.md`.
- Migration plan: none needed going forward — vendor proxy JSON is still parsed/normalized (for record-keeping/tests) but never selected for actual use.

## Missing Critical Features

**Workspace-level "green corridor" sending policy:**
- Problem: there is no UI/API yet for a workspace to see recommended safe rate-limit/schedule values with a warning when they deviate, despite this being called out as core to the v1 differentiator (self-service external customers configuring their own outreach risk tolerance).
- Blocks: external customers cannot yet safely self-tune their own sending policy without staff guidance; a customer could misconfigure rates and trigger account restrictions with no in-product guardrail.

**No workspace-level notification/alerting channel:**
- Problem: `app/services/queue.py:1169` explicitly stubs an alert as future work; more broadly, there's no generalized "notify workspace admin" mechanism (email/webhook) for account restrictions, campaign stalls, or checker pool exhaustion — all of this is currently surfaced only via direct DB/log inspection by the operator.
- Blocks: external customers (not just the internal operator) need to be told when their senders get restricted or their campaign stalls, without requiring a support ticket.

## Test Coverage Gaps

**Cross-module test isolation (see Fragile Areas above):**
- What's not tested: the full-suite run itself is not a trustworthy signal; there's no test verifying that running the whole suite together doesn't leak state between modules.
- Files: `tests/conftest.py`, all of `tests/`
- Risk: a genuine regression could be masked by "the whole suite is red anyway" fatigue, or conversely a real fix could look like it broke things due to unrelated cross-test pollution.
- Priority: High — this undermines confidence in every other test-coverage claim in this document.

**No automated guard linking AI prompt/context length to reasoning-model token budget:**
- What's not tested: whether raising a character cap elsewhere (dialogue-stage instructions, knowledge-base injected context, per-agent character budget) can still fit within `max_completion_tokens` for the configured `reasoning_effort` without tripping the empty-response failure mode.
- Files: `app/services/ai_engine.py`
- Risk: silent empty AI responses recur every time someone increases a length cap without manually re-deriving the token math.
- Priority: Medium — has already caused one production incident; retry-on-empty mitigates but does not eliminate wasted LLM calls/latency.

**Checker false-negative detection lacks an end-to-end regression test simulating gradual pool-wide throttle onset:**
- What's not tested: the exact multi-batch, multi-checker collapse scenario from the Igor-base incident (mixed role reassignment + cache cross-contamination + inline anomaly gate defeated by cache-served results) doesn't appear to have a single integration test reproducing all three compounding factors together — the fixes (14-05/14-07/b7j) each have targeted unit tests, but the full incident scenario was diagnosed live in production, not first caught by a test.
- Files: `app/services/contact_check_worker.py`, `app/services/checker.py`
- Risk: a future change to any one of the three subsystems (role gating, cache lookup, rest/cooldown escalation) could reintroduce the combined failure without any single unit test catching it.
- Priority: Medium — the individual mechanisms are well-tested in isolation; the integration gap is the risk.

---

*Concerns audit: 2026-07-09*
