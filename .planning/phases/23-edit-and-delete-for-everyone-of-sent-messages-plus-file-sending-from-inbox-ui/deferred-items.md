# Phase 23 — Deferred / Out-of-Scope Items

## Pre-existing test-fixture bug: `*_cross_workspace_404` never actually cross-tenant

**Discovered during:** 23-05 (Task 2, download endpoint verification)
**Scope:** Pre-existing — affects Wave-2 tests too (NOT introduced by 23-05).

`tests/test_phase23_inbox_mutations.py` has three tenant-isolation tests:
`test_edit_cross_workspace_404`, `test_delete_cross_workspace_404`,
`test_download_cross_workspace_404`. All three build the "other workspace"
conversation with `test_conversation_factory()` (no explicit `workspace_id`).

But `test_conversation_factory` defaults the conversation's `workspace_id` to its
sender's workspace, and `test_sender_factory` defaults to `test_workspace` — the
SAME workspace the JWT user is `_bind`-ed to. So the conversation is NOT in a
different workspace; the endpoint's `WHERE c.workspace_id = :wid` gate correctly
MATCHES, the handler proceeds to the (unmocked) Telethon call, and the response
is `502` instead of the asserted `404`.

All three tests are `@pytest.mark.xfail(strict=False)`, so this failure is masked
(xfail, not a suite failure). The endpoints' workspace gates are correct by
inspection — identical `c.workspace_id = :wid` filter across edit/delete/download.

**Fix (deferred, not done here to avoid touching Wave-2's committed tests):**
create a genuinely separate workspace for the "other" conversation, e.g. add a
`second_workspace` fixture and pass its id to `test_conversation_factory(
workspace_id=second_ws.id, ...)` in all three cross-workspace tests. Then drop
the `@_WAVE2` xfail marker on them.

**Impact:** none on production behaviour — only reduces automated coverage of the
tenant-isolation gate for these three cases (gate itself is correct).
