---
phase: 24
slug: campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-07
---

# Phase 24 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (async, existing suite) |
| **Config file** | `tests/conftest.py` (test-overlay guarded) |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -k "variation or attachment or send_file or file_opener" -q` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` |
| **Estimated runtime** | ~60–120 seconds (full); ~15s (quick subset) |

> ⚠ NEVER run `docker compose run --rm api pytest` without the test-overlay — DATABASE_URL points at prod and the conftest teardown runs DROP SCHEMA. Always use both `-f` files.

---

## Sampling Rate

- **After every task commit:** Run the quick run command (targeted `-k`)
- **After every plan wave:** Run the full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

> Task IDs are filled in by the planner once PLAN.md files exist. Rows below are the
> derived-from-CONTEXT verification targets (no REQ-IDs mapped in ROADMAP — success criteria
> derive from decisions D-01..D-20).

| Behavior | Decision | Test Type | Automated Command | Status |
|----------|----------|-----------|-------------------|--------|
| Variation invisibility invariant: `strip(zero-width + NBSP + thin-space) == original` | D-09/D-10/D-14 | unit | `pytest -k variation` | ⬜ pending |
| Byte-uniqueness: two variations of same text differ in bytes | D-16 | unit | `pytest -k variation` | ⬜ pending |
| Variation never inserts inside URLs / bare domains / @mentions / emoji / markdown delimiters | D-09 | unit | `pytest -k variation` | ⬜ pending |
| Density stays within green corridor (≤ cap insertions) | D-15 | unit | `pytest -k variation` | ⬜ pending |
| `send_file` blob→temp→send round-trip with preserved extension (photo→photo) | D-06/D-08 | unit (Telethon mocked) | `pytest -k send_file` | ⬜ pending |
| `send_file` auto-media flag defaults preserve existing force_document=True callers | D-06 | unit | `pytest -k send_file` | ⬜ pending |
| Caption >1024 → overflow branch (file no-caption + follow-up text) reused | D-07 | unit (Telethon mocked) | `pytest -k send_file` | ⬜ pending |
| Campaign attachment upload stores blob; >50MB → FILE_TOO_LARGE | D-03 | integration | `pytest -k attachment` | ⬜ pending |
| Attachment DELETE clears blob | D-19 | integration | `pytest -k attachment` | ⬜ pending |
| Enqueue creates `item_type='file'` row with caption when campaign has attachment | D-05/D-17 | integration | `pytest -k attachment` | ⬜ pending |
| `rerender_pending_queue` propagates template edit to pending file-row captions | D-17 | integration | `pytest -k rerender` | ⬜ pending |
| Worker applies variation to opener copy only (kind != followup, flag on); DB stays clean | D-12/D-14 | integration | `pytest -k variation` | ⬜ pending |
| File-opener messages inbox row carries concrete message_type (photo/video/document) + file_name/mime_type/size_bytes so it renders a media bubble (bridges Phase 23 mig 053); text opener row stays message_type='text' | D-05/D-06 (+ Phase 23 mig 053) | integration | `pytest -k file_opener` | ⬜ pending |
| `duplicate_campaign` copies attachment blob + variation flag | D-20 | integration | `pytest -k attachment` | ⬜ pending |
| New NOT NULL columns have server_default (no NotNullViolation on raw INSERT) | D-04/D-13 | integration | `pytest -k campaign` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_variation.py` — pure-function tests for the invisible-variation module (no DB, no Telethon)
- [ ] `tests/test_campaign_attachment.py` — attachment upload/delete/enqueue/duplicate (async DB fixtures)
- [ ] Extend existing `send_file` tests for blob source + auto-media + overflow (Telethon mocked)

*Existing pytest infrastructure + async DB fixtures cover the rest — no framework install needed.*

---

## Manual-Only Verifications

| Behavior | Decision | Why Manual | Test Instructions |
|----------|----------|------------|-------------------|
| Real photo arrives as a photo (not document) in a real Telegram client | D-06 | Requires a live Telegram account + recipient; auto-media detection is client-rendered | Attach a `.jpg` to a test campaign, enqueue to a controlled test contact, confirm it renders as inline photo with caption |
| Invisible chars truly invisible in Telegram Desktop/mobile clients | D-09/D-10 | Rendering is client-side; automated test can only assert codepoint stripping, not glyph rendering | Send a varied opener to a test contact, visually confirm no boxes/artifacts/extra spacing |
| Variation actually reduces dedup/spam flags | D-11 | Accepted risk; Telegram dedup internals are not observable | Longitudinal — monitor `sender_restriction_events` for spam_limited rate before/after enabling variation |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
</content>
