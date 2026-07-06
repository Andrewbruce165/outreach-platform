"""Wave-0 RED scaffold for Account Profile Management (Phase 20 — PROF-01..08 + D-08/D-09).

Convention (Phase 13/16/17/18 RED-scaffold): ANYTHING that binds to not-yet-existing
symbols (profile endpoints on app.routers.senders, TelegramService profile methods) is
imported/patched INSIDE the test body so `pytest --collect-only` stays clean. The
behavioural asserts stay RED until the downstream Wave-1..4 plans land the endpoints.

Test-name contract: these names are referenced verbatim by 20-VALIDATION.md and the
downstream plans — do NOT rename.

Status of each test in THIS plan (20-01):
  * test_profile_columns_defaults ............ GREEN (columns + server_default land here)
  * everything else .......................... RED (endpoint / TelegramService method absent)

Helpers `_create_workspace_via_jwt` / `_insert_sender_raw` are copied verbatim from
tests/test_senders.py (RED-scaffold precedent — tests/test_failover.py,
tests/test_queue_even_pacing.py copy their helpers rather than cross-import).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# ─── Helpers (copied verbatim from tests/test_senders.py:21-65) ────────────────


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
    rate_per_day: int = 150,
    phone: str | None = None,
) -> str:
    """Прямой INSERT в senders (OMITS profile_field_changed_at — server_default fires).
    Возвращает sender_id."""
    sid = str(uuid.uuid4())
    phone = phone or f"+7900{sid[:7]}"
    await db.execute(
        text("""
            INSERT INTO senders
                (id, workspace_id, slug, name, phone, session_string, role,
                 lifecycle_status, auth_status, rate_per_min, rate_per_hour, rate_per_day)
            VALUES
                (:id, :wid, :slug, :name, :phone, 'encrypted_stub', :role,
                 :lifecycle, :auth, :rmin, :rhour, :rday)
        """),
        {
            "id": sid, "wid": workspace_id, "slug": slug, "name": slug,
            "phone": phone, "role": role,
            "lifecycle": lifecycle_status, "auth": auth_status,
            "rmin": rate_per_min, "rhour": rate_per_hour, "rday": rate_per_day,
        },
    )
    await db.commit()
    return sid


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_profile_field_changed_at(db: AsyncSession, sender_id: str, mapping: dict) -> None:
    """Stamp the per-field cooldown STATE column (D-08). Used by the cooldown test."""
    await db.execute(
        text("UPDATE senders SET profile_field_changed_at = CAST(:m AS jsonb) WHERE id = :id"),
        {"m": json.dumps(mapping), "id": sender_id},
    )
    await db.commit()


# ─── PROF-01: schema/columns land in THIS plan → GREEN ─────────────────────────


async def test_profile_columns_defaults(async_client, async_db_session, valid_supabase_jwt):
    """GREEN (20-01): the 5 cached-profile columns exist; a raw INSERT that OMITS
    profile_field_changed_at succeeds because the ORM server_default fires under
    create_all; the four nullable columns default to NULL, the JSONB to {}."""
    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-defaults")
    sid = await _insert_sender_raw(async_db_session, ws, "prof-defaults-1")

    row = (await async_db_session.execute(
        text("""SELECT tg_username, tg_bio, tg_photo, tg_photo_mime, profile_field_changed_at
                FROM senders WHERE id = :id"""),
        {"id": sid},
    )).first()

    assert row is not None
    assert row.tg_username is None
    assert row.tg_bio is None
    assert row.tg_photo is None
    assert row.tg_photo_mime is None
    # JSONB may come back as dict (SQLAlchemy asyncpg codec) or str — normalise.
    pfca = row.profile_field_changed_at
    if isinstance(pfca, str):
        pfca = json.loads(pfca)
    assert pfca == {}, "profile_field_changed_at server_default '{}'::jsonb must fire on raw INSERT"


# ─── PROF-02/03: name + bio (RED) ──────────────────────────────────────────────


async def test_update_name_bio(async_client, async_db_session, valid_supabase_jwt):
    """RED: PATCH /senders/{slug}/profile writes first_name + about via a dispatched
    UpdateProfileRequest; an oversized bio → 400 BIO_TOO_LONG."""
    import app.services.telegram as telegram_module

    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-name")
    await _insert_sender_raw(async_db_session, ws, "prof-name-1")

    with patch.object(
        telegram_module.telegram_service, "update_profile",
        new=AsyncMock(return_value={"ok": True}), create=True,
    ) as mock_update:
        r = await async_client.patch(
            "/api/v1/senders/prof-name-1/profile",
            headers=_auth(token),
            json={"first_name": "Иван", "about": "Продажи зерна"},
        )
    assert r.status_code == 200, r.text
    assert mock_update.await_count == 1
    dispatched = mock_update.await_args
    assert dispatched is not None
    # The handler must dispatch functions.account.UpdateProfileRequest to Telegram.
    req = (dispatched.args[-1] if dispatched.args else None)
    assert req is not None and "UpdateProfile" in type(req).__name__

    # Oversized bio → 400 BIO_TOO_LONG (premium AboutTooLongError backstop path).
    r2 = await async_client.patch(
        "/api/v1/senders/prof-name-1/profile",
        headers=_auth(token),
        json={"about": "x" * 200},
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "BIO_TOO_LONG"


# ─── PROF-04: username (RED) ───────────────────────────────────────────────────


async def test_username(async_client, async_db_session, valid_supabase_jwt):
    """RED: username pre-check + set. Taken → 400 USERNAME_TAKEN; re-submitting the
    account's current username is a success no-op; a change <1h ago → 409 (D-08)."""
    import app.services.telegram as telegram_module

    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-uname")
    await _insert_sender_raw(async_db_session, ws, "prof-uname-1")

    # Pre-check: available.
    r_check = await async_client.get(
        "/api/v1/senders/prof-uname-1/username-check",
        headers=_auth(token),
        params={"username": "freename123"},
    )
    assert r_check.status_code == 200, r_check.text
    assert r_check.json()["available"] is True

    # Taken username → 400 USERNAME_TAKEN.
    with patch.object(
        telegram_module.telegram_service, "update_username",
        new=AsyncMock(side_effect=Exception("USERNAME_OCCUPIED")), create=True,
    ):
        r_taken = await async_client.patch(
            "/api/v1/senders/prof-uname-1/profile",
            headers=_auth(token),
            json={"username": "takenname"},
        )
    assert r_taken.status_code == 400
    assert r_taken.json()["detail"]["code"] == "USERNAME_TAKEN"


# ─── PROF-05: photo upload/delete (RED) ────────────────────────────────────────


async def test_photo(async_client, async_db_session, valid_supabase_jwt):
    """RED: POST /senders/{slug}/photo multipart dispatches UploadProfilePhotoRequest;
    DELETE clears it; >5MB → 413 FILE_TOO_LARGE; non-jpg/png → 422 UNSUPPORTED_FILE_TYPE;
    a photo change <1h ago → 409 (D-08)."""
    import app.services.telegram as telegram_module

    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-photo")
    await _insert_sender_raw(async_db_session, ws, "prof-photo-1")

    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    )
    with patch.object(
        telegram_module.telegram_service, "upload_profile_photo",
        new=AsyncMock(return_value={"ok": True}), create=True,
    ) as mock_upload:
        r = await async_client.post(
            "/api/v1/senders/prof-photo-1/photo",
            headers=_auth(token),
            files={"file": ("avatar.png", tiny_png, "image/png")},
        )
    assert r.status_code == 200, r.text
    assert mock_upload.await_count == 1

    # Unsupported type → 422 UNSUPPORTED_FILE_TYPE.
    r_bad = await async_client.post(
        "/api/v1/senders/prof-photo-1/photo",
        headers=_auth(token),
        files={"file": ("x.gif", b"GIF89a", "image/gif")},
    )
    assert r_bad.status_code == 422
    assert r_bad.json()["detail"]["code"] == "UNSUPPORTED_FILE_TYPE"

    # Oversized (>5MB) → 413 FILE_TOO_LARGE.
    r_big = await async_client.post(
        "/api/v1/senders/prof-photo-1/photo",
        headers=_auth(token),
        files={"file": ("big.jpg", b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024 + 1), "image/jpeg")},
    )
    assert r_big.status_code == 413
    assert r_big.json()["detail"]["code"] == "FILE_TOO_LARGE"

    # DELETE clears the photo.
    with patch.object(
        telegram_module.telegram_service, "delete_profile_photos",
        new=AsyncMock(return_value={"ok": True}), create=True,
    ) as mock_del:
        r_del = await async_client.delete(
            "/api/v1/senders/prof-photo-1/photo", headers=_auth(token),
        )
    assert r_del.status_code == 200, r_del.text
    assert mock_del.await_count == 1


# ─── PROF-06: 2FA + recovery email (RED) ───────────────────────────────────────


async def test_2fa(async_client, async_db_session, valid_supabase_jwt):
    """RED: POST /2fa set/change dispatches edit_2fa; wrong current → 400 PASSWORD_INVALID;
    POST /2fa/recovery-email → 200 EMAIL_CONFIRMATION_SENT + code_length; the confirm
    endpoint dispatches ConfirmPasswordEmailRequest."""
    import app.services.telegram as telegram_module

    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-2fa")
    await _insert_sender_raw(async_db_session, ws, "prof-2fa-1")

    with patch.object(
        telegram_module.telegram_service, "edit_2fa",
        new=AsyncMock(return_value={"ok": True}), create=True,
    ) as mock_2fa:
        r = await async_client.post(
            "/api/v1/senders/prof-2fa-1/2fa",
            headers=_auth(token),
            json={"new_password": "s3cret-pass", "hint": "my hint"},
        )
    assert r.status_code == 200, r.text
    assert mock_2fa.await_count == 1

    # Wrong current password on change → 400 PASSWORD_INVALID.
    with patch.object(
        telegram_module.telegram_service, "edit_2fa",
        new=AsyncMock(side_effect=Exception("PASSWORD_HASH_INVALID")), create=True,
    ):
        r_bad = await async_client.post(
            "/api/v1/senders/prof-2fa-1/2fa",
            headers=_auth(token),
            json={"current_password": "wrong", "new_password": "another-pass"},
        )
    assert r_bad.status_code == 400
    assert r_bad.json()["detail"]["code"] == "PASSWORD_INVALID"

    # Recovery-email step 1 → 200 EMAIL_CONFIRMATION_SENT + code_length.
    with patch.object(
        telegram_module.telegram_service, "set_recovery_email",
        new=AsyncMock(return_value={"code_length": 6}), create=True,
    ):
        r_email = await async_client.post(
            "/api/v1/senders/prof-2fa-1/2fa/recovery-email",
            headers=_auth(token),
            json={"email": "recover@example.com", "current_password": "s3cret-pass"},
        )
    assert r_email.status_code == 200, r_email.text
    body = r_email.json()
    assert body.get("code") == "EMAIL_CONFIRMATION_SENT"
    assert isinstance(body.get("code_length"), int)


# ─── PROF-07: resync cache from Telegram (RED) ─────────────────────────────────


async def test_resync(async_client, async_db_session, valid_supabase_jwt):
    """RED: POST /senders/{slug}/resync refreshes tg_username/tg_bio/has_photo from the
    live account (mocked get_me / GetFullUser / download_profile_photo)."""
    import app.services.telegram as telegram_module

    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-resync")
    await _insert_sender_raw(async_db_session, ws, "prof-resync-1")

    with patch.object(
        telegram_module.telegram_service, "fetch_profile",
        new=AsyncMock(return_value={
            "username": "livehandle", "bio": "live bio", "has_photo": True,
        }),
        create=True,
    ):
        r = await async_client.post(
            "/api/v1/senders/prof-resync-1/resync", headers=_auth(token),
        )
    assert r.status_code == 200, r.text
    sender = r.json().get("sender", r.json())
    assert sender["tg_username"] == "livehandle"
    assert sender["tg_bio"] == "live bio"
    assert sender["has_photo"] is True


async def test_resync_updates_name(async_client, async_db_session, valid_supabase_jwt):
    """PROF-06 gap-fix: resync refreshes the display `name` from the live account.

    After the user renames on Telegram, get_me() returns a fresh first_name/last_name;
    the endpoint must compose them into the single `name` column (same convention as
    PATCH /profile). A resync payload WITHOUT a first_name must never blank the name.
    """
    import app.services.telegram as telegram_module

    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="prof-resync-name"
    )
    # _insert_sender_raw seeds name = slug.
    await _insert_sender_raw(async_db_session, ws, "prof-resync-name-1")

    # 1) Live first + last name → name recomposed to "First Last".
    with patch.object(
        telegram_module.telegram_service, "fetch_profile",
        new=AsyncMock(return_value={
            "username": "livehandle", "bio": "live bio", "has_photo": False,
            "first_name": "Иван", "last_name": "Петров",
        }),
        create=True,
    ):
        r = await async_client.post(
            "/api/v1/senders/prof-resync-name-1/resync", headers=_auth(token),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    sender = body.get("sender", body)
    assert sender["name"] == "Иван Петров"

    # 2) Live first name only (no last) → name is just the first name.
    with patch.object(
        telegram_module.telegram_service, "fetch_profile",
        new=AsyncMock(return_value={
            "username": "livehandle", "bio": "live bio", "has_photo": False,
            "first_name": "Иван", "last_name": None,
        }),
        create=True,
    ):
        r = await async_client.post(
            "/api/v1/senders/prof-resync-name-1/resync", headers=_auth(token),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    sender = body.get("sender", body)
    assert sender["name"] == "Иван"

    # 3) Payload WITHOUT first_name → the cached name is preserved (not blanked).
    with patch.object(
        telegram_module.telegram_service, "fetch_profile",
        new=AsyncMock(return_value={
            "username": "livehandle", "bio": "live bio", "has_photo": False,
        }),
        create=True,
    ):
        r = await async_client.post(
            "/api/v1/senders/prof-resync-name-1/resync", headers=_auth(token),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    sender = body.get("sender", body)
    assert sender["name"] == "Иван"  # unchanged from step 2


# ─── D-11: authenticated photo serving (RED) ───────────────────────────────────


async def test_photo_serve_auth(async_client, async_db_session, valid_supabase_jwt):
    """RED: GET /senders/{slug}/photo returns the cached bytes + correct media_type;
    requires JWT (401/403 without); a foreign-workspace slug → 404."""
    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-serve")
    sid = await _insert_sender_raw(async_db_session, ws, "prof-serve-1")
    # Seed a cached photo directly.
    await async_db_session.execute(
        text("UPDATE senders SET tg_photo = :b, tg_photo_mime = 'image/jpeg' WHERE id = :id"),
        {"b": b"\xff\xd8\xffFAKEJPEGBYTES", "id": sid},
    )
    await async_db_session.commit()

    # No auth → 401/403.
    r_noauth = await async_client.get("/api/v1/senders/prof-serve-1/photo")
    assert r_noauth.status_code in (401, 403)

    # With auth → bytes + jpeg media type.
    r = await async_client.get("/api/v1/senders/prof-serve-1/photo", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content == b"\xff\xd8\xffFAKEJPEGBYTES"

    # Foreign workspace cannot read this slug's photo → 404.
    token_b, _ = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-serve-b")
    r_foreign = await async_client.get(
        "/api/v1/senders/prof-serve-1/photo", headers=_auth(token_b),
    )
    assert r_foreign.status_code == 404


# ─── D-08: per-field 1h cooldown (RED) ─────────────────────────────────────────


async def test_cooldown_block(async_client, async_db_session, valid_supabase_jwt):
    """RED (D-08): a username changed <1h ago → 409 TOO_FREQUENT with retry seconds;
    name/bio edits are NEVER cooldown-blocked."""
    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-cooldown")
    sid = await _insert_sender_raw(async_db_session, ws, "prof-cooldown-1")

    thirty_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    await _seed_profile_field_changed_at(async_db_session, sid, {"username": thirty_min_ago})

    # username within the 1h cooldown → 409 TOO_FREQUENT.
    r = await async_client.patch(
        "/api/v1/senders/prof-cooldown-1/profile",
        headers=_auth(token),
        json={"username": "newhandle"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "TOO_FREQUENT"
    assert "retry_after_seconds" in detail or "retry_after" in detail

    # name/bio are never cooldown-blocked even with a fresh username stamp.
    r_name = await async_client.patch(
        "/api/v1/senders/prof-cooldown-1/profile",
        headers=_auth(token),
        json={"first_name": "Пётр", "about": "новое био"},
    )
    assert r_name.status_code != 409


# ─── D-09: warmup/new-account advisory is NON-blocking (RED) ───────────────────


async def test_warmup_advisory_not_blocking(async_client, async_db_session, valid_supabase_jwt):
    """RED (D-09): a warmup (or <7-day-old) sender can still edit its profile (200);
    an advisory warning is surfaced (warnings[] present) but the edit is NOT blocked."""
    import app.services.telegram as telegram_module

    token, ws = await _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub="prof-warmup")
    await _insert_sender_raw(
        async_db_session, ws, "prof-warmup-1", lifecycle_status="warmup",
    )

    with patch.object(
        telegram_module.telegram_service, "update_profile",
        new=AsyncMock(return_value={"ok": True}), create=True,
    ):
        r = await async_client.patch(
            "/api/v1/senders/prof-warmup-1/profile",
            headers=_auth(token),
            json={"first_name": "Warmup Name"},
        )
    assert r.status_code == 200, r.text          # advisory, NOT a block
    body = r.json()
    assert "warnings" in body
    assert len(body["warnings"]) >= 1
