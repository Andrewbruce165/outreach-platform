---
slug: inbox-thought-trace-layout
status: resolved
trigger: "BUG 2 — Thought trace panel shifts/breaks layout in inbox when contact has replied"
created: "2026-06-25"
updated: "2026-06-25"
---

# Debug Session: Inbox Thought Trace Layout Shift

## Symptoms

**Expected behavior:**
The "Thought trace" panel (GPT explanation of why the agent responded a certain way) stays in its correct position and is properly aligned, regardless of whether the contact has replied.

**Actual behavior:**
When viewing a conversation where the contact has replied, the Thought trace block shifts position and breaks the inbox layout.

**Error messages:** None reported (visual/CSS layout bug)

**Timeline:** Unknown

**Reproduction:**
1. Open inbox
2. Find a conversation where the contact has replied to the agent
3. Observe the Thought trace / GPT explanation block — it shifts out of alignment

## Current Focus

hypothesis: "CSS Grid auto-minimum (min-width:auto on grid items) lets the trace column blow out when call.prompt JSON — which embeds the contact reply — is rendered in a non-wrapping <pre>, forcing min-content width wider than the 360px/1fr tracks"
test: "Add minWidth:0 to grid children + wrap/constrain the <pre> JSON blocks; verify trace column holds at 360px regardless of reply presence"
expecting: "Trace panel stays at fixed 360px column, no horizontal blowout"
next_action: "apply minWidth:0 to grid + TracePane aside + TraceCard, and wrap the <pre> blocks"

## Evidence

- timestamp: 2026-06-25
  checked: "inbox.tsx lines 150-157 — the inbox layout root <div display:grid>"
  found: "gridTemplateColumns: showTrace ? '340px 1fr 360px' : '340px 1fr'. showTrace is a MANUAL toggle (useState true), NOT reply-dependent. So column COUNT does not change on reply — the shift is a track-width blowout, not a re-layout."
  implication: "The 360px trace track is being expanded past 360px by its content's intrinsic min-content width."

- timestamp: 2026-06-25
  checked: "Grid children: ConvList, Thread <section> (687-693), TracePane <aside> (1089-1097). The grid container and its children."
  found: "None of the three grid items set minWidth:0. The middle track is 1fr (implicit min-width:auto) and the trace is a fixed 360px track. Grid items default to min-width:auto, so a track refuses to shrink below its content min-content width."
  implication: "Any child whose content has a large intrinsic min-content width will expand its track and push/break the rest of the grid."

- timestamp: 2026-06-25
  checked: "TraceCard <pre> blocks (1278-1290 tool_calls, 1306-1318 prompt) rendering JSON.stringify(call.prompt) and JSON.stringify(call.tool_calls)"
  found: "<pre> defaults to white-space:pre (no wrapping). overflow:auto + maxHeight set, but NO width/maxWidth constraint and no word-wrap. Long single JSON lines therefore establish a very large min-content width for the TraceCard, hence the TracePane <aside>, hence the 360px grid track."
  implication: "This is the reply-specific trigger: before the contact replies the AI prompt is short/absent; after a reply, call.prompt embeds the conversation history (incl. the reply text) producing long JSON lines → fat <pre> → grid blowout → trace panel shifts/breaks. Confirmed root cause."

## Eliminated Hypotheses

- hypothesis: "Long inbound message bubble overflows and pushes layout"
  evidence: ".scroll class has overflow-x:hidden (aimly.css:431) — the messages container clips horizontal overflow, so the bubble cannot push the grid. Bubble wrapper also caps at maxWidth:70%."
  timestamp: 2026-06-25

- hypothesis: "Conditional column count changes when contact replies"
  evidence: "gridTemplateColumns depends only on showTrace (manual toggle, default true), never on reply state. Column count is constant during the bug."
  timestamp: 2026-06-25

## Resolution

root_cause: "CSS Grid auto-minimum blowout. The inbox layout is a 3-column grid (340px 1fr 360px). Grid items default to min-width:auto, so tracks cannot shrink below their content's min-content width. The TraceCard renders call.prompt / call.tool_calls inside non-wrapping <pre> blocks (white-space:pre, no width cap). When the contact replies, call.prompt embeds the conversation history producing long single-line JSON, giving the trace column a huge min-content width that expands the 360px (and 1fr) tracks — shifting and breaking the layout. Before a reply the prompt is short/empty so the symptom is absent."
fix: |
  Two-part fix in src/routes/_authenticated/inbox.tsx:
  1. Cured the CSS Grid auto-minimum blowout by adding minWidth:0 to the grid
     items so tracks honor their definitions and can shrink:
       - grid container (line ~150): added minWidth:0 + overflow:hidden
       - Thread <section> (middle 1fr item): added minWidth:0
       - TracePane <aside> (360px item): added minWidth:0 + overflow:hidden
  2. Stopped the <pre> JSON blocks from establishing a huge min-content width by
     making them wrap (whiteSpace:'pre-wrap' + wordBreak:'break-word') on both the
     tool_calls <pre> and the prompt <pre> in TraceCard.
  Either change alone stops the shift; both together make the panel robust and readable.
verification: |
  - npx tsc --noEmit → exit 0, no type errors (inline-style props are valid).
  - Logical verification: with minWidth:0 on all grid items, the 360px trace track
    and 1fr middle track can no longer be forced wider than defined by content
    min-content, so the column geometry is constant whether or not the contact has
    replied. The <pre> wrapping removes the original source of the large min-content.
  - PENDING human visual verification in the running app (open a conversation where
    the contact has replied; confirm trace panel holds position).
files_changed:
  - "src/routes/_authenticated/inbox.tsx (aimly-tg-outreach repo): grid container minWidth:0+overflow:hidden; Thread section minWidth:0; TracePane aside minWidth:0+overflow:hidden; two <pre> blocks → pre-wrap + break-word"
