---
quick_id: 260629-g2z
slug: prompt-template-v2
title: Multi-tenant prompt template v2 (preset-driven core_directive)
date: 2026-06-29
status: complete
commits: 4022984..929c293
---

# Quick Task 260629-g2z — SUMMARY

## What shipped

Reworked `build_system_prompt` onto the multi-tenant v2 scaffold. `<core_directive>`
is now universal/campaign-agnostic; the three things that vary per campaign —
objective, disclosure policy, agent authority — render from preset libraries
(`_OBJECTIVE_LINES` / `_DISCLOSURE_LINES` / `_AUTHORITY_LINES`) keyed on four new
nullable campaign columns. NULL presets fall back to safe defaults
(`disclosure→reveal_nothing`, `authority→handoff_only`, `objective→primary_goal`),
so every existing campaign reproduces the prior call-booking text — nothing breaks.

## Commits (atomic, on `main`)

1. `4022984` — migration 037 + ORM columns (objective/disclosure/authority_preset, style_examples)
2. `f903b95` — schemas (Literal enums) + router wiring (create / response / clone; PATCH via setattr)
3. `2f38b1b` — ai_engine v2: `<role>`→`<identity>`, `<core_directive>`, objective/disclosure/authority blocks, preset libs, rewritten `<message_style>` (one-message rule, curated banlist, before-you-send checklist, both-language few-shot, `style_examples` override), `<dialogue_flow>`/`<banlist>` text updates, get_context SELECT
4. `929c293` — tests (PMT-01/06 `<role>`→`<identity>` + 10 new v2 tests) and de-bracket core_directive/preset block refs

## Key decisions

- core_directive universal; goal/disclosure/authority preset-driven (user D1).
- Few-shot fallback ships BOTH Russian and English; overridable via `campaign.style_examples` (user D2).
- Disclosure-gated conditionals: leak self-check line + disclosure few-shot only for
  `reveal_nothing` / (leak-line also for `list_price_ok`).
- Dropped the "split into a couple of short messages" tail (contradicted the one-message rule).
- Block names referenced in plain English (no literal `<tag>`) inside core_directive /
  preset lines — avoids the model echoing tags.

## Verification

- `tests/test_ai_engine.py`: 20 passed (10 PMT + 10 v2).
- campaign router/model/v2 tests: 54 passed (migration 037 applied cleanly in test DB).
- Run via test-overlay only (`docker-compose.test.yml`).

## Not done / follow-ups

- **Not deployed.** Deploy = `docker compose up -d --build api` (applier picks up mig 037),
  then `--build listener`.
- Frontend (separate repo `aimly-tg-outreach`) does not yet send the 4 new fields —
  backend accepts them; UI wiring is a separate task.
- STATE.md "Quick Tasks Completed" row added but left **unstaged**: STATE.md carried a
  parallel agent's uncommitted edits, so it was not committed here (parallel-commit safety).
