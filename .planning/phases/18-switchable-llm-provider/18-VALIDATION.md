---
phase: 18
slug: switchable-llm-provider
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-02
---

# Phase 18 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.23+ |
| **Config file** | `pyproject.toml` / `pytest.ini` (asyncio mode auto) + `tests/conftest.py` |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_provider.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | quick suite < 30s (no network); full suite ~865 tests |

**CRITICAL:** Tests ONLY via the test-overlay (ephemeral `db-test` in tmpfs). NEVER `docker compose run --rm api pytest` without overlay — conftest guard fires; prod DROP SCHEMA history (2026-05-26 incident).

---

## Sampling Rate

- **After every task commit:** Run `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_*.py -x`
- **After every plan wave:** Run full suite via test-overlay
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD at planning | — | — | LLMP-01/02 (llm_settings table + default-off resolution) | integration | `pytest tests/test_llm_settings_api.py -x` | ❌ W0 | ⬜ pending |
| TBD at planning | — | — | LLMP-04 (key encrypted at rest, masked in response, absent from logs) | integration + grep guard | `pytest tests/test_llm_settings_api.py -x` | ❌ W0 | ⬜ pending |
| TBD at planning | — | — | LLMP-05 (test-connection probe valid/invalid) | integration (mocked client) | `pytest tests/test_llm_settings_api.py::test_test_connection -x` | ❌ W0 | ⬜ pending |
| TBD at planning | — | — | LLMP-06 (fallback only on key-level errors) | unit | `pytest tests/test_llm_fallback.py -x` | ❌ W0 | ⬜ pending |
| TBD at planning | — | — | LLMP-07 (logger records provider + key_source) | integration | `pytest tests/test_llm_logger_provider.py -x` | ❌ W0 | ⬜ pending |
| TBD at planning | — | — | LLMP-08 (model list family/capability filter) | unit | `pytest tests/test_llm_models_filter.py -x` | ❌ W0 | ⬜ pending |
| TBD at planning | — | — | LLMP-09/10 (capability gating + clamp, reasoning floor ≥4000) | unit | `pytest tests/test_llm_capabilities.py -x` | ❌ W0 | ⬜ pending |
| TBD at planning | — | — | LLMP-11 (adapter translation both providers; answerer + warmup routed) | unit + integration | `pytest tests/test_llm_provider.py tests/test_ai_engine.py -x` | partial | ⬜ pending |
| TBD at planning | — | — | LLMP-12 (Whisper + embeddings stay on platform OpenAI) | grep/introspection guard | `pytest tests/test_llm_isolation.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*
*Task IDs filled in by gsd-planner when PLAN.md files are created.*

---

## Wave 0 Requirements

- [ ] `tests/test_llm_provider.py` — adapter translation both directions (LLMP-11)
- [ ] `tests/test_llm_capabilities.py` — capability gating + clamp/green-corridor (LLMP-09/10)
- [ ] `tests/test_llm_fallback.py` — `is_key_level_error` taxonomy (LLMP-06)
- [ ] `tests/test_llm_settings_api.py` — settings CRUD + masking + test-connection (LLMP-01/02/04/05)
- [ ] `tests/test_llm_models_filter.py` — server-side model filter (LLMP-08)
- [ ] `tests/test_llm_logger_provider.py` — provider/key_source columns (LLMP-07)
- [ ] `tests/test_llm_isolation.py` — Whisper/embeddings still use platform singleton (LLMP-12)
- [ ] Framework install: add `anthropic>=0.69,<1.0` to `requirements.txt`, rebuild api + listener before running tests
- [ ] Update `tests/test_ai_engine_empty_retry.py` — empty-guard/retry must work through the adapter for both providers

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live `/v1/models` list from real provider keys | LLMP-08 | Needs a real (billable) API key; CI uses mocked responses | In UI Settings, enter a real key, open model selector, confirm chat-capable models appear and embeddings/whisper/tts are absent |
| Test-connection against live providers | LLMP-05 | Cheap but billable network call | Enter valid key → green check; enter garbage key → invalid status shown |
| End-to-end: switched model answers in chat | LLMP-11 | Full loop involves Telegram + live LLM | Switch workspace to Claude in Settings, message a test contact, confirm reply and `llm_calls` row shows provider=anthropic, key_source=byok |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
