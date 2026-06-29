"""Phase 15 — Warmup worker RED guards (WARM-06 / WARM-10 / WARM-14).

These tests are intentionally RED at the end of Plan 15-01. They assert worker
behaviours that Plan 03 implements in ``app/services/warmup.py``:

- test_disabled_workspace_skipped (WARM-06): a workspace whose
  ``warmup_settings.enabled = false`` (or has no settings row) must contribute
  ZERO active-pool members to a worker tick — the enabled gate (D-06) drops
  disabled workspaces. RED until ``_get_active_pool`` honours the flag.
- test_content_defaults_when_empty (WARM-10): a workspace with no
  ``warmup_settings`` row (or empty topics / NULL system_prompt) must resolve to
  the 24 RU ``WARMUP_TOPICS`` + ``WARMUP_SYSTEM_PROMPT`` via a new content
  resolver helper. RED until the helper exists.
- test_restricted_sender_excluded (WARM-14): a sender with
  ``restriction_status='spam_limited'`` (or a future ``restricted_until``) must
  be excluded from ``_get_active_pool`` selection (RESV-05 model). RED until the
  restriction clause is added.

RED rationale: the enabled gate, the content resolver, and the restriction
clause do not exist yet, so the behavioural assertions fail (or AttributeError
on the missing helper) for the right reason. Imports of not-yet-existing symbols
are deferred into the test bodies so ``pytest --collect-only`` stays clean.
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── Helpers (raw INSERT to avoid coupling to ORM defaults) ───────────────────


async def _enroll_active_sender(db, wid: str, slug: str, **sender_overrides) -> str:
    """Create a role='sender', active+ok account and enroll it in warmup_pool."""
    sid = str(_uuid.uuid4())
    cols = dict(
        restriction_status="none",
        restricted_until=None,
    )
    cols.update(sender_overrides)
    await db.execute(text("""
        INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                             role, auth_status, lifecycle_status,
                             restriction_status, restricted_until,
                             rate_per_min, rate_per_hour, rate_per_day)
        VALUES (:id, :wid, :slug, :name, :phone, 'stub',
                'sender', 'ok', 'active',
                :restriction_status, :restricted_until, 4, 20, 150)
    """), {"id": sid, "wid": wid, "slug": slug, "name": slug,
           "phone": f"+790{abs(hash(sid)) % 10_000_000:07d}", **cols})
    await db.execute(text("""
        INSERT INTO warmup_pool (id, workspace_id, sender_id, is_active, enrolled_at)
        VALUES (gen_random_uuid(), :wid, :sid, true, NOW() - INTERVAL '3 days')
        ON CONFLICT (sender_id) DO NOTHING
    """), {"wid": wid, "sid": sid})
    await db.commit()
    return sid


async def _make_workspace(db) -> str:
    wid = str(_uuid.uuid4())
    await db.execute(text("INSERT INTO workspaces (id, name) VALUES (:id, :n)"),
                     {"id": wid, "n": f"WS {wid[:8]}"})
    await db.commit()
    return wid


# ── WARM-06: disabled workspace produces no active-pool members ──────────────


async def test_disabled_workspace_skipped(async_db_session):
    """A workspace with no warmup_settings row (enabled defaults OFF) must
    contribute no members to _get_active_pool — the worker skips it (D-06)."""
    db = async_db_session
    wid = await _make_workspace(db)
    await _enroll_active_sender(db, wid, f"disabled-{_uuid.uuid4().hex[:6]}")
    # No warmup_settings row → enabled is effectively FALSE.

    from app.services.warmup import warmup_worker

    pool = await warmup_worker._get_active_pool(db)
    members = [p for p in pool if p["workspace_id"] == wid]
    assert members == [], (
        "disabled workspace (no enabled warmup_settings) must yield zero "
        "active-pool members — enabled gate not implemented yet (WARM-06)"
    )


# ── WARM-10: empty settings → code-default topics + prompt ───────────────────


async def test_content_defaults_when_empty(async_db_session):
    """A workspace with no warmup_settings row resolves to the 24 RU topics +
    default system prompt via the content resolver helper (D-10)."""
    db = async_db_session
    wid = await _make_workspace(db)

    from app.services.warmup import (
        warmup_worker,
        WARMUP_TOPICS,
        WARMUP_SYSTEM_PROMPT,
    )

    resolver = getattr(warmup_worker, "_get_warmup_content", None)
    assert resolver is not None, (
        "WarmupWorker._get_warmup_content missing — content resolver not "
        "implemented yet (WARM-10)"
    )

    topics, prompt = await resolver(db, wid)
    assert topics == WARMUP_TOPICS, (
        "empty settings must resolve to the 24 hard-coded RU WARMUP_TOPICS"
    )
    assert prompt == WARMUP_SYSTEM_PROMPT, (
        "empty settings must resolve to the hard-coded WARMUP_SYSTEM_PROMPT"
    )


# ── WARM-14: restricted sender excluded from selection ───────────────────────


async def test_restricted_sender_excluded(async_db_session):
    """A sender with restriction_status='spam_limited' must not appear in
    _get_active_pool (RESV-05 model, D-14)."""
    db = async_db_session
    wid = await _make_workspace(db)
    # Enable warmup so the only thing keeping it out is the restriction clause.
    await db.execute(text("""
        INSERT INTO warmup_settings (workspace_id, enabled)
        VALUES (:wid, true)
        ON CONFLICT (workspace_id) DO UPDATE SET enabled = true
    """), {"wid": wid})
    await db.commit()

    restricted_slug = f"restricted-{_uuid.uuid4().hex[:6]}"
    await _enroll_active_sender(
        db, wid, restricted_slug, restriction_status="spam_limited",
    )

    from app.services.warmup import warmup_worker

    pool = await warmup_worker._get_active_pool(db)
    slugs = [p["slug"] for p in pool]
    assert restricted_slug not in slugs, (
        "spam_limited sender must be excluded from warmup pool selection — "
        "restriction clause not added yet (WARM-14)"
    )
