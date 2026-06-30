---
phase: 16-rag-knowledge-bases-for-agents
plan: 05
subsystem: ui
tags: [react, tanstack-start, shadcn, rag, knowledge-base, frontend, pgvector]

requires:
  - phase: 16-03-api-endpoints-and-handoff
    provides: workspace-scoped KB API (CRUD, upload/paste, reindex, agent↔KB attach/detach, manual search) + regenerated openapi/types
  - phase: 16-04-search-tool-wiring
    provides: kb_search + search_knowledge_base agent tool (live-conversation retrieval)
provides:
  - "Knowledge bases sidebar tab + list page (create/delete, empty state)"
  - "KB detail page (D-09 header + 5-metric row + 4 tabs: Documents/Search/Agents/Settings, poll-while-processing)"
  - "Agent-editor KB multi-select (M:N attach/detach) — incl. one-step deferred attach on agent CREATE"
affects: [future KB UI work, agent editor, campaign wizard]

tech-stack:
  added: []
  patterns:
    - "Deferred M:N attach: collect selections locally on create, attach after the parent entity is POSTed"
    - "Reverse-list derivation: agent's attached KBs derived from each KB's /agents list (no agent-scoped endpoint)"

key-files:
  created:
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.index.tsx
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.$id.tsx
  modified:
    - /root/apps/aimly/aimly-tg-outreach/src/components/AppSidebar.tsx
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx
    - /root/apps/aimly/aimly-tg-outreach/src/lib/api.ts
    - /root/apps/aimly/aimly-tg-outreach/src/types/api.ts
    - app/services/kb_ingest.py        # human-verify fix
    - app/services/kb_search.py        # human-verify fix (hybrid)
    - app/services/ai_engine.py        # human-verify fix (RAG-awareness)
    - app/config.py                    # human-verify tuning
    - app/database.py                  # human-verify fix (pgvector init ordering)
    - app/models/__init__.py           # human-verify fix (kb id server_default)
    - migrations/042_kb_id_server_defaults.sql
    - migrations/043_kb_chunks_fts_index.sql

key-decisions:
  - "Light theme across all KB surfaces (D-12), verified."
  - "Static AIContext.knowledge_base text field is independent of the new M:N attachment (D-08) — both coexist, verified."
  - "Deferred attach on agent create: pick KBs locally, attach via POST /knowledge-bases/{id}/agents right after the agent POST returns (one-step UX, parity with edit)."

patterns-established:
  - "Deferred-attach for M:N relations on create forms (Promise.allSettled, partial-failure toast, no lost parent)."

requirements-completed: [KB-01, KB-02, KB-03, KB-04, KB-05]

duration: ~3h (incl. live human-verify + 7 gap fixes)
completed: 2026-06-30
---

# Phase 16 Plan 05: Frontend Surfaces Summary

**Shipped the three Phase-16 UI surfaces (Knowledge bases tab + list, KB detail with 4 tabs, agent↔KB multi-select) and — through live human-verify — drove the full RAG chain to work end-to-end in a real conversation, fixing 7 backend defects surfaced only by real PDFs and real queries.**

## Accomplishments (frontend, sibling repo `aimly-tg-outreach`)

- **Knowledge bases** sidebar tab → list page (create modal, list, empty state).
- **KB detail** page: D-09 header summary + 5-metric stat row + 4 tabs (Documents upload/paste with background polling `pending→processing→indexed/failed`, manual Search, Agents reverse-list, Settings).
- **Agent editor** «Базы знаний» M:N multi-select, separate from the static `knowledge_base` text field (D-08).
- **One-step deferred attach** (added after human-verify feedback): the create-agent form now lets you pick KBs directly; they attach automatically right after the agent is created (was edit-only).
- Commits (frontend): `8907fb2`, `9cb42d7`, `3f571ae`. `npx tsc --noEmit` clean. Pushed to origin/main (Lovable/Cloudflare deploy).

## Human-Verify Findings & Fixes (backend, this repo — all deployed)

Live testing with a real résumé PDF + Russian/English queries exposed defects that the RED scaffold couldn't:

1. **pgvector init ordering** (`d73e813`): `init_db` ran `create_all` (declares `Vector(1536)`) before migration 041's `CREATE EXTENSION vector` → fresh-DB/recovery crash `type "vector" does not exist`. Fix: `CREATE EXTENSION IF NOT EXISTS vector` before `create_all`. (Also restored the documented DROP-recovery path.)
2. **NUL byte in extracted text** (`d97486c`): pypdf emitted `0x00`; Postgres `text` rejects it → `kb_chunks` insert failed → doc `failed`. Fix: strip `\x00` in `extract_text` + regression test.
3. **kb id NotNullViolation** (`dee0eb7`, mig 042): `create_all` won over migration 041's `DEFAULT gen_random_uuid()` (ORM had only client-side `default=`), so the worker's raw-SQL `kb_chunks` insert hit NULL id. Fix: `server_default=gen_random_uuid()` on the 3 KB id columns + `ALTER … SET DEFAULT` migration. (Same drift class as `sender_restriction_events`.)
4. **Search threshold mis-calibrated** (`e99e8f3`): default `max_distance=0.55` filtered even verbatim matches (text-embedding-3-small puts relevant hits ~0.6–0.7). Raised to 0.8.
5. **Hybrid keyword+vector search** (`530468f`, mig 043): pure dense missed terse/1-word queries ("education") — a 1-word query embeds far from any prose passage. Added a full-text `'simple'` keyword leg OR'd with the cosine leg (+ GIN index); chunk size reverted to 800/120, then set to **250/50** (`b8d4b39`) so a keyword hit returns a focused passage, not the whole doc.
6. **Agent not RAG-aware** (`85b8451`): the `search_knowledge_base` tool was offered every turn but nothing told the model to USE it; after one empty result the history poisoned it into never searching again. Fix: inject a `<knowledge_base>` system-prompt directive (search-first, retry, never assume empty from a prior search) when the agent has an attached KB + stronger tool description.

**End-to-end verified:** live conversation, agent called `search_knowledge_base("Полина образование")`, kb_search returned the EDUCATION passages, agent replied with Polina's degree (Siberian State Transport University, BA HR Management + Psikhodemia ACT) — sourced purely from the KB.

## Notes / out-of-scope

- **Cold-inbound has no agent context (known gap, NOT Phase 16):** the listener's AI-gate resolves the agent from the conversation's denormalized `ai_context_id` (set by the campaign opener). An unsolicited inbound with no campaign link → "AI включён, но нет контекста" → no reply. This is the unbuilt "what to do with inbound from strangers" feature (CLAUDE.md). For verification the test conversation was manually linked to the campaign (`campaign_id` + `ai_context_id`).
- Deploy state: backend deployed (api+listener rebuilt; migrations 041/042/043 applied; pgvector db image). Frontend pushed (Cloudflare). Phase 15 warmup WIP was deployed alongside per user choice (separate concern).
