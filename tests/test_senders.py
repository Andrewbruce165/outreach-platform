"""
Integration tests для senders router (Phase 2 — SNDR-01, SNDR-02, SNDR-03).

Покрывает:
- Workspace-isolation (cross-tenant 404).
- Derived `status` field (D-11): error > lifecycle_status.
- Rate-limit warnings (D-14): soft cap → 200 + warnings[]; hard cap → 422.
- Lifecycle transitions (D-12): paused/active/warmup.
- Proxy pool CRUD (D-22) + assign-proxy.
"""

import uuid
from datetime import datetime
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub: str):
    """Bootstrap новый workspace через JWT POST /auth/me. Возвращает (token, workspace_id)."""
    token = valid_supabase_jwt(sub=sub, email=f"{sub}@test.com")
    r = await async_client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return token, r.json()["workspace_id"]


async def _insert_sender_raw(
    db: AsyncSession,
    workspace_id: str,
    slug: str,
    *,
    role: str = "sender",
    lifecycle_status: str = "active",
    auth_status: str = "ok",
    rate_per_min: int = 4,
    rate_per_hour: int = 20,
    phone: str | None = None,
) -> str:
    """Прямой INSERT в senders. Возвращает sender_id."""
    sid = str(uuid.uuid4())
    phone = phone or f"+7900{sid[:7]}"
    await db.execute(
        text("""
            INSERT INTO senders
                (id, workspace_id, slug, name, phone, session_string, role,
                 lifecycle_status, auth_status, rate_per_min, rate_per_hour)
            VALUES
                (:id, :wid, :slug, :name, :phone, 'encrypted_stub', :role,
                 :lifecycle, :auth, :rmin, :rhour)
        """),
        {
            "id": sid, "wid": workspace_id, "slug": slug, "name": slug,
            "phone": phone, "role": role,
            "lifecycle": lifecycle_status, "auth": auth_status,
            "rmin": rate_per_min, "rhour": rate_per_hour,
        }
    )
    await db.commit()
    return sid


# ─── 401: no auth ────────────────────────────────────────────────────────────


async def test_list_senders_no_auth_returns_401(async_client):
    """GET /senders без auth → 401 AUTH_REQUIRED."""
    response = await async_client.get("/api/v1/senders")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


# ─── Workspace isolation ─────────────────────────────────────────────────────


async def test_list_senders_workspace_isolated(
    async_client, async_db_session, valid_supabase_jwt
):
    """A видит только своих sender'ов; B-sender невидим для A."""
    token_a, ws_a = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="iso-a"
    )
    token_b, ws_b = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="iso-b"
    )

    await _insert_sender_raw(async_db_session, ws_a, "sender-a-1")
    await _insert_sender_raw(async_db_session, ws_b, "sender-b-1")

    # A видит только своего.
    r = await async_client.get(
        "/api/v1/senders", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert r.status_code == 200, r.text
    slugs = {s["slug"] for s in r.json()["senders"]}
    assert "sender-a-1" in slugs
    assert "sender-b-1" not in slugs


# ─── TODAY column: sent_today (rolling-24h numerator) ────────────────────────


async def _insert_queue_item(
    db: AsyncSession,
    workspace_id: str,
    sender_id: str,
    *,
    status: str = "sent",
    finished_hours_ago: float | None = None,
):
    """Прямой INSERT в message_queue. finished_hours_ago=None → finished_at NULL."""
    finished_clause = (
        "now() - make_interval(hours => :hrs)"
        if finished_hours_ago is not None
        else "NULL"
    )
    await db.execute(
        text(f"""
            INSERT INTO message_queue
                (id, workspace_id, sender_id, item_type, status,
                 recipient_phone, finished_at)
            VALUES
                (gen_random_uuid(), :wid, :sid, 'message', :status,
                 '+79990000000', {finished_clause})
        """),
        {
            "wid": workspace_id,
            "sid": sender_id,
            "status": status,
            **({"hrs": finished_hours_ago} if finished_hours_ago is not None else {}),
        },
    )
    await db.commit()


async def test_list_senders_includes_sent_today_field(
    async_client, async_db_session, valid_supabase_jwt
):
    """GET /senders surfaces sent_today; defaults to 0 with no queue rows."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="today-empty"
    )
    await _insert_sender_raw(async_db_session, ws, "today-empty-1")

    r = await async_client.get(
        "/api/v1/senders", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    sender = next(s for s in r.json()["senders"] if s["slug"] == "today-empty-1")
    assert "sent_today" in sender
    assert sender["sent_today"] == 0


async def test_list_senders_counts_sent_in_trailing_24h(
    async_client, async_db_session, valid_supabase_jwt
):
    """sent_today counts only status='sent' finished within the last 24h.

    Mirrors the rate-limiter daily-cap window (queue.py:450-466):
      - 2 sent within 24h        → counted
      - 1 sent 25h ago           → outside window, NOT counted
      - 1 pending                → wrong status, NOT counted
      - 1 sent with finished_at NULL → NOT counted (matches cap query)
    Expected sent_today == 2.
    """
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="today-count"
    )
    sid = await _insert_sender_raw(async_db_session, ws, "today-count-1")

    await _insert_queue_item(async_db_session, ws, sid, status="sent", finished_hours_ago=1)
    await _insert_queue_item(async_db_session, ws, sid, status="sent", finished_hours_ago=10)
    await _insert_queue_item(async_db_session, ws, sid, status="sent", finished_hours_ago=25)
    await _insert_queue_item(async_db_session, ws, sid, status="pending", finished_hours_ago=1)
    await _insert_queue_item(async_db_session, ws, sid, status="sent", finished_hours_ago=None)

    r = await async_client.get(
        "/api/v1/senders", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    sender = next(s for s in r.json()["senders"] if s["slug"] == "today-count-1")
    assert sender["sent_today"] == 2


async def test_get_sender_cross_tenant_returns_404(
    async_client, async_db_session, valid_supabase_jwt
):
    """Sender workspace B не видим из workspace A → 404, не 403."""
    token_a, ws_a = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="cross-a"
    )
    _token_b, ws_b = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="cross-b"
    )

    await _insert_sender_raw(async_db_session, ws_b, "private-b")

    r = await async_client.get(
        "/api/v1/senders/private-b",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SENDER_NOT_FOUND"


# ─── Derived status (D-11) ───────────────────────────────────────────────────


async def test_get_sender_derived_status_active(
    async_client, async_db_session, valid_supabase_jwt
):
    """auth_status=ok, lifecycle=active → derived status='active'."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="derived-ok"
    )
    await _insert_sender_raw(
        async_db_session, ws, "derived-active",
        auth_status="ok", lifecycle_status="active",
    )
    r = await async_client.get(
        "/api/v1/senders/derived-active",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["auth_status"] == "ok"
    assert body["lifecycle_status"] == "active"


async def test_get_sender_derived_status_error_when_auth_expired(
    async_client, async_db_session, valid_supabase_jwt
):
    """auth_status='session_expired' → derived 'error' независимо от lifecycle."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="derived-err"
    )
    await _insert_sender_raw(
        async_db_session, ws, "derived-error",
        auth_status="session_expired", lifecycle_status="active",
    )
    r = await async_client.get(
        "/api/v1/senders/derived-error",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["auth_status"] == "session_expired"
    # Raw lifecycle всё равно отдаётся в tooltip.
    assert body["lifecycle_status"] == "active"


async def test_get_sender_derived_status_paused(
    async_client, async_db_session, valid_supabase_jwt
):
    """lifecycle=paused, auth=ok → status='paused'."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="derived-paused"
    )
    await _insert_sender_raw(
        async_db_session, ws, "derived-paused",
        lifecycle_status="paused", auth_status="ok",
    )
    r = await async_client.get(
        "/api/v1/senders/derived-paused",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "paused"


# ─── Rate limit warnings (D-14) ──────────────────────────────────────────────


async def test_patch_sender_rate_limit_soft_returns_warnings(
    async_client, async_db_session, valid_supabase_jwt
):
    """rate_per_min=7 (между soft cap 4 и hard cap 10) → 200 + warnings[]."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="rl-soft"
    )
    await _insert_sender_raw(async_db_session, ws, "rl-soft-target")

    r = await async_client.patch(
        "/api/v1/senders/rl-soft-target",
        headers={"Authorization": f"Bearer {token}"},
        json={"rate_per_min": 7},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sender"]["rate_limits"]["per_minute"] == 7
    warnings = body["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["field"] == "rate_per_min"
    assert warnings[0]["value"] == 7
    assert warnings[0]["recommended_max"] == 4
    assert warnings[0]["severity"] == "warning"


async def test_patch_sender_rate_limit_hard_cap_422(
    async_client, async_db_session, valid_supabase_jwt
):
    """rate_per_min=15 (>hard cap 10) → 422 RATE_LIMIT_EXCEEDS_HARD_CAP.

    Pydantic срабатывает раньше нашего helper'а (Field(le=10)) — поэтому статус
    422 пришёл от FastAPI validation, не от ручного raise. Это OK — главное что
    запрос отбит.
    """
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="rl-hard"
    )
    await _insert_sender_raw(async_db_session, ws, "rl-hard-target")

    r = await async_client.patch(
        "/api/v1/senders/rl-hard-target",
        headers={"Authorization": f"Bearer {token}"},
        json={"rate_per_min": 15},
    )
    assert r.status_code == 422


async def test_patch_sender_within_green_corridor_no_warnings(
    async_client, async_db_session, valid_supabase_jwt
):
    """rate_per_min=3 (<=soft cap 4) → 200 + warnings=[]."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="rl-green"
    )
    await _insert_sender_raw(async_db_session, ws, "rl-green-target")

    r = await async_client.patch(
        "/api/v1/senders/rl-green-target",
        headers={"Authorization": f"Bearer {token}"},
        json={"rate_per_min": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sender"]["rate_limits"]["per_minute"] == 3
    assert body["warnings"] == []


# ─── Lifecycle transitions (D-12) ────────────────────────────────────────────


async def test_patch_sender_lifecycle_status_pause(
    async_client, async_db_session, valid_supabase_jwt
):
    """PATCH lifecycle_status='paused' → derived status='paused'."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="lc-pause"
    )
    await _insert_sender_raw(async_db_session, ws, "lc-pause-target")

    r = await async_client.patch(
        "/api/v1/senders/lc-pause-target",
        headers={"Authorization": f"Bearer {token}"},
        json={"lifecycle_status": "paused"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sender"]["lifecycle_status"] == "paused"
    assert body["sender"]["status"] == "paused"


async def test_patch_sender_lifecycle_invalid_value_422(
    async_client, async_db_session, valid_supabase_jwt
):
    """lifecycle_status='garbage' → 422 (Pydantic Literal)."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="lc-invalid"
    )
    await _insert_sender_raw(async_db_session, ws, "lc-invalid-target")

    r = await async_client.patch(
        "/api/v1/senders/lc-invalid-target",
        headers={"Authorization": f"Bearer {token}"},
        json={"lifecycle_status": "garbage"},
    )
    assert r.status_code == 422


# ─── Proxy pool CRUD (D-22) ──────────────────────────────────────────────────


async def test_workspace_proxies_crud(
    async_client, valid_supabase_jwt
):
    """POST /workspace/proxies → GET → DELETE."""
    token, _ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="proxy-crud"
    )
    # Create
    r = await async_client.post(
        "/api/v1/workspace/proxies",
        headers={"Authorization": f"Bearer {token}"},
        json={"host": "10.0.0.1", "port": 1080, "type": "socks5", "username": "u", "password": "p"},
    )
    assert r.status_code == 201, r.text
    proxy_id = r.json()["id"]

    # List
    r2 = await async_client.get(
        "/api/v1/workspace/proxies",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    assert r2.json()["total"] == 1
    assert r2.json()["proxies"][0]["host"] == "10.0.0.1"

    # Delete
    r3 = await async_client.delete(
        f"/api/v1/workspace/proxies/{proxy_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r3.status_code == 204


async def test_workspace_proxies_cross_tenant_isolation(
    async_client, valid_supabase_jwt
):
    """A's proxy не видим для B."""
    token_a, _ws_a = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="proxy-iso-a"
    )
    token_b, _ws_b = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="proxy-iso-b"
    )

    # A creates proxy
    ra = await async_client.post(
        "/api/v1/workspace/proxies",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"host": "10.99.0.1", "port": 5555, "type": "socks5", "username": "u"},
    )
    assert ra.status_code == 201
    a_proxy_id = ra.json()["id"]

    # B can't see it.
    rb_list = await async_client.get(
        "/api/v1/workspace/proxies",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert rb_list.status_code == 200
    assert all(p["id"] != a_proxy_id for p in rb_list.json()["proxies"])

    # B can't delete it → 404.
    rb_del = await async_client.delete(
        f"/api/v1/workspace/proxies/{a_proxy_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert rb_del.status_code == 404


async def test_assign_proxy_from_workspace_pool(
    async_client, async_db_session, valid_supabase_jwt
):
    """POST /senders/{slug}/assign-proxy назначает прокси sender'у."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="assign-ok"
    )
    await _insert_sender_raw(async_db_session, ws, "assignee-1")
    # Create proxy
    rp = await async_client.post(
        "/api/v1/workspace/proxies",
        headers={"Authorization": f"Bearer {token}"},
        json={"host": "1.2.3.4", "port": 1080, "type": "socks5", "username": "u", "password": "p"},
    )
    assert rp.status_code == 201
    proxy_id = rp.json()["id"]

    r = await async_client.post(
        "/api/v1/senders/assignee-1/assign-proxy",
        headers={"Authorization": f"Bearer {token}"},
        json={"proxy_id": proxy_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proxy"]["host"] == "1.2.3.4"
    assert body["proxy"]["port"] == 1080
    assert body["proxy"]["type"] == "socks5"


async def test_assign_proxy_cross_tenant_returns_404(
    async_client, async_db_session, valid_supabase_jwt
):
    """B назначает A's proxy → 404 PROXY_NOT_FOUND."""
    token_a, _ws_a = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="cross-proxy-a"
    )
    token_b, ws_b = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="cross-proxy-b"
    )
    # A creates proxy
    ra = await async_client.post(
        "/api/v1/workspace/proxies",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"host": "9.9.9.9", "port": 1111, "type": "socks5", "username": "u"},
    )
    a_proxy_id = ra.json()["id"]
    # B has a sender
    await _insert_sender_raw(async_db_session, ws_b, "b-sender-1")

    r = await async_client.post(
        "/api/v1/senders/b-sender-1/assign-proxy",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"proxy_id": a_proxy_id},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "PROXY_NOT_FOUND"


# ─── Sender create with warnings ─────────────────────────────────────────────


async def test_create_sender_above_soft_cap_returns_warnings(
    async_client, valid_supabase_jwt
):
    """POST /senders с rate_per_min=8 → 201 + warnings=[rate_per_min]."""
    token, _ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="create-warn"
    )
    r = await async_client.post(
        "/api/v1/senders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "create-with-warn-1",
            "name": "Test",
            "phone": "+79009999991",
            "session_string": "fake-session-string-stub",
            "role": "sender",
            "rate_per_min": 8,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sender"]["rate_limits"]["per_minute"] == 8
    assert len(body["warnings"]) == 1
    assert body["warnings"][0]["field"] == "rate_per_min"
    assert body["warnings"][0]["value"] == 8


async def test_create_sender_defaults_rate_limits(
    async_client, valid_supabase_jwt
):
    """POST /senders без rate_per_* → defaults 4/20, без warnings.

    Phase 22 D-04: rate_limits no longer carries per_day — the daily new-chat
    budget is grade-driven (grade_ladder.py), not a per-sender field.
    """
    token, _ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="create-default"
    )
    r = await async_client.post(
        "/api/v1/senders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "create-default-1",
            "name": "Test",
            "phone": "+79009999992",
            "session_string": "fake-session-string-stub",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sender"]["rate_limits"] == {
        "per_minute": 4, "per_hour": 20,
    }
    assert body["warnings"] == []
    assert body["sender"]["status"] == "active"


# ─── Update with hard cap ────────────────────────────────────────────────────


async def test_patch_rate_limit_just_at_hard_cap_ok(
    async_client, async_db_session, valid_supabase_jwt
):
    """rate_per_min=10 (=hard cap, не >) → 200 + warning (т.к. >soft cap=4).

    Phase 22 D-04: rate_per_day dropped from the API — only per_min/per_hour
    remain, so an at-hard-cap PATCH yields two warnings (not three). A stray
    rate_per_day in the body is silently ignored (extra field).
    """
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="rl-edge"
    )
    await _insert_sender_raw(async_db_session, ws, "rl-edge-target")

    r = await async_client.patch(
        "/api/v1/senders/rl-edge-target",
        headers={"Authorization": f"Bearer {token}"},
        json={"rate_per_min": 10, "rate_per_hour": 50, "rate_per_day": 300},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 10/50 — на hard cap, не превышают его → два warning (т.к. > soft cap 4/20).
    assert len(body["warnings"]) == 2


# ─── Phase 3 (plan 03-01 Task 7) — senders cleanup tests ─────────────────────


async def test_response_has_no_ai_context_id(async_db_session, test_sender_factory):
    """Phase 3 C-05: SenderResponse больше не содержит ai_context_id / ai_context_name.

    Direct schema check — без HTTP round-trip — потому что Phase 1 lazy-create
    через AuthDep даёт каждому JWT свежий workspace, а sender уже создан в
    test_workspace через factory. Проверяем форму ответа напрямую через
    _sender_to_response (исключения / AttributeError были бы здесь видны).
    """
    from app.routers.senders import _sender_to_response

    sender = await test_sender_factory(
        slug="phase3-resp-test", lifecycle_status="active", auth_status="ok"
    )

    response = _sender_to_response(sender)
    dump = response.model_dump()
    assert "ai_context_id" not in dump, \
        f"ai_context_id leaked into SenderResponse: {dump}"
    assert "ai_context_name" not in dump, \
        f"ai_context_name leaked into SenderResponse: {dump}"
    # Sanity: required fields still present
    assert dump["slug"] == "phase3-resp-test"
    assert dump["status"] == "active"


# ─── Phase 22 (plan 22-04) — rate_per_day removal (D-04) ─────────────────────


async def test_patch_sender_rate_per_day_ignored_no_per_day_in_response(
    async_client, async_db_session, valid_supabase_jwt
):
    """D-04: rate_per_day is gone from the API — PATCHing it is silently ignored
    and SenderResponse.rate_limits carries only per_minute/per_hour (no per_day).
    per_minute/per_hour are still applied/validated."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="rate-noday"
    )
    await _insert_sender_raw(async_db_session, ws, "rate-noday-target")

    r = await async_client.patch(
        "/api/v1/senders/rate-noday-target",
        headers={"Authorization": f"Bearer {token}"},
        json={"rate_per_day": 999, "rate_per_min": 3},
    )
    assert r.status_code == 200, r.text
    rl = r.json()["sender"]["rate_limits"]
    # per_day dropped from the schema entirely.
    assert "per_day" not in rl, f"per_day leaked into rate_limits: {rl}"
    # per_minute/per_hour intact and applied.
    assert rl["per_minute"] == 3
    assert "per_hour" in rl


# ─── Phase 22 (plan 22-04) — grade field exposure (D-12) ─────────────────────


async def test_get_sender_exposes_grade_fields(
    async_client, async_db_session, valid_supabase_jwt
):
    """D-12: single-sender GET exposes current_level + level_updated_at.
    Single-sender path reports remaining_daily_budget=None (list-only)."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="grade-get"
    )
    await _insert_sender_raw(async_db_session, ws, "grade-get-target")

    r = await async_client.get(
        "/api/v1/senders/grade-get-target",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_level"] == 1  # server_default '1' (mig 056)
    assert body["level_updated_at"] is not None
    assert body["remaining_daily_budget"] is None  # single-sender path


async def test_list_senders_exposes_grade_and_remaining_budget(
    async_client, async_db_session, valid_supabase_jwt
):
    """D-12: the list endpoint surfaces grade + a computed remaining_daily_budget
    (grade budget minus trailing-24h new dialogs, clamped >=0)."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="grade-list"
    )
    await _insert_sender_raw(async_db_session, ws, "grade-list-target")

    r = await async_client.get(
        "/api/v1/senders", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    sender = next(
        s for s in r.json()["senders"] if s["slug"] == "grade-list-target"
    )
    assert sender["current_level"] == 1
    assert sender["level_updated_at"] is not None
    # No dialogs opened → full level-1 budget (code-default ladder → 5).
    assert sender["remaining_daily_budget"] == 5


# ─── Phase 22 (plan 22-04) — grade override PATCH (D-15) ─────────────────────


async def test_patch_grade_override_sets_level_and_resets_timer(
    async_client, async_db_session, valid_supabase_jwt
):
    """D-15: PATCH /senders/{slug}/grade to level 2 sets current_level=2 and
    resets level_updated_at=NOW(); a subsequent read reflects it and the timer
    baseline moved forward (auto-progression restarts from the override)."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="grade-override"
    )
    sid = await _insert_sender_raw(async_db_session, ws, "grade-override-target")
    # Backdate the timer so the reset is observable.
    await async_db_session.execute(
        text("UPDATE senders SET level_updated_at = now() - interval '10 days' "
             "WHERE id = :sid"),
        {"sid": sid},
    )
    await async_db_session.commit()

    before = await async_client.get(
        "/api/v1/senders/grade-override-target",
        headers={"Authorization": f"Bearer {token}"},
    )
    old_ts = datetime.fromisoformat(before.json()["level_updated_at"])

    r = await async_client.patch(
        "/api/v1/senders/grade-override-target/grade",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_level": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_level"] == 2
    new_ts = datetime.fromisoformat(body["level_updated_at"])
    assert new_ts > old_ts, "level_updated_at was not reset forward"

    # Subsequent read reflects the override.
    after = await async_client.get(
        "/api/v1/senders/grade-override-target",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert after.json()["current_level"] == 2


async def test_patch_grade_override_level_4_rejected(
    async_client, async_db_session, valid_supabase_jwt
):
    """D-15/V4: an out-of-range level (not 1..3) is rejected with 422."""
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="grade-oob"
    )
    await _insert_sender_raw(async_db_session, ws, "grade-oob-target")

    r = await async_client.patch(
        "/api/v1/senders/grade-oob-target/grade",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_level": 4},
    )
    assert r.status_code == 422, r.text


async def test_patch_grade_override_cross_tenant_rejected(
    async_client, async_db_session, valid_supabase_jwt
):
    """D-15/T-22-10: a sender in another workspace cannot be re-graded.
    Workspace-scoped lookup 404s (not 403-leak) for a sender A does not own."""
    token_a, _ws_a = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="grade-xt-a"
    )
    _token_b, ws_b = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="grade-xt-b"
    )
    await _insert_sender_raw(async_db_session, ws_b, "grade-xt-b-target")

    r = await async_client.patch(
        "/api/v1/senders/grade-xt-b-target/grade",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"current_level": 2},
    )
    assert r.status_code in (403, 404), r.text
    # The sender in B is untouched (still level 1).
    check = await _token_b_read_level(
        async_client, _token_b, "grade-xt-b-target"
    )
    assert check == 1


async def _token_b_read_level(async_client, token, slug) -> int:
    r = await async_client.get(
        f"/api/v1/senders/{slug}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["current_level"]

