---
phase: 20-account-profile-management
plan: 03
type: execute
wave: 3
depends_on: ["20-02"]
files_modified:
  - app/services/telegram.py
  - app/routers/senders.py
autonomous: true
requirements: [PROF-04, PROF-06, PROF-07]
must_haves:
  truths:
    - "User uploads a JPG/PNG avatar and it becomes the Telegram profile photo; the cached bytes are refreshed and served back through an authenticated endpoint (D-11)"
    - "User deletes the profile photo; Telegram photo removed and cache cleared"
    - "Photo change <1h ago is hard-blocked (D-08); oversized/wrong-format uploads are rejected before any Telegram call"
    - "Manual resync re-fetches live username/bio/photo from Telegram into the cache (D-12)"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "set_profile_photo / delete_profile_photo / resync_profile (per-op client)"
      contains: "async def resync_profile"
    - path: "app/routers/senders.py"
      provides: "POST/DELETE/GET /senders/{slug}/photo + POST /senders/{slug}/resync"
      contains: "/senders/{slug}/photo"
  key_links:
    - from: "app/routers/senders.py (GET /senders/{slug}/photo)"
      to: "Sender.tg_photo (BYTEA)"
      via: "Response(content=sender.tg_photo, media_type=sender.tg_photo_mime)"
      pattern: "Response\\(content="
    - from: "app/routers/senders.py (POST /senders/{slug}/photo)"
      to: "app/services/telegram.py (set_profile_photo)"
      via: "telegram_service.set_profile_photo(...)"
      pattern: "set_profile_photo"
    - from: "app/routers/senders.py (POST /senders/{slug}/resync)"
      to: "app/services/telegram.py (resync_profile)"
      via: "telegram_service.resync_profile(...)"
      pattern: "resync_profile"
---

<objective>
Ship the profile-photo lifecycle (upload / delete / authenticated serve) and the manual resync (D-12). Photo bytes stay server-side and are served only through an auth-gated endpoint (D-11) — never a raw blob URL, never base64-inlined into the account list. Reuses the `_check_profile_cooldown`/`_stamp_profile_change` guardrail helpers from Plan 20-02.

Purpose: closes PROF-04 (photo), PROF-06 (resync), PROF-07 (serve). Sequential after 20-02 because both share `telegram.py` and `senders.py`.
Output: 3 TelegramService methods + 4 endpoints.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/20-account-profile-management/20-CONTEXT.md
@.planning/phases/20-account-profile-management/20-RESEARCH.md
@.planning/phases/20-account-profile-management/20-UI-SPEC.md

<interfaces>
<!-- Multipart upload precedent (app/routers/knowledge_bases.py:317-362) -->
```python
from fastapi import UploadFile, File
@router.post(...)
async def upload_document(slug: str, file: UploadFile = File(...), ctx: AuthCtx = Depends(auth_dep), db=Depends(get_db)):
    raw = await file.read()
    # size + content_type validation → structured 413/422
```
<!-- Telethon photo helpers (RESEARCH §Code Example 3/4/6, verified 1.42.0) -->
- upload:  `input_file = await client.upload_file(io.BytesIO(raw), file_name=...)` → `await client(photos.UploadProfilePhotoRequest(file=input_file))`
- delete:  `photos = await client.get_profile_photos('me', limit=1)` → `await client(photos.DeletePhotosRequest(id=[photos[0]]))` (fetch fresh — Pitfall 6 file_reference expiry)
- resync:  `me = await client.get_me()` (.username/.first_name/.last_name) ; `full = await client(users.GetFullUserRequest('me'))` (.full_user.about) ; `photo_bytes = await client.download_profile_photo('me', file=bytes)` (bytes | None)
<!-- guardrail helpers ALREADY EXIST from 20-02 in senders.py: _check_profile_cooldown / _stamp_profile_change / _profile_advisory ; _sender_to_response already carries has_photo -->
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: TelegramService photo + resync methods</name>
  <read_first>
    - app/services/telegram.py (update_profile/set_username from 20-02 for the exact skeleton; get_client ~284; disconnect_client ~334)
    - .planning/phases/20-account-profile-management/20-RESEARCH.md (§Code Example 3/4/6, §Pitfall 6 file_reference, §Open Question 3 cache Telegram's normalized avatar)
  </read_first>
  <behavior>
    - set_profile_photo(raw_bytes) dispatches upload_file then photos.UploadProfilePhotoRequest, then re-downloads the normalized avatar → returns {"success": True, "photo": <bytes>, "photo_mime": "image/jpeg"}
    - delete_profile_photo dispatches get_profile_photos('me', limit=1) then photos.DeletePhotosRequest → {"success": True}
    - resync_profile returns {"success": True, "username": ..., "bio": ..., "photo": <bytes|None>, "photo_mime": ...}
    - PhotoCropSizeSmallError → {"success": False, "error": {"code": "PHOTO_TOO_SMALL"}}; PhotoExtInvalidError → PHOTO_FORMAT_INVALID
  </behavior>
  <action>
Add three methods to `TelegramService` in `app/services/telegram.py`, each per-op client (create → op → `finally: disconnect_client`), SessionAuthError propagates.

```python
async def set_profile_photo(self, sender_slug, encrypted_session, raw_bytes: bytes, *, file_name="avatar.jpg", proxy=None) -> dict:
    import io
    from telethon.tl.functions.photos import UploadProfilePhotoRequest
    from telethon.errors import PhotoCropSizeSmallError, PhotoExtInvalidError, FloodWaitError
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        input_file = await client.upload_file(io.BytesIO(raw_bytes), file_name=file_name)
        await client(UploadProfilePhotoRequest(file=input_file))
        # OQ3: cache Telegram's own normalized small avatar (already square-ish), not the raw upload.
        norm = await client.download_profile_photo('me', file=bytes)
        return {"success": True, "photo": norm, "photo_mime": "image/jpeg"}
    except PhotoCropSizeSmallError:
        return {"success": False, "error": {"code": "PHOTO_TOO_SMALL", "message": "Фото слишком маленькое"}}
    except PhotoExtInvalidError:
        return {"success": False, "error": {"code": "PHOTO_FORMAT_INVALID", "message": "Неподдерживаемый формат. Загрузите JPG или PNG"}}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)

async def delete_profile_photo(self, sender_slug, encrypted_session, *, proxy=None) -> dict:
    from telethon.tl.functions.photos import DeletePhotosRequest
    from telethon.errors import FloodWaitError
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        photos = await client.get_profile_photos('me', limit=1)   # fresh file_reference (Pitfall 6)
        if photos:
            await client(DeletePhotosRequest(id=[photos[0]]))
        return {"success": True}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)

async def resync_profile(self, sender_slug, encrypted_session, *, proxy=None) -> dict:
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.errors import FloodWaitError
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        me = await client.get_me()
        try:
            full = await client(GetFullUserRequest('me'))
            bio = getattr(full.full_user, "about", None)
        except Exception:
            bio = None
        photo_bytes = await client.download_profile_photo('me', file=bytes)   # bytes | None
        return {"success": True, "username": getattr(me, "username", None), "bio": bio,
                "photo": photo_bytes, "photo_mime": "image/jpeg" if photo_bytes else None}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)
```
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_photo tests/test_account_profile.py::test_resync -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "async def set_profile_photo" app/services/telegram.py` matches
    - `grep -n "async def delete_profile_photo" app/services/telegram.py` matches
    - `grep -n "async def resync_profile" app/services/telegram.py` matches
    - `grep -n "get_profile_photos('me', limit=1)" app/services/telegram.py` matches (fresh file_reference)
    - `grep -n "download_profile_photo('me', file=bytes)" app/services/telegram.py` matches
    - each method has `await self.disconnect_client(client)` in a `finally:` block
    - test_photo and test_resync pass
  </acceptance_criteria>
  <done>Photo upload/delete + resync methods exist; upload caches Telegram's normalized avatar; delete fetches fresh file_reference; resync tests GREEN.</done>
</task>

<task type="auto">
  <name>Task 2: Photo endpoints (upload/delete/serve) + resync endpoint</name>
  <read_first>
    - app/routers/senders.py (_check_profile_cooldown/_stamp_profile_change/_profile_advisory from 20-02; _sender_to_response with has_photo; spambot-check try/finally shape; import block top of file)
    - app/routers/knowledge_bases.py (upload_document ~317-362 — UploadFile/File + size/content_type validation → 413/422)
    - .planning/phases/20-account-profile-management/20-RESEARCH.md (§Architecture Pattern 3 serve BYTEA + §Pitfall 3 + §Example 7 upload validation)
    - .planning/phases/20-account-profile-management/20-UI-SPEC.md (§Interaction Contracts C1 serve / C2 resync / C6 photo validation)
  </read_first>
  <action>
In `app/routers/senders.py`. Ensure `from fastapi import UploadFile, File, Response` (add to the existing fastapi import) and `SenderResponse` is imported.

Add module constants near the guardrail helpers:
```python
MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_MIME = {"image/jpeg", "image/png"}
```

1. **POST upload** (multipart). Validate size/mime BEFORE the Telegram call; cooldown check BEFORE upload:
```python
@router.post("/senders/{slug}/photo", response_model=SenderCreateResponse)
async def upload_sender_photo(slug: str, file: UploadFile = File(...), ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    _check_profile_cooldown(sender, "photo")     # D-08 hard block
    raw = await file.read()
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(413, detail={"code": "FILE_TOO_LARGE", "message": "Файл слишком большой (максимум 5 МБ)"})
    if file.content_type not in ALLOWED_PHOTO_MIME:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_FILE_TYPE", "message": "Только JPG или PNG"})
    try:
        res = await telegram_service.set_profile_photo(sender.slug, sender.session_string, raw, file_name=file.filename or "avatar.jpg", proxy=sender.proxy)
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    if not res.get("success"):
        raise HTTPException(429 if res["error"].get("code") == "FLOOD_WAIT" else 400, detail=res["error"])
    sender.tg_photo = res.get("photo") or raw
    sender.tg_photo_mime = res.get("photo_mime") or file.content_type
    _stamp_profile_change(sender, "photo")
    await db.commit(); await db.refresh(sender)
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=_profile_advisory(sender))
```

2. **DELETE photo** (delete counts as a "change" → cooldown + stamp):
```python
@router.delete("/senders/{slug}/photo", response_model=SenderCreateResponse)
async def delete_sender_photo(slug: str, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    _check_profile_cooldown(sender, "photo")
    try:
        res = await telegram_service.delete_profile_photo(sender.slug, sender.session_string, proxy=sender.proxy)
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    if not res.get("success"):
        raise HTTPException(429 if res["error"].get("code") == "FLOOD_WAIT" else 400, detail=res["error"])
    sender.tg_photo = None
    sender.tg_photo_mime = None
    _stamp_profile_change(sender, "photo")
    await db.commit(); await db.refresh(sender)
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=_profile_advisory(sender))
```

3. **GET serve** (D-11 — auth-gated bytes, never a raw URL; 404 when no cached photo):
```python
@router.get("/senders/{slug}/photo")
async def serve_sender_photo(slug: str, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    sender = await _load_sender_by_slug(db, ctx, slug)
    if not sender.tg_photo:
        raise HTTPException(404, detail={"code": "NO_PHOTO", "message": "No cached photo"})
    return Response(content=sender.tg_photo, media_type=sender.tg_photo_mime or "image/jpeg")
```

4. **POST resync** (D-12 — pull live profile into cache; does NOT open the edit form, is read-from-Telegram, so NO cooldown/stamp):
```python
@router.post("/senders/{slug}/resync", response_model=SenderResponse)
async def resync_sender_profile(slug: str, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    try:
        res = await telegram_service.resync_profile(sender.slug, sender.session_string, proxy=sender.proxy)
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    if not res.get("success"):
        raise HTTPException(429 if res["error"].get("code") == "FLOOD_WAIT" else 400, detail=res["error"])
    sender.tg_username = res.get("username")
    sender.tg_bio = res.get("bio")
    if res.get("photo") is not None:
        sender.tg_photo = res["photo"]
        sender.tg_photo_mime = res.get("photo_mime") or "image/jpeg"
    else:
        sender.tg_photo = None
        sender.tg_photo_mime = None
    await db.commit(); await db.refresh(sender)
    return _sender_to_response(sender)
```

NB: the GET serve endpoint MUST be declared so it does not collide with the existing `GET /senders/{slug}` — the `/photo` suffix makes it a distinct path, safe.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_photo tests/test_account_profile.py::test_resync tests/test_account_profile.py::test_photo_serve_auth -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n '@router.post("/senders/{slug}/photo"' app/routers/senders.py` matches
    - `grep -n '@router.delete("/senders/{slug}/photo"' app/routers/senders.py` matches
    - `grep -n '@router.get("/senders/{slug}/photo"' app/routers/senders.py` matches and body contains `Response(content=sender.tg_photo`
    - `grep -n '@router.post("/senders/{slug}/resync"' app/routers/senders.py` matches
    - `grep -n "MAX_PHOTO_BYTES = 5" app/routers/senders.py` matches
    - upload path calls `_check_profile_cooldown(sender, "photo")` before `set_profile_photo`; resync path does NOT call `_check_profile_cooldown`
    - test_photo, test_resync, test_photo_serve_auth all pass
  </acceptance_criteria>
  <done>Photo upload/delete/serve + resync endpoints live; photo bytes auth-gated (D-11); 1h block on photo (D-08); size/mime validation; resync refreshes cache (D-12). PROF-04/06/07 tests GREEN.</done>
</task>

</tasks>

<verification>
- `... run --rm api pytest tests/test_account_profile.py -x` — all photo/resync/serve tests GREEN.
- Photo bytes never appear in `/senders` list JSON (only `has_photo: bool`); `GET /senders/{slug}/photo` requires a JWT.
- `... run --rm api pytest tests/test_senders.py tests/test_account_profile.py tests/test_onboarding.py` (wave-merge sample) stays GREEN.
</verification>

<success_criteria>
- PROF-04 (photo upload/delete + block), PROF-06 (resync), PROF-07 (auth-gated serve) closed.
</success_criteria>

<output>
After completion, create `.planning/phases/20-account-profile-management/20-03-SUMMARY.md`.
</output>
