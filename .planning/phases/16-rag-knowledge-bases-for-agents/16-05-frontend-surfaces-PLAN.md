---
phase: 16-rag-knowledge-bases-for-agents
plan: 05
type: execute
wave: 4
depends_on: ["16-03", "16-04"]
files_modified:
  - /root/apps/aimly/aimly-tg-outreach/src/components/AppSidebar.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.index.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.$id.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/lib/api.ts
autonomous: false
requirements: [KB-01, KB-02, KB-03, KB-04, KB-05]
must_haves:
  truths:
    - "A new 'Knowledge bases' sidebar tab opens a list of workspace KBs with a New-KB CTA and empty state"
    - "KB detail shows the D-09 header summary + 5-metric stat row + 4 tabs (Documents/Search/Agents/Settings)"
    - "Documents tab uploads files (dropzone) + pastes text, lists per-doc status, and polls while any doc is processing"
    - "Search tab runs a manual KB search and shows retrieved chunks; Agents tab lists attached agents"
    - "The agent editor has a 'Базы знаний' multi-select that attaches/detaches KBs (M:N), separate from the static knowledge_base field (D-08)"
  artifacts:
    - path: "/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.index.tsx"
      provides: "KB list page"
      contains: "knowledge-bases"
    - path: "/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.$id.tsx"
      provides: "KB detail with header summary + 5 metrics + 4 tabs"
      contains: "Documents"
    - path: "/root/apps/aimly/aimly-tg-outreach/src/components/AppSidebar.tsx"
      provides: "new sidebar nav item after Agents"
      contains: "knowledge-bases"
  key_links:
    - from: "knowledge-bases.$id.tsx Documents tab"
      to: "POST /api/v1/knowledge-bases/{id}/documents"
      via: "dropzone upload + React Query refetchInterval while processing"
      pattern: "refetchInterval|documents"
    - from: "agents.tsx AgentEditor"
      to: "POST/DELETE /api/v1/knowledge-bases/{kb_id}/agents"
      via: "Базы знаний multi-select attach/detach"
      pattern: "Базы знаний"
---

<objective>
Build the three Phase 16 frontend surfaces in the SEPARATE frontend repo
(`/root/apps/aimly/aimly-tg-outreach`), all in the existing light theme per the
APPROVED 16-UI-SPEC: (1) the new "Knowledge bases" sidebar tab + KB list page,
(2) the KB detail view with the D-09 header summary + 5-metric stat row + 4-tab
bar (Documents / Search / Agents / Settings), and (3) the agent-editor KB
multi-select. End with a human-verify checkpoint.

Purpose: closes the user-facing UI for KB-01..KB-05. The frontend is a separate
repo generated via Lovable from `lovable-handoff/openapi.json` (regenerated in
plan 16-03), so this work happens in that repo and consumes the new endpoints.

Output: sidebar item, two route files, agent-editor field, regenerated API types,
verified by the user in the deployed app.
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
@.planning/phases/16-rag-knowledge-bases-for-agents/16-UI-SPEC.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-03-SUMMARY.md
@.planning/phases/16-rag-knowledge-bases-for-agents/16-04-SUMMARY.md

<cross_repo_note>
CRITICAL: The frontend is a DIFFERENT git repo. Commits inside
`/root/apps/aimly/aimly-tg-outreach` push to `AGS-Venture-Lab/aimly-tg-outreach`;
commits in `tg-outreach` push to `Andrewbruce165/outreach-platform`. Do NOT mix.
The frontend consumes `lovable-handoff/openapi.json` (regenerated in 16-03) — copy
the spec / regenerate the typed client (`src/lib/api.ts` or the generated types
module) in the frontend repo via the project's handoff flow; do NOT hand-edit types.
A parallel agent / Lovable may be committing here — stage SPECIFIC files only, never
`git add -A` (memory: feedback_parallel_agent_careful_commits).
</cross_repo_note>

<interfaces>
<!-- Existing frontend patterns to mirror — read these files in the frontend repo first. -->

- Sidebar nav: src/components/AppSidebar.tsx — `NAV_ITEMS` array; add
  `{ to: "/knowledge-bases", label: "Knowledge bases", icon: <Library/> }` (lucide) after Agents.
  Active state = `.sb__item.is-active`.
- Routes (TanStack file-based): mirror `src/routes/_authenticated/campaigns.index.tsx`
  (list) and `campaigns.$id.tsx` (detail-with-tabs). New files:
  `knowledge-bases.index.tsx`, `knowledge-bases.$id.tsx`.
- Page shell: `<Topbar title crumbs right>` + `<div className="scroll" style={{flex:1, padding:24}}>`.
- Tabs: `.tabs` / `.tab` / `.tab.is-active` / `.tab .count` (mirror campaigns.$id.tsx tab bar).
- Stat row: the `Metric` helper in campaigns.$id.tsx (`.metric__value` 26px/600 + uppercase
  label) + `.pill__dot` colored per token.
- File upload: the EXACT `.ct__dropzone` + hidden `<input type="file">` from contacts.tsx (~line 1248).
- Modal: `.modal__scrim` + `.modal` (+ `.modal--wide`).
- Status pills: `StatusPill` / `STATUS_PILL` from campaigns.$id.tsx (`.pill .pill--green/orange/red/ghost` + `.pill__dot`).
- Empty state: `agents.tsx::EmptyState` / `contacts.tsx::EmptyState`.
- Toasts: `sonner`; invalidate React Query keys after mutations.
- Error: `ApiError` → `errMsg()`, `.card` danger banner with Dismiss (campaigns.$id.tsx).

NEW endpoints (from 16-03, openapi.json): GET/POST /api/v1/knowledge-bases,
GET/PATCH/DELETE /{id}, GET/POST /{id}/documents, POST /{id}/documents/paste,
POST /{id}/documents/{doc}/reindex, DELETE /{id}/documents/{doc},
POST /{id}/search, GET /{id}/agents, POST /{id}/agents, DELETE /{id}/agents/{agent_id}.

D-09 5 metrics + dot tokens: DOCUMENTS(--text-faint) · INDEXED(--success) ·
PROCESSING(--warning) · FAILED(--danger) · STORAGE(--tg-blue).
Header icon buttons (verbatim aria-labels): Re-index `RefreshCw` aria-label="Re-index knowledge base";
Edit `Edit3` aria-label="Edit knowledge base"; Settings `Settings` aria-label="Knowledge base settings";
Delete `Trash2` aria-label="Delete knowledge base".

agents.tsx DISCREPANCY (UI-SPEC §Surface 3): the deployed AgentEditor has NO existing
KB placeholder and NO knowledge_base field — ADD a fresh `Field label="Базы знаний"` between
"Жёсткие правила" and the checkboxes. Hint: "Агент обращается к этим базам по необходимости
во время ответа." Empty: "Базы знаний не подключены." Do NOT remove/merge the static
knowledge_base field (D-08) — the multi-select is ADDITIONAL.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: API types regen + sidebar tab + KB list page + agent-editor KB multi-select</name>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/components/AppSidebar.tsx (NAV_ITEMS)
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.index.tsx (list page pattern)
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx (AgentEditor — confirm no KB field exists; find the Field/.select/chip idiom + "Жёсткие правила" anchor)
    - /root/apps/aimly/aimly-tg-outreach/src/lib/api.ts (typed client / how endpoints are called)
    - /root/apps/aimly/tg-outreach/lovable-handoff/openapi.json (the regenerated spec from 16-03 — source of truth for shapes)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-UI-SPEC.md (Surface 1 + Surface 3 + Copywriting Contract)
  </read_first>
  <files>/root/apps/aimly/aimly-tg-outreach/src/lib/api.ts, /root/apps/aimly/aimly-tg-outreach/src/components/AppSidebar.tsx, /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.index.tsx, /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx</files>
  <action>
    1. Regenerate the typed API client/types in the frontend repo from the 16-03 openapi.json
       via the project's handoff flow (do NOT hand-edit generated types). Confirm KB types appear.
    2. AppSidebar.tsx — add to `NAV_ITEMS` after the Agents entry:
       `{ to: "/knowledge-bases", label: "Knowledge bases", icon: <Library /> }` (import `Library`
       from lucide-react). Keep the existing `.sb__item.is-active` active styling.
    3. `knowledge-bases.index.tsx` (mirror campaigns.index.tsx): Topbar title "Knowledge bases",
       top-right `+ New knowledge base` (`btn btn--primary btn--sm`) → opens a create modal
       (name + optional description → POST /knowledge-bases → invalidate + navigate to detail).
       List workspace KBs (GET /knowledge-bases) as a `.tbl` table: name (Telegram-blue link →
       `/knowledge-bases/$id`), Type pill `Files`, Status pill (Ready=green / Indexing=amber /
       Failed=red / Empty=neutral derived from the aggregate `status`), DOCUMENTS count, STORAGE
       (human bytes), Updated. Empty state (0 KBs): heading `No knowledge bases yet`, body copy
       verbatim from the UI-SPEC + `Create your first knowledge base` button. Use light-theme
       tokens only (no dark surfaces).
    4. agents.tsx AgentEditor — ADD a `Field label="Базы знаний"` between "Жёсткие правила" and
       the checkboxes (it does NOT exist today; build fresh). A multi-select of workspace KBs
       (GET /knowledge-bases) showing selected items as removable `.pill` chips with an `X`
       (reuse the chip+remove idiom from campaigns.$id.tsx SendersPanel). On the agent's KBs:
       initial selection = GET the agent's attached KBs (via the reverse list or an agent-scoped
       call); attach = POST /knowledge-bases/{kb_id}/agents `{agent_id}`; detach = DELETE
       /knowledge-bases/{kb_id}/agents/{agent_id}; invalidate + sonner toast. Hint copy:
       "Агент обращается к этим базам по необходимости во время ответа." Empty: "Базы знаний не
       подключены." Do NOT touch / remove the static `knowledge_base` text field (D-08) — this
       multi-select is additional.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && npx tsc --noEmit 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `src/components/AppSidebar.tsx` contains a `/knowledge-bases` nav entry placed after Agents
    - `src/routes/_authenticated/knowledge-bases.index.tsx` exists with the `+ New knowledge base` CTA, the `.tbl` list, and the verbatim empty-state copy `No knowledge bases yet`
    - `src/routes/_authenticated/agents.tsx` AgentEditor contains a `Базы знаний` Field with attach/detach wired to `/knowledge-bases/{kb_id}/agents` and the verbatim hint copy
    - The static `knowledge_base` field handling (if present) is unchanged (D-08)
    - `npx tsc --noEmit` exits 0 (no type errors)
  </acceptance_criteria>
  <done>Sidebar tab, KB list page, and agent-editor KB multi-select all built in light theme; tsc clean; static knowledge_base field untouched.</done>
</task>

<task type="auto">
  <name>Task 2: KB detail view — D-09 header + 5-metric row + 4 tabs (Documents/Search/Agents/Settings)</name>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx (detail-with-tabs, Metric helper, StatusPill, tab bar, error banner)
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/contacts.tsx (~line 1248: .ct__dropzone + hidden file input pattern)
    - /root/apps/aimly/tg-outreach/lovable-handoff/openapi.json (KB detail / documents / search / agents shapes)
    - .planning/phases/16-rag-knowledge-bases-for-agents/16-UI-SPEC.md (Surface 2 in full: header D-09, 5-metric row, tab specs D-10/D-11, Copywriting Contract incl. aria-labels + destructive confirmations)
  </read_first>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/knowledge-bases.$id.tsx</files>
  <action>
    Build `knowledge-bases.$id.tsx` mirroring campaigns.$id.tsx structure, light theme only:
    - HEADER (D-09): Row 1 meta — `Type:` `.pill--blue` `Files` · `Status:` status pill with
      `.pill__dot` (Ready=success/Indexing=warning/Failed=danger/Empty=text-faint) · `Clock`
      icon + `Updated <Mon DD, YYYY, HH:MM>` in `.muted`. Top-right icon-only buttons with the
      VERBATIM aria-labels: Re-index (`RefreshCw`, aria-label="Re-index knowledge base"),
      Edit (`Edit3`, aria-label="Edit knowledge base"), Settings (`Settings`,
      aria-label="Knowledge base settings"), Delete (`Trash2`, danger, aria-label="Delete
      knowledge base"). Delete → AlertDialog with verbatim copy `Удалить базу знаний «{name}»?
      Все документы и индекс будут удалены безвозвратно.` confirm `Удалить`(danger)/cancel `Отмена`.
    - STAT ROW (D-09): a `repeat(5,1fr)` grid of Metric cells from the KB aggregate —
      DOCUMENTS(dot --text-faint) · INDEXED(--success) · PROCESSING(--warning) · FAILED(--danger)
      · STORAGE(human bytes, dot --tg-blue). `.metric__value` 26px/600 + uppercase label.
    - TAB BAR (D-11): `.tabs` with 4 `.tab` (lucide icon + label), active underline; optional
      `.tab .count` on Documents (doc count) + Agents (attached count). Default = Documents.
      1. Documents (`FileText`): `.tbl` per-doc list (name, source-kind badge / "Pasted text",
         status pill Indexed/Processing/Failed, size, uploaded date, per-row actions: Re-index on
         Failed, Delete with verbatim confirm `Удалить документ «{name}» из базы? Его чанки будут
         удалены из индекса.`). Header actions: `Upload files` (primary → dropzone modal, reuse
         `.ct__dropzone` + hidden file input → POST /{id}/documents multipart) + `Paste text`
         (ghost → textarea modal → POST /{id}/documents/paste). Empty state copy verbatim
         (`No documents yet` + body). **Async poll (D-02):** React Query `refetchInterval` while
         any doc is `processing`; pills transition processing→indexed/failed; `Loader2.ob__spin`
         "Indexing…" affordance.
      2. Search (`Search`): one `.input` + `Search knowledge base` button → POST /{id}/search →
         results list (snippet + source-document name + optional distance/score). Idle hint
         (verbatim `Введите запрос, чтобы проверить, что находит агент в этой базе.`), no-results
         (`Ничего не найдено по этому запросу.`), disabled/hint when 0 indexed docs.
      3. Agents (`Users`): GET /{id}/agents → list of attached agents (name → link to agent).
         Read-only is acceptable for v1; if detach offered, mirror the campaign-pool `X` remove.
         Empty: `Эта база пока не подключена ни к одному агенту. Подключите её в настройках агента.`
      4. Settings (`Settings`): KB name `.input` + read-only `Type: Files` + optional description;
         Save = `btn--primary` (PATCH /{id}); Delete-KB (danger) here too.
    - Error envelope: `errMsg(e)` in a `.card` danger banner with Dismiss. Upload/parse failure
      copy verbatim: `Не удалось обработать файл «{name}». Проверьте формат (PDF, DOCX, TXT, MD,
      CSV) и попробуйте снова.`
    Use only the 4 type sizes + the aimly.css tokens; no dark surfaces, no oklch, no bare shadcn.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && npx tsc --noEmit 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `knowledge-bases.$id.tsx` exists with the D-09 header (Type/Status/Updated + 4 icon buttons carrying the verbatim aria-labels) and the 5-metric stat row with the correct dot tokens
    - The 4 tabs Documents/Search/Agents/Settings are present with the verbatim copy strings (grep `No documents yet`, `Search knowledge base`, `Введите запрос`, the delete confirmations)
    - Documents tab wires multipart upload + paste-text + per-doc re-index/delete and uses `refetchInterval` while a doc is `processing`
    - Search tab calls `/{id}/search`; Agents tab calls `/{id}/agents`
    - `npx tsc --noEmit` exits 0
  </acceptance_criteria>
  <done>KB detail view complete with D-09 header + 5 metrics + 4 functional tabs, background polling, and the exact copy/aria contract; tsc clean.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 3: Human UAT — KB lifecycle end-to-end in the deployed app</name>
  <what-built>
    Backend (deployed: `docker compose up -d --build api` + `--build listener`, AND the OPS-gated
    db image swap to pgvector/pgvector:pg16 from plan 16-01 applied) + frontend (deployed via
    Cloudflare/wrangler): Knowledge bases tab, KB CRUD, document upload/paste with background
    indexing, manual Search tab, agent KB multi-select, and the search_knowledge_base tool.
  </what-built>
  <how-to-verify>
    1. Confirm the db image swap + migration applied: `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SHOW log_statement;"` returns `ddl`, and `\dx vector` shows the extension.
    2. In the app, open the new "Knowledge bases" sidebar tab → "+ New knowledge base" → create one.
    3. Documents tab → upload a real PDF and a DOCX, and paste some text. Watch each transition
       Processing → Indexed (the list should poll automatically). Confirm chunk-bearing docs end
       Indexed and a deliberately-bad file ends Failed with a Re-index action.
    4. Search tab → run a query answerable by the uploaded content → confirm relevant chunks surface.
    5. Open an Agent → "Базы знаний" → attach the KB → save. Confirm the KB's Agents tab now lists
       that agent.
    6. In a live conversation with that agent, ask something only answerable from the uploaded doc →
       confirm the reply reflects the KB content (the model called search_knowledge_base).
    7. Confirm light theme throughout (no dark surfaces), and that the agent's separate static
       knowledge_base field still works independently (D-08).
  </how-to-verify>
  <resume-signal>Type "approved" or describe any issues (wrong status, no retrieval, theme drift, isolation leak).</resume-signal>
</task>

</tasks>

<verification>
- `npx tsc --noEmit` clean in the frontend repo after each code task.
- Frontend commits land in `AGS-Venture-Lab/aimly-tg-outreach` only; backend openapi already committed in 16-03.
- Human UAT confirms create → upload → index → search → attach → live retrieval, light theme, workspace isolation, D-08 separation.
</verification>

<success_criteria>
- Knowledge bases sidebar tab + list page (KB-01) with New-KB CTA + empty state.
- KB detail: D-09 header + 5-metric row + 4 tabs (Documents/Search/Agents/Settings), background polling (KB-03).
- Documents tab upload/paste (KB-02), Search tab (KB-05 manual), Agents reverse list (KB-04).
- Agent-editor KB multi-select attach/detach (KB-04), separate from static field (D-08).
- All light theme, exact copy + aria contract; human UAT approved.
</success_criteria>

<output>
After completion, create `.planning/phases/16-rag-knowledge-bases-for-agents/16-05-SUMMARY.md`
</output>
