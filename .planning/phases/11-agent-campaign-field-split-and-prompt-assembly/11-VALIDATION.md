---
phase: 11
slug: agent-campaign-field-split-and-prompt-assembly
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-24
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 11-RESEARCH.md §Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (в проде) |
| **Config file** | `tests/conftest.py` (ephemeral db-test в tmpfs через test-overlay) |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_ai_engine.py tests/test_migration_030.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~30s quick / full suite varies |

> **НИКОГДА** `docker compose run --rm api pytest` без overlay (DROP SCHEMA уйдёт на прод). **НИКОГДА** `down -v` (удаляет прод-volume).

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_ai_engine.py tests/test_migration_030.py -x` (< 30s)
- **After every plan wave:** Run full suite (test-overlay)
- **Before `/gsd:verify-work`:** Full suite green + frontend `cd ../aimly-tg-outreach && bun run tsc` clean
- **Max feedback latency:** ~30 seconds (quick), full suite per wave

---

## Per-Task Verification Map

| Requirement | Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|-----------|-------------------|-------------|--------|
| MIG-01 | `voice_baseline`→`tone_preset` маппится, `voice_baseline` дропнут | integration | `pytest tests/test_migration_030.py::test_tone_preset_backfill -x` | ❌ W0 | ⬜ pending |
| MIG-02 | `tone` (JSONB) и `tone_of_voice` дропнуты | integration | `pytest tests/test_migration_030.py::test_legacy_tone_dropped -x` | ❌ W0 | ⬜ pending |
| MIG-03 | `success_criteria`→`lead_trigger_hint` (existing hint не теряется), `success_criteria` дропнут | integration | `pytest tests/test_migration_030.py::test_lead_hint_merge -x` | ❌ W0 | ⬜ pending |
| FLD-01..06 | новые колонки существуют с правильными CHECK/типом | integration | `pytest tests/test_migration_030.py::test_new_columns -x` | ❌ W0 | ⬜ pending |
| PMT-01 | блоки в точном порядке §7 (ИДЕНТИЧНОСТЬ→…→ФОРМАТ ОТВЕТА) | unit | `pytest tests/test_ai_engine.py::test_prompt_block_order -x` | ⚠️ extend | ⬜ pending |
| PMT-02 | tone рендерится ТОЛЬКО из `tone_preset`; нет voice_baseline/слайдеров | unit | `pytest tests/test_ai_engine.py::test_tone_single_source -x` | ⚠️ extend | ⬜ pending |
| PMT-03 | `dialogue_flow` → нумерованные стадии; нет static goal | unit | `pytest tests/test_ai_engine.py::test_dialogue_flow_render -x` | ⚠️ extend | ⬜ pending |
| PMT-04 | `arguments_facts` + guard «не выдумывай» | unit | `pytest tests/test_ai_engine.py::test_arguments_facts_guard -x` | ⚠️ extend | ⬜ pending |
| PMT-05 | дедуп: правило не появляется дважды (поведенческое ядро) | unit | `pytest tests/test_ai_engine.py::test_rules_dedup_no_duplicate -x` | ⚠️ extend | ⬜ pending |
| PMT-06 | `[ЗАДАЧА+КОМУ ПИШЕМ]` из campaign; задача убрана из `who_is_agent` | unit | `pytest tests/test_ai_engine.py::test_task_source_campaign -x` | ⚠️ extend | ⬜ pending |
| PMT-07 | brief raw-текст НЕ в промпте | unit | `pytest tests/test_ai_engine.py::test_brief_excluded -x` | ⚠️ extend | ⬜ pending |
| RT-01 | `response_speed=manual`→delay==`response_delay_seconds`; instant→~0; human→default | unit | `pytest tests/test_listener_response_speed.py -x` | ❌ W0 | ⬜ pending |
| UI-FLD-01..03 | tsc clean; формы рендерят новые поля | manual + tsc | `cd ../aimly-tg-outreach && bun run tsc` | manual UAT | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Behavioral Core — как доказать «нет дубля»

Главная цель фазы — устранить повтор инструкций в промпте. Тестируется детерминированно:

1. **Golden-prompt order test (PMT-01):** собрать `build_system_prompt` с полностью заполненным агентом+кампанией, проверить порядок тегов §7: `assert prompt.index("<role>") < prompt.index("<tone>") < prompt.index("<dialogue_flow>") < ...`.
2. **Single-source tone (PMT-02):** задать `tone_preset` + остаточные `voice_baseline` в БД → в промпте есть строка пресета и НЕТ «Baseline persona»/«Tone calibration»/слайдеров.
3. **No-duplicate rules (PMT-05):** агент.rules = «Не давить.» + campaign_rules = «Не давить.\nОтвечать кратко.» → `assert prompt.count("Не давить") == 1` и «Отвечать кратко» присутствует.
4. **Tone-not-in-rules (D-03):** задать `tone_preset` → блок `[ТОН]` единственное место с tone-инструкцией (нет tone-текста внутри `<rules>`).

---

## Wave 0 Requirements

- [ ] `tests/test_migration_030.py` — MIG-01/02/03 + FLD-01..06 (применить 030 на эфемерной БД, проверить backfill + drop + CHECK)
- [ ] `tests/test_listener_response_speed.py` — RT-01 (мок context, проверить расчёт delay по enum)
- [ ] Extend `tests/test_ai_engine.py` — PMT-01..07 golden-prompt assertions
- [ ] `tests/conftest.py:127-150` — **ДОБАВИТЬ** `028_*`, `029_campaign_pause_reason.sql`, `030_*.sql` в migration list (иначе UndefinedColumn — RESEARCH Pitfall 3)
- [ ] conftest fixture `test_agent_factory` — расширить под `tone_preset`/`response_speed` (сейчас даёт voice_baseline-эру)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Формы Агента/Кампании рендерят новые поля; редактор стадий add/remove/reorder работает | UI-FLD-01, UI-FLD-02 | Lovable-генерируемый фронт, визуальная проверка | Открыть визард кампании → пройти шаги Agent/Campaign → проверить поля §3/§4 BRIEF + стадии «Ход разговора» |
| openapi.json синхронизирован после изменения схем | UI-FLD-03 | Cross-repo артефакт | `scripts/export-handoff.sh` → diff openapi.json → `bun run tsc` во фронте clean |

---

## Validation Sign-Off

- [ ] All requirements have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (test_migration_030, test_listener_response_speed, conftest migration list)
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s (quick)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
