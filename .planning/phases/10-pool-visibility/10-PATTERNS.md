# Phase 10: Pool Visibility & Restriction Audit - Pattern Map

**Mapped:** 2026-06-24
**Files analyzed:** 9 (3 new, 6 modified) + 2 new test files
**Analogs found:** 9 / 9 (all exact or strong role-match — pure-additive brownfield phase)

All line numbers below are **CURRENT as of 2026-06-24** (verified live; CONTEXT/RESEARCH lines had drifted post-Phase-9 — these supersede them where they differ).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| NEW `app/services/restriction_audit.py` | service | event-driven (write + transform) | `app/services/failover.py` | exact (dual-mode `db=None` helper) |
| NEW `migrations/030_sender_restriction_events.sql` | migration | n/a (DDL) | `migrations/028_sender_restriction.sql` | exact (idempotent raw-SQL, CHECK, indexes) |
| NEW ORM `SenderRestrictionEvent` in `app/models/__init__.py` | model | n/a | `MessageLog` (L108) + `Sender.proxy` JSONB (L87) | exact (workspace_id + sender_id FK + JSONB) |
| NEW endpoint `GET /senders/{slug}/restriction-events` `app/routers/senders.py` | route | request-response (read) | `GET /senders/{slug}/spambot-check` (L626) + `_load_sender_by_slug` (L209) | exact (slug-keyed, workspace-scoped) |
| MOD `app/routers/campaigns.py` `_campaign_to_response`/`_build_attached_senders` | route helper | transform (computed-field) | itself (L196 / L230, existing computed-field shape) | exact (extend in place) |
| MOD `app/schemas/__init__.py` (`PoolHealth`, `CampaignSenderAttach`, `CampaignResponse`, `RestrictionEventResponse`) | schema | n/a | `SenderResponse` L133-134 + `CampaignSenderAttach` L574 + `CampaignResponse` L685 | exact (reuse field names verbatim) |
| MOD `app/services/queue.py` (PEER_FLOOD/ACCOUNT_FROZEN/FLOOD_WAIT wiring) | service | event-driven | itself (existing `db2` blocks) | exact (insert helper call into existing TX) |
| MOD `app/services/listener.py` (antispam + reconcile wiring) | service | event-driven | itself (existing `session`/`db` blocks) | exact (insert helper call into existing TX) |
| NEW `tests/test_restriction_audit.py` | test | n/a | `tests/test_failover.py` | exact (import-inside-body RED stub) |
| NEW `tests/test_pool_health.py` | test | n/a | `tests/test_rebalance.py` + `tests/test_pool_endpoints.py` | exact (factory fixtures) |

---

## Pattern Assignments

### NEW `app/services/restriction_audit.py` (service, event-driven)

**Analog:** `app/services/failover.py` — the dual-mode `db=None` session helper. This is the single most load-bearing pattern: the helper must write the event in the **same transaction** as the `restriction_status` UPDATE (queue.py `db2`, listener `session`/`db`) OR open its own session when called bare.

**Imports pattern** (`app/services/failover.py:47-56`):
```python
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.services.rotation import _pick_least_loaded

logger = logging.getLogger(__name__)
```

**Dual-mode session skeleton — COPY THIS VERBATIM** (`app/services/failover.py:87-114`, public dispatcher + `_failover` core split):
```python
async def failover_cold_backlog(
    frozen_sender_id: UUID,
    db: AsyncSession | None = None,
) -> int:
    if db is None:
        async with AsyncSessionLocal() as own_db:
            moved = await _failover(frozen_sender_id, own_db)
            await own_db.commit()
            return moved
    # Transaction-neutral: caller owns the commit.
    return await _failover(frozen_sender_id, db)


async def _failover(frozen_sender_id: UUID, db: AsyncSession) -> int:
    """Core reassignment over a live session (no commit — caller decides)."""
    ...
```

For `record_restriction_event`: same shape — public fn dispatches on `db is None` (open+commit own) vs passed (`_record(...)` core, caller commits). RESEARCH §Code Examples L337-387 already has the proposed body (the slice SELECT over `messages_log` + INSERT). Note the `_failover` doc string convention "no commit — caller decides" — mirror it on `_record`.

**Module docstring convention:** failover.py opens with a Phase tag + "Why this exists" + "Session ownership" paragraph (L1-45). Match this — explain D-01 (event only on state-change/forward-shift), D-05 (slice snapshot at write time), and the same-TX guarantee.

---

### NEW `migrations/030_sender_restriction_events.sql` (migration)

**Analog:** `migrations/028_sender_restriction.sql` (full file read — 29 lines).

**Idempotent CHECK + index pattern** (`migrations/028_sender_restriction.sql:20-28`):
```sql
-- Guard against typos from raw-SQL writers (idempotent — drop+recreate).
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_restriction_status_chk;
ALTER TABLE senders ADD CONSTRAINT senders_restriction_status_chk
    CHECK (restriction_status IN ('none', 'spam_limited', 'frozen'));

CREATE INDEX IF NOT EXISTS idx_senders_restriction
    ON senders (restricted_until)
    WHERE restriction_status <> 'none';
```

Copy this exact `DROP CONSTRAINT IF EXISTS` → `ADD CONSTRAINT … CHECK` and `CREATE INDEX IF NOT EXISTS` idiom. RESEARCH §Proposed Table L240-266 has the proposed `CREATE TABLE IF NOT EXISTS sender_restriction_events` body — it already follows this style (`gen_random_uuid()` default, `workspace_id`/`sender_id` FK `ON DELETE CASCADE`, two indexes, `sre_category_chk`). The leading comment block in 028 (L1-16 explaining WHY the column exists) is the convention — mirror it.

**Next number confirmed:** highest existing migration is `029_campaign_pause_reason.sql`, so `030_*` is correct. Applier (`app/database.py::_apply_migrations`) runs lexically; idempotency is mandatory (file re-runs on any drift).

---

### NEW ORM model `SenderRestrictionEvent` in `app/models/__init__.py` (model)

**Analog:** `MessageLog` (L108-127) — the nearest model with `workspace_id` + `sender_id` FK + JSONB + `created_at` server default.

**Column pattern** (`app/models/__init__.py:108-124`):
```python
class MessageLog(Base):
    __tablename__ = "messages_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id"), nullable=False)
    ...
    extra_data = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

For `SenderRestrictionEvent`, reuse this exact `Column(UUID…, ForeignKey(…, ondelete="CASCADE"))` + `Column(JSONB, …)` (for `activity_slice` and `proxy`) + `Column(DateTime(timezone=True), server_default=func.now())` style. The `Sender.proxy` JSONB column (L87) is the proxy-snapshot type reference: `proxy = Column(JSONB, nullable=True)`. The restriction columns to read at write time are `Sender.restriction_status` (L93), `restricted_until` (L94), `rate_per_min/hour/day` (L95-97).

**Note:** the model is needed for ORM reads in the history endpoint (`from_attributes=True`); the migration is the source of truth for DDL (don't rely on `create_all` for the new table — it works but the idempotent migration is the contract).

---

### NEW endpoint `GET /senders/{slug}/restriction-events` (route, request-response)

**Analog:** `GET /senders/{slug}/spambot-check` (`app/routers/senders.py:626-631`) + `_load_sender_by_slug` (L209-234).

**Endpoint signature + auth_dep + workspace-scoping** (`app/routers/senders.py:400-408` — cleanest read example, simpler than spambot-check):
```python
@router.get("/senders/{slug}", response_model=SenderResponse)
async def get_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Get sender by slug (workspace-scoped). Returns derived status."""
    sender = await _load_sender_by_slug(db, ctx, slug)
    return _sender_to_response(sender)
```

**Workspace-scoped slug lookup — REUSE this helper directly** (`app/routers/senders.py:209-234`):
```python
async def _load_sender_by_slug(
    db: AsyncSession, ctx: AuthCtx, slug: str
) -> Sender:
    result = await db.execute(
        select(Sender)
        .where(
            Sender.slug == slug,
            Sender.workspace_id == ctx.workspace_id,
        )
    )
    sender = result.scalar_one_or_none()
    if sender is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SENDER_NOT_FOUND", "message": f"Sender '{slug}' not found"},
        )
    return sender
```

Endpoint body: `sender = await _load_sender_by_slug(db, ctx, slug)` then SELECT events `WHERE sender_id = :sid` (or `workspace_id = ctx.workspace_id AND sender_id`) `ORDER BY created_at DESC` (matches index `idx_sre_sender_created`). Return `list[RestrictionEventResponse]`. Imports already present in senders.py: `from app.utils.auth import AuthCtx, auth_dep` (L53), `select`, `text`, `HTTPException`, `get_db`.

---

### MOD `app/routers/campaigns.py` — `_build_attached_senders` + `_campaign_to_response` (route helper, transform)

**Analog:** the functions themselves — extend in place.

**`_build_attached_senders` current body** (`app/routers/campaigns.py:196-227`) — POOLV-02 enrichment goes here. Add `s.restriction_status, s.restricted_until` to the SELECT via `JOIN senders s ON s.id = cs.sender_id` and pass them into `CampaignSenderAttach(...)`:
```python
async def _build_attached_senders(
    db: AsyncSession, ctx: AuthCtx, campaign_id: UUID
) -> list[CampaignSenderAttach]:
    rows = await db.execute(text("""
        SELECT cs.sender_id,
               (SELECT c.id FROM campaign_senders cs2 ... LIMIT 1) AS locked_by_id,
               (SELECT c.name FROM campaign_senders cs2 ... LIMIT 1) AS locked_by_name
        FROM campaign_senders cs
        WHERE cs.campaign_id = :cid
        ORDER BY cs.added_at
    """), {"cid": str(campaign_id), "wid": str(ctx.workspace_id)})
    return [
        CampaignSenderAttach(
            sender_id=row[0],
            locked_by_campaign_id=row[1],
            locked_by_campaign_name=row[2],
        )
        for row in rows.fetchall()
    ]
```

**`_campaign_to_response` current body** (`app/routers/campaigns.py:230-277`) — POOLV-01 `pool_health` goes here. It already calls `_build_attached_senders` (L233) then builds `CampaignResponse(...)`. Add a sibling aggregate query (RESEARCH §Pattern 3 L170-180 SQL) and pass `pool_health=...` into the response constructor alongside `attached_senders=attached` (L273):
```python
async def _campaign_to_response(
    db: AsyncSession, ctx: AuthCtx, campaign: Campaign
) -> CampaignResponse:
    attached = await _build_attached_senders(db, ctx, campaign.id)
    if campaign.folder_id is None:
        is_exhausted = False
    else:
        is_exhausted = await _compute_is_exhausted(db, campaign.id, campaign.folder_id)
    return CampaignResponse(
        ...
        attached_senders=attached,
        is_exhausted=is_exhausted,   # ← add pool_health=pool_health nearby
        ...
    )
```

The `_compute_is_exhausted` call (L237-239) is the precedent for "compute a derived value in `_campaign_to_response` then pass it to the constructor" — `pool_health` follows the identical shape (one `await db.execute(text(...))` aggregate, mapped into a Pydantic sub-model).

---

### MOD `app/schemas/__init__.py` (schema)

**Analog:** `SenderResponse` (L133-134) for the restriction field defs, `CampaignSenderAttach` (L574) and `CampaignResponse` (L685) for the targets.

**Restriction fields to COPY VERBATIM into `CampaignSenderAttach`** (`app/schemas/__init__.py:133-134`):
```python
# Migration 028: write-restriction state, orthogonal to auth_status.
restriction_status: Literal["none", "spam_limited", "frozen"] = "none"
restricted_until: Optional[datetime] = None
```

**`CampaignSenderAttach` current shape** (`app/schemas/__init__.py:574-591`) — add the two fields above; keep the `@computed_field id` property:
```python
class CampaignSenderAttach(BaseModel):
    sender_id: UUID
    locked_by_campaign_id: Optional[UUID] = None
    locked_by_campaign_name: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> UUID:
        return self.sender_id
```

**`CampaignResponse` tail** (`app/schemas/__init__.py:685-729`) — add `pool_health: PoolHealth` near `attached_senders`/`is_exhausted` (L726-727). Note `model_config = ConfigDict(from_attributes=True)` (L687). New `PoolHealth(BaseModel)`: `{active: int, paused: int, total: int, earliest_resume_at: Optional[datetime] = None}` (exact D-08 names). New `RestrictionEventResponse(BaseModel)` with `model_config = ConfigDict(from_attributes=True)` mirroring the table columns (id, event_type, source, category, restricted_until, raw_text, activity_slice, proxy, created_at).

---

### MOD `app/services/queue.py` — wiring (service, event-driven)

**Analog:** the existing `db2` blocks (already verified pattern). The event-write goes **inside the existing `async with AsyncSessionLocal() as db2:` block, before `await db2.commit()`** — same TX as the `senders` UPDATE.

**PEER_FLOOD write-point** (`app/services/queue.py:733-754`) — insert `await record_restriction_event(..., db=db2)` after the senders UPDATE (L753), before `await db2.commit()` (L754):
```python
elif error_code == "PEER_FLOOD":
    pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
    recheck_at = datetime.now(timezone.utc) + timedelta(
        seconds=get_settings().restriction_recheck_interval_seconds
    )
    async with AsyncSessionLocal() as db2:
        await db2.execute(text("""UPDATE message_queue SET scheduled_at = :pause_until
            WHERE sender_id = :sid AND status = 'pending'"""),
            {"pause_until": pause_until, "sid": str(sender.id)})
        await db2.execute(text("""UPDATE senders
            SET restriction_status = 'spam_limited', restricted_until = :recheck_at
            WHERE id = :sid"""), {"recheck_at": recheck_at, "sid": str(sender.id)})
        # ← INSERT HERE: record_restriction_event(sender.id, "spam_limited",
        #    "queue_error", recheck_at, error_msg, db=db2)
        await db2.commit()
```
- event_type=`spam_limited`, source=`queue_error`, restricted_until=`recheck_at`, raw_text=`error_msg` (L697 `error.get("message")`), category=`restriction`.
- Note: `failover_cold_backlog(sender.id)` is called AFTER this block (L768-769) with its OWN session — do NOT fold the event into that; it belongs in `db2`.

**ACCOUNT_FROZEN write-point** (`app/services/queue.py:783-802`) — identical structure, `db2` block at L791-802. event_type=`frozen`, source=`queue_error`, restricted_until=`recheck_at`, raw_text=`error_msg`.

**HARD FloodWait write-point** (`app/services/queue.py:704-715`) — the `db2` block here ONLY pauses the queue; it does **NOT** UPDATE `senders.restriction_status` (Pitfall 4). If logging a `flood_wait` event (Open Q #1), record it with `restricted_until=reschedule_at`, source=`queue_error`, category=`restriction`, but it must NOT affect `pool_health` (which reads `restriction_status`). Planner to confirm scope.

---

### MOD `app/services/listener.py` — wiring (service, event-driven)

**Analog:** the existing `session`/`db` blocks. Both write-points already use a single session committed once — add the event-write before the commit (mirrors how `failover_cold_backlog(sender_id, session)` is called transaction-neutral here).

**antispam path** (`app/services/listener.py:881-965`, the `session` block L919-955) — the `failover_cold_backlog(sender_id, session)` call at L953 (transaction-neutral, passing `session`) is the exact precedent. Insert `await record_restriction_event(sender_id, "spam_limited", source, recheck_at, message_text, db=session)` before `await session.commit()` (L955). source: research Open Q #2 suggests `antispam_signal` (or `queue_error` for strict D-02). raw_text=`message_text` (the bot message param, L886).

**reconcile path** (`app/services/listener.py:1360-1457`) — three terminal branches, each in its own `async with AsyncSessionLocal() as db:` block (L1402), committed per-branch. Insert the helper call before each `await db.commit()`:
- **`cleared`** (verdict=='free', L1403-1419, commit L1417): `record_restriction_event(r[0], "cleared", "spambot_reconcile", None, result.get("raw_text"), db=db)`.
- **`banned`** (verdict=='suspended', L1420-1426, commit L1424): `record_restriction_event(r[0], "banned", "spambot_reconcile", restricted_until, result.get("raw_text"), db=db)`.
- **`extension`** (else, L1427-1447, commit L1442): **GATED** — emit ONLY on a real forward shift (D-01 / Pitfall 1).

**D-01 extension gate — CRITICAL.** The reconcile SELECT (L1377) currently loads `id, slug, restriction_status`. It MUST also load `restricted_until` so the helper can compare old vs new:
```python
# CURRENT (app/services/listener.py:1376-1382):
text("""
    SELECT id, slug, restriction_status
    FROM senders
    WHERE restriction_status <> 'none'
      AND restricted_until IS NOT NULL
      AND restricted_until <= NOW()
""")
# → add restricted_until to the SELECT, then in the else-branch (L1439):
# if next_at > old_until + timedelta(minutes=1):
#     await record_restriction_event(r[0], "extension", "spambot_reconcile",
#                                    next_at, result.get("raw_text"), db=db)
# await db.execute(text("UPDATE senders SET restricted_until = :next WHERE id = :sid"), ...)
```
The unconditional `UPDATE senders SET restricted_until = :next` (L1439-1441) STAYS; only the **event-write** is gated by the diff. A pure recheck-interval bump (no `limit_until` from SpamBot) is NOT a real extension — suppress it.

---

### NEW `tests/test_restriction_audit.py` + `tests/test_pool_health.py` (test)

**Analog:** `tests/test_failover.py` (import-inside-body RED stub) + `tests/test_rebalance.py` (factory usage) + conftest fixtures.

**Import-inside-body RED stub pattern** (`tests/test_failover.py:1-37`) — module docstring explains why the import is inside each test body (keeps `--collect-only` clean while RED at runtime), `pytestmark = pytest.mark.asyncio`, then local SQL helpers:
```python
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _pending_counts(db, campaign_id) -> dict[str, int]:
    rows = (await db.execute(text("""
        SELECT sender_id, COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND status = 'pending'
        GROUP BY sender_id
    """), {"cid": str(campaign_id)})).all()
    return {str(r[0]): int(r[1]) for r in rows}
```
Each test does `from app.services.restriction_audit import record_restriction_event` INSIDE the body (RED until Wave implements it). Test→requirement map is documented in the module docstring (see test_failover.py:19-31 for the format).

**Factory fixtures (conftest)** — use, do NOT reinvent:
- `async_db_session` (`tests/conftest.py:187`) — the in-test session passed to the helper so writes are visible.
- `test_running_campaign_factory` (`tests/conftest.py:703`) — `camp, senders = await test_running_campaign_factory(sender_count=2)`.
- `test_queue_item_factory` (`tests/conftest.py:600`) — `await test_queue_item_factory(camp["id"], sender.id, "+7990…", status="pending", with_cca=True, with_conversation=False)` (usage at `tests/test_rebalance.py:102-105`).
- For HLTH-02 slice assertions, seed `messages_log` rows directly (`message_type='sent'`, `created_at` windowed) — there is no dedicated factory; INSERT via `async_db_session.execute(text(...))` mirroring the `messages_log` columns (model L108-124: workspace_id, sender_id, recipient_phone, message_text, message_type, created_at).

**Freeze-state helper** (`tests/test_failover.py:62-70`) — `_freeze_sender(db, sender_id, status)` UPDATEs `senders.restriction_status` exactly as the real paths do; reuse for POOLV-01 3-state pool_health arithmetic (all-active / partial / all-paused).

---

## Shared Patterns

### Dual-mode session helper (own-session vs transaction-neutral)
**Source:** `app/services/failover.py:87-114`
**Apply to:** `app/services/restriction_audit.py::record_restriction_event` (and every call-site decides `db=` vs bare).
The single most important pattern of the phase. `db=None` → `async with AsyncSessionLocal() as own: … await own.commit()`. `db` passed → core fn, caller commits. Guarantees event + `restriction_status` UPDATE land atomically.

### Idempotent raw-SQL migration
**Source:** `migrations/028_sender_restriction.sql` (full file)
**Apply to:** `migrations/030_sender_restriction_events.sql`
`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS` → `ADD CONSTRAINT … CHECK`. Leading comment block stating WHY. Auto-applied at api start (`app/database.py::_apply_migrations`); must survive re-run.

### Workspace-scoped slug lookup with opaque 404
**Source:** `app/routers/senders.py:209-234` (`_load_sender_by_slug`)
**Apply to:** the new `GET /senders/{slug}/restriction-events` endpoint — call it directly, then SELECT events for `sender.id`.

### `Depends(auth_dep)` + `Depends(get_db)` on every endpoint
**Source:** `app/routers/senders.py:400-408` (`get_sender`)
**Apply to:** the new read endpoint. `ctx: AuthCtx = Depends(auth_dep)` gives `ctx.workspace_id` for scoping.

### Computed/derived value in `_campaign_to_response`
**Source:** `app/routers/campaigns.py:230-277` (existing `is_exhausted` / `attached_senders`)
**Apply to:** `pool_health` (POOLV-01) and per-sender enrichment (POOLV-02). One aggregate `await db.execute(text(...))`, mapped to a Pydantic sub-model, passed to the `CampaignResponse(...)` constructor.

### Pydantic response sub-model with `from_attributes=True`
**Source:** `app/schemas/__init__.py:687` (`CampaignResponse.model_config`) + `SenderResponse:133-134` (restriction fields)
**Apply to:** `PoolHealth`, `RestrictionEventResponse`, and the two new `CampaignSenderAttach` fields (copy field defs verbatim for name/type consistency).

### Same-transaction event-write at restriction-status UPDATE points
**Source:** `app/services/queue.py:743-754` (`db2` block) + `app/services/listener.py:919-955` (`session` block, `failover_cold_backlog(…, session)` at L953)
**Apply to:** all five write-points. The event-write goes inside the existing session block, before its commit — never a separate session (divergence-on-crash risk, Pitfall 2).

---

## No Analog Found

None. This is a pure-additive brownfield phase — every primitive (restriction columns, write-points, raw-text sources, `messages_log` slice source with index, computed-field response shape, dual-mode session helper, idempotent migration applier, factory fixtures) already exists in the codebase. The phase is wiring + one table, not new machinery.

---

## Metadata

**Analog search scope:** `app/services/` (failover, queue, listener), `app/routers/` (senders, campaigns), `app/models/__init__.py`, `app/schemas/__init__.py`, `migrations/`, `tests/`.
**Files scanned:** 10 source files + 3 test files + conftest + migration listing.
**Pattern extraction date:** 2026-06-24
**Line-number authority:** verified live this session — these supersede CONTEXT/RESEARCH where drifted (e.g. reconcile SELECT at L1377, antispam session block L919-955, `_campaign_to_response` L230-277).
