# Phase 8: Pool Management and Even Distribution - Pattern Map

**Mapped:** 2026-06-23
**Files analyzed:** 7 (4 backend new/modified, 3 frontend modified + 1 generated)
**Analogs found:** 7 / 7 (every touch point has a verified in-repo analog; the only net-new code is the campaign-scoped rebalance pass, which adapts existing SQL patterns)

> Brownfield reuse phase. The risk is **diverging from existing contracts**, not writing new code. Where a contract is shared (the `SENDER_LOCK_CONFLICT` 409, the `attached_senders` shape, the worker's `FOR UPDATE OF mq SKIP LOCKED`), copy it byte-for-byte. All cited line numbers were re-read from source this session and confirmed.

---

## File Classification

| New/Modified File | Repo | New/Mod | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|---------|------|-----------|----------------|---------------|
| `app/routers/campaigns.py` (2 new endpoints: `POST /{id}/senders`, `DELETE /{id}/senders/{sid}`) | backend | modify | route/controller | request-response (CRUD on `campaign_senders`) | `start_campaign` / `resume_campaign` in same file (campaigns.py:578, 657) | exact |
| `app/services/rebalance.py` (NEW — `rebalance_on_attach`) | backend | new | service | batch / transform (set-based UPDATE) | `rotation._pick_least_loaded` (rotation.py:198) for SQL shape; `queue._process_next_for_sender` (queue.py:294) for lock discipline | role-match (composed from two analogs) |
| `app/schemas/__init__.py` (request body for attach; docstring fix) | backend | modify | schema/model | request-response | `CampaignSenderAttach` (schemas:566), `CampaignCreate` (schemas:577) | exact |
| `tests/test_pool_endpoints.py` (NEW — POOL-01..06b) | backend | new | test | request-response | `tests/test_campaign_router.py` + conftest factories | role-match |
| `tests/test_rebalance.py` (NEW — POOL-07/08/08b) | backend | new | test | batch | `tests/test_rotation_campaign.py` + conftest | role-match |
| `tests/conftest.py` (NEW fixture `test_queue_item_factory`) | backend | modify | test fixture | factory | `attach_sender_to_campaign` (conftest:583), `test_contacts_factory` (conftest:441) | exact |
| `src/routes/_authenticated/campaigns.$id.tsx` (Senders panel → interactive) | frontend | modify | component/route | request-response (mutations) | existing read-only Senders `<section>` (campaigns.$id.tsx:361-405) + `lifecycleMut` (campaigns.$id.tsx:89-104) + `AccountsStep` toggle (campaigns.new.tsx:1037-1093) | exact |
| `src/lib/error-codes.ts` (add MIN_POOL_GUARD, DETACH_BLOCKED_PENDING; fix SENDER_LOCK_CONFLICT) | frontend | modify | utility | transform | `CODE_MAP` entries (error-codes.ts:4-25) | exact |
| `lovable-handoff/openapi.json` + `src/types/api.ts` | both | regenerate | config (generated) | — | run `scripts/export-handoff.sh` (do NOT hand-edit) | exact (mechanism) |

---

## Pattern Assignments

### `app/routers/campaigns.py` → `attach_sender` (route, request-response)

**Analog:** `start_campaign` (campaigns.py:578-633) for the validate→lock→409 contract; `create_campaign` for `CampaignSender` insert.

**Imports already present** — no new imports needed (campaigns.py:25-46): `select, text, sql_func` from sqlalchemy; `CampaignSender, Sender` from `app.models`; `CampaignResponse, CampaignSenderAttach` from `app.schemas`; `HTTPException`. Only a `delete` import may be added for the detach endpoint.

**Workspace-ownership validation (reuse verbatim, campaigns.py:141-162):**
```python
await _validate_workspace_owns_senders(db, ctx, [payload.sender_id])
# raises 404 {code:"SENDER_NOT_FOUND", message:..., missing_sender_ids:[...]}
```

**Sender-lock 409 contract — copy from start_campaign (campaigns.py:621-627) byte-for-byte:**
```python
conflicts = await _check_sender_lock(db, ctx, c.id)
if conflicts:
    raise HTTPException(
        status_code=409,
        detail={"code": "SENDER_LOCK_CONFLICT", "conflicts": conflicts},
    )
```
`_check_sender_lock(db, ctx, campaign_id)` (campaigns.py:275-298) returns
`[{"sender_id": str, "campaign_id": str, "campaign_name": str}, ...]`. It checks **all** senders currently in `campaign_senders` for that `campaign_id`. **Subtlety (Research Pattern 1, option A):** insert the new `campaign_senders` row first (`await db.flush()`), THEN call `_check_sender_lock` so the incoming sender is in scope; on conflict `await db.rollback()` + raise 409. This keeps the 409 body bit-identical to `/start`.

**Insert pattern (mirror create_campaign):**
```python
db.add(CampaignSender(campaign_id=c.id, sender_id=payload.sender_id,
                      workspace_id=ctx.workspace_id))
await db.flush()
```
Idempotency: PK is `(campaign_id, sender_id)` — pre-check existence with a `select(CampaignSender).where(...)` and no-op if already attached (avoids IntegrityError). The conftest factory uses `ON CONFLICT DO NOTHING` (conftest:592) — same intent.

**Response (reuse verbatim):** `return await _campaign_to_response(db, ctx, c)` (campaigns.py:228) — already includes `attached_senders` via `_build_attached_senders` (campaigns.py:194) with `locked_by_campaign_id/name`. New endpoints return the **same shape** as GET.

**Status guard (D-01):** attach allowed on draft/paused/running. Do NOT add an `INVALID_TRANSITION` block — unlike lifecycle endpoints, there is no status restriction. Only `_load_campaign` 404 applies.

**Rebalance branch (D-08):** after insert + lock pass, `if c.status == "running": await rebalance_on_attach(c.id, payload.sender_id, db)`. Skip for draft/paused (D-07 enqueue distributes later).

---

### `app/routers/campaigns.py` → `detach_sender` (route, request-response)

**Analog:** lifecycle endpoints for the 409 envelope style; `_cancel_pending_queue` (campaigns.py:86-102) for the raw-SQL UPDATE-on-message_queue convention.

**Min-pool guard (D-03, POOL-05)** — count via the same idiom `start_campaign` uses (campaigns.py:610-613):
```python
cnt = (await db.execute(
    select(sql_func.count()).select_from(CampaignSender)
    .where(CampaignSender.campaign_id == c.id)
)).scalar()
if c.status == "running" and cnt <= 1:
    raise HTTPException(409, detail={"code": "MIN_POOL_GUARD",
        "message": "Cannot detach the last sender of a running campaign. Pause it first."})
```
draft/paused may go to 0 senders (consistent with `CampaignCreate.sender_ids` default `[]`, schemas:587, and `start` re-checking `NO_SENDERS_ATTACHED` at campaigns.py:614-619).

**Cold-pending guard (D-04, POOL-06)** — raw `text()` EXISTS, scoped to the detached sender. Uses `recipient_phone` join key (same key `_compute_is_exhausted` uses via `cca.contact_phone`, campaigns.py:182) and `NOT EXISTS conversations` to exclude engaged dialogs (D-05):
```python
has_cold = (await db.execute(text("""
    SELECT EXISTS (
      SELECT 1 FROM message_queue mq
      WHERE mq.campaign_id = :cid AND mq.sender_id = :sid AND mq.status = 'pending'
        AND NOT EXISTS (SELECT 1 FROM message_queue s
                        WHERE s.campaign_id = mq.campaign_id
                          AND s.recipient_phone = mq.recipient_phone
                          AND s.status = 'sent')
        AND NOT EXISTS (SELECT 1 FROM conversations cv
                        WHERE cv.workspace_id = mq.workspace_id
                          AND cv.contact_phone = mq.recipient_phone)
    )
"""), {"cid": str(c.id), "sid": str(sender_id)})).scalar()
if has_cold:
    raise HTTPException(409, detail={"code": "DETACH_BLOCKED_PENDING",
        "message": "This sender still has un-sent contacts in the campaign. "
                   "Pause the campaign or wait for the queue to drain, then detach."})
```

**Delete (mirror raw-SQL/ORM delete convention):**
```python
await db.execute(delete(CampaignSender).where(
    CampaignSender.campaign_id == c.id, CampaignSender.sender_id == sender_id))
await db.commit(); await db.refresh(c)
return await _campaign_to_response(db, ctx, c)
```

---

### `app/services/rebalance.py` (service, batch/transform) — NEW

This is the only genuinely-new logic. It is **composed** from two analogs; do NOT reuse `_pick_least_loaded` directly.

**Why `_pick_least_loaded` is NOT reusable (verified rotation.py:204-214):**
```sql
SELECT s.id AS sid, COUNT(cca.id) AS cnt
FROM senders s
LEFT JOIN campaign_contact_assignments cca ON cca.sender_id = s.id   -- NO campaign filter!
WHERE s.id = ANY(:ids)
GROUP BY s.id ORDER BY cnt ASC, s.created_at ASC LIMIT 1
```
It counts assignments **globally across all campaigns** and returns **one** sender. Rebalance needs **per-campaign** counts and a **set-based** move. Leave rotation.py untouched.

**Eligible-pool filter — copy the candidate filter from rotation.py:113-123 verbatim** (so a `spam_limited` new sender does not receive moved rows):
```sql
SELECT s.id AS sid
FROM campaign_senders cs JOIN senders s ON s.id = cs.sender_id
WHERE cs.campaign_id = :cid
  AND s.lifecycle_status = 'active'
  AND s.auth_status = 'ok'
  AND s.role = 'sender'
  AND s.restriction_status = 'none'
  AND s.workspace_id = :wid
```

**Concurrency lock — copy the worker's discipline (queue.py:313):** the rebalance donor SELECT MUST use `FOR UPDATE OF mq SKIP LOCKED` and filter `status='pending'`, exactly as `_process_next_for_sender` claims rows:
```sql
... WHERE mq.sender_id = :sid AND mq.status = 'pending' ... FOR UPDATE OF mq SKIP LOCKED
```
This guarantees rebalance never grabs a row the worker is mid-sending (it's flipped to `processing` and committed before Telegram, queue.py:351-357) and vice-versa.

**Synchronous CCA sync (Pitfall 3) — keyed on `recipient_phone`/`contact_phone`:** in the same transaction, after `UPDATE message_queue SET sender_id`, also `UPDATE campaign_contact_assignments SET sender_id WHERE campaign_id=:cid AND contact_phone=:phone`. The UNIQUE `idx_cca_campaign_phone (campaign_id, contact_phone)` (migration 016) guarantees one CCA row per recipient. ON-CONFLICT/sticky pattern reference: rotation.py:150-163.

**Conventions:** `async def rebalance_on_attach(campaign_id, new_sender_id, db: AsyncSession)`, all raw `text()`, single `await db.commit()`, `logger.info("rebalance: moved N cold-pending rows ...")` (count only — never payloads, per CLAUDE.md). Full pseudocode + fair-share math (`target = total // P`, `BATCH_CAP = 500`) is in RESEARCH §"Rebalance Algorithm".

---

### `app/schemas/__init__.py` (schema, request-response)

**Analog:** `CampaignSenderAttach` (schemas:566-574) and `CampaignCreate` (schemas:577).

`CampaignSenderAttach` is the **response** sub-object (inside `attached_senders[]`). For the attach **request** body, add a thin model (Claude's discretion per CONTEXT/RESEARCH §OpenAPI) so the OpenAPI schema stays clean:
```python
class CampaignSenderAttachRequest(BaseModel):
    sender_id: UUID
```
**Docstring fix (POOL housekeeping, schemas:625-626):** the `CampaignUpdate` note says sender_ids is "удали → создай новую" — update it to point at the new attach/detach endpoints (D-12: PATCH still ignores `sender_ids` by design — do NOT wire it).

---

### `tests/conftest.py` → `test_queue_item_factory` (test fixture, factory) — NEW

**Analog:** `attach_sender_to_campaign` (conftest:583-596) for the raw-SQL insert + commit shape; `test_contacts_factory` (conftest:441-470) for the `count` + `defaults.update(overrides)` override pattern.

Follow the exact established factory style (verified conftest:583-596):
```python
@pytest_asyncio.fixture
async def test_queue_item_factory(async_db_session, test_workspace):
    from sqlalchemy import text as _t
    async def _make(campaign_id, sender_id, recipient_phone, status="pending", **overrides):
        await async_db_session.execute(_t("""
            INSERT INTO message_queue (workspace_id, campaign_id, sender_id,
                                       recipient_phone, status, scheduled_at)
            VALUES (:wid, :cid, :sid, :phone, :status, NOW())
        """), {"wid": str(test_workspace.id), "cid": str(campaign_id),
               "sid": str(sender_id), "phone": recipient_phone, "status": status, **overrides})
        await async_db_session.commit()
    return _make
```
(Confirm `message_queue` column names against `app/models/__init__.py::MessageQueue` — `recipient_phone` at models:204 — and add a matching `campaign_contact_assignments` insert when a test needs sticky assignment, mirroring rotation.py:151-156.)

---

### `tests/test_pool_endpoints.py` (test, request-response) — NEW, Wave 0

**Analog:** `tests/test_campaign_router.py` (endpoint integration style) + conftest factories.

Available fixtures (verified conftest): `async_client` (198), `valid_supabase_jwt` (206), `test_campaign_factory` (476), `test_sender_factory` (359), `attach_sender_to_campaign` (583), `test_running_campaign_factory` (600 — returns `(camp, senders)`, `sender_count=N` param). Test map: POOL-01 (attach 200 + row), POOL-02 (locked → 409 SENDER_LOCK_CONFLICT), POOL-03 (foreign sender → 404), POOL-04 (detach), POOL-05 (last running → 409 MIN_POOL_GUARD), POOL-06 (cold pending → 409 DETACH_BLOCKED_PENDING), POOL-06b (engaged-only → detach OK).

---

### `tests/test_rebalance.py` (test, batch) — NEW, Wave 0

**Analog:** `tests/test_rotation_campaign.py` + new `test_queue_item_factory`. POOL-07 (skewed backlog → even split, assert `±1` of `total/P` via `SELECT sender_id, COUNT(*) FROM message_queue WHERE campaign_id=:cid AND status='pending' GROUP BY sender_id`), POOL-08 (idempotent — 2nd call moves 0), POOL-08b (never moves sent/processing/engaged: seed a `sent` row + a `conversations` row, assert untouched).

**Run command (MANDATORY test-overlay — CLAUDE.md hard guard):**
```
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_endpoints.py tests/test_rebalance.py -x
```
Never bare `docker compose run --rm api pytest` (prod DROP SCHEMA — 2026-05-26 incident).

---

### `src/routes/_authenticated/campaigns.$id.tsx` (component, request-response)

**Analog:** existing read-only Senders `<section>` (campaigns.$id.tsx:361-405) + `lifecycleMut` (campaigns.$id.tsx:89-104) + `AccountsStep` toggle (campaigns.new.tsx:1037-1093).

**Existing Senders section to upgrade (campaigns.$id.tsx:361-405):** a `<section className="card">` mapping `c.attached_senders[]` into `<li>` rows that already render `s.locked_by_campaign_name` in `var(--danger)`. Phase 8 makes this interactive — keep the structure, add controls.

**Workspace senders query (copy from wizard, campaigns.new.tsx:141-143):**
```ts
const sendersQ = useQuery({
  queryKey: ["senders"],
  queryFn: () => api<{ senders: Sender[] }>("/api/v1/senders"),
});
```

**Attach/detach mutations — mirror `lifecycleMut` (campaigns.$id.tsx:89-104):** same `useMutation` + `qc.invalidateQueries({ queryKey: ["campaign", id] })` onSuccess + `setActionError(errMsg(e))` onError. The existing `actionError` banner (state at campaigns.$id.tsx:58, set at :103) already renders `errMsg(e)` — no new error surface.
```ts
const attachMut = useMutation({
  mutationFn: (sid: string) =>
    api<Campaign>(`/api/v1/campaigns/${id}/senders`, { method: "POST", body: { sender_id: sid } }),
  onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaign", id] }),
  onError: (e) => setActionError(errMsg(e)),
});
const detachMut = useMutation({
  mutationFn: (sid: string) =>
    api<Campaign>(`/api/v1/campaigns/${id}/senders/${sid}`, { method: "DELETE" }),
  onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaign", id] }),
  onError: (e) => setActionError(errMsg(e)),
});
```

**Multiselect toggle UI — copy the `AccountsStep` block (campaigns.new.tsx:1037-1093):** checkbox + `avatar avatar--sm` + status `pill pill--green`, styled with `--tg-blue` / `--border` / `--bg-soft` tokens. On the panel, clicking an un-attached eligible sender fires `attachMut`; each attached `<li>` gets a remove button firing `detachMut`. Filter out already-attached senders and `status === "error"` ones. Disable add/remove on `locked_by_campaign_name` senders (already surfaced in danger color).

**Design tokens (reuse, no new CSS):** `card`, `pill`/`pill--green`/`pill--red`, `avatar avatar--sm`, `--tg-blue*`, `--bg-soft`, `--danger`, `--border`. CONTEXT D-10 says inline panel on the campaign page (not a modal) — extend the existing `<section>`, do NOT use `EditCampaignModal`.

---

### `src/lib/error-codes.ts` (utility, transform)

**Analog:** `CODE_MAP` entries (error-codes.ts:4-25). `api.ts:98-100` routes `detail.code` through `errorMessageFromEnvelope`.

**Add two keys:**
```ts
MIN_POOL_GUARD: () => "Can't remove the last account from a running campaign. Pause it first.",
DETACH_BLOCKED_PENDING: () => "This account still has un-sent contacts. Pause the campaign or wait for the queue to drain.",
```

**⚠ FIX the pre-existing SENDER_LOCK_CONFLICT mismatch (error-codes.ts:16-19):** the current formatter reads `d.name` / `d.other` (single-conflict phrasing), but the backend has always emitted `conflicts: [...]` (campaigns.py:625-626, `_check_sender_lock` array). This is a latent bug on the `/start` path that Phase 8 surfaces on attach. Rewrite to read `detail.conflicts[]`:
```ts
SENDER_LOCK_CONFLICT: (d) => {
  const conflicts = (d.conflicts as Array<{ campaign_name?: string }>) ?? [];
  const names = conflicts.map((c) => c.campaign_name).filter(Boolean).join(", ");
  return names
    ? `Account is already in running campaign(s): ${names}. Stop them to free the account.`
    : "Account is locked by another running campaign.";
},
```

---

## Shared Patterns

### Sender-lock 409 contract (`SENDER_LOCK_CONFLICT`)
**Source:** `_check_sender_lock` (campaigns.py:275-298); raised at start_campaign:621-627 & resume_campaign:671-677.
**Apply to:** attach endpoint. Body MUST be `{"code":"SENDER_LOCK_CONFLICT","conflicts":[{sender_id,campaign_id,campaign_name}]}` — identical to `/start`. Do NOT invent a new code.

### `attached_senders` response shape
**Source:** `_build_attached_senders` (campaigns.py:194-225) → `_campaign_to_response` (campaigns.py:228-272).
**Apply to:** both new endpoints' responses (`response_model=CampaignResponse`). Already supplies `locked_by_campaign_id/name` the frontend panel consumes.

### Worker-safe row claim (`FOR UPDATE OF mq SKIP LOCKED` + `status='pending'`)
**Source:** `_process_next_for_sender` (queue.py:294-313).
**Apply to:** rebalance donor SELECT, and the cold-pending guard predicate. The `status='pending'` filter + SKIP LOCKED is what prevents racing in-flight sends.

### Cold-pending predicate (never-sent + no-dialog)
**Source:** new, but composed from `_compute_is_exhausted` join key (campaigns.py:182, `contact_phone`) + `conversations.contact_phone` model.
**Apply to:** detach guard (D-04) AND rebalance donor filter (D-08) — same `NOT EXISTS sent` + `NOT EXISTS conversations` shape, keyed on `recipient_phone`. Keeping them identical guarantees detach blocks exactly the rows rebalance would move.

### Raw-SQL `text()` for multi-table queries; ORM for single-table
**Source:** campaigns.py (`_check_sender_lock`, `_build_attached_senders`, `_cancel_pending_queue` use `text()`; CRUD inserts use ORM `db.add` / `select`). rotation.py & queue.py all `text()`.
**Apply to:** rebalance (all `text()`), endpoints (ORM for insert/delete/count, `text()` for the cold-pending EXISTS).

### Frontend mutation + invalidate + actionError banner
**Source:** `lifecycleMut` (campaigns.$id.tsx:89-104) + `errMsg` (campaigns.$id.tsx:29-33) + `actionError` state.
**Apply to:** attachMut / detachMut. Reuse the existing banner; no new error UI.

### OpenAPI / types regeneration (do NOT hand-edit)
**Source:** `scripts/export-handoff.sh`.
**Apply to:** after the two endpoints merge, run the script and commit regenerated `lovable-handoff/openapi.json` + `src/types/api.ts`. Two-repo discipline: backend → `Andrewbruce165/outreach-platform`, frontend → `AGS-Venture-Lab/aimly-tg-outreach`.

---

## No Analog Found

None. Every file has a confirmed in-repo analog. The closest to "no analog" is `rebalance.py`'s campaign-scoped even-split, but it is **composed** from the verified `rotation._pick_least_loaded` SQL shape + `queue` SKIP-LOCKED lock + `rotation` CCA upsert — not invented from scratch.

| File | Role | Data Flow | Note |
|------|------|-----------|------|
| `app/services/rebalance.py` | service | batch | Net-new function, but every SQL building block is copied from rotation.py + queue.py (cited above). No external pattern needed. |

---

## Migration Note (explicit negative)

**Phase 8 adds NO migration.** Migration `016_phase4.sql` already creates `campaign_senders` (PK `(campaign_id, sender_id)`, indexes on sender_id/workspace_id), `campaign_contact_assignments` (UNIQUE `idx_cca_campaign_phone`), and the `message_queue (workspace_id, campaign_id, status, scheduled_at)` composite index. Rebalance + guards are pure reads + UPDATEs on existing columns. State this negative in the plan to prevent accidental drift.

---

## Metadata

**Analog search scope:** `app/routers/campaigns.py`, `app/services/{rotation,queue}.py`, `app/schemas/__init__.py`, `tests/conftest.py`, `migrations/016_phase4.sql`; frontend `src/routes/_authenticated/{campaigns.$id,campaigns.new}.tsx`, `src/lib/{api,error-codes}.ts`.
**Files scanned (read in source this session):** 8.
**Pattern extraction date:** 2026-06-23
