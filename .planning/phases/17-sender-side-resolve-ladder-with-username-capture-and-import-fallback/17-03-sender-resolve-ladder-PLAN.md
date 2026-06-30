---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 03
type: execute
wave: 2
depends_on: ['17-01']
files_modified:
  - app/services/telegram.py
autonomous: true
requirements: [SRLD-03, SRLD-04, SRLD-05, SRLD-06, SRLD-07]
must_haves:
  truths:
    - "The sending account resolves a cold contact via cache → ResolveUsername(captured @username) → ImportContacts; the sender's own ResolvePhone is never called"
    - "ImportContacts is attempted only when the checker verdict is registered; not_registered → no import (skip)"
    - "Exactly one import per send, right before send; the sender never deletes the imported contact (hot entity-cache for follow-ups); queue.py rate intervals are untouched"
    - "A stale captured username (UsernameNotOccupiedError/UsernameInvalidError) falls through to the import tier and is NEVER finalized as not_registered"
    - "A suspect is_registered=false cross-sender cache row does not short-circuit the sender's resolve → live re-resolve happens"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "resolve_contact 3-tier ladder (no sender ResolvePhone, Import tier-3 gated on registered); _resolve_username fall-through; _get_cached_contact confidence-gated false read"
      contains: "ImportContactsRequest"
  key_links:
    - from: "telegram.py::resolve_contact"
      to: "contacts.tg_status (import gate) + contacts.tg_username_resolved (tier-2)"
      via: "SELECT verdict + captured username for the phone"
      pattern: "tg_status|tg_username_resolved"
    - from: "telegram.py::_resolve_username"
      to: "resolve_contact import tier"
      via: "stale-username signal → fall through (never cache False)"
      pattern: "UsernameNotOccupiedError|UsernameInvalidError"
---

<objective>
Rebuild the sender's `resolve_contact` into the 3-tier ladder (D-01/D-02): cache(access_hash) → `ResolveUsername`(captured @username) → `ImportContacts`, REMOVING the sender's own `ResolvePhone` entirely. Gate the import on the checker verdict `registered` (D-03/D-11), make a stale captured username fall through to import instead of finalizing False (D-09), and confidence-gate the sender's `is_registered=false` cache read (D-12). This is the structural fix for the live "Barter - ВЭД хук" incident (22 live RU mobiles that the sender's ResolvePhone falsely rejected).

Purpose: A checker's resolve (and its access_hash) can never be reused on a sender (per-account, Telethon — verified). The sender MUST do its own per-recipient lookup. ResolvePhone gave the false "нет" in the incident (throttle/privacy); ResolveUsername(captured) is the safe transferable top tier and ImportContacts additionally surfaces registered-but-privacy-hidden numbers that ResolvePhone misses.

Output: `app/services/telegram.py` resolve path rebuilt. NO migration (tier-2 reads existing `contacts.tg_username_resolved`; gate reads existing `contacts.tg_probe_state`/`tg_confidence`).
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
@.planning/notes/sender-side-resolve-redesign.md

<interfaces>
<!-- Exact current shapes the executor rebuilds. -->

telegram.py:494-556 resolve_contact (CURRENT tail — D-01 removes ResolvePhone):
```python
cached = await self._get_cached_contact(workspace_id, sender_id, phone)
if cached: return cached
if is_username_key(phone):                                   # '@handle' identity-key contacts
    return await self._resolve_username(client, workspace_id, sender_id, phone)
result = await client(ResolvePhoneRequest(phone=phone))      # <-- D-01 DELETES this sender-side tail
...
```
telegram.py:558-603 _resolve_username (CURRENT — D-09 must fall through, not cache False):
```python
except Exception as e:
    if "username_not_occupied" in low or "username_invalid" in low:
        contact_info = {"is_registered": False}
        await self._save_contact_cache(...)   # D-09 FORBIDS caching/finalizing False
        return {"is_registered": False}
```
telegram.py:605-637 check_contact — ready ImportContacts(InputPhoneContact)→users[0] call shape to COPY (OMIT any DeleteContacts).
telegram.py:442-456 _get_cached_contact cross-sender is_registered=false shortcut (CURRENT serves false blind — D-12 gate).

Telethon error imports (verified 1.42.0, telethon.errors):
```python
from telethon.errors import UsernameNotOccupiedError, UsernameInvalidError, PhoneNotOccupiedError, FloodWaitError
```
Existing columns (NO migration): contacts.tg_status, contacts.tg_username_resolved, contacts.tg_probe_state, contacts.tg_confidence. utils/phone.py: is_username_key / username_from_key.
queue.py rate constants (PROTECTED — DO NOT TOUCH): MIN/MAX_SEND_INTERVAL, MAX_NEW_CONTACTS_PER_HOUR, 4/min/20/hr/150/day.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add a helper to load the captured username + verdict for a phone (tier-2/tier-3 inputs)</name>
  <files>app/services/telegram.py</files>
  <read_first>
    - app/services/telegram.py:387-458 (_get_cached_contact — DB access pattern to mirror, AsyncSessionLocal + text())
    - app/models/__init__.py:467-499 (Contact: tg_status, tg_username_resolved, tg_probe_state, tg_confidence)
    - .planning/phases/17-...-/17-CONTEXT.md D-03 (import gate on registered), D-07 (captured username on contacts.tg_username_resolved)
    - .planning/phases/17-...-/17-RESEARCH.md §"Pattern 1: 3-tier sender resolve ladder"
  </read_first>
  <behavior>
    - Test (covered indirectly by resolve_ladder/import_gate tests): given a phone with a contacts row tg_status='registered' + tg_username_resolved='handle', the helper returns {"tg_status": "registered", "captured_username": "handle"}.
    - Given tg_status='not_registered', returns {"tg_status": "not_registered", "captured_username": None}.
    - Given no contacts row (e.g. username-key contact or ad-hoc send), returns {"tg_status": None, "captured_username": None} (caller treats None verdict permissively per existing send-path semantics — but only registered triggers import).
  </behavior>
  <action>
    Add a private async method `_load_contact_verdict(self, workspace_id, phone) -> dict` to `TelegramService` near `_get_cached_contact`. It runs (async, AsyncSessionLocal + `text()`):
    ```sql
    SELECT tg_status, tg_username_resolved
      FROM contacts
     WHERE workspace_id = :workspace_id AND phone = :phone
     ORDER BY (tg_status = 'registered') DESC, updated_at DESC
     LIMIT 1
    ```
    Return `{"tg_status": row[0] if row else None, "captured_username": row[1] if row else None}`. The ORDER BY prefers a `registered` row when a phone maps to multiple contacts (conservative — favors reachability). This is the shared input for tier-2 (captured_username) and the tier-3 import gate (tg_status).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send.py -k "import_gate" --collect-only -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "_load_contact_verdict" app/services/telegram.py` returns ≥1 hit (definition present).
    - `grep -n "tg_username_resolved\|tg_status" app/services/telegram.py` shows both read inside the new helper.
    - `--collect-only` still clean.
  </acceptance_criteria>
  <done>_load_contact_verdict returns {tg_status, captured_username} for a phone, preferring a registered row.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Rebuild resolve_contact as cache → ResolveUsername(captured) → ImportContacts (SRLD-03, SRLD-04, SRLD-05)</name>
  <files>app/services/telegram.py</files>
  <read_first>
    - app/services/telegram.py:494-556 (resolve_contact — replace the ResolvePhone tail)
    - app/services/telegram.py:605-637 (check_contact — ImportContactsRequest call shape to copy, OMIT DeleteContacts)
    - app/services/telegram.py:558-603 (_resolve_username — tier-2 implementation reused; Task 3 makes it fall through)
    - app/utils/phone.py:82-87 (is_username_key / username_from_key)
    - .planning/phases/17-...-/17-CONTEXT.md D-01 (drop sender ResolvePhone), D-02 (Import-only tier-3), D-03/D-11 (gate on registered), D-04 (NO DeleteContacts on sender), D-05 (one import per send)
    - .planning/phases/17-...-/17-RESEARCH.md §"Anti-Patterns to Avoid" (no DeleteContacts on sender, don't touch queue intervals)
    - tests/test_send.py (RED tests resolve_ladder / import_gate / lazy_import from 17-01)
  </read_first>
  <behavior>
    - Test resolve_ladder: registered contact with captured tg_username_resolved → ResolveUsername fires, ResolvePhoneRequest is NEVER in client.calls.
    - Test import_gate: registered + no captured username → ImportContacts fires and resolves; not_registered → ImportContacts NEVER fires (skip), returns not-registered.
    - Test lazy_import: on the import path, DeleteContactsRequest is NEVER in client.calls.
  </behavior>
  <action>
    Rewrite the tail of `resolve_contact` (everything after the `cached = await self._get_cached_contact(...)` hit return and the existing `if is_username_key(phone): return await self._resolve_username(...)` branch). For a PHONE key (not a username key):
    1. `verdict = await self._load_contact_verdict(workspace_id, phone)`.
    2. Tier-2: if `verdict["captured_username"]` is set → `res = await self._resolve_username(client, workspace_id, sender_id, "@" + verdict["captured_username"].lstrip("@"))`. If `res.get("is_registered")` → return it (cache the access_hash under the PHONE key via `_save_contact_cache` so follow-ups are phone-cache hits — mirror existing cache writes). If `_resolve_username` signals stale (Task 3 returns `{"stale_username": True}`) → fall through to tier-3.
    3. Tier-3 (ImportContacts), gated: ONLY if `verdict["tg_status"] == "registered"` (D-03/D-11). Copy the `ImportContactsRequest(contacts=[InputPhoneContact(client_id=0, phone=phone, first_name=recipient_name or "", last_name="")])` call from check_contact:612. If `result.users` → build contact_info {is_registered, telegram_id, access_hash, first_name, last_name, username} from `result.users[0]`, `_save_contact_cache`, return it. DO NOT call DeleteContactsRequest (D-04 — keep the contact, hot entity-cache). If import returns no users → return `{"is_registered": False}` (do NOT cache False here — leave finalization to the checker per D-09 semantics).
    4. If `verdict["tg_status"]` is `'not_registered'` (or no captured username AND not registered) → return `{"is_registered": False}` WITHOUT calling ImportContacts (skip, D-03).
    5. REMOVE the `result = await client(ResolvePhoneRequest(phone=phone))` block entirely (lines ~518-556) — the sender no longer self-resolves by phone (D-01). Remove now-unused `ResolvePhoneRequest` import if nothing else in telegram.py uses it on the sender path (verify with grep; `check_contact`/`send_*` must not regress). FloodWaitError from any tier must still propagate (do not mask).
    Keep `as_draft`/send semantics in `send_message` unchanged — only the resolve path changes.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send.py -k "resolve_ladder or import_gate or lazy_import" -x</automated>
  </verify>
  <acceptance_criteria>
    - In the resolve_contact phone path, `grep -n "ResolvePhoneRequest" app/services/telegram.py` shows NO call inside resolve_contact (SRLD-03). (probe/checker paths live in checker.py, not telegram.py.)
    - `grep -n "DeleteContacts" app/services/telegram.py` returns 0 hits in the send/resolve path (SRLD-05/D-04).
    - `grep -n "ImportContactsRequest" app/services/telegram.py` shows the tier-3 call inside resolve_contact, gated on `tg_status == 'registered'`.
    - `grep -n "MIN_SEND_INTERVAL\|MAX_SEND_INTERVAL\|MAX_NEW_CONTACTS_PER_HOUR" app/services/telegram.py` returns 0 hits (no queue-interval edits here; constants live in queue.py and are untouched).
    - `pytest tests/test_send.py -k "resolve_ladder or import_gate or lazy_import" -x` exits 0.
  </acceptance_criteria>
  <done>resolve_contact is cache→ResolveUsername(captured)→ImportContacts(registered-gated, no Delete); sender ResolvePhone removed; intervals untouched.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Stale-username fall-through + confidence-gate the sender's false cache read (SRLD-06, SRLD-07)</name>
  <files>app/services/telegram.py</files>
  <read_first>
    - app/services/telegram.py:558-603 (_resolve_username — change the except branch to fall through)
    - app/services/telegram.py:387-458 (_get_cached_contact — gate the cross-sender is_registered=false shortcut at :442)
    - .planning/phases/17-...-/17-CONTEXT.md D-09 (never finalize not_registered on stale username), D-12 (gate the false read; never delete cache)
    - .planning/phases/17-...-/17-RESEARCH.md §"Code Examples" (D-09 fall-through target) + §"Open Questions #1" (conservative join predicate)
    - tests/test_send.py (RED tests stale_username_fallthrough / confidence_gated_cache from 17-01)
  </read_first>
  <behavior>
    - Test stale_username_fallthrough: ResolveUsername raises UsernameNotOccupiedError → resolve_contact then fires ImportContacts (registered contact); final is_registered=True; the contact is never cached/finalized as not_registered.
    - Test confidence_gated_cache (sender): a suspect cross-sender is_registered=false cache row does NOT short-circuit; a live resolve (ResolveUsername or ImportContacts) is attempted.
    - Negative control: a clean/high-confidence is_registered=false cache row still short-circuits (no needless live resolve).
  </behavior>
  <action>
    Part A (D-09, SRLD-06) — `_resolve_username`: import `from telethon.errors import UsernameNotOccupiedError, UsernameInvalidError` at module top (verify not already imported). Replace the `if "username_not_occupied" ... return {"is_registered": False}` block: on `UsernameNotOccupiedError`/`UsernameInvalidError` (typed catch, keep the string-match as defence-in-depth), do NOT call `_save_contact_cache` and do NOT return False — instead `return {"stale_username": True}`. resolve_contact (Task 2 step 2) routes a `stale_username` result to the import tier. FloodWait / frozen / other errors still propagate (raise) unchanged.

    Part B (D-12, SRLD-07) — `_get_cached_contact`: the cross-sender `is_registered=false` shortcut (telegram.py:442-456) must NOT serve a suspect false row. Before returning the cross-sender false, add a correlated check (same AsyncSessionLocal block, conservative per Research OQ#1): only return the false shortcut when NO matching contacts row in the workspace is suspect:
    ```sql
    SELECT 1 FROM contacts
     WHERE workspace_id = :workspace_id AND phone = :phone
       AND (tg_probe_state = 'suspect' OR tg_confidence IS DISTINCT FROM 'high')
     LIMIT 1
    ```
    If that returns a row → do NOT return the false shortcut (let resolve_contact fall through to live tier-2/tier-3). Also apply the same gate to the per-sender `is_registered=false` early return at telegram.py:417-418 (the `if row and row[4] is False:` branch) so a suspect per-sender false is likewise not served. Never DELETE a cache row.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send.py -k "stale_username_fallthrough or confidence_gated_cache" -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "stale_username" app/services/telegram.py` shows `_resolve_username` returning the stale signal and resolve_contact routing on it.
    - `grep -n "UsernameNotOccupiedError\|UsernameInvalidError" app/services/telegram.py` shows the typed catch.
    - `grep -n "tg_probe_state\|tg_confidence" app/services/telegram.py` shows the gate inside `_get_cached_contact`.
    - `grep -n "DELETE FROM contacts_cache" app/services/telegram.py` returns 0 hits.
    - `pytest tests/test_send.py -k "stale_username_fallthrough or confidence_gated_cache" -x` exits 0.
  </acceptance_criteria>
  <done>Stale username falls through to import (never finalized not_registered); suspect false cache rows are not served on the sender path; cache never deleted.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send.py -x` GREEN for SRLD-03/04/05/06/07 (sender) tests.
- `grep -n "ResolvePhoneRequest" app/services/telegram.py` shows it is NOT called inside resolve_contact (sender ResolvePhone gone).
- `grep -n "DeleteContacts" app/services/telegram.py` = 0 (no sender-side cleanup).
- queue.py NOT in this plan's diff — rate intervals untouched.
</verification>

<success_criteria>
- The 22-failed Barter-ВЭД class is structurally fixed: a registered RU mobile now resolves via captured-username ResolveUsername or ImportContacts on the sender, never via the sender's own ResolvePhone.
- Full suite green after the wave merge.
</success_criteria>

<output>
After completion, create `.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-03-SUMMARY.md` noting: the new ladder order, the exact stale-username signal contract (`{"stale_username": True}`), whether `ResolvePhoneRequest` import was removed, and the suspect predicate used (shared with 17-02).
</output>
