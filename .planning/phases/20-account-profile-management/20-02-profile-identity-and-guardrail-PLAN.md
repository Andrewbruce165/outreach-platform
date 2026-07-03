---
phase: 20-account-profile-management
plan: 02
type: execute
wave: 2
depends_on: ["20-01"]
files_modified:
  - app/services/telegram.py
  - app/routers/senders.py
  - app/routers/onboarding.py
autonomous: true
requirements: [PROF-02, PROF-03, PROF-08]
must_haves:
  truths:
    - "User edits first/last name and bio and it is written to Telegram (name/bio warning-only, never blocked — D-07)"
    - "User edits username with an availability pre-check; taken → error, current username → no-op success; a username changed <1h ago is hard-blocked (D-08)"
    - "A newly onboarded account has tg_username populated in cache without a manual resync (PROF-08)"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "TelegramService.update_profile / check_username / set_username (per-op client)"
      contains: "async def update_profile"
    - path: "app/routers/senders.py"
      provides: "PATCH /senders/{slug}/profile + GET /senders/{slug}/username-check + _check_profile_cooldown/_stamp_profile_change/_profile_advisory helpers"
      contains: "def _check_profile_cooldown"
    - path: "app/routers/onboarding.py"
      provides: "cache tg_username at finalize"
      contains: "tg_username"
  key_links:
    - from: "app/routers/senders.py (PATCH /senders/{slug}/profile)"
      to: "app/services/telegram.py (update_profile / set_username)"
      via: "telegram_service.update_profile(...) / set_username(...)"
      pattern: "telegram_service\\.(update_profile|set_username)"
    - from: "app/routers/senders.py (_check_profile_cooldown)"
      to: "Sender.profile_field_changed_at"
      via: "reads iso timestamp for username, raises 409 if <1h"
      pattern: "profile_field_changed_at"
---

<objective>
Ship the Section-A identity edit path: name/last-name/bio via `UpdateProfileRequest` (warning-only), username via `CheckUsernameRequest` pre-check + `UpdateUsernameRequest` (1h hard-block), the per-field frequency guardrail (D-06/D-07/D-08/D-09), and onboarding-finalize cache population (PROF-08). This is the first of three sequential backend plans that share the `telegram.py`/`senders.py` spine.

Purpose: closes PROF-02, PROF-03, PROF-08 and stands up the guardrail helpers that Plan 20-03 (photo) reuses.
Output: 3 new TelegramService methods, 2 new endpoints + 3 guardrail helpers on the senders router, onboarding cache write.
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
<!-- Per-operation client pattern — MIRROR EXACTLY (app/services/telegram.py:1053 send_message_by_telegram_id) -->
```python
async def send_message_by_telegram_id(self, sender_slug, encrypted_session, telegram_id, message, proxy=None) -> dict:
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        ...op...
        return {"success": True, ...}
    except FloodWaitError as e:
        return {"success": False, "error": f"Rate limited. Retry after {e.seconds} seconds"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if client:
            await self.disconnect_client(client)
```
`get_client` raises `SessionAuthError(slug, auth_status, msg)` on dead sessions; `disconnect_client` is the mandatory finally.

<!-- senders router (app/routers/senders.py) -->
- `router = APIRouter(prefix="/api/v1", tags=["senders"])`
- `_load_sender_by_slug(db, ctx, slug)` — workspace-scoped SELECT, opaque 404 {"code":"SENDER_NOT_FOUND"}.
- `_sender_to_response(sender, sent_today=0, locked_by_campaign_id=None, locked_by_campaign_name=None)` — MUST extend to pass the new profile fields (see Task 2).
- spambot-check handler (lines 655-745) is the canonical `try / except SessionAuthError → 403 AUTH_ERROR / except Exception → 500 / finally disconnect_client` shape to copy.
- `from app.services.telegram import telegram_service, SessionAuthError` (in-function import, as spambot-check does).

<!-- onboarding finalize (app/routers/onboarding.py) -->
`_create_sender_from_session` already computes `me = await client.get_me()` and reads `first_name = getattr(me, "first_name", None)`. Add `getattr(me, "username", None)` to the created Sender AND to the `_update_in_place` path.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: TelegramService profile methods (update_profile / check_username / set_username)</name>
  <read_first>
    - app/services/telegram.py (send_message_by_telegram_id ~line 1053 — copy its client-per-op skeleton; get_client ~284; disconnect_client ~334; SessionAuthError ~118)
    - .planning/phases/20-account-profile-management/20-RESEARCH.md (§Code Example 1 + Example 2 + §Standard Stack error table + §Pitfall 4)
  </read_first>
  <behavior>
    - update_profile passing only changed fields dispatches `account.UpdateProfileRequest(first_name=, last_name=, about=)` → {"success": True}
    - about > cap raises AboutTooLongError → {"success": False, "error": {"code": "BIO_TOO_LONG"}}
    - check_username("free") returns {"success": True, "available": True}; taken → available False
    - set_username("taken") → {"success": False, "error": {"code": "USERNAME_TAKEN"}}; UsernameNotModifiedError → {"success": True} (no-op)
    - dead session → SessionAuthError propagates (endpoint maps to 403)
  </behavior>
  <action>
Add three methods to `TelegramService` in `app/services/telegram.py`, each following the `send_message_by_telegram_id` client-per-op skeleton (create via `get_client`, op, `finally: disconnect_client`). Do NOT catch `SessionAuthError` here — let it propagate so the endpoint maps it to 403 (same as spambot-check). Use in-method imports for the TL functions (matches the codebase's local-import style).

```python
async def update_profile(self, sender_slug, encrypted_session, *, first_name=None,
                         last_name=None, about=None, proxy=None) -> dict:
    from telethon.tl.functions.account import UpdateProfileRequest
    from telethon.errors import AboutTooLongError, FirstNameInvalidError, FloodWaitError
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        # Only pass fields the caller actually changed (None = leave untouched — RESEARCH anti-pattern).
        await client(UpdateProfileRequest(first_name=first_name, last_name=last_name, about=about))
        return {"success": True}
    except AboutTooLongError:
        return {"success": False, "error": {"code": "BIO_TOO_LONG", "message": "Описание слишком длинное (максимум 70 символов)"}}
    except FirstNameInvalidError:
        return {"success": False, "error": {"code": "NAME_INVALID", "message": "Недопустимое имя"}}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds, "message": f"Слишком часто. Повторите через {e.seconds} c."}}
    finally:
        if client:
            await self.disconnect_client(client)

async def check_username(self, sender_slug, encrypted_session, username, *, proxy=None) -> dict:
    from telethon.tl.functions.account import CheckUsernameRequest
    from telethon.errors import UsernameInvalidError, FloodWaitError
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        try:
            available = await client(CheckUsernameRequest(username))
        except UsernameInvalidError:
            return {"success": True, "available": False, "reason": "invalid"}
        return {"success": True, "available": bool(available), "reason": None if available else "taken"}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)

async def set_username(self, sender_slug, encrypted_session, username, *, proxy=None) -> dict:
    # username="" clears it.
    from telethon.tl.functions.account import UpdateUsernameRequest
    from telethon.errors import (UsernameOccupiedError, UsernameInvalidError,
                                 UsernameNotModifiedError, UsernamePurchaseAvailableError, FloodWaitError)
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        try:
            await client(UpdateUsernameRequest(username))
        except UsernameNotModifiedError:
            return {"success": True}   # submitting current username = no-op success (Pitfall 4)
        return {"success": True}
    except UsernameOccupiedError:
        return {"success": False, "error": {"code": "USERNAME_TAKEN", "message": "Этот username уже занят"}}
    except UsernameInvalidError:
        return {"success": False, "error": {"code": "USERNAME_INVALID", "message": "Недопустимый формат username (a-z, 0-9, _, 5–32 символа)"}}
    except UsernamePurchaseAvailableError:
        return {"success": False, "error": {"code": "USERNAME_PURCHASE_REQUIRED", "message": "Этот username платный (Fragment)"}}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)
```
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_update_name_bio tests/test_account_profile.py::test_username -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "async def update_profile" app/services/telegram.py` matches
    - `grep -n "async def check_username" app/services/telegram.py` matches
    - `grep -n "async def set_username" app/services/telegram.py` matches
    - Each of the three methods contains `await self.disconnect_client(client)` inside a `finally:` block
    - `grep -n "UsernameNotModifiedError" app/services/telegram.py` matches (no-op success path present)
    - test_update_name_bio and test_username pass
  </acceptance_criteria>
  <done>update_profile/check_username/set_username exist, per-op client pattern, structured error dicts, no-op-on-not-modified; the two identity tests are GREEN.</done>
</task>

<task type="auto">
  <name>Task 2: Guardrail helpers + PATCH /profile + username-check endpoints</name>
  <read_first>
    - app/routers/senders.py (_load_sender_by_slug ~238; _sender_to_response ~116; update_sender PATCH ~440; spambot-check try/except/finally shape ~655-745; imports block top of file)
    - app/schemas/__init__.py (ProfileUpdate, UsernameCheckResponse, ProfileWarningItem, ProfileUpdateResponse — added in 20-01. CAUTION: the PRE-EXISTING `WarningItem` at ~lines 82-87 is the D-14 rate-limit shape {field:str, value:int, recommended_max:int} — constructing it with code=/message= raises pydantic.ValidationError; use ProfileWarningItem for advisories and leave WarningItem + the `_validate_rate_limits` path untouched)
    - .planning/phases/20-account-profile-management/20-CONTEXT.md (D-06/D-07/D-08/D-09 guardrail decisions)
    - .planning/phases/20-account-profile-management/20-UI-SPEC.md (§Interaction Contracts C3 + C5 + §Copywriting error strings)
  </read_first>
  <action>
In `app/routers/senders.py`:

1. **Extend `_sender_to_response`** to pass the new profile fields to `SenderResponse`:
```python
        tg_username=getattr(sender, "tg_username", None),
        tg_bio=getattr(sender, "tg_bio", None),
        has_photo=bool(getattr(sender, "tg_photo", None)),
        profile_field_changed_at=getattr(sender, "profile_field_changed_at", {}) or {},
```

2. **Add three module-level helpers** (near `_validate_rate_limits`):
```python
from datetime import datetime, timezone, timedelta   # ensure timedelta imported

_HARD_BLOCK_FIELDS = {"username", "photo"}   # D-08: only these hard-block
_HARD_BLOCK_WINDOW = timedelta(hours=1)

def _stamp_profile_change(sender: Sender, field: str) -> None:
    """Record last-change for a field. Reassign a NEW dict — SQLAlchemy does not track
    in-place JSONB mutation (no MutableDict), so mutating in place would not persist."""
    changed = dict(sender.profile_field_changed_at or {})
    changed[field] = datetime.now(timezone.utc).isoformat()
    sender.profile_field_changed_at = changed

def _check_profile_cooldown(sender: Sender, field: str) -> None:
    """D-08 HARD block for username/photo changed <1h ago → 409 TOO_FREQUENT.
    name/bio are warning-only (D-07) → no-op here."""
    if field not in _HARD_BLOCK_FIELDS:
        return
    ts = (sender.profile_field_changed_at or {}).get(field)
    if not ts:
        return
    try:
        last = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - last
    if elapsed < _HARD_BLOCK_WINDOW:
        retry_after = int((_HARD_BLOCK_WINDOW - elapsed).total_seconds())
        raise HTTPException(status_code=409, detail={
            "code": "TOO_FREQUENT",
            "message": f"{field} можно менять не чаще раза в час. Попробуйте снова через {retry_after} c.",
            "retry_after": retry_after,
            "field": field,
        })

def _profile_advisory(sender: Sender) -> list[ProfileWarningItem]:
    """D-09 advisory (NEVER blocks): warmup OR account < 7 days old.
    Returns ProfileWarningItem (code/message) — NOT the rate-limit WarningItem (D-14 shape)."""
    warnings: list[ProfileWarningItem] = []
    young = sender.created_at is not None and (datetime.now(timezone.utc) - sender.created_at) < timedelta(days=7)
    if sender.lifecycle_status == "warmup" or young:
        warnings.append(ProfileWarningItem(code="PROFILE_WARMUP_ADVISORY",
            message="Аккаунт ещё прогревается (моложе 7 дней). Резкие изменения профиля повышают риск ограничений."))
    return warnings
```

3. **Add `GET /senders/{slug}/username-check`** (response_model=UsernameCheckResponse):
```python
@router.get("/senders/{slug}/username-check", response_model=UsernameCheckResponse)
async def username_check(slug: str, username: str, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    try:
        res = await telegram_service.check_username(sender.slug, sender.session_string, username, proxy=sender.proxy)
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    if not res.get("success"):
        raise HTTPException(429 if res["error"].get("code") == "FLOOD_WAIT" else 400, detail=res["error"])
    return UsernameCheckResponse(available=res["available"], reason=res.get("reason"))
```

4. **Add `PATCH /senders/{slug}/profile`** (response_model=ProfileUpdateResponse). Order of operations: cooldown check (username) → Telegram writes → stamp + cache refresh → commit → advisory warnings.
```python
@router.patch("/senders/{slug}/profile", response_model=ProfileUpdateResponse)
async def update_profile(slug: str, request: ProfileUpdate, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    changing_username = request.username is not None
    if changing_username:
        _check_profile_cooldown(sender, "username")   # D-08 hard block BEFORE any Telegram call
    try:
        if request.first_name is not None or request.last_name is not None or request.about is not None:
            res = await telegram_service.update_profile(sender.slug, sender.session_string,
                first_name=request.first_name, last_name=request.last_name, about=request.about, proxy=sender.proxy)
            if not res.get("success"):
                raise HTTPException(429 if res["error"].get("code") == "FLOOD_WAIT" else 400, detail=res["error"])
            # refresh cached name/bio
            if request.first_name is not None:
                sender.name = ((request.first_name or "") + (" " + request.last_name if request.last_name else "")).strip() or sender.name
                _stamp_profile_change(sender, "name")
            if request.about is not None:
                sender.tg_bio = request.about
                _stamp_profile_change(sender, "bio")
        if changing_username:
            res = await telegram_service.set_username(sender.slug, sender.session_string, request.username, proxy=sender.proxy)
            if not res.get("success"):
                raise HTTPException(429 if res["error"].get("code") == "FLOOD_WAIT" else 400, detail=res["error"])
            sender.tg_username = request.username or None
            _stamp_profile_change(sender, "username")
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    await db.commit()
    await db.refresh(sender)
    return ProfileUpdateResponse(sender=_sender_to_response(sender), warnings=_profile_advisory(sender))
```

Ensure `ProfileUpdate`, `UsernameCheckResponse`, `ProfileWarningItem`, `ProfileUpdateResponse` are added to the schema import block at the top of senders.py. Do NOT touch the existing `WarningItem` import or the `_validate_rate_limits` soft-cap path (~line 198) — that D-14 flow stays byte-identical.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_update_name_bio tests/test_account_profile.py::test_username tests/test_account_profile.py::test_cooldown_block tests/test_account_profile.py::test_warmup_advisory_not_blocking -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "def _check_profile_cooldown" app/routers/senders.py` matches
    - `grep -n "def _stamp_profile_change" app/routers/senders.py` matches and its body contains `sender.profile_field_changed_at = changed` (new-dict reassignment)
    - `grep -n '@router.patch("/senders/{slug}/profile"' app/routers/senders.py` matches
    - `grep -n '@router.get("/senders/{slug}/username-check"' app/routers/senders.py` matches
    - `_check_profile_cooldown` raises 409 with code `TOO_FREQUENT` for username; name/bio are exempt (only `_HARD_BLOCK_FIELDS`)
    - `_sender_to_response` passes `has_photo=` and `tg_username=` to SenderResponse
    - `grep -nE '(^|[^A-Za-z])WarningItem\(' app/routers/senders.py` returns exactly ONE match, at the pre-existing _validate_rate_limits soft-cap append (~line 198) — no NEW bare WarningItem( constructions added by this task (advisories use ProfileWarningItem; the regex excludes the ProfileWarningItem prefix)
    - test_update_name_bio, test_username, test_cooldown_block, test_warmup_advisory_not_blocking all pass
  </acceptance_criteria>
  <done>PATCH /profile + /username-check live; guardrail hard-blocks username/photo <1h and never blocks name/bio; D-09 advisory surfaced in warnings[]; the four identity/guardrail tests GREEN.</done>
</task>

<task type="auto">
  <name>Task 3: Cache tg_username at onboarding finalize (PROF-08)</name>
  <read_first>
    - app/routers/onboarding.py (_create_sender_from_session ~295-404 — the `me = await client.get_me()` block, the `_update_in_place` nested fn, and the `Sender(...)` constructor)
    - tests/test_onboarding.py (test_finalize_caches_profile RED scaffold from 20-01)
  </read_first>
  <action>
In `app/routers/onboarding.py`, inside `_create_sender_from_session`:

1. After `first_name = getattr(me, "first_name", None) or ""`, add:
```python
    tg_username = getattr(me, "username", None)
```
2. In the `_update_in_place(row)` nested function, after setting `row.auth_status = "ok"`, add:
```python
        if tg_username is not None:
            row.tg_username = tg_username
```
3. In the `Sender(...)` constructor kwargs (the new-row branch), add:
```python
        tg_username=tg_username,
```

This populates the cache at onboarding for BOTH the fresh-create and the plain-flow re-auth upsert paths. Bio/photo are left to the manual/edit resync (PROF-06, Plan 20-03) — PROF-08 requires "username at minimum".
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_onboarding.py::test_finalize_caches_profile -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "tg_username = getattr(me" app/routers/onboarding.py` matches
    - `grep -n "tg_username=tg_username" app/routers/onboarding.py` matches (constructor)
    - `grep -n "row.tg_username = tg_username" app/routers/onboarding.py` matches (upsert path)
    - test_finalize_caches_profile passes
  </acceptance_criteria>
  <done>Onboarding finalize writes tg_username for both create and re-auth-upsert paths; PROF-08 test GREEN.</done>
</task>

</tasks>

<verification>
- `... run --rm api pytest tests/test_account_profile.py tests/test_onboarding.py -x` — identity + guardrail + onboarding tests GREEN.
- No PROTECTED queue constants touched (this plan does not touch queue.py).
- SessionAuthError from a dead session maps to 403 AUTH_ERROR (not 500).
</verification>

<success_criteria>
- PROF-02 (name/bio), PROF-03 (username + 1h block), PROF-08 (onboarding cache) closed.
- D-08 hard block + D-09 advisory enforced server-side.
</success_criteria>

<output>
After completion, create `.planning/phases/20-account-profile-management/20-02-SUMMARY.md`.
</output>
