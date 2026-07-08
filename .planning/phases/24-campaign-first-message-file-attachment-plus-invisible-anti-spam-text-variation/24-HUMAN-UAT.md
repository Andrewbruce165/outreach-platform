---
status: partial
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
source: [24-VERIFICATION.md]
started: 2026-07-08T08:00:00Z
updated: 2026-07-08T08:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. D-06 live smoke — real photo arrives as inline photo
expected: Attach a real .jpg/.png (not a PDF) to a test campaign with variation_enabled ON, one warmed sender, one controlled test contact. Start the campaign and let the worker send on normal timing. On the recipient's Telegram Desktop AND mobile, the message should render as an INLINE PHOTO (not a document icon) with the opener text as its caption, and `messages.message_type` should be `'photo'` in the DB for that row.
result: [pending]

### 2. Phase 23 scheduling decision — inbox media rendering gap
expected: Decide whether to schedule/fast-track Phase 23 (already planned, 6 PLAN.md files, zero SUMMARY.md — never executed) or a scoped hot-fix to close the gap where the app's own inbox UI doesn't show file-opener attachments (`GET /conversations/{id}/messages` + `MessageResponse` schema + frontend `MessageBubble` never got `message_type`/`file_name`/`mime_type`/`size_bytes`). Not a Phase 24 blocker — deferred by explicit user instruction ("не сейчас") — but flagged so it isn't lost, since Phase 23's own plans already scope the exact fix.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
