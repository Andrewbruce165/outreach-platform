---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 04
subsystem: listener
tags: [telethon, listener, inbox, incoming-media, message-type, lazy-download]

# Dependency graph
requires:
  - phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
    provides: "migration 053 messages.message_type/file_name/mime_type/size_bytes columns (23-01); bridged by mig 055"
provides:
  - "save_message(): keyword-optional message_type/file_name/mime_type/size_bytes persisted to the mig-053 columns"
  - "handle_incoming_message media classification: concrete message_type (photo/video/voice/document/text) + pre-download File metadata"
affects: [23-03, 23-05, inbox-ui, download-endpoint]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "telethon File wrapper (message.file.name/.mime_type/.size) read WITHOUT downloading bytes (D-15 lazy download)"
    - "DB DEFAULT 'text' as sole source of message_type default (messages has no ORM model — avoids ORM default drift)"

key-files:
  created: []
  modified:
    - "app/services/listener.py — save_message() +4 media params/columns; handle_incoming_message media-type classifier + metadata capture threaded into the incoming save_message call"

key-decisions:
  - "Classification computed once, immediately before the single shared incoming save_message call, so it covers all three branches (voice / photo-video-document / text) with no duplication."
  - "Photo/video/document persist metadata only — NO download_media (D-15/D-16 lazy bytes fetched later by the 23-05 endpoint). The pre-existing voice-transcription temp download is untouched."
  - "New save_message params are keyword-optional (message_type defaults 'text', metadata None) so every existing text/voice/outbound call-site is byte-identical; the mig-053 DB DEFAULT backfills message_type when omitted."
  - "Never read the deprecated telethon file id attribute (Pitfall 6) — only name/mime/size."

patterns-established:
  - "Incoming media becomes a typed, metadata-bearing row the inbox renders as a file bubble and the download endpoint lazily fetches, replacing the old text-label-only storage."

requirements-completed: [INBM-04]

# Metrics
duration: ~22min
completed: 2026-07-08
---

# Phase 23 Plan 04: Listener Incoming Media Summary

**The listener now persists incoming photo/video/voice/document as a typed row (`message_type` + `file_name`/`mime_type`/`size_bytes`) read from the telethon File wrapper WITHOUT downloading bytes, while voice transcription and the photo/video/document AI-dispatch label are fully preserved.**

## Performance

- **Duration:** ~22 min (incl. resolving a shared docker network-pool exhaustion)
- **Completed:** 2026-07-08
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- `save_message()` gained keyword-optional `message_type`/`file_name`/`mime_type`/`size_bytes` params and the INSERT now writes the four mig-053 media columns; the `IntegrityError` → `return False` duplicate handling is preserved.
- `handle_incoming_message` computes a concrete `message_type` (photo/video/voice/document/text) and reads `event.message.file.{name,mime_type,size}` pre-download, threading them into the single incoming `save_message` call.
- Voice path unchanged: still transcribes to `message_text`, keeps `is_voice=True`, and reaches the AI-dispatch block (transcript still fed to the answerer). Photo/video/document keep the `document_info` label + caption composition.
- No `download_media` added for photo/video/document (only the pre-existing voice transcription download remains); no reference to the deprecated file id attribute.

## Task Commits

Each task was committed atomically:

1. **Task 1: save_message() media params + INSERT columns** - `487c664` (feat)
2. **Task 2: incoming media classification + metadata capture** - `cfe4a17` (feat)

## Files Created/Modified
- `app/services/listener.py` — `save_message()` signature + INSERT extended with the four media columns (Task 1); media-type classifier + File-wrapper metadata capture inserted just before the incoming `save_message` call, threaded into it (Task 2).

## Verification
- `pytest tests/test_phase23_inbox_mutations.py -k save_message_persists` → 1 passed (XPASS).
- `pytest tests/test_phase23_inbox_mutations.py -k incoming_media` → 3 passed (typed row + metadata; voice still transcribed; idempotent duplicate).
- Full `test_phase23_inbox_mutations.py`: 2 passed, 10 xpassed, 10 xfailed (the 10 xfailed belong to 23-03/23-05 endpoints not yet merged to main), 0 failures.
- grep: no `.file.id` in `listener.py`; the incoming `save_message(` call carries `message_type=`; `download_media` only in the voice branch.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test invocation adapted around exhausted docker network address pool**
- **Found during:** Task 1 (automated verify)
- **Issue:** ~30 parallel worktree agents exhausted the docker daemon's default address pools (all 172.17–172.31/16 and all 192.168.x/20 chunks allocated), so `docker compose run` could not create the test-overlay network. `docker network prune` and attaching to the prod `tg-outreach_default` network were both denied as shared-resource modifications.
- **Fix:** Created my OWN isolated, dedicated network (`aa9cdac_testnet`, subnet `10.211.0.0/24`, an entirely free range), brought up only `db-test` on it, and ran pytest with `--no-deps` (so the prod `db`/`outreach-platform-db` container is never touched) plus `--env-file` (worktree has no `.env`) with the test DSN still overridden to `outreach_test`. Test semantics unchanged; my network is torn down at completion.
- **Files modified:** none (test invocation only; ephemeral override lives in scratchpad, not the repo)
- **Verification:** all 4 target tests pass.
- **Committed in:** n/a (no code change)

---

**Total deviations:** 1 (blocking — test invocation only, no source change).
**Impact on plan:** None on delivered code. Both tasks implemented exactly per spec.

## Issues Encountered
- **Stale worktree base — RESOLVED by fast-forward, not cherry-pick.** This worktree branched from `92bd54b` (Phase 19 complete), 5 phases behind main (`a491c0d`, Phase 24). Because `92bd54b` is a direct ancestor of main, I fast-forwarded the worktree to main's HEAD with zero conflicts BEFORE implementing — giving the correct base including this plan's Wave-1 dependencies (23-01 migration 053, 23-02 service methods). My two feature commits (`487c664`, `cfe4a17`) therefore sit directly on top of main's HEAD and fast-forward / cherry-pick trivially. This differs from the sibling 23-02 executor, which stayed on the stale base.
- **STATE.md / ROADMAP.md / REQUIREMENTS.md intentionally NOT committed here.** This plan ran as a parallel Wave-2 executor alongside 23-03 and 23-05, which mutate the same shared planning files. To avoid merge contention, wave-level `state advance-plan` / `update-progress` / `record-metric` / `roadmap update-plan-progress` / `requirements mark-complete [INBM-04]` are left for the orchestrator to run once against main after all Wave-2 agents complete. Only the plan-owned `listener.py` code and this SUMMARY.md are committed.

## User Setup Required
None.

## Next Phase Readiness
- 23-05 download endpoint can lazily fetch the bytes for these typed incoming media rows (message_type + metadata now persisted).
- Deploy note: rebuild BOTH `api` and `listener` containers (`docker compose up -d --build api listener`).

---
*Phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui*
*Completed: 2026-07-08*

## Self-Check: PASSED

- SUMMARY.md present at plan directory.
- Task commits `487c664`, `cfe4a17` present in git log.
- `app/services/listener.py` present with both changes.
