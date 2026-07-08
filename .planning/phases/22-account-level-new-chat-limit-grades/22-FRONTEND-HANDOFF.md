# Phase 22 — Frontend Handoff: Account-Level New-Chat Grade Limits

**Target repo:** the sibling frontend `/root/apps/aimly/aimly-tg-outreach` (independent origin `https://github.com/AGS-Venture-Lab/aimly-tg-outreach.git`). This is a **cross-repo** deliverable — the backend + contract live in `outreach-platform` (this repo); the UI is built in the sibling repo.

**Contract source of truth:** the regenerated `lovable-handoff/openapi.json` + `lovable-handoff/types/api.ts` (this commit) ARE the contract. **No UI-SPEC.md exists for this phase** — the user chose to proceed without one, so 22-VALIDATION marks these UI behaviors manual-only. Build against the openapi paths/shapes below; never invent response shapes.

**Backend requirement (D-11, D-12):** the backend is fully deployed after Waves 1–3 (grade schema, ladder API, per-account queue gate, sender grade surface, warmup budget sharing, and migration 059 dropping the dead throttle columns). The grade endpoints are live on the prod api.

---

## What the account grade system does (one-paragraph mental model)

Each Telegram sender account carries a **grade level (1–3)**. The level caps how many **new chats/day** that account may open (its new-chat budget), and the account **auto-progresses** to the next level after N days at the current level. The per-workspace **grade ladder** defines, for each level, `chats_per_day` and `step_days` (days-to-next). Level 3 is the permanent top level — it has a `chats_per_day` budget but **no** `step_days` (D-17). The old per-campaign "max new dialogs/day" cap and the per-sender daily-message rate cap are **removed** — budget is now purely grade-driven and shared across outreach + warmup.

---

## Deliverable 1 — Workspace grade-ladder editor (D-11, D-16)

**Endpoints:** `GET /api/v1/sender-grade-settings` and `PUT /api/v1/sender-grade-settings`.

**GET response shape** (untyped in openapi — documented here, from `app/routers/grade_settings.py::_shape`):

```jsonc
{
  "levels": [
    { "level": 1, "chats_per_day": 5,  "step_days": 30 },
    { "level": 2, "chats_per_day": 9,  "step_days": 30 },
    { "level": 3, "chats_per_day": 13, "step_days": null }  // top level: no step (D-17)
  ],
  "recommended": { "max_chats_per_day": 13, "min_step_days": 30 }
}
```

A missing workspace row resolves to the code-default ladder (5/30, 9/30, 13) — byte-identical to the unconfigured default, so the editor always has values to render.

**PUT body** = `GradeLadderUpdate` (see `types/api.ts`):

| Field | Default | Hard bounds (422 on breach) |
|-------|---------|------------------------------|
| `level1_chats_per_day` | 5 | 1..100 |
| `level1_step_days` | 30 | 1..365 |
| `level2_chats_per_day` | 9 | 1..100 |
| `level2_step_days` | 30 | 1..365 |
| `level3_chats_per_day` | 13 | 1..100 |

**PUT response shape** (untyped in openapi — documented here):

```jsonc
{
  "status": "saved",
  "settings": { /* same shape as GET response above */ },
  "warnings": [
    { "field": "level1_chats_per_day", "value": 20, "recommended_max": 13, "severity": "warning" }
  ]
}
```

**UI requirements:**
- **3 fixed rows** (D-16 — no add/remove). Columns: `chats/day` + `days-to-next`. **Level 3 has no `step_days`** — render its days-to-next as blank/"—" (permanent top level).
- Bind the form to GET on load, PUT on save.
- Show **green-corridor warnings** from the PUT response `warnings[]` in the **same visual style as the existing rate-limit warnings** (`WarningItem` = `{field, value, recommended_max, severity:"warning"}`). Soft caps: `chats_per_day > 13` warns; `step_days < 30` warns (for step-days, `recommended_max` carries the recommended floor). These are **soft** (200 + warning); the Pydantic bounds above are the **hard** cap (422).

---

## Deliverable 2 — Per-account card: grade display (D-11, D-12)

**Source:** the extended `SenderResponse` (already on every sender payload — `GET /api/v1/senders`, `GET /api/v1/senders/{slug}`, etc.). New fields:

| Field | Type | Meaning |
|-------|------|---------|
| `current_level` | `int` (default 1) | the account's grade level (1–3) |
| `level_updated_at` | `datetime \| null` | when the level was last set — the anchor auto-progression measures `step_days` from |
| `remaining_daily_budget` | `int \| null` | trailing-24h new-chat headroom = grade budget minus distinct new dialogs opened in the last 24h |

**UI requirements:**
- On each sender card, display the **current grade/level**, the **level-updated timestamp**, and the **remaining daily new-chat budget**.
- Optional: a **progress-to-next-level** indicator (compute from `level_updated_at` + the ladder's `step_days` for the current level; level 3 has no next).

---

## Deliverable 3 — Manual grade-override control (D-11, D-15)

**Endpoint:** `PATCH /api/v1/senders/{slug}/grade`, body `GradeOverrideRequest` = `{ "current_level": <1..3> }` (hard bounds ge=1, le=3 → 422 on breach). **Response:** the updated `SenderResponse` (200).

**UI requirements:**
- A grade-override control on the sender card that PATCHes `{current_level}`.
- **Note to surface in the UI:** the override **resets the progression timer** — it sets `level_updated_at = NOW()` (identical to auto-progression; no separate frozen flag), so the account starts fresh at the chosen level (D-15). After a successful PATCH, re-read the card from the returned `SenderResponse` (its `level_updated_at` will be "now").

---

## Removals to purge from the frontend (D-04, D-07)

The following backend fields are **gone** from the contract (migration 059) — remove any UI that reads/writes them:

1. **Campaign `max_new_dialogs_per_day` form field** — the per-campaign new-dialog cap no longer exists. Remove the field from the campaign create/edit form.
2. **Sender daily-message (`per_day`) rate editor** — the per-sender daily-message rate cap is removed. Remove the daily-message-limit field from the sender editor. (Note: `per_day` now appears in the contract **only** as the grade-ladder `levelN_chats_per_day` fields — those are Deliverable 1, not the retired rate field.)

### Sender-list "TODAY x/150" usage column — must change

The sender list currently renders a **"TODAY x/150"** usage column as `sent_today / rate_per_day`. The numerator `SenderResponse.sent_today` was computed specifically so `{sent_today}/{rate_per_day}` never desyncs (see `app/routers/senders.py` sent_today logic and `app/schemas/__init__.py`). **With the daily-message cap removed (D-04), that `/150` denominator no longer exists.**

Instruct the frontend to **either**:
- **Drop the "TODAY x/150" column entirely**, OR
- **Repurpose it as a plain "messages sent today" counter** — numerator only (`sent_today`), **no `/150` denominator**.

(Do **not** substitute `remaining_daily_budget` as the denominator — that is the *new-chat* budget, a different metric from messages-sent.)

---

## Notes

- **No UI-SPEC.md exists for this phase** (user chose to proceed without one). This note + the regenerated `lovable-handoff/openapi.json` / `types/api.ts` ARE the contract for the sibling repo.
- 22-VALIDATION marks the three UI behaviors above as **manual-only** verification (cross-repo UI checkpoint).
- Regenerate the contract any time the backend changes: `bash scripts/export-handoff.sh` from this repo root (rebuilds api, dumps openapi, regenerates types).
