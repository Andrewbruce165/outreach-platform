---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 02
type: execute
wave: 2
depends_on: ['17-01']
files_modified:
  - app/services/checker.py
autonomous: true
requirements: [SRLD-01, SRLD-02, SRLD-07]
must_haves:
  truths:
    - "When the checker resolves a registered phone, the captured @username flows through to contacts.tg_username_resolved (the worker already writes res.get('username') at worker:875)"
    - "A is_registered=false cache row from a suspect/low-confidence resolver is NOT served by the checker's _lookup_cache → forces a live re-resolve (the Igor cross-contamination fix)"
    - "A clean/high-confidence is_registered=false cache row IS still served (no needless re-resolve)"
    - "contacts.username (CSV provenance) is never written by the capture path"
  artifacts:
    - path: "app/services/checker.py"
      provides: "resolve_phone_with_fallback returns username; _lookup_cache confidence-gates is_registered=false reads"
      contains: "username"
  key_links:
    - from: "checker.py::resolve_phone_with_fallback"
      to: "contact_check_worker._apply_results (tg_username_resolved = res.get('username'))"
      via: "return-dict key 'username'"
      pattern: "\"username\""
    - from: "checker.py::_lookup_cache"
      to: "contacts.tg_probe_state / tg_confidence"
      via: "correlated EXISTS on phone+workspace"
      pattern: "tg_probe_state|tg_confidence"
---

<objective>
Make the checker a pure filter that ALSO captures the transferable `@username` (D-06/SRLD-01, SRLD-02), and stop serving poisoned `is_registered=false` cache rows blind (D-12/SRLD-07, checker read site). This is the checker half of the resolve redesign; the sender half is 17-03 (different file, parallel).

Purpose: Username capture gives the sender a cheap, safe, transferable tier-2 resolve (`ResolveUsername`), unlike the per-account `access_hash` which can never be reused. The confidence-gated read fixes the Igor incident root cause — a false-negative cached by a US/throttled checker currently short-circuits a re-check before Telegram is ever called, so the live re-resolve never happens.

Output: `app/services/checker.py` edited in two places. NO migration (all storage columns already exist: `contacts.tg_username_resolved` mig 013, `contacts_cache.username`, `contacts.tg_probe_state`/`tg_confidence` mig 034).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-CONTEXT.md
@.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-RESEARCH.md
@.planning/debug/checker-fn-igor-base.md

<interfaces>
<!-- Exact current shapes the executor changes. -->

checker.py:102-103 (resolve_phone_with_fallback, ResolvePhone success branch) — CURRENT:
```python
if result and result.users:
    return {"is_registered": True, "telegram_id": result.users[0].id}   # .username DISCARDED
```
checker.py:146 (import fallback success branch) — CURRENT:
```python
return {"is_registered": True, "telegram_id": imported_user.id}          # .username DISCARDED
```
checker.py:182-202 (_lookup_cache) — CURRENT SQL serves is_registered blind:
```sql
SELECT is_registered, telegram_id FROM contacts_cache
 WHERE workspace_id = :w AND phone = :p AND updated_at > NOW() - INTERVAL '7 days'
 ORDER BY updated_at DESC LIMIT 1
```
Downstream consumer (NO change needed): contact_check_worker.py:875 already does
`tg_username_resolved = :uname` with `"uname": res.get("username")` — it goes live the moment the checker returns "username".

contacts table confidence columns (mig 034, exist): tg_probe_state ('clean'|'suspect'|NULL), tg_confidence ('high'|NULL).
contacts_cache.source — resolver provenance string.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Checker captures @username from ResolvePhone and ImportContacts (SRLD-01, SRLD-02)</name>
  <files>app/services/checker.py</files>
  <read_first>
    - app/services/checker.py:69-146 (resolve_phone_with_fallback — full function; two success returns to edit at :103 and :146)
    - app/services/contact_check_worker.py:864-892 (proof the worker already persists res.get("username") into tg_username_resolved — no worker edit needed)
    - .planning/phases/17-...-/17-CONTEXT.md D-06, D-07 (capture username; durable on contacts.tg_username_resolved + contacts_cache; never clobber CSV contacts.username)
    - .planning/phases/17-...-/17-RESEARCH.md §"Pattern 2: Username capture in the checker"
    - tests/test_checker.py (the SRLD-01 RED test test_username_capture from 17-01)
  </read_first>
  <behavior>
    - Test: ResolvePhone returns a user with `.username="durov"` → `resolve_phone_with_fallback` returns dict containing `"username": "durov"` (plus is_registered=True, telegram_id).
    - Test: ResolvePhone empty, ImportContacts returns user with `.username="hidden_user"` → returned dict has `"username": "hidden_user"`.
    - Test: a not-registered / invalid phone → returned dict has `"username": None` (key present, value None) — keep the return shape uniform so the worker's `res.get("username")` never KeyErrors.
  </behavior>
  <action>
    In `app/services/checker.py::resolve_phone_with_fallback`:
    1. ResolvePhone success branch (currently line 103): change
       `return {"is_registered": True, "telegram_id": result.users[0].id}`
       to `return {"is_registered": True, "telegram_id": result.users[0].id, "username": result.users[0].username}`.
    2. ImportContacts fallback success branch (currently line 146): change
       `return {"is_registered": True, "telegram_id": imported_user.id}`
       to `return {"is_registered": True, "telegram_id": imported_user.id, "username": getattr(imported_user, "username", None)}`.
    3. Every `{"is_registered": False, "telegram_id": None}` return in the function (lines 108, 119, 134, 137) — add `"username": None` so the dict shape is uniform.
    Do NOT touch the docstring's privacy caveat. Do NOT change contact_check_worker.py — it already writes `tg_username_resolved = res.get("username")` (worker:875) and never touches `contacts.username` (CSV). Confirm by reading worker:864-892.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_checker.py tests/test_contact_check_worker.py -k "username_capture or captured_username" -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n '"username":' app/services/checker.py` shows the key present in BOTH the ResolvePhone success and ImportContacts success returns.
    - `grep -n "contacts.username\|c.username" app/services/contact_check_worker.py` shows NO write to `contacts.username` from the capture path (only `tg_username_resolved`).
    - `pytest tests/test_checker.py -k username_capture -x` exits 0.
    - `pytest tests/test_contact_check_worker.py -k captured_username -x` exits 0 (tg_username_resolved set, contacts.username untouched).
  </acceptance_criteria>
  <done>resolve_phone_with_fallback returns username on both registered paths and None elsewhere; worker persistence GREEN; CSV username untouched.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Confidence-gate the checker's _lookup_cache is_registered=false read (SRLD-07)</name>
  <files>app/services/checker.py</files>
  <read_first>
    - app/services/checker.py:175-202 (_lookup_cache — current blind SELECT)
    - app/services/checker.py:342-353 (consumption site: check_phones reads cache BEFORE Telegram)
    - .planning/debug/checker-fn-igor-base.md §"Why a plain reset is NOT enough" (poisoned cache served cross-sender before Telegram is the root cause)
    - .planning/phases/17-...-/17-CONTEXT.md D-12, D-13 (gate the READ; never delete cache; suspect false → live re-resolve)
    - .planning/phases/17-...-/17-RESEARCH.md §"Pattern 3: Confidence-gated cache read" + §"Open Questions #1" (join-cardinality — conservative predicate)
    - tests/test_checker.py (SRLD-07 RED test test_confidence_gated_cache_checker_read from 17-01)
  </read_first>
  <behavior>
    - Test: cache row is_registered=false for phone P, and a matching contacts row for P in the workspace has tg_probe_state='suspect' (or tg_confidence NULL) → `_lookup_cache(workspace_id, P)` returns None (NOT served → live re-resolve).
    - Test: cache row is_registered=false for P, matching contact tg_probe_state='clean' AND tg_confidence='high' → `_lookup_cache` returns the row (clean false IS served).
    - Test: cache row is_registered=TRUE → always served regardless of confidence (only false-negatives are suspect-gated; a positive is not a contamination risk here).
  </behavior>
  <action>
    Modify `app/services/checker.py::_lookup_cache` so a cached `is_registered=false` row is suppressed when the matching contact is suspect/low-confidence. Conservative predicate (per Research OQ#1 — a phone may map to multiple contacts; when ANY matching contact is suspect, fall through to live resolve):

    Keep the existing SELECT but only RETURN a `is_registered=false` row when NO matching contacts row in the same workspace is suspect. Concretely, after fetching `row`, if `row.is_registered is False`, run a correlated check:
    ```sql
    SELECT 1 FROM contacts
     WHERE workspace_id = :workspace_id
       AND phone = :phone
       AND (tg_probe_state = 'suspect' OR tg_confidence IS DISTINCT FROM 'high')
     LIMIT 1
    ```
    If that returns a row → the cached false is suspect → return None (force live re-resolve). If it returns nothing → serve the false row as before. Positive (`is_registered=true`) rows are served unchanged.
    Implement this in the same `AsyncSessionLocal()` block (async). Do NOT delete any cache row (ROADMAP "кэш не чистим"). Do NOT change the consumption site at check_phones:344 — it already handles `None` as a cache miss.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_checker.py -k "confidence_gated_cache" -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "tg_probe_state\|tg_confidence" app/services/checker.py` returns ≥1 hit inside `_lookup_cache`.
    - `grep -n "DELETE FROM contacts_cache" app/services/checker.py` returns 0 hits (cache never purged).
    - `pytest tests/test_checker.py -k confidence_gated_cache -x` exits 0 (suspect false → None; clean false → served; positive → served).
  </acceptance_criteria>
  <done>_lookup_cache suppresses suspect is_registered=false rows, serves clean false + all positives, deletes nothing.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_checker.py tests/test_contact_check_worker.py -x` GREEN for SRLD-01/02/07 checker-side tests.
- `grep -n '"username"' app/services/checker.py` — username captured on both registered returns.
- No migration file added (verify `ls migrations/044* 2>/dev/null` is empty — Phase 17 needs none).
</verification>

<success_criteria>
- SRLD-01 (checker emits username), SRLD-02 (persisted to tg_username_resolved, CSV untouched), SRLD-07 (checker read gated) GREEN.
- Full suite stays green after this plan's wave (`pytest` full, TEST_EXIT==0).
</success_criteria>

<output>
After completion, create `.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-02-SUMMARY.md` noting the exact return-shape change, the chosen suspect predicate (the `tg_confidence IS DISTINCT FROM 'high'` clause), and confirmation that no migration was needed.
</output>
