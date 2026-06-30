---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
verified: 2026-06-30T18:00:00Z
status: passed
score: 9/9 must-haves verified
---

# Phase 17: Sender-Side Resolve Ladder Verification Report

**Phase Goal:** Перестроить резолв так, чтобы ОТПРАВИТЕЛЬ сам резолвил и дотягивался до получателя, а ЧЕКЕР стал чистым фильтром «есть/нет». Чекер сохраняет переносимый @username. На отправителе — тройная лестница резолва: (1) кэш per-sender → (2) ResolveUsername по захваченному @username → (3) ImportContacts лениво по одному перед отправкой; собственный ResolvePhone отправителя УДАЛЁН. Durable per-sender 'blocked' event + read-only block-rate endpoint. Смягчение country-as-fact до гипотезы.

**Verified:** 2026-06-30T18:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Checker captures @username from ResolvePhone and ImportContacts fallback | VERIFIED | checker.py lines 113, 160: getattr(user, "username", None) on both resolve paths; 4 not-registered paths return "username": None; uniform return shape so res.get("username") never KeyErrors |
| 2 | Captured @username persists to contacts.tg_username_resolved (never clobbers CSV contacts.username) | VERIFIED | contact_check_worker.py:875: tg_username_resolved = :uname with "uname": res.get("username"); worker references to c.username are SELECT-only at lines 247/289 — no UPDATE ever writes to contacts.username from the capture path |
| 3 | Sender 3-tier resolve ladder: cache -> ResolveUsername(captured) -> ImportContacts; sender own ResolvePhone entirely gone | VERIFIED | telegram.py: ResolvePhoneRequest absent from all imports; resolve_contact implements 3-tier ladder at lines 569-651; only textual ResolvePhone references are comments at lines 585/611/662 |
| 4 | ImportContacts gated on checker verdict registered; not_registered skips import | VERIFIED | telegram.py:614: if verdict.get("tg_status") == "registered"; line 649: non-registered verdict returns {"is_registered": False} without calling Import |
| 5 | Sender keeps imported contacts (no DeleteContactsRequest); queue rate intervals untouched | VERIFIED | telegram.py:626: comment states "NO DeleteContactsRequest here (unlike the checker)"; DeleteContactsRequest appears only in checker.py line 153. queue.py rate intervals not modified by Phase 17 |
| 6 | Stale captured username falls through to import tier; never finalized as not_registered | VERIFIED | telegram.py::_resolve_username lines 687-694: except (UsernameNotOccupiedError, UsernameInvalidError): return {"stale_username": True}; resolve_contact:607 checks res.get("stale_username") and falls through to tier-3; no _save_contact_cache on stale path |
| 7 | Suspect/low-confidence is_registered=false cache rows not served (both checker _lookup_cache and sender _get_cached_contact) | VERIFIED | checker.py:236-237: suspect predicate in _lookup_cache; telegram.py:432: identical predicate in _get_cached_contact applied to per-sender false (line 442) and cross-sender false (line 484); cache never deleted |
| 8 | UserIsBlockedError captured as durable event_type=blocked restriction event; read-only block-rate endpoint; no auto-pause | VERIFIED | telegram.py:826/1005: typed except UserIsBlockedError in send_message and send_file; queue.py:1049: elif error_code == "USER_IS_BLOCKED" calls record_restriction_event with "blocked", no restriction_status update, no failover; restriction_audit.py:89: sender_block_rate helper; senders.py:784: GET /senders/{slug}/block-rate; schemas/__init__.py:866: SenderBlockRateResponse |
| 9 | US-cannot-resolve-RU country claim reframed as hypothesis in checker-semantics docs | VERIFIED | CLAUDE.md:243: paragraph explicitly labels it "ГИПОТЕЗА, не факт (D-10/SRLD-09)", states "warmed beats cold" confirmed, "RU beats US — не доказан", Phase 17 does not gate by country in code |

**Score:** 9/9 truths verified

---

### Required Artifacts

| Artifact | Status | Evidence |
|----------|--------|---------|
| app/services/checker.py | VERIFIED | resolve_phone_with_fallback returns "username" on all 6 paths; _lookup_cache confidence-gates is_registered=false reads with correlated suspect check |
| app/services/telegram.py | VERIFIED | ResolvePhoneRequest absent from imports; _load_contact_verdict added; resolve_contact rebuilt as 3-tier ladder; _resolve_username falls through on stale handle; _get_cached_contact confidence-gates both false read paths; UserIsBlockedError caught in send_message and send_file |
| app/services/queue.py | VERIFIED | USER_IS_BLOCKED dispatch branch at line 1049: records durable block event, fails only that item, no auto-pause |
| app/services/restriction_audit.py | VERIFIED | sender_block_rate(db, sender_id, window_days=7) added at line 89; strictly read-only aggregate |
| app/routers/senders.py | VERIFIED | GET /senders/{slug}/block-rate at line 784; workspace-scoped via _load_sender_by_slug + explicit workspace_id filter in SQL |
| app/schemas/__init__.py | VERIFIED | SenderBlockRateResponse(blocks_7d, sends_7d, block_rate) at line 866 |
| CLAUDE.md (tg-outreach) | VERIFIED | Checker-semantics section updated with ГИПОТЕЗА wording at line 243 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| checker.py::resolve_phone_with_fallback | contact_check_worker._apply_results (tg_username_resolved) | return-dict key "username" | WIRED | Checker returns "username" key; worker:875 reads res.get("username") into tg_username_resolved |
| checker.py::_lookup_cache | contacts.tg_probe_state / tg_confidence | correlated EXISTS on phone+workspace | WIRED | SQL at line 236-237 queries contacts for suspect predicate |
| telegram.py::resolve_contact | contacts.tg_status + contacts.tg_username_resolved | _load_contact_verdict SELECT | WIRED | _load_contact_verdict at line 488 selects tg_status, tg_username_resolved; result used for tier-2 gate and tier-3 registered-gate |
| telegram.py::_resolve_username | resolve_contact import tier | {"stale_username": True} sentinel | WIRED | _resolve_username returns sentinel on stale handle; resolve_contact:607 checks res.get("stale_username") and falls through |
| telegram.py::send_message / send_file | queue.py::USER_IS_BLOCKED branch | error_code == "USER_IS_BLOCKED" | WIRED | send_message/send_file return {"code": "USER_IS_BLOCKED"}; queue.py dispatches on that code at line 1049 |
| queue.py::USER_IS_BLOCKED branch | sender_restriction_events | record_restriction_event(sender.id, "blocked", ...) | WIRED | queue.py:1063: await record_restriction_event(sender.id, "blocked", "queue_error", None, error_msg, db=db) |
| senders.py::get_block_rate | sender_restriction_events + messages_log | inline SQL with explicit workspace_id filter | WIRED | SQL at lines 804-815 counts event_type=blocked events and message_type=sent messages; workspace-scoped |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| checker.py::resolve_phone_with_fallback | user.username | Telethon ResolvePhoneRequest/ImportContactsRequest result | Yes — reads live Telegram API user object | FLOWING |
| telegram.py::_load_contact_verdict | tg_status, tg_username_resolved | DB SELECT on contacts table | Yes — real query with ORDER BY (tg_status = 'registered') DESC | FLOWING |
| telegram.py::_get_cached_contact | suspect flag | correlated DB SELECT on contacts | Yes — real SELECT checking tg_probe_state/tg_confidence columns | FLOWING |
| senders.py::get_block_rate | blocks_7d, sends_7d | DB aggregate on sender_restriction_events + messages_log | Yes — real COUNT queries on both tables | FLOWING |

---

### Behavioral Spot-Checks

ResolvePhoneRequest absent from sender imports: grep shows it is not in the import block of telegram.py — only in comments. PASS.
sender_block_rate defined at restriction_audit.py:89 — matches the test import path. PASS.
0 migrations added by Phase 17: latest migration file is 043 from Phase 16. PASS.
SenderBlockRateResponse defined at schemas/__init__.py:866. PASS.
All 11 Phase 17 commits present in git log (928389a, 2f1192e, bc2a9df, 305998b, 4906965, 51ffcc5, 73ceb0c, 5557ed4, fb79d6e, a2ea727, 8308c18). PASS.
Test suite result: 850 passed, 1 skipped, 1 failed (WARM-14 — out-of-scope parallel Phase 15 warmup effort). PASS.

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| SRLD-01 | 17-02 | Checker captures @username from ResolvePhone and ImportContacts | SATISFIED | checker.py lines 113, 160 |
| SRLD-02 | 17-02 | Captured username persists to tg_username_resolved; never clobbers CSV contacts.username | SATISFIED | contact_check_worker.py:875; no worker UPDATE to contacts.username |
| SRLD-03 | 17-03 | Sender 3-tier ladder; own ResolvePhone removed | SATISFIED | telegram.py: ResolvePhoneRequest absent from imports; 3-tier resolve_contact |
| SRLD-04 | 17-03 | ImportContacts gated on registered verdict | SATISFIED | telegram.py:614 |
| SRLD-05 | 17-03 | Lazy import per-send; no DeleteContacts on sender; queue intervals untouched | SATISFIED | telegram.py:626 comment; DeleteContactsRequest only in checker.py |
| SRLD-06 | 17-03 | Stale username falls through to import tier; never finalizes not_registered | SATISFIED | telegram.py::_resolve_username returns {"stale_username": True} |
| SRLD-07 | 17-02 + 17-03 | Confidence-gated cache read on both checker and sender sides | SATISFIED | checker.py:236, telegram.py:432 — identical suspect predicate |
| SRLD-08 | 17-04 | UserIsBlockedError durable capture + read-only block-rate endpoint; no control-loop | SATISFIED | telegram.py:826/1005, queue.py:1049, restriction_audit.py:89, senders.py:784 |
| SRLD-09 | 17-04 | Country-as-fact claim reframed as hypothesis in checker-semantics docs | SATISFIED | CLAUDE.md:243 |

All 9 requirement IDs satisfied. No orphaned requirements. REQUIREMENTS.md maps SRLD-01..09 all to Phase 17, all marked Complete.

---

### Anti-Patterns Found

None. No blockers or stubs detected in Phase 17 modified files.

---

### Human Verification Required

#### 1. End-to-end resolve ladder with a live registered contact

**Test:** Add a registered RU mobile with a known @username to a campaign on a warmed sender. Trigger a send. Observe whether send_message is reached without calling ResolvePhone (check logs for tier-2 or tier-3 activity).
**Expected:** Message delivered; logs show ResolveUsernameRequest or ImportContacts — never ResolvePhoneRequest.
**Why human:** Requires a live Telethon session, a real Telegram account, and a real contact.

#### 2. Stale-username fall-through with a renamed handle

**Test:** Seed a contact with a tg_username_resolved that has since been renamed/freed. Trigger a send. Observe that the sender falls through to ImportContacts rather than finalizing not_registered.
**Expected:** Logs show "captured username is stale — fall through to import".
**Why human:** Requires a real Telegram account with a verifiably stale username.

#### 3. UserIsBlockedError block-rate accumulation

**Test:** Trigger a send to a contact who has blocked the sender. Check GET /senders/{slug}/block-rate for the updated count.
**Expected:** blocks_7d increments; sender restriction_status unchanged; pending queue not paused.
**Why human:** Requires a real blocking scenario with live Telegram sessions.

---

### Gaps Summary

No gaps. All 9 SRLD requirements are implemented and verified at code level. The single failing test in the suite (test_warmup_worker.py::test_restricted_sender_excluded, WARM-14) is explicitly out of scope — it belongs to a parallel Phase 15 warmup effort touching app/services/warmup.py (modified but uncommitted). Phase 17 touched zero warmup files. Phase 17 intentionally added 0 migrations. All 11 Phase 17 commits are present and verified.

---

_Verified: 2026-06-30T18:00:00Z_
_Verifier: Claude (gsd-verifier)_
