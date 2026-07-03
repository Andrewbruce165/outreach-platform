---
phase: 20-account-profile-management
plan: 04
type: execute
wave: 4
depends_on: ["20-03"]
files_modified:
  - app/services/telegram.py
  - app/routers/senders.py
autonomous: true
requirements: [PROF-05]
must_haves:
  truths:
    - "User sets a 2FA password on an account with no 2FA (no current-password field), or changes it with the current password (D-04)"
    - "Wrong current 2FA password → 400 PASSWORD_INVALID (never a 500)"
    - "Setting/changing the recovery email is a two-request flow: start returns EMAIL_CONFIRMATION_SENT + code_length, confirm submits the emailed code"
    - "The 2FA password is never stored anywhere — it is a transient request field only (D-03)"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "change_2fa_password / start_recovery_email / confirm_recovery_email (per-op client)"
      contains: "async def change_2fa_password"
    - path: "app/routers/senders.py"
      provides: "POST /2fa + POST /2fa/recovery-email + POST /2fa/recovery-email/confirm"
      contains: "/2fa/recovery-email/confirm"
  key_links:
    - from: "app/routers/senders.py (POST /senders/{slug}/2fa)"
      to: "app/services/telegram.py (change_2fa_password → client.edit_2fa)"
      via: "telegram_service.change_2fa_password(...)"
      pattern: "change_2fa_password"
    - from: "app/services/telegram.py (start_recovery_email)"
      to: "telethon account.UpdatePasswordSettingsRequest raising EmailUnconfirmedError"
      via: "GetPasswordRequest + compute_check + UpdatePasswordSettingsRequest"
      pattern: "EmailUnconfirmedError"
---

<objective>
Ship the Section-B security path. Password set/change goes through `client.edit_2fa` in ONE stateless request (no email callback). The recovery-email change is split into a TWO-request confirm flow using raw functions (`GetPasswordRequest` → `compute_check` → `UpdatePasswordSettingsRequest` → catch `EmailUnconfirmedError` → later `ConfirmPasswordEmailRequest`) — because `edit_2fa(email=...)` needs a synchronous `email_code_callback` that a per-op disconnect-between-requests client cannot provide (RESEARCH §Pitfall 2, CRITICAL). The 2FA password is never persisted (D-03).

Purpose: closes PROF-05. Sequential after 20-03 (shares telegram.py/senders.py).
Output: 3 TelegramService methods + 3 endpoints.

Risk note: the raw two-request recovery-email flow is the phase's MEDIUM-LOW-confidence assumption (RESEARCH §Sources tertiary + 20-VALIDATION §Manual-Only). Automated tests mock Telethon; the live end-to-end confirm against a real Telegram inbox is a manual verification carried by Plan 20-05's human-verify gate.
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
<!-- edit_2fa (high-level, verified 1.42.0): (current_password=None, new_password=None, *, hint='', email=None, email_code_callback=None) -> bool -->
<!-- Password-only path (D-04): -->
```python
await client.edit_2fa(current_password=None, new_password="NewPass1!")        # set (no 2fa yet)
await client.edit_2fa(current_password="OldPass", new_password="NewPass1!")    # change
# PasswordHashInvalidError = wrong current → 400 PASSWORD_INVALID (reuse onboarding code)
```
<!-- Recovery-email two-request path (RESEARCH §Code Example 5): -->
```python
from telethon.tl.functions.account import (GetPasswordRequest, UpdatePasswordSettingsRequest, ConfirmPasswordEmailRequest)
from telethon.tl.types.account import PasswordInputSettings
from telethon.password import compute_check
from telethon.errors import EmailUnconfirmedError
# Step 1:
pwd = await client(GetPasswordRequest())
srp = compute_check(pwd, current_password)
try:
    await client(UpdatePasswordSettingsRequest(password=srp, new_settings=PasswordInputSettings(email=new_email)))
except EmailUnconfirmedError as e:
    return {"status": "EMAIL_CONFIRMATION_SENT", "code_length": e.code_length}
# Step 2 (fresh per-op client):
await client(ConfirmPasswordEmailRequest(code=user_code))
```
<!-- Error map (RESEARCH §Standard Stack error table): PasswordHashInvalidError→400 PASSWORD_INVALID, EmailInvalidError→400 EMAIL_INVALID, PasswordTooFreshError/SessionTooFreshError→409 TOO_FRESH (carry seconds), FloodWaitError→429 -->
<!-- Schemas from 20-01: TwoFAPasswordUpdate{current_password?, new_password, hint?}, RecoveryEmailStart{current_password?, email}, RecoveryEmailConfirm{code} -->
<!-- Reuse the spambot-check try/except SessionAuthError→403 / finally disconnect_client shape (senders.py 655-745). -->
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: TelegramService 2FA methods (password + recovery-email two-step)</name>
  <read_first>
    - app/services/telegram.py (set_profile_photo/update_profile from prior plans for the per-op skeleton)
    - app/routers/onboarding.py (_map_telethon_error ~407 — PasswordHashInvalidError→PASSWORD_INVALID / FloodWaitError→FLOOD_WAIT canonical codes to reuse)
    - .planning/phases/20-account-profile-management/20-RESEARCH.md (§Pitfall 2 CRITICAL, §Code Example 5, §Pitfall 5 TOO_FRESH, §Standard Stack error table)
  </read_first>
  <behavior>
    - change_2fa_password(current=None, new="X") dispatches client.edit_2fa(current_password=None, new_password="X") → {"success": True}
    - PasswordHashInvalidError → {"success": False, "error": {"code": "PASSWORD_INVALID"}}
    - start_recovery_email dispatches GetPasswordRequest + compute_check + UpdatePasswordSettingsRequest; EmailUnconfirmedError → {"success": True, "status": "EMAIL_CONFIRMATION_SENT", "code_length": n}
    - EmailInvalidError → {"success": False, "error": {"code": "EMAIL_INVALID"}}
    - PasswordTooFreshError/SessionTooFreshError → {"success": False, "error": {"code": "TOO_FRESH", "retry_after": <seconds if available>}}
    - confirm_recovery_email(code) dispatches ConfirmPasswordEmailRequest(code=...) → {"success": True}
  </behavior>
  <action>
Add three methods to `TelegramService` in `app/services/telegram.py` (per-op client, SessionAuthError propagates, `finally: disconnect_client`). Never log the passwords.

```python
async def change_2fa_password(self, sender_slug, encrypted_session, *, current_password=None, new_password, hint="", proxy=None) -> dict:
    from telethon.errors import PasswordHashInvalidError, PasswordTooFreshError, SessionTooFreshError, FloodWaitError
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        # No email here → no email_code_callback → completes synchronously (Pitfall 2).
        await client.edit_2fa(current_password=current_password, new_password=new_password, hint=hint or "")
        return {"success": True}
    except PasswordHashInvalidError:
        return {"success": False, "error": {"code": "PASSWORD_INVALID", "message": "Неверный текущий пароль 2FA"}}
    except (PasswordTooFreshError, SessionTooFreshError) as e:
        return {"success": False, "error": {"code": "TOO_FRESH", "retry_after": getattr(e, "seconds", None),
                "message": "Telegram временно блокирует это действие на новом аккаунте."}}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)

async def start_recovery_email(self, sender_slug, encrypted_session, *, current_password=None, email, proxy=None) -> dict:
    from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
    from telethon.tl.types.account import PasswordInputSettings
    from telethon.password import compute_check
    from telethon.errors import (EmailUnconfirmedError, EmailInvalidError, PasswordHashInvalidError,
                                 PasswordTooFreshError, SessionTooFreshError, FloodWaitError)
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        pwd = await client(GetPasswordRequest())
        srp = compute_check(pwd, current_password or "")
        try:
            await client(UpdatePasswordSettingsRequest(password=srp, new_settings=PasswordInputSettings(email=email)))
        except EmailUnconfirmedError as e:
            # Pending-email state now lives account-side on Telegram → step 2 can use a fresh client.
            return {"success": True, "status": "EMAIL_CONFIRMATION_SENT", "code_length": getattr(e, "code_length", None)}
        # No exception = no confirmation needed (rare) — treat as done.
        return {"success": True, "status": "EMAIL_SET"}
    except EmailInvalidError:
        return {"success": False, "error": {"code": "EMAIL_INVALID", "message": "Некорректный email"}}
    except PasswordHashInvalidError:
        return {"success": False, "error": {"code": "PASSWORD_INVALID", "message": "Неверный текущий пароль 2FA"}}
    except (PasswordTooFreshError, SessionTooFreshError) as e:
        return {"success": False, "error": {"code": "TOO_FRESH", "retry_after": getattr(e, "seconds", None),
                "message": "Telegram временно блокирует смену email на новом аккаунте."}}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)

async def confirm_recovery_email(self, sender_slug, encrypted_session, *, code, proxy=None) -> dict:
    from telethon.tl.functions.account import ConfirmPasswordEmailRequest
    from telethon.errors import FloodWaitError
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        try:
            await client(ConfirmPasswordEmailRequest(code=str(code)))
        except Exception as e:
            return {"success": False, "error": {"code": "EMAIL_CODE_INVALID", "message": "Неверный или просроченный код"}}
        return {"success": True}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    finally:
        if client:
            await self.disconnect_client(client)
```
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_2fa -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "async def change_2fa_password" app/services/telegram.py` matches
    - `grep -n "async def start_recovery_email" app/services/telegram.py` matches
    - `grep -n "async def confirm_recovery_email" app/services/telegram.py` matches
    - `grep -n "EmailUnconfirmedError" app/services/telegram.py` matches (two-request pivot present)
    - `grep -n "client.edit_2fa" app/services/telegram.py` matches AND no `email=` kwarg is passed to it (password-only path — Pitfall 2)
    - each method has `await self.disconnect_client(client)` in a `finally:` block
    - test_2fa passes
  </acceptance_criteria>
  <done>Password path uses edit_2fa (no email kwarg); recovery-email uses the raw two-request flow pivoting on EmailUnconfirmedError; error taxonomy mapped; test_2fa GREEN.</done>
</task>

<task type="auto">
  <name>Task 2: 2FA endpoints (password + recovery-email start/confirm)</name>
  <read_first>
    - app/routers/senders.py (spambot-check try/except SessionAuthError→403/finally shape ~655-745; PATCH /profile from 20-02 as the endpoint template; import block)
    - app/schemas/__init__.py (TwoFAPasswordUpdate, RecoveryEmailStart, RecoveryEmailConfirm — from 20-01)
    - .planning/phases/20-account-profile-management/20-UI-SPEC.md (§Interaction Contracts C4 two-step + §Copywriting 2FA copy)
  </read_first>
  <action>
In `app/routers/senders.py`, add three endpoints (import `TwoFAPasswordUpdate`, `RecoveryEmailStart`, `RecoveryEmailConfirm` from schemas). Each maps `success=False` errors: `PASSWORD_INVALID`/`EMAIL_INVALID`/`EMAIL_CODE_INVALID` → 400, `TOO_FRESH` → 409, `FLOOD_WAIT` → 429, else 400. No 2FA field is written to the DB (D-03).

```python
@router.post("/senders/{slug}/2fa")
async def update_2fa_password(slug: str, request: TwoFAPasswordUpdate, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    try:
        res = await telegram_service.change_2fa_password(sender.slug, sender.session_string,
            current_password=request.current_password, new_password=request.new_password,
            hint=request.hint or "", proxy=sender.proxy)
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    if not res.get("success"):
        raise HTTPException(_status_for_profile_error(res["error"].get("code")), detail=res["error"])
    return {"success": True}

@router.post("/senders/{slug}/2fa/recovery-email")
async def start_2fa_recovery_email(slug: str, request: RecoveryEmailStart, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    try:
        res = await telegram_service.start_recovery_email(sender.slug, sender.session_string,
            current_password=request.current_password, email=str(request.email), proxy=sender.proxy)
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    if not res.get("success"):
        raise HTTPException(_status_for_profile_error(res["error"].get("code")), detail=res["error"])
    return {"status": res.get("status", "EMAIL_CONFIRMATION_SENT"), "code_length": res.get("code_length")}

@router.post("/senders/{slug}/2fa/recovery-email/confirm")
async def confirm_2fa_recovery_email(slug: str, request: RecoveryEmailConfirm, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    from app.services.telegram import telegram_service, SessionAuthError
    sender = await _load_sender_by_slug(db, ctx, slug)
    try:
        res = await telegram_service.confirm_recovery_email(sender.slug, sender.session_string, code=request.code, proxy=sender.proxy)
    except SessionAuthError as e:
        raise HTTPException(403, detail={"code": "AUTH_ERROR", "message": f"Session auth failed: {e.auth_status}", "auth_status": e.auth_status})
    if not res.get("success"):
        raise HTTPException(_status_for_profile_error(res["error"].get("code")), detail=res["error"])
    return {"success": True}
```

Add the small shared status-mapper helper near the guardrail helpers (reused by the 2FA endpoints):
```python
def _status_for_profile_error(code: str | None) -> int:
    return {"TOO_FRESH": 409, "FLOOD_WAIT": 429}.get(code or "", 400)
```
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_2fa -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n '@router.post("/senders/{slug}/2fa")' app/routers/senders.py` matches
    - `grep -n '@router.post("/senders/{slug}/2fa/recovery-email")' app/routers/senders.py` matches
    - `grep -n '@router.post("/senders/{slug}/2fa/recovery-email/confirm")' app/routers/senders.py` matches
    - `grep -n "def _status_for_profile_error" app/routers/senders.py` matches (TOO_FRESH→409, FLOOD_WAIT→429, else 400)
    - No DB column is written from any 2FA endpoint (grep the three endpoint bodies for `sender.` assignments — there must be none; password is transient, D-03)
    - test_2fa passes
  </acceptance_criteria>
  <done>Three 2FA endpoints live; password never persisted; recovery-email two-step returns EMAIL_CONFIRMATION_SENT + code_length then confirms; error taxonomy → correct HTTP codes. PROF-05 automated coverage GREEN.</done>
</task>

</tasks>

<verification>
- `... run --rm api pytest tests/test_account_profile.py -x` — full account-profile suite GREEN.
- `... run --rm api pytest` full suite GREEN before phase gate (baseline ~896+).
- Manual (deferred to 20-05 human-verify): live recovery-email confirm against a real test account with 2FA set (20-VALIDATION §Manual-Only).
</verification>

<success_criteria>
- PROF-05 closed: password set/change + recovery-email two-step; wrong password → 400; TOO_FRESH → 409; password never stored.
</success_criteria>

<output>
After completion, create `.planning/phases/20-account-profile-management/20-04-SUMMARY.md`.
</output>
