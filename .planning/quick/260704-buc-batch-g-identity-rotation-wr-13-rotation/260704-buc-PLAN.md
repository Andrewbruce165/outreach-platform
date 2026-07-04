---
phase: quick-260704-buc
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/rotation.py
  - app/services/failover.py
  - app/services/queue.py
  - app/services/telegram.py
  - app/services/checker.py
  - app/services/warmup.py
  - app/services/contact_check_worker.py
  - app/routers/senders.py
  - app/routers/conversations.py
  - tests/test_rotation_campaign.py
  - tests/test_queue_position.py
  - tests/test_batch_g_identity.py
autonomous: true
requirements: [WR-13, WR-14, IN-04]
must_haves:
  truths:
    - "WR-13: a spam_limited/frozen (or role != 'sender') sticky-assigned sender is NOT returned by rotation.get_or_assign_sender happy-path — is_eligible=False → falls through to healthy-pool reassignment"
    - "WR-14: a dead session on a sender whose slug is duplicated across workspaces flips auth_status by primary key (not slug) and raises SessionAuthError — never MultipleResultsFound"
    - "WR-14: TelegramService client locks + CheckerService checker locks serialize per account id, not per (non-unique) slug"
    - "IN-04: the pre-send manual-takeover guard (non-recontact branch) reads the newest conversation row via ORDER BY updated_at DESC when duplicate rows exist"
    - "api and listener containers restart on the new code with no import errors"
  artifacts:
    - path: "app/services/rotation.py"
      provides: "is_eligible predicate incl. role='sender' AND restriction_status='none'"
      contains: "restriction_status = 'none'"
    - path: "app/services/telegram.py"
      provides: "_set_auth_status by id + get_client(sender_slug, sender_id, ...) locked by id"
    - path: "app/services/checker.py"
      provides: "client locks keyed by checker_id"
    - path: "app/services/queue.py"
      provides: "IN-04 ORDER BY updated_at DESC in non-recontact pre-send guard"
    - path: "tests/test_batch_g_identity.py"
      provides: "WR-14 + IN-04 regression tests"
  key_links:
    - from: "app/services/queue.py / app/routers/senders.py / app/services/warmup.py / app/routers/conversations.py"
      to: "telegram_service.get_client / send_message_by_telegram_id"
      via: "pass sender_id positional arg"
      pattern: "get_client\\([^)]*sender"
    - from: "app/services/contact_check_worker.py"
      to: "checker_service.probe_control"
      via: "pass checker_id kwarg"
      pattern: "probe_control\\([^)]*checker_id"
---

<objective>
Batch G of the checker+campaigns review fix plan — identity/rotation correctness. Three independent defects, no schema change:

- **WR-13** (`rotation.py`): the sticky-assignment happy-path eligibility predicate ignores `restriction_status` and `role`, so a spam_limited/frozen sender keeps receiving new queue rows for its already-assigned contacts instead of rotating to a healthy pool member.
- **WR-14** (`telegram.py` + `checker.py`): `_set_auth_status` and the per-client asyncio locks key on `slug`, which since migration 014 is unique only per-workspace. A slug duplicated across workspaces makes `scalar_one_or_none()` raise `MultipleResultsFound` inside the auth-error handler, replacing `SessionAuthError` with an unrelated crash → the queue worker burns 3 attempts forever and never flips `auth_status`.
- **IN-04** (`queue.py`): the pre-send manual-takeover guard's non-recontact branch reads `ai_enabled` with `LIMIT 1` and no `ORDER BY`, so with the duplicate conversation rows the recontact machinery deliberately creates it reads an arbitrary row.

Purpose: cross-workspace-safe account identity keying + restriction-aware rotation + deterministic conversation guard, so multi-tenant SaaS onboarding of the same Telegram account into two workspaces cannot corrupt session-death handling, and restricted senders stop absorbing sends.
Output: surgical edits to 9 app/ files + tests, full-suite regression via test-overlay, deploy of api + listener.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@/root/apps/aimly/tg-outreach/CLAUDE.md
@/root/CLAUDE.md

<constraints>
- NO migration — no schema changes, no new columns.
- Follow CLAUDE.md: async everywhere, no time.sleep/requests/print, API_KEY/session not in logs, empirical rate-limit intervals untouched.
- Parallel work on Phase 20 is in the repo — stage ONLY the files this plan touches, never `git add -A`.
- `app/routers/proxy_pool.py:240` ALSO calls `get_client(sender.slug, ...)` but it is DEAD unmounted code being deleted in Batch H — do NOT touch or reference it. Do not update it as a call site.
- `app/services/checker.py::_flag_checker_auth` is ALREADY id-based (prior Batch B) — do NOT change it; mirror its `UPDATE senders SET ... WHERE id=:sid` pattern for `telegram.py::_set_auth_status`.
- Do NOT change `SessionAuthError`'s constructor signature — callers read `.auth_status` only (queue.py:1219-1234, senders.py:728). `sender_slug` stays as the human-readable first arg.
- Do NOT change `failover.py` query logic — only its stale docstring comment.
- Do NOT change checker's `probe_control` `_get_client` call (it intentionally passes no `sender_id` so the probe swallows auth errors as a miss) — only the lock key.
</constraints>

<interfaces>
<!-- Current signatures the executor edits. Use directly — no exploration needed. -->

telegram.py — module-level, ONLY called from get_client (lines 310-329):
```python
async def _set_auth_status(slug: str, auth_status: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Sender).where(Sender.slug == slug))  # <-- non-unique slug bug
        sender = result.scalar_one_or_none()  # <-- MultipleResultsFound on dup slug
        ...

class TelegramService:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}   # keyed by slug today
    async def get_client(self, sender_slug, encrypted_session, proxy=None) -> TelegramClient:
        if sender_slug not in self._locks: ...
        async with self._locks[sender_slug]:
            ... _set_auth_status(sender_slug, "session_expired") ...   # 5 call sites, lines 310/313/319/325/329
    async def send_message_by_telegram_id(self, sender_slug, encrypted_session, telegram_id, message, proxy=None):
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)  # line 1077 internal call site
```

checker.py — the id-based pattern to mirror (do NOT edit this method):
```python
async def _flag_checker_auth(self, sender_id: str | None, auth_status: str) -> None:
    if not sender_id: return
    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(text("UPDATE senders SET auth_status = :st WHERE id = :sid"),
                             {"st": auth_status, "sid": sender_id})

def _get_lock(self, checker_slug: str) -> asyncio.Lock: ...     # keyed by slug today (line 197)
async def check_phones(self, workspace_id, checker_id, checker_slug, ...): async with self._get_lock(checker_slug):   # line 383 (checker_id in scope)
async def probe_control(self, checker_slug, encrypted_session, phones, proxy=None): async with self._get_lock(checker_slug):  # line 411 (NO checker_id param)
async def check_usernames(self, workspace_id, checker_id, checker_slug, ...): async with self._get_lock(checker_slug):  # line 608 (checker_id in scope)
```

rotation.py — Step-1 sticky predicate (line 76) vs Step-3 candidate filter (lines 118-121):
```python
(s.lifecycle_status = 'active' AND s.auth_status = 'ok') AS is_eligible   # STEP 1 — missing role + restriction
# Step 3 candidates already have:  AND s.role = 'sender'  AND s.restriction_status = 'none'
```

conversations.py:412-424 `row` already exposes `s.id AS sender_id` — use `row.sender_id`.
warmup.py from_sender dict has `from_sender["id"]`; queue.py/senders.py have `sender.id` (ORM row).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: WR-13 restriction-aware sticky rotation + IN-04 deterministic guard</name>
  <files>app/services/rotation.py, app/services/failover.py, app/services/queue.py, tests/test_rotation_campaign.py, tests/test_queue_position.py</files>
  <behavior>
    - WR-13: get_or_assign_sender with an EXISTING campaign_contact_assignments row whose sender has restriction_status='spam_limited' (or 'frozen', or role != 'sender') must NOT return that sender via the happy path — is_eligible must be False so it falls through and reassigns to a healthy pool member (restriction_status='none', role='sender'); if a healthy candidate exists it is returned, and the CCA row is UPDATEd to the new sender.
    - WR-13: an existing assignment to a fully-healthy sender (active + auth ok + role sender + restriction none) is still returned unchanged (no regression).
    - IN-04: the non-recontact pre-send guard SQL string includes `ORDER BY updated_at DESC` before `LIMIT 1` (assert on the composed guard_sql, or an integration test with two conversation rows for the same (workspace, sender, phone) differing in updated_at + ai_enabled proving the newest row's ai_enabled wins).
  </behavior>
  <action>
    1. rotation.py line 76: extend the Step-1 `is_eligible` computed column from
       `(s.lifecycle_status = 'active' AND s.auth_status = 'ok') AS is_eligible`
       to `(s.lifecycle_status = 'active' AND s.auth_status = 'ok' AND s.role = 'sender' AND s.restriction_status = 'none') AS is_eligible`
       — matching the Step-3 candidate filter (lines 118-121) exactly. The existing "assignment exists but sender went offline → reassign below" branch (lines 96-97 + Step 5 UPDATE) already handles is_eligible=False correctly; no other rotation.py change. Update the docstring at line 49 (Step 3 description) if it still says only `auth_status='ok' AND lifecycle_status='active'`.
    2. failover.py docstring lines 26-30 (Selection / Pitfall 1): rewrite the "we do NOT call rotation.get_or_assign_sender — its stale-CCA short-circuit ignores restriction_status" comment. New wording: the rotation happy-path eligibility gap is now FIXED (WR-13 — rotation.py Step-1 predicate now includes restriction_status='none' AND role='sender'); failover.py keeps its own separate implementation NOT to route around a live bug but for its distinct bulk-claim concurrency contract (per-row `FOR UPDATE OF mq SKIP LOCKED` across many rows/campaigns vs rotation.py's single-contact path). Do NOT change any failover.py SQL or logic.
    3. queue.py: in the pre-send manual-takeover guard's `else` (non-recontact) branch (~lines 793-799), add `ORDER BY updated_at DESC` before `LIMIT 1` in guard_sql — mirroring the `if allow_recontact:` branch right above (line 789) and the lookup at ~1356. Only that one SQL string changes.
    4. Tests: add a WR-13 case to tests/test_rotation_campaign.py (seed a CCA to a spam_limited sender + a healthy pool sender → assert reassignment to the healthy one) and an IN-04 case to tests/test_queue_position.py (or a duplicate-conversation integration test) asserting the newest conversation's ai_enabled governs the guard.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_rotation_campaign.py tests/test_queue_position.py -x</automated>
  </verify>
  <done>rotation.py is_eligible includes role + restriction_status; failover.py docstring updated (logic untouched); queue.py non-recontact guard has ORDER BY updated_at DESC; targeted tests green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: WR-14 TelegramService — auth-status + client locks keyed by sender_id</name>
  <files>app/services/telegram.py, app/services/queue.py, app/routers/senders.py, app/services/warmup.py, app/routers/conversations.py, tests/test_batch_g_identity.py</files>
  <behavior>
    - _set_auth_status updates by primary key: given two senders sharing a slug across workspaces, flipping auth_status for one id updates ONLY that row (no MultipleResultsFound, no cross-row write).
    - get_client: on a dead/unauthorized session it flips the correct sender's auth_status BY ID and raises SessionAuthError(auth_status='session_expired') — verifiable even when the slug is duplicated across workspaces (i.e. the update/lock no longer depends on slug uniqueness). SessionAuthError.auth_status stays readable by existing callers.
    - self._locks is keyed by sender_id: two senders with the same slug in different workspaces get distinct locks (no cross-workspace serialization/collision).
  </behavior>
  <action>
    Do ALL of the following atomically so the module imports cleanly:
    1. telegram.py `_set_auth_status`: rename param `slug` → `sender_id`; replace the ORM select+scalar_one_or_none with a raw `UPDATE senders SET auth_status = :st WHERE id = :sid` inside `async with AsyncSessionLocal() as db: async with db.begin():` — mirroring `CheckerService._flag_checker_auth` (checker.py:202-213). Keep the warning log (log the id, not the session).
    2. telegram.py `get_client`: change signature to `get_client(self, sender_slug: str, sender_id: str, encrypted_session: str, proxy: dict | None = None)`. Key `self._locks` by `sender_id` (`if sender_id not in self._locks: ...`, `async with self._locks[sender_id]:`). Pass `sender_id` into every `_set_auth_status(...)` call (5 sites, lines ~310/313/319/325/329) — the `SessionAuthError(sender_slug, ...)` raises keep `sender_slug` for the human-readable message. Do NOT change SessionAuthError.
    3. telegram.py `send_message_by_telegram_id`: add a `sender_id: str` param (place it right after `sender_slug`); pass it into the internal `self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy)` at line 1077.
    4. Update the call sites (all pass sender_id as the new 2nd positional):
       - queue.py:891 → `telegram_service.get_client(sender.slug, sender.id, sender.session_string, proxy=sender.proxy)`
       - senders.py:682-684 → `telegram_service.get_client(sender.slug, sender.id, sender.session_string, proxy=sender.proxy)`
       - warmup.py:714-717 → `telegram_service.get_client(from_sender["slug"], from_sender["id"], from_sender["session_string"])`
       - conversations.py:466 → add `sender_id=row.sender_id` to the `send_message_by_telegram_id(...)` kwargs (row already selects `s.id AS sender_id`).
       Cast ids to `str(...)` where the ORM column is a UUID, matching how existing code passes ids (checker _flag_checker_auth binds a plain str). Do NOT touch proxy_pool.py.
    5. Tests: create tests/test_batch_g_identity.py. WR-14 cases: (a) seed two senders with the SAME slug in two workspaces, call `_set_auth_status(sender_id=<id1>, "session_expired")`, assert ONLY sender 1 flipped and no exception; (b) assert `TelegramService._locks` is keyed by id — two same-slug senders yield two distinct locks (or, at minimum, a test that get_client's lock/update path takes sender_id and does not query by slug). Mock the Telethon client/connect so no real network.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_batch_g_identity.py tests/test_phase5_inbox_send_takeover.py tests/test_phase5_bot_filter.py -x</automated>
  </verify>
  <done>_set_auth_status + get_client + send_message_by_telegram_id keyed by sender_id; all 4 live call sites pass sender_id; SessionAuthError unchanged; new + existing send tests green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: WR-14 CheckerService — client locks keyed by checker_id</name>
  <files>app/services/checker.py, app/services/contact_check_worker.py, tests/test_batch_g_identity.py</files>
  <behavior>
    - CheckerService locks serialize per checker_id, not per (non-unique) slug: two checkers sharing a slug across workspaces get distinct locks.
    - probe_control accepts an optional checker_id and locks on `checker_id or checker_slug` (omitting it preserves the old slug-keyed behavior).
    - check_phones / check_usernames lock on checker_id (already a param in both).
  </behavior>
  <action>
    1. checker.py `_get_lock`: rename param `checker_slug` → `key` (it is just a dict key now); update the class comment at line 193 ("One Lock per checker_slug" → "per checker id"). Body unchanged (keyed by `key`).
    2. checker.py `check_phones` (line 383): `async with self._get_lock(checker_slug):` → `async with self._get_lock(checker_id):` (checker_id already a param).
    3. checker.py `check_usernames` (line 608): same change → `self._get_lock(checker_id)`.
    4. checker.py `probe_control`: add param `checker_id: str | None = None` (append to signature, keyword-safe); line 411 `self._get_lock(checker_slug)` → `self._get_lock(checker_id or checker_slug)`. Do NOT change its `self._get_client(...)` call (stays sender_id-less by design).
    5. contact_check_worker.py: pass checker_id to both probe_control call sites — ~618-623 add `checker_id=checker_id` (checker_id in scope), ~810-815 add `checker_id=r.id` (loop row has r.id).
    6. Tests: add a WR-14 checker case to tests/test_batch_g_identity.py asserting two same-slug checker_ids produce distinct locks (`checker_service._get_lock(id_a) is not checker_service._get_lock(id_b)` while `_get_lock(id_a) is _get_lock(id_a)`), and that probe_control(checker_id=...) uses the id key.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_batch_g_identity.py tests/test_checker.py tests/test_checker_probe.py tests/test_contact_check_worker.py tests/test_checker_resilience_batch_b.py -x</automated>
  </verify>
  <done>CheckerService locks keyed by checker_id in check_phones/check_usernames/probe_control; both worker probe_control call sites pass checker_id; targeted checker tests green.</done>
</task>

<task type="auto">
  <name>Task 4: Full-suite regression, deploy api+listener, live-verify</name>
  <files>(no source edits — verification + deploy)</files>
  <action>
    1. Run the FULL suite via test-overlay (WR-14 touches shared telegram.py, used by both api and listener — full regression is mandatory, not just targeted tests):
       `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`
       Compare against the known baseline: a pre-existing WARM-14 RED scaffold failure and the test_phase5_migration_017 pooled-conn cascade may appear depending on ordering (documented in Batch A/B SUMMARYs). Acceptance = ZERO NEW failures attributable to this plan's files (rotation/failover/queue/telegram/checker/warmup/contact_check_worker/senders/conversations). If a failure references those files or get_client/_set_auth_status/probe_control signatures, fix it before deploying.
    2. Deploy both containers (telegram.py is imported by the listener too):
       `cd /root/apps/aimly/tg-outreach && docker compose up -d --build api listener`
    3. Live-verify (automated spot checks):
       - `docker compose logs --tail=80 api` and `docker compose logs --tail=80 listener` — confirm both booted with NO ImportError / TypeError (a missed get_client positional caller would surface here) and migrations applied clean (no new migration expected).
       - `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SELECT count(*) FROM senders;"` — confirm DB reachable and app healthy.
       - If any sender is live, spot-check one auth-status path is intact (optional; otherwise confirm container health via the logs above).
    4. Commit ONLY this plan's files (parallel Phase 20 work in repo — never `git add -A`):
       `node "/root/apps/aimly/tg-outreach/.claude/get-shit-done/bin/gsd-tools.cjs" commit "fix(quick-260704-buc): batch G identity/rotation — WR-13 restriction-aware sticky rotation, WR-14 sender_id-keyed auth/locks, IN-04 deterministic guard" --files app/services/rotation.py app/services/failover.py app/services/queue.py app/services/telegram.py app/services/checker.py app/services/warmup.py app/services/contact_check_worker.py app/routers/senders.py app/routers/conversations.py tests/test_rotation_campaign.py tests/test_queue_position.py tests/test_batch_g_identity.py`
  </action>
  <verify>
    <automated>docker compose logs --tail=40 api 2>&1 | grep -iE "error|traceback|importerror|typeerror" | grep -viE "log_statement|error_message|error_tracking" || echo "no boot errors"</automated>
  </verify>
  <done>Full suite shows no NEW failures from this plan's files; api + listener rebuilt and booted clean (no ImportError/TypeError); commit landed with only the listed files.</done>
</task>

</tasks>

<verification>
- WR-13: `grep -n "restriction_status = 'none'" app/services/rotation.py` shows it in BOTH the Step-1 is_eligible predicate and Step-3 candidate filter.
- WR-14 telegram: `grep -n "WHERE id = :sid\|_locks\[sender_id\]\|get_client(self" app/services/telegram.py` confirms id-based update + id-keyed lock + new signature; no `get_client(` call site anywhere in app/ (except dead proxy_pool.py) passes only slug.
- WR-14 checker: `grep -n "_get_lock(checker_id" app/services/checker.py` shows check_phones + check_usernames; `grep -n "checker_id or checker_slug" app/services/checker.py` shows probe_control; `grep -n "checker_id=" app/services/contact_check_worker.py` shows both probe_control call sites.
- IN-04: the non-recontact guard branch in queue.py (~793) contains `ORDER BY updated_at DESC`.
- No migration added (no new file in migrations/).
- Full test-overlay suite: no new failures vs baseline.
- api + listener containers healthy after `up -d --build`.
</verification>

<success_criteria>
- A restricted/wrong-role sticky-assigned sender is no longer returned by rotation and reassigns to a healthy pool member (WR-13).
- Session-death handling on a cross-workspace duplicate slug updates by primary key and raises SessionAuthError instead of MultipleResultsFound; client + checker locks are id-keyed (WR-14).
- The pre-send manual-takeover guard reads the newest conversation row deterministically (IN-04).
- Full suite regression clean, api + listener deployed and verified booting on the new code.
</success_criteria>

<output>
After completion, create `.planning/quick/260704-buc-batch-g-identity-rotation-wr-13-rotation/260704-buc-SUMMARY.md` recording: exact edits per file, the new get_client/send_message_by_telegram_id/probe_control signatures, full-suite result vs baseline, deploy confirmation, and the commit hash.
</output>
