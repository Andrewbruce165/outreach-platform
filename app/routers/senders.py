"""
Senders router (Phase 2 — SNDR-01, SNDR-02, SNDR-03).

Все endpoint'ы workspace-scoped через `Depends(auth_dep)` (Phase 1 D-12 AuthCtx).

Endpoints:
  GET    /api/v1/senders                       — list workspace senders + derived status
  GET    /api/v1/senders/{slug}                — single sender by slug
  POST   /api/v1/senders                       — create (returns SenderCreateResponse с warnings[])
  PATCH  /api/v1/senders/{slug}                — update + warnings[] (D-14 soft cap)
  DELETE /api/v1/senders/{slug}                — cascade delete (контакты/диалоги/логи)
  POST   /api/v1/senders/{slug}/assign-proxy   — назначить прокси из workspace pool (D-22)
  GET    /api/v1/senders/{slug}/spambot-check  — SpamBot status через @SpamBot
  GET    /api/v1/workspace/proxies             — list workspace proxies (D-22)
  POST   /api/v1/workspace/proxies             — добавить прокси в pool
  DELETE /api/v1/workspace/proxies/{proxy_id}  — удалить из pool

Phase 2 ключевые изменения относительно legacy:
- Удалён docker-restart workaround (D-18). Reconcile делает listener сам
  (план 02-01 / Phase 2 W1).
- Удалена зависимость от старой legacy-auth: теперь Depends(auth_dep).
- is_active колонка дропнута в миграции 013 — derived status (D-11).
- Hard cap на rate_per_*: 10/50/300 (D-14) → 422 RATE_LIMIT_EXCEEDS_HARD_CAP.
- Soft cap (зелёный коридор): 4/20/150 → 200 + warnings[] (D-14).
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Sender, ProxyPool, SenderRestrictionEvent
from app.schemas import (
    AssignProxyRequest,
    GradeOverrideRequest,
    ProfileUpdate,
    ProfileUpdateResponse,
    ProfileWarningItem,
    ProxyConfig,
    ProxyPoolCreate,
    ProxyPoolItem,
    ProxyPoolListResponse,
    RateLimits,
    RecoveryEmailConfirm,
    RecoveryEmailStart,
    RestrictionEventResponse,
    SenderBlockRateResponse,
    SenderCreate,
    SenderCreateResponse,
    SenderListResponse,
    SenderResponse,
    SenderUpdate,
    TwoFAPasswordUpdate,
    UsernameCheckResponse,
    WarningItem,
)
from app.services.encryption import encrypt_session
from app.services.grade_ladder import budget_for_level, load_ladder
from app.utils.auth import AuthCtx, auth_dep
from app.utils.location import phone_location

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["senders"])

# D-14: hard cap = "exceeds maximum safe limit" → 422.
# Phase 22 D-04: the daily field dropped — new-chat budget is grade-driven.
RATE_HARD_CAP = {"rate_per_min": 10, "rate_per_hour": 50}
# D-14: soft cap = "зелёный коридор" → 200 + warnings[].
RATE_SOFT_CAP = {"rate_per_min": 4, "rate_per_hour": 20}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _derive_status(sender: Sender) -> str:
    """D-11 + migration 028: derived status — error > frozen > limited > lifecycle.

    Precedence:
      auth_status != 'ok' (session_expired / banned / etc) → 'error'
      restriction_status == 'frozen'                       → 'frozen'
      restriction_status == 'spam_limited'                 → 'limited'
      else                                                 → lifecycle_status

    Restriction is orthogonal to auth (a restricted account still authenticates),
    so it is checked only after auth is confirmed healthy.
    """
    if sender.auth_status != "ok":
        return "error"
    if sender.restriction_status == "frozen":
        return "frozen"
    if sender.restriction_status == "spam_limited":
        return "limited"
    return sender.lifecycle_status


def _derive_checker_status(sender: Sender) -> Optional[str]:
    """Checker-specific UI status (role='checker' only; None for senders).

    Distinct from _derive_status (sender-oriented). Splits the generic
    'error'/'limited' into action-vs-auto buckets so the UI can show
    'Re-auth needed'/'Banned' (red, needs the user) separately from
    'Cooling down' (amber, auto-recovering contacts-API throttle — no action).

    Precedence: banned > reauth_needed > frozen > cooling_down > paused > active.
    """
    if sender.role != "checker":
        return None
    if sender.auth_status == "banned":
        return "banned"
    if sender.auth_status != "ok":
        return "reauth_needed"
    if sender.restriction_status == "frozen":
        return "frozen"
    if sender.restriction_status == "spam_limited":
        return "cooling_down"
    if sender.lifecycle_status == "paused":
        return "paused"
    return "active"


def _sender_to_response(
    sender: Sender,
    sent_today: int = 0,
    locked_by_campaign_id: Optional[UUID] = None,
    locked_by_campaign_name: Optional[str] = None,
    remaining_daily_budget: Optional[int] = None,
) -> SenderResponse:
    """Build SenderResponse с derived status + nested RateLimits.

    Phase 3 C-05: ai_context_id / ai_context_name fields removed — sender
    больше не «знает» агента, связь через Campaign в Phase 4.

    Phase 22 D-04/D-12: RateLimits no longer carries per_day (the daily new-chat
    budget is grade-driven); `current_level`/`level_updated_at` surface the
    account grade and `remaining_daily_budget` its trailing-24h headroom.

    `sent_today` — trailing-24h sent count (TODAY column numerator). Computed
    only on the list endpoint; other paths use the default 0.

    `remaining_daily_budget` (D-12) — grade budget minus distinct new dialogs
    opened in the trailing 24h, clamped at 0. Computed only on the list
    endpoint; single-sender paths report None (same convention as sent_today=0).

    `locked_by_campaign_id` / `locked_by_campaign_name` (POOL-09, 08-04 UAT) —
    the first running campaign in the workspace holding this sender, so the pool
    add-picker can disable locked entries instead of returning a confusing 409.
    Populated only on the list endpoint; single-sender paths report no lock
    (same convention as sent_today=0).
    """
    return SenderResponse(
        id=sender.id,
        slug=sender.slug,
        name=sender.name,
        phone=sender.phone,
        location=phone_location(sender.phone),
        status=_derive_status(sender),
        checker_status=_derive_checker_status(sender),
        checker_trip_count=getattr(sender, "checker_trip_count", 0) or 0,
        auth_status=sender.auth_status,
        lifecycle_status=sender.lifecycle_status,
        restriction_status=sender.restriction_status,
        restricted_until=sender.restricted_until,
        rate_limits=RateLimits(
            per_minute=sender.rate_per_min,
            per_hour=sender.rate_per_hour,
        ),
        # Phase 22 D-12: account grade + trailing-24h remaining new-chat budget.
        current_level=getattr(sender, "current_level", 1) or 1,
        level_updated_at=getattr(sender, "level_updated_at", None),
        remaining_daily_budget=remaining_daily_budget,
        role=sender.role,
        proxy=ProxyConfig(**sender.proxy) if sender.proxy else None,
        last_used_at=sender.last_used_at,
        created_at=sender.created_at,
        sent_today=sent_today,
        locked_by_campaign_id=locked_by_campaign_id,
        locked_by_campaign_name=locked_by_campaign_name,
        # Phase 20 (PROF-01/02/03/07 + D-08): cached Telegram profile fields.
        tg_username=getattr(sender, "tg_username", None),
        tg_bio=getattr(sender, "tg_bio", None),
        tg_premium=bool(getattr(sender, "tg_premium", False)),
        has_photo=bool(getattr(sender, "tg_photo", None)),
        profile_field_changed_at=getattr(sender, "profile_field_changed_at", {}) or {},
    )


def _validate_rate_limits(
    rate_per_min: Optional[int],
    rate_per_hour: Optional[int],
) -> List[WarningItem]:
    """D-14: hard cap → 422; soft cap → warnings[].

    Pydantic уже отрезает hard cap через Field(le=...), но Lovable иногда шлёт
    через сырой JSON — двойная проверка тут не вредна и даёт более ясное сообщение.

    Phase 22 D-04: the daily field dropped — the new-chat budget is now
    grade-driven (grade_ladder.py), no longer a per-sender validated field.
    """
    warnings: List[WarningItem] = []
    values = {
        "rate_per_min": rate_per_min,
        "rate_per_hour": rate_per_hour,
    }
    for field, val in values.items():
        if val is None:
            continue
        if val > RATE_HARD_CAP[field]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "RATE_LIMIT_EXCEEDS_HARD_CAP",
                    "field": field,
                    "value": val,
                    "hard_cap": RATE_HARD_CAP[field],
                    "message": (
                        "exceeds maximum safe limit, contact support "
                        "if you need higher"
                    ),
                },
            )
        if val > RATE_SOFT_CAP[field]:
            warnings.append(
                WarningItem(
                    field=field,
                    value=val,
                    recommended_max=RATE_SOFT_CAP[field],
                )
            )
    return warnings


# ─── Account profile guardrail (Phase 20 — D-06/D-07/D-08/D-09) ───────────────

# Telegram username rule: 5–32 chars, must start with a letter, only [A-Za-z0-9_].
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
# Free-account bio cap; AboutTooLongError is the premium (140) backstop.
_BIO_MAX_LEN = 70
# D-08: ONLY these fields hard-block when changed <1h ago. name/bio are warning-only (D-07).
_HARD_BLOCK_FIELDS = {"username", "photo"}
_HARD_BLOCK_WINDOW = timedelta(hours=1)

# PROF-04 photo upload guards — validated BEFORE any Telegram call (413/422).
MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_MIME = {"image/jpeg", "image/png"}


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise a possibly-naive datetime to UTC-aware for safe arithmetic."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _stamp_profile_change(sender: Sender, field: str) -> None:
    """Record the last-change time for a field. Reassign a NEW dict — SQLAlchemy does
    not track in-place JSONB mutation (no MutableDict), so mutating in place would not
    persist on commit."""
    changed = dict(sender.profile_field_changed_at or {})
    changed[field] = datetime.now(timezone.utc).isoformat()
    sender.profile_field_changed_at = changed


def _check_profile_cooldown(sender: Sender, field: str) -> None:
    """D-08 HARD block: username/photo changed <1h ago → 409 TOO_FREQUENT.

    name/bio are warning-only (D-07) → not in _HARD_BLOCK_FIELDS → no-op here.
    """
    if field not in _HARD_BLOCK_FIELDS:
        return
    ts = (sender.profile_field_changed_at or {}).get(field)
    if not ts:
        return
    try:
        last = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return
    last = _as_aware(last)
    elapsed = datetime.now(timezone.utc) - last
    if elapsed < _HARD_BLOCK_WINDOW:
        retry_after = int((_HARD_BLOCK_WINDOW - elapsed).total_seconds())
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TOO_FREQUENT",
                "message": (
                    f"{field} можно менять не чаще раза в час. "
                    f"Попробуйте снова через {retry_after} c."
                ),
                "retry_after": retry_after,
                "retry_after_seconds": retry_after,
                "field": field,
            },
        )


def _profile_advisory(sender: Sender) -> List[ProfileWarningItem]:
    """D-09 advisory (NEVER blocks): warmup OR account younger than 7 days.

    Returns ProfileWarningItem (code/message) — NOT the rate-limit WarningItem (D-14).
    """
    warnings: List[ProfileWarningItem] = []
    created = _as_aware(getattr(sender, "created_at", None))
    young = created is not None and (datetime.now(timezone.utc) - created) < timedelta(days=7)
    if sender.lifecycle_status == "warmup" or young:
        warnings.append(
            ProfileWarningItem(
                code="PROFILE_WARMUP_ADVISORY",
                message=(
                    "Аккаунт ещё прогревается (моложе 7 дней). Резкие изменения "
                    "профиля повышают риск ограничений."
                ),
            )
        )
    return warnings


def _status_for_profile_error(code: str | None) -> int:
    """HTTP status for a structured profile / 2FA error code (D-05 taxonomy).

    TOO_FRESH → 409 (Telegram temporarily blocks the action on a fresh account /
    session), FLOOD_WAIT → 429, everything else → 400.
    """
    return {"TOO_FRESH": 409, "FLOOD_WAIT": 429}.get(code or "", 400)


def _raise_profile_telegram_error(e: Exception) -> None:
    """Map a Telegram/Telethon profile / 2FA error to a structured HTTPException.

    Matches on BOTH the exception class name and its message text, so the live
    Telethon errors (UsernameOccupiedError, AboutTooLongError, PasswordHashInvalidError,
    FloodWaitError, ...) and a test that raises a bare Exception("PASSWORD_HASH_INVALID")
    both resolve to the same code. The HTTP status is derived from the code via
    _status_for_profile_error. Unknown errors → 500 PROFILE_UPDATE_FAILED.
    """
    blob = f"{type(e).__name__} {e}".upper()
    table = (
        ("USERNAME_OCCUPIED", "USERNAME_TAKEN", "Этот username уже занят"),
        ("USERNAMEOCCUPIED", "USERNAME_TAKEN", "Этот username уже занят"),
        ("USERNAME_INVALID", "USERNAME_INVALID",
         "Недопустимый формат username (a-z, 0-9, _, 5–32 символа)"),
        ("USERNAMEINVALID", "USERNAME_INVALID",
         "Недопустимый формат username (a-z, 0-9, _, 5–32 символа)"),
        ("USERNAME_PURCHASE", "USERNAME_PURCHASE_REQUIRED",
         "Этот username платный (Fragment)"),
        ("USERNAMEPURCHASE", "USERNAME_PURCHASE_REQUIRED",
         "Этот username платный (Fragment)"),
        ("ABOUT_TOO_LONG", "BIO_TOO_LONG",
         f"Описание слишком длинное (максимум {_BIO_MAX_LEN} символов)"),
        ("ABOUTTOOLONG", "BIO_TOO_LONG",
         f"Описание слишком длинное (максимум {_BIO_MAX_LEN} символов)"),
        ("FIRSTNAME_INVALID", "NAME_INVALID", "Недопустимое имя"),
        ("FIRSTNAMEINVALID", "NAME_INVALID", "Недопустимое имя"),
        ("PHOTO_CROP_SIZE_SMALL", "PHOTO_TOO_SMALL", "Фото слишком маленькое"),
        ("PHOTOCROPSIZESMALL", "PHOTO_TOO_SMALL", "Фото слишком маленькое"),
        ("PHOTO_EXT_INVALID", "PHOTO_FORMAT_INVALID",
         "Неподдерживаемый формат. Загрузите JPG или PNG"),
        ("PHOTOEXTINVALID", "PHOTO_FORMAT_INVALID",
         "Неподдерживаемый формат. Загрузите JPG или PNG"),
        # ─── 2FA + recovery email (Phase 20 — PROF-05, D-03/D-04) ───
        ("PASSWORD_HASH_INVALID", "PASSWORD_INVALID", "Неверный текущий пароль 2FA"),
        ("PASSWORDHASHINVALID", "PASSWORD_INVALID", "Неверный текущий пароль 2FA"),
        ("EMAIL_UNCONFIRMED", "EMAIL_CODE_INVALID", "Неверный или просроченный код"),
        ("EMAILUNCONFIRMED", "EMAIL_CODE_INVALID", "Неверный или просроченный код"),
        ("EMAIL_INVALID", "EMAIL_INVALID", "Некорректный email"),
        ("EMAILINVALID", "EMAIL_INVALID", "Некорректный email"),
        ("CODE_INVALID", "EMAIL_CODE_INVALID", "Неверный или просроченный код"),
        ("CODEINVALID", "EMAIL_CODE_INVALID", "Неверный или просроченный код"),
        ("PASSWORD_TOO_FRESH", "TOO_FRESH",
         "Telegram временно блокирует это действие на новом аккаунте."),
        ("PASSWORDTOOFRESH", "TOO_FRESH",
         "Telegram временно блокирует это действие на новом аккаунте."),
        ("SESSION_TOO_FRESH", "TOO_FRESH",
         "Telegram временно блокирует это действие на новой сессии."),
        ("SESSIONTOOFRESH", "TOO_FRESH",
         "Telegram временно блокирует это действие на новой сессии."),
        ("FLOOD_WAIT", "FLOOD_WAIT", "Слишком часто. Попробуйте позже."),
        ("FLOODWAIT", "FLOOD_WAIT", "Слишком часто. Попробуйте позже."),
    )
    for needle, code, message in table:
        if needle in blob:
            detail = {"code": code, "message": message}
            if code in ("FLOOD_WAIT", "TOO_FRESH"):
                secs = getattr(e, "seconds", None)
                if secs is not None:
                    detail["retry_after"] = secs
            raise HTTPException(status_code=_status_for_profile_error(code), detail=detail)
    logger.error(f"[senders] profile telegram error: {type(e).__name__}: {e}")
    raise HTTPException(
        status_code=500,
        detail={"code": "PROFILE_UPDATE_FAILED", "message": str(e)},
    )


async def _check_sender_not_in_running_campaign(
    db: AsyncSession, ctx: AuthCtx, sender_id: UUID
) -> None:
    """Phase 4 close (new check, не TODO marker): sender нельзя удалить или
    pause/warmup-flip пока он прицеплен к running campaign в workspace.

    409 with detail{campaigns:[...]} — UX-friendly hint.
    """
    active = (await db.execute(text("""
        SELECT c.id, c.name
        FROM campaign_senders cs
        JOIN campaigns c ON c.id = cs.campaign_id
        WHERE cs.sender_id = :sid
          AND c.workspace_id = :wid
          AND c.status = 'running'
        ORDER BY c.name
    """), {"sid": str(sender_id), "wid": str(ctx.workspace_id)})).fetchall()
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SENDER_USED_BY_RUNNING_CAMPAIGN",
                "message": (
                    "Sender is attached to running campaign(s) — "
                    "pause / finish them first"
                ),
                "campaigns": [{"id": str(r[0]), "name": r[1]} for r in active],
            },
        )


async def _load_sender_by_slug(
    db: AsyncSession, ctx: AuthCtx, slug: str
) -> Sender:
    """Workspace-scoped SELECT по slug.

    Phase 3 C-05: больше не selectinload(Sender.ai_context) — relationship
    дропнут вместе с senders.ai_context_id колонкой (D-04).

    404 без раскрытия "not yours" vs "not found" (security: same response
    как и в Phase 1 workspace.py).
    """
    result = await db.execute(
        select(Sender)
        .where(
            Sender.slug == slug,
            Sender.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    sender = result.scalar_one_or_none()
    if sender is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SENDER_NOT_FOUND", "message": f"Sender '{slug}' not found"},
        )
    return sender


# ─── Senders CRUD ────────────────────────────────────────────────────────────


@router.get("/senders", response_model=SenderListResponse)
async def list_senders(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List all senders in current workspace."""
    # Phase 3 C-05: selectinload(Sender.ai_context) удалён — relationship дропнут.
    result = await db.execute(
        select(Sender)
        .where(Sender.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy
        .order_by(Sender.name)
    )
    senders = result.scalars().all()

    # TODAY column numerator: messages sent per sender in the trailing 24h.
    # Single GROUP BY over message_queue (no N+1), scoped to the senders just
    # loaded. Window + status match the rate-limiter daily cap (queue.py:450-466)
    # so {sent_today} never desyncs from the grade budget (no "151/150").
    sent_today_map: dict = {}
    sender_ids = [s.id for s in senders]
    if sender_ids:
        rows = (await db.execute(
            text("""
                SELECT sender_id, COUNT(*) AS sent_today
                  FROM message_queue
                 WHERE sender_id = ANY(:sender_ids)
                   AND status = 'sent'
                   AND finished_at >= now() - interval '24 hours'
                 GROUP BY sender_id
            """),
            {"sender_ids": sender_ids},
        )).fetchall()
        sent_today_map = {row[0]: row[1] for row in rows}

    # POOL-09 (08-04 UAT fix): per-sender lock state for the pool add-picker.
    # One grouped query (no N+1, like sent_today_map above), workspace-scoped,
    # with semantics IDENTICAL to _check_sender_not_in_running_campaign (sender
    # attached to a campaign with status='running' in this workspace). DISTINCT ON
    # picks the first running campaign per sender deterministically (ORDER BY
    # c.name, mirroring the existing helper) so the disabled-pill tooltip is
    # stable. None here = the sender is free to attach.
    lock_map: dict = {}
    if sender_ids:
        lock_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (cs.sender_id) cs.sender_id, c.id, c.name
                  FROM campaign_senders cs
                  JOIN campaigns c ON c.id = cs.campaign_id
                 WHERE cs.sender_id = ANY(:sender_ids)
                   AND c.workspace_id = :wid
                   AND c.status = 'running'
                 ORDER BY cs.sender_id, c.name
            """),
            {"sender_ids": sender_ids, "wid": str(ctx.workspace_id)},
        )).fetchall()
        lock_map = {row[0]: (row[1], row[2]) for row in lock_rows}

    # Phase 22 D-12: remaining daily new-chat budget per sender = the grade
    # budget for its current level minus distinct new dialogs opened in the
    # trailing 24h, clamped at 0. The ladder is workspace-level (single load
    # via grade_ladder.load_ladder, code-defaults if unconfigured — D-16); the
    # new-dialog count is per-sender (one grouped query, no N+1). Only computed
    # on the list endpoint; single-sender paths report None.
    ladder = await load_ladder(db, ctx.workspace_id)
    new_dialogs_map: dict = {}
    if sender_ids:
        dialog_rows = (await db.execute(
            text("""
                SELECT sender_id, COUNT(DISTINCT recipient_phone) AS new_dialogs
                  FROM message_queue
                 WHERE sender_id = ANY(:sender_ids)
                   AND status = 'sent'
                   AND finished_at >= now() - interval '24 hours'
                 GROUP BY sender_id
            """),
            {"sender_ids": sender_ids},
        )).fetchall()
        new_dialogs_map = {row[0]: row[1] for row in dialog_rows}

    def _remaining_budget(s: Sender) -> int:
        level = getattr(s, "current_level", 1) or 1
        opened = new_dialogs_map.get(s.id, 0)
        return max(0, budget_for_level(ladder, level) - opened)

    return SenderListResponse(
        senders=[
            _sender_to_response(
                s,
                sent_today=sent_today_map.get(s.id, 0),
                locked_by_campaign_id=lock_map.get(s.id, (None, None))[0],
                locked_by_campaign_name=lock_map.get(s.id, (None, None))[1],
                remaining_daily_budget=_remaining_budget(s),
            )
            for s in senders
        ]
    )


@router.post("/senders", response_model=SenderCreateResponse, status_code=201)
async def create_sender(
    request: SenderCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Add new sender.

    Slug — globally unique (Phase 1 inheritance). Возвращает sender + warnings[]
    если rate_per_* выше soft cap.
    """
    # Globally unique slug check.
    existing = await db.execute(select(Sender).where(Sender.slug == request.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SLUG_EXISTS",
                "message": f"Sender with slug '{request.slug}' already exists",
            },
        )

    warnings = _validate_rate_limits(
        request.rate_per_min, request.rate_per_hour
    )

    encrypted_session = encrypt_session(request.session_string)

    sender = Sender(
        workspace_id=ctx.workspace_id,
        slug=request.slug,
        name=request.name,
        phone=request.phone,
        session_string=encrypted_session,
        role=request.role or "sender",
        proxy=request.proxy.model_dump() if request.proxy else None,
        # Phase 3 C-05: ai_context_id removed from constructor — column dropped.
    )
    if request.rate_per_min is not None:
        sender.rate_per_min = request.rate_per_min
    if request.rate_per_hour is not None:
        sender.rate_per_hour = request.rate_per_hour
    # Phase 22 D-04: the daily field is no longer set via API (grade-driven budget).

    db.add(sender)
    await db.commit()
    await db.refresh(sender)

    # Phase 3 C-05: no longer reload with ai_context — relationship dropped.

    # Auto-assign free proxy from workspace pool (D-22). Skip if user passed proxy explicitly.
    if not sender.proxy:
        pool_result = await db.execute(
            select(ProxyPool)
            .where(
                ProxyPool.workspace_id == ctx.workspace_id,
                ProxyPool.assigned_to_sender_id.is_(None),
            )
            .limit(1)
        )
        pool_entry = pool_result.scalar_one_or_none()
        if pool_entry is not None:
            sender.proxy = {
                "type": "socks5",
                "host": pool_entry.host,
                "port": pool_entry.port,
                "username": pool_entry.username,
                "password": pool_entry.password,
            }
            pool_entry.assigned_to_sender_id = sender.id
            await db.commit()
            await db.refresh(sender)
            logger.info(
                f"[proxy-pool] auto-assigned port {pool_entry.port} → sender {sender.slug}"
            )
        else:
            logger.warning(
                f"[proxy-pool] no free proxy in workspace {ctx.workspace_id} "
                f"for sender {sender.slug}"
            )

    logger.info(
        f"[senders] created workspace={ctx.workspace_id} slug={sender.slug} "
        f"role={sender.role} warnings={len(warnings)}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=warnings)


@router.get("/senders/{slug}", response_model=SenderResponse)
async def get_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Get sender by slug (workspace-scoped). Returns derived status."""
    sender = await _load_sender_by_slug(db, ctx, slug)
    return _sender_to_response(sender)


@router.patch("/senders/{slug}", response_model=SenderCreateResponse)
async def update_sender(
    slug: str,
    request: SenderUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Update sender. Returns warnings[] (D-14 soft cap)."""
    sender = await _load_sender_by_slug(db, ctx, slug)

    warnings = _validate_rate_limits(
        request.rate_per_min, request.rate_per_hour
    )

    if request.name is not None:
        sender.name = request.name
    if request.phone is not None:
        sender.phone = request.phone
    if request.session_string is not None:
        sender.session_string = encrypt_session(request.session_string)
    if request.lifecycle_status is not None:
        # Phase 4 close: cannot pause/warmup-flip a sender that is currently in running campaign.
        if request.lifecycle_status != "active" and sender.lifecycle_status == "active":
            await _check_sender_not_in_running_campaign(db, ctx, sender.id)
        sender.lifecycle_status = request.lifecycle_status
    if request.rate_per_min is not None:
        sender.rate_per_min = request.rate_per_min
    if request.rate_per_hour is not None:
        sender.rate_per_hour = request.rate_per_hour
    # Phase 22 D-04: the daily field is no longer set via API (grade-driven budget).
    # Phase 3 C-05: ai_context_id setter removed — column dropped.
    # PFH-03: symmetric checker guard. Flipping an in-running-campaign sender to
    # role='checker' would pull it out of sending, so require an explicit override.
    # Distinct code + force bypass from _check_sender_not_in_running_campaign
    # (which raises SENDER_USED_BY_RUNNING_CAMPAIGN and has no escape hatch); no
    # guard at all when the sender is idle.
    if request.role == "checker" and sender.role != "checker" and not request.force:
        in_running = (await db.execute(text("""
            SELECT EXISTS (
              SELECT 1 FROM campaign_senders cs
              JOIN campaigns c ON c.id = cs.campaign_id
              WHERE cs.sender_id = :sid
                AND c.workspace_id = :wid
                AND c.status = 'running'
            )
        """), {"sid": str(sender.id), "wid": str(ctx.workspace_id)})).scalar()
        if in_running:
            raise HTTPException(status_code=409, detail={
                "code": "CHECKER_ROLE_CONFLICT",
                "message": (
                    "Sender is attached to a running campaign — flipping it to the "
                    "checker pool would pull it out of sending. Pause/finish the "
                    "campaign or pass force=true to override."
                ),
                "sender_id": str(sender.id),
            })
    if request.role is not None:
        sender.role = request.role
    if request.proxy is not None:
        sender.proxy = request.proxy.model_dump()

    await db.commit()
    await db.refresh(sender)

    # Phase 3 C-05: no longer reload with ai_context — relationship dropped.

    logger.info(
        f"[senders] updated workspace={ctx.workspace_id} slug={sender.slug} "
        f"lifecycle={sender.lifecycle_status} warnings={len(warnings)}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=warnings)


@router.patch("/senders/{slug}/grade", response_model=SenderResponse)
async def override_sender_grade(
    slug: str,
    request: GradeOverrideRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Manually override a sender's account grade (Phase 22 D-15).

    Writes the SAME two fields as auto-progression — current_level and
    level_updated_at = NOW() — in one operation, so the progression timer
    restarts from the override baseline (no separate frozen flag, D-15).

    Workspace-scoped: `_load_sender_by_slug` resolves the sender only within
    ctx.workspace_id and 404s otherwise, so a tenant cannot re-grade a sender it
    does not own (T-22-10). `current_level` is bounded 1..3 by GradeOverrideRequest
    (T-22-11) and mirrored by the mig 056 CHECK; the UPDATE binds params only
    (T-22-12).
    """
    sender = await _load_sender_by_slug(db, ctx, slug)

    await db.execute(
        text(
            "UPDATE senders SET current_level = :lvl, level_updated_at = NOW() "
            "WHERE id = :sid"
        ),
        {"lvl": request.current_level, "sid": str(sender.id)},
    )
    await db.commit()
    await db.refresh(sender)

    logger.info(
        f"[senders] grade override workspace={ctx.workspace_id} slug={sender.slug} "
        f"level={request.current_level}"
    )
    return _sender_to_response(sender)


@router.delete("/senders/{slug}", status_code=204)
async def delete_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Hard delete sender + связанные диалоги/сообщения/контакты.

    Phase 4 close: 409 if sender is attached to any running campaign in workspace.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)

    # Phase 4 close: block delete if sender in running campaign.
    await _check_sender_not_in_running_campaign(db, ctx, sender.id)

    sender_id = str(sender.id)

    # CASCADE через FK не покрывает 'messages' (нет sender_id напрямую — через conversations).
    await db.execute(
        text(
            "DELETE FROM messages WHERE conversation_id IN "
            "(SELECT id FROM conversations WHERE sender_id = :sid)"
        ),
        {"sid": sender_id},
    )
    await db.execute(
        text("DELETE FROM conversations WHERE sender_id = :sid"),
        {"sid": sender_id},
    )
    await db.execute(
        text("DELETE FROM contacts_cache WHERE sender_id = :sid"),
        {"sid": sender_id},
    )
    await db.execute(
        text("DELETE FROM messages_log WHERE sender_id = :sid"),
        {"sid": sender_id},
    )

    await db.delete(sender)
    await db.commit()
    logger.info(f"[senders] deleted workspace={ctx.workspace_id} slug={slug}")


# ─── Lifecycle endpoints (Phase 05.1 — UI-ACCT-01) ───────────────────────────


@router.post("/senders/{slug}/pause", response_model=SenderCreateResponse)
async def pause_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """UI-SPEC §5.10 row action Pause — flip lifecycle_status active → paused.

    Phase 05.1 (UI-ACCT-01): explicit endpoint added alongside existing PATCH path
    so the UI doesn't need to send a typed body for a one-button action. The Phase 4
    sender-lock guard still applies — cannot pause a sender mid-campaign without
    explicit campaign teardown.

    Idempotent: already-paused → 200 with current state (matches frontend retry
    semantics on flaky network).
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    if sender.lifecycle_status == "paused":
        # Idempotent — return current state instead of erroring.
        return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])
    if sender.lifecycle_status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": sender.lifecycle_status, "to": "paused"},
        )
    # Same guard PATCH uses (line 351): cannot pause if locked in running campaign.
    await _check_sender_not_in_running_campaign(db, ctx, sender.id)
    sender.lifecycle_status = "paused"
    await db.commit()
    await db.refresh(sender)
    logger.info(
        f"[senders] paused workspace={ctx.workspace_id} slug={sender.slug}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])


@router.post("/senders/{slug}/resume", response_model=SenderCreateResponse)
async def resume_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """UI-SPEC §5.10 row action Resume — flip lifecycle_status paused → active.

    Idempotent: already-active → 200 with current state.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    if sender.lifecycle_status == "active":
        return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])
    if sender.lifecycle_status != "paused":
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": sender.lifecycle_status, "to": "active"},
        )
    sender.lifecycle_status = "active"
    await db.commit()
    await db.refresh(sender)
    logger.info(
        f"[senders] resumed workspace={ctx.workspace_id} slug={sender.slug}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])


# ─── Proxy assignment ────────────────────────────────────────────────────────


@router.post("/senders/{slug}/assign-proxy", response_model=SenderResponse)
async def assign_proxy(
    slug: str,
    request: AssignProxyRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-22: назначить sender'у прокси из workspace pool.

    Записывает proxy JSON в sender.proxy и привязывает proxy_pool.assigned_to_sender_id.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)

    # Workspace-scoped proxy lookup.
    proxy_result = await db.execute(
        select(ProxyPool).where(
            ProxyPool.id == request.proxy_id,
            ProxyPool.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    proxy_row = proxy_result.scalar_one_or_none()
    if proxy_row is None:
        # 404, не 403 (security: не раскрываем cross-tenant существование).
        raise HTTPException(
            status_code=404,
            detail={"code": "PROXY_NOT_FOUND", "message": "Proxy not found"},
        )

    sender.proxy = {
        "type": "socks5",
        "host": proxy_row.host,
        "port": proxy_row.port,
        "username": proxy_row.username,
        "password": proxy_row.password,
    }
    proxy_row.assigned_to_sender_id = sender.id
    # proxy-switch-listener-lag (mig 062): mark the switch pending in the SAME
    # transaction as the new proxy. Until the listener confirms a reconnect on the
    # new IP (it clears this) the send/warmup/checker selection paths skip this
    # sender, so it never opens a temp connection on the NEW proxy while the
    # listener may still hold the OLD IP (double-IP → Telegram auth_key kill).
    # DB-clock now() (not app time) so it compares cleanly against the NOW()/TTL
    # gate in the selection queries. A reconcile-loop TTL sweep lifts a stale flag.
    sender.proxy_switch_pending_at = func.now()
    await db.commit()
    await db.refresh(sender)

    # Phase 3 C-05: no longer reload with ai_context — relationship dropped.

    logger.info(
        f"[senders] assign-proxy workspace={ctx.workspace_id} slug={slug} "
        f"proxy_id={proxy_row.id} port={proxy_row.port}"
    )
    return _sender_to_response(sender)


# ─── SpamBot ─────────────────────────────────────────────────────────────────


@router.get("/senders/{slug}/spambot-check")
async def check_spambot(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Check sender's account status via @SpamBot.

    Returns:
        status: 'free' | 'limited' | 'suspended' | 'unknown'
        raw_text: full SpamBot response
        auth_status_updated: новый auth_status (если поменялся)

    Phase 2 (D-11): больше не пишет is_active=False — derived 'error' computed
    из auth_status при чтении.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)
    if not sender.session_string:
        raise HTTPException(
            status_code=400,
            detail={"code": "NO_SESSION", "message": "Sender has no session"},
        )

    client = None
    try:
        client = await telegram_service.get_client(
            sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
        # selfcheck_key passed for intent/forward-compat. NOTE: this endpoint runs
        # in the api process; the SpamBot reply is handled by the listener's
        # persistent client in the listener process, where this in-memory marker is
        # NOT visible — so the antispam auto-cancel is not suppressed here (known
        # cross-process limitation, decided 2026-06-22).
        spambot_result = await telegram_service.check_spambot(client, selfcheck_key=sender.slug)

        # Migration 028: map SpamBot verdict onto the right column.
        #   free      → clear restriction (restriction_status='none')
        #   frozen    → restriction_status='frozen' (reversible read-only, session intact)
        #   limited   → restriction_status='spam_limited' + recheck window
        #   suspended → real ban → auth_status='banned' (auth-level, not restriction)
        # Previously this wrote a bogus auth_status='limited' (not a valid enum value).
        from app.config import get_settings

        verdict = spambot_result["status"]
        recheck_at = datetime.now(timezone.utc) + timedelta(
            seconds=get_settings().restriction_recheck_interval_seconds
        )
        # Guard (frozen-spambot-check-error.md): Telegram's read-only FREEZE is
        # reversible and session-intact, but SpamBot reports it with "blocked"/
        # «заблокирован» wording that classify_spambot_text maps to 'suspended'. A
        # sender already flagged 'frozen' (set by a reliable FROZEN_* RPC signal in
        # queue.py) must NOT be escalated to a permanent auth_status='banned' by an
        # ambiguous SpamBot text — that would flip derived status frozen→error and
        # demand reauth on a live session. The check itself succeeding proves the
        # session authenticates; a genuine hard ban surfaces on the AUTH path
        # (SessionAuthError below), not here. Treat it as still-frozen.
        if verdict == "suspended" and sender.restriction_status == "frozen":
            verdict = "frozen"
        if verdict == "frozen":
            if sender.restriction_status != "frozen":
                sender.restriction_status = "frozen"
            sender.restricted_until = recheck_at
            await db.commit()
            spambot_result["status"] = "frozen"
            spambot_result["restriction_status_updated"] = "frozen"
        elif verdict == "suspended" and sender.auth_status != "banned":
            sender.auth_status = "banned"
            await db.commit()
            spambot_result["auth_status_updated"] = "banned"
        elif verdict == "limited":
            sender.restriction_status = "spam_limited"
            # Prefer SpamBot's quoted release time (+5min buffer); else fixed interval.
            iso = spambot_result.get("limit_until")
            recheck_at = datetime.now(timezone.utc) + timedelta(
                seconds=get_settings().restriction_recheck_interval_seconds
            )
            if iso:
                try:
                    candidate = datetime.fromisoformat(iso) + timedelta(minutes=5)
                    if candidate > datetime.now(timezone.utc):
                        recheck_at = candidate
                except ValueError:
                    pass
            sender.restricted_until = recheck_at
            await db.commit()
            spambot_result["restriction_status_updated"] = "spam_limited"
        elif verdict == "free" and sender.restriction_status != "none":
            sender.restriction_status = "none"
            sender.restricted_until = None
            # flush so the raw-SQL eligible-pool query below reads this sender as
            # restriction_status='none' inside the SAME (uncommitted) transaction.
            await db.flush()
            # Un-pause this sender's own paused pending rows (ACCOUNT_FROZEN pushed
            # them +24h; PeerFlood didn't pause but this is a harmless no-op then).
            await db.execute(
                text("""
                    UPDATE message_queue SET scheduled_at = NOW()
                    WHERE sender_id = :sid AND status = 'pending'
                      AND scheduled_at > NOW()
                """),
                {"sid": str(sender.id)},
            )
            # Rebalance-back (inverse of Phase-9 failover): while restricted, the
            # sender's cold-pending backlog was moved onto the healthy pool by
            # failover_cold_backlog. Un-pause alone can't bring it back — it only
            # touches rows still assigned here. Now that the sender is eligible
            # again, pull a fair ±1-of-total/P share of cold-pending backlog back
            # onto it per campaign, so it resumes cold outreach within its own
            # rate limits instead of returning active with an empty queue.
            # Mirrors listener._restriction_reconcile_tick's automatic recovery.
            from app.services.rebalance import rebalance_on_attach
            camp_rows = (await db.execute(
                text("SELECT campaign_id FROM campaign_senders WHERE sender_id = :sid"),
                {"sid": str(sender.id)},
            )).fetchall()
            for cr in camp_rows:
                await rebalance_on_attach(cr[0], sender.id, db)
            await db.commit()
            spambot_result["restriction_status_updated"] = "none"

        return spambot_result
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except Exception as e:
        logger.error(f"SpamBot check failed for {slug}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"code": "SPAMBOT_CHECK_FAILED", "message": str(e)},
        )
    finally:
        if client:
            await telegram_service.disconnect_client(client)


@router.post("/senders/{slug}/spambot-conversation")
async def get_or_create_spambot_conversation(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Get-or-create the per-sender live chat with @SpamBot (id 178220800).

    Backing store for the account-page "Text to SpamBot" side panel (quick task
    260713-hiw). Returns the conversation id the frontend then polls
    (GET /conversations/{id}/messages) and sends into (POST /conversations/{id}/send).

    Workspace-scoped via _load_sender_by_slug (cross-tenant slug → 404). The
    conversation gets a dedicated status='spambot' so it is EXCLUDED from the
    normal Inbox list, ai_enabled=false (no AI dispatch), and a sentinel
    contact_phone that matches no real recipient (so the send path's queue-cancel
    is a harmless no-op). No Telethon call here — entity cold-start is handled by
    the send path's get_dialogs fallback.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)

    existing = (await db.execute(text("""
        SELECT id FROM conversations
        WHERE sender_id = :sid AND contact_telegram_id = 178220800
          AND status = 'spambot'
        ORDER BY created_at DESC LIMIT 1
    """), {"sid": str(sender.id)})).fetchone()

    if existing is not None:
        return {"conversation_id": str(existing.id), "status": "spambot"}

    conv_id = uuid4()
    await db.execute(text("""
        INSERT INTO conversations (
            id, workspace_id, sender_id, contact_phone, contact_name,
            contact_telegram_id, ai_enabled, status, paused_at, paused_reason
        )
        VALUES (
            :id, :wid, :sid, 'spambot:178220800', '@SpamBot', 178220800,
            false, 'spambot', NOW(), 'SpamBot manual chat'
        )
    """), {
        "id": str(conv_id),
        "wid": str(sender.workspace_id),
        "sid": str(sender.id),
    })
    await db.commit()

    return {"conversation_id": str(conv_id), "status": "spambot"}


# ─── Restriction event history (HLTH-03) ─────────────────────────────────────


@router.get(
    "/senders/{slug}/restriction-events",
    response_model=list[RestrictionEventResponse],
)
async def list_restriction_events(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """HLTH-03: append-only restriction-event history for a sender, newest-first.

    Workspace-scoped via _load_sender_by_slug (opaque 404 for foreign/unknown
    slugs — no cross-tenant leakage). Read-only over the append-only log; the
    event SELECT is also constrained by workspace_id (defence-in-depth) and
    bounded by a default LIMIT.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    result = await db.execute(
        select(SenderRestrictionEvent)
        .where(
            SenderRestrictionEvent.sender_id == sender.id,
            SenderRestrictionEvent.workspace_id == ctx.workspace_id,
        )
        .order_by(SenderRestrictionEvent.created_at.desc())
        .limit(200)
    )
    return result.scalars().all()


# ─── Block-rate aggregate (SRLD-08, D-15/D-16) ───────────────────────────────


@router.get(
    "/senders/{slug}/block-rate",
    response_model=SenderBlockRateResponse,
)
async def get_block_rate(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """SRLD-08 (D-15/D-16): read-only per-sender block-rate over a 7-day window.

    Counts durable event_type='blocked' restriction events vs message_type='sent'
    messages_log rows. block_rate = blocks_7d / sends_7d (0.0 when no sends) — the
    design-doc "metric that actually matters" (blocks → reports → PeerFlood →
    freeze). STRICTLY read-only (D-16): NO control-loop, NO auto-pause, NO writes.

    Workspace-scoped via _load_sender_by_slug (opaque 404 for foreign/unknown
    slugs) PLUS an explicit workspace_id filter in the SQL (defence-in-depth,
    mirroring list_restriction_events).
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    row = (await db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM sender_restriction_events e
            WHERE e.sender_id = :sid AND e.workspace_id = :wid
              AND e.event_type = 'blocked'
              AND e.created_at > now() - interval '7 days')  AS blocks_7d,
          (SELECT COUNT(*) FROM messages_log m
            WHERE m.sender_id = :sid AND m.workspace_id = :wid
              AND m.message_type = 'sent'
              AND m.created_at > now() - interval '7 days')  AS sends_7d
    """), {"sid": str(sender.id), "wid": str(ctx.workspace_id)})).one()
    blocks_7d = int(row.blocks_7d)
    sends_7d = int(row.sends_7d)
    return SenderBlockRateResponse(
        blocks_7d=blocks_7d,
        sends_7d=sends_7d,
        block_rate=(blocks_7d / sends_7d) if sends_7d else 0.0,
    )


# ─── Account profile edit (Phase 20 — PROF-02/03, D-06/07/08/09) ─────────────


@router.get("/senders/{slug}/username-check", response_model=UsernameCheckResponse)
async def username_check(
    slug: str,
    username: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Username availability pre-check (C5).

    1. Local format validation (Telegram rules) — invalid → available=False/'invalid'.
    2. Re-submitting the account's own current username → available (no-op).
    3. Best-effort live check via CheckUsernameRequest. If the session can't be reached
       we fall back to the format-valid verdict — the authoritative occupancy check runs
       at PATCH time (UpdateUsernameRequest → USERNAME_TAKEN).
    """
    from app.services.telegram import telegram_service

    sender = await _load_sender_by_slug(db, ctx, slug)

    if not username or not _USERNAME_RE.match(username):
        return UsernameCheckResponse(available=False, reason="invalid")

    if sender.tg_username and username.lower() == sender.tg_username.lower():
        return UsernameCheckResponse(available=True, reason=None)

    try:
        res = await telegram_service.check_username(
            sender.slug, str(sender.id), sender.session_string, username,
            proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
    except TypeError:
        # CR-04 regression guard: a broken call signature is a programming error,
        # not an unreachable session — never mask it as "available".
        raise
    except Exception as e:  # noqa: BLE001 — session unreachable → best-effort fall-through
        logger.info(f"[senders] username-check live probe failed for {slug}: {e}")
        return UsernameCheckResponse(available=True, reason=None)

    return UsernameCheckResponse(
        available=bool(res.get("available", True)), reason=res.get("reason")
    )


@router.patch("/senders/{slug}/profile", response_model=ProfileUpdateResponse)
async def update_sender_profile(
    slug: str,
    request: ProfileUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PATCH Section-A identity: first/last name + bio (warning-only, D-07) and username
    (1h hard block, D-08). Order: bio-length guard → username cooldown → Telegram writes
    → cache refresh + stamp → commit → D-09 advisory warnings.
    """
    from telethon.tl.functions.account import UpdateProfileRequest
    from app.services.telegram import (
        telegram_service,
        SessionAuthError,
        ProfileChangeRejectedError,
    )

    sender = await _load_sender_by_slug(db, ctx, slug)

    # Bio length guard → 400 BIO_TOO_LONG (before any Telegram call).
    if request.about is not None and len(request.about) > _BIO_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "BIO_TOO_LONG",
                "message": f"Описание слишком длинное (максимум {_BIO_MAX_LEN} символов)",
            },
        )

    # first_name cannot be cleared: Telegram requires a non-empty first name and
    # returns FIRSTNAME_INVALID for an empty one. The clear-field fix makes the UI
    # send an explicit "" for an emptied field (to distinguish "cleared" from "not
    # touched" = None); reject a blank first_name here with a clear message BEFORE
    # any Telegram RPC — avoids a pointless round-trip and a fresh-account throttle
    # hit, and prevents a silent no-op. last_name/about MAY be "" (cleared).
    if request.first_name is not None and request.first_name.strip() == "":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "FIRST_NAME_REQUIRED",
                "message": "Имя обязательно — его нельзя оставить пустым.",
            },
        )

    changing_profile = (
        request.first_name is not None
        or request.last_name is not None
        or request.about is not None
    )
    changing_username = request.username is not None

    # D-08 hard block for username BEFORE any Telegram call (photo has its own endpoint).
    if changing_username:
        _check_profile_cooldown(sender, "username")

    try:
        if changing_profile:
            # Only pass fields the user actually changed (None leaves them untouched).
            req = UpdateProfileRequest(
                first_name=request.first_name,
                last_name=request.last_name,
                about=request.about,
            )
            await telegram_service.update_profile(
                sender.slug, str(sender.id), sender.session_string, req,
                proxy=sender.proxy,
                fingerprint=sender.client_fingerprint,
            )
            if request.first_name is not None:
                composed = (
                    (request.first_name or "")
                    + (" " + request.last_name if request.last_name else "")
                ).strip()
                sender.name = composed or sender.name
                _stamp_profile_change(sender, "name")
            if request.about is not None:
                sender.tg_bio = request.about
                _stamp_profile_change(sender, "bio")
        if changing_username:
            await telegram_service.update_username(
                sender.slug, str(sender.id), sender.session_string, request.username,
                proxy=sender.proxy,
                fingerprint=sender.client_fingerprint,
            )
            sender.tg_username = request.username or None
            _stamp_profile_change(sender, "username")
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except ProfileChangeRejectedError as e:
        # Telegram accepted the RPC but silently did NOT apply the change (anti-abuse
        # name/bio throttle). NOTHING is stamped/committed — surface a clear retry
        # message instead of a false "Профиль обновлён" (D-07 hardening).
        _FIELD_RU = {"first_name": "имя", "last_name": "фамилию", "about": "описание"}
        fields_ru = ", ".join(_FIELD_RU.get(f, f) for f in e.fields)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_CHANGE_REJECTED",
                "message": (
                    f"Telegram не применил новое значение ({fields_ru}) — вероятно, "
                    "срабатывает ограничение на слишком частую смену профиля "
                    "(особенно на новых аккаунтах). Попробуйте позже."
                ),
                "fields": e.fields,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error
        _raise_profile_telegram_error(e)

    await db.commit()
    await db.refresh(sender)
    return ProfileUpdateResponse(
        sender=_sender_to_response(sender), warnings=_profile_advisory(sender)
    )


# ─── Account profile photo (Phase 20 — PROF-04/07, D-08/D-11) ────────────────


@router.post("/senders/{slug}/photo", response_model=ProfileUpdateResponse)
async def upload_sender_photo(
    slug: str,
    file: UploadFile = File(...),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-04: upload a new profile photo (multipart).

    Order: size/mime validation (413/422, BEFORE any Telegram call) → D-08 photo
    cooldown (409, BEFORE the Telegram write) → upload → cache Telegram's normalized
    avatar (falls back to the raw upload) → per-field stamp → commit → D-09 advisory.

    NB: validation runs before the cooldown so a bad upload always reports the input
    error, not a stale-cooldown 409.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)

    raw = await file.read()
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "FILE_TOO_LARGE", "message": "Файл слишком большой (максимум 5 МБ)"},
        )
    if file.content_type not in ALLOWED_PHOTO_MIME:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNSUPPORTED_FILE_TYPE", "message": "Только JPG или PNG"},
        )

    # D-08 hard block for photo BEFORE the Telegram write (after input validation).
    _check_profile_cooldown(sender, "photo")

    try:
        res = await telegram_service.upload_profile_photo(
            sender.slug,
            str(sender.id),
            sender.session_string,
            raw,
            file_name=file.filename or "avatar.jpg",
            proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error (incl. FLOOD_WAIT)
        _raise_profile_telegram_error(e)

    res = res or {}
    # Cache Telegram's own normalized avatar when the service returns it; otherwise
    # fall back to the raw upload bytes (D-11: bytes stay server-side, served via GET).
    sender.tg_photo = res.get("photo") or raw
    sender.tg_photo_mime = res.get("photo_mime") or file.content_type
    _stamp_profile_change(sender, "photo")
    await db.commit()
    await db.refresh(sender)
    return ProfileUpdateResponse(
        sender=_sender_to_response(sender), warnings=_profile_advisory(sender)
    )


@router.delete("/senders/{slug}/photo", response_model=ProfileUpdateResponse)
async def delete_sender_photo(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-04: remove the profile photo and clear the cache.

    A delete is a de-escalation (removing, not spamming a fresh image), so it is
    NOT itself cooldown-blocked — but it DOES stamp the photo field, so a rapid
    follow-up UPLOAD is still throttled by D-08.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)

    try:
        res = await telegram_service.delete_profile_photos(
            sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error (incl. FLOOD_WAIT)
        _raise_profile_telegram_error(e)

    sender.tg_photo = None
    sender.tg_photo_mime = None
    _stamp_profile_change(sender, "photo")
    await db.commit()
    await db.refresh(sender)
    return ProfileUpdateResponse(
        sender=_sender_to_response(sender), warnings=_profile_advisory(sender)
    )


@router.get("/senders/{slug}/photo")
async def serve_sender_photo(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-07 / D-11: serve the cached profile photo bytes through an AUTH-GATED
    endpoint — never a raw blob URL, never base64-inlined into the list. 404 when
    no photo is cached; a foreign-workspace slug is an opaque 404 (workspace scope).

    The distinct `/photo` suffix keeps this from colliding with GET /senders/{slug}.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    if not sender.tg_photo:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_PHOTO", "message": "No cached photo"},
        )
    return Response(content=sender.tg_photo, media_type=sender.tg_photo_mime or "image/jpeg")


# ─── Account profile resync (Phase 20 — PROF-06, D-12) ───────────────────────


@router.post("/senders/{slug}/resync", response_model=SenderResponse)
async def resync_sender_profile(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-06 / D-12: pull the live username / bio / photo from Telegram into the
    cache. This is a READ-from-Telegram (does not open the edit form / does not
    mutate the account), so it carries NO cooldown and NO per-field stamp.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)

    try:
        res = await telegram_service.fetch_profile(
            sender.slug, str(sender.id), sender.session_string, proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error (incl. FLOOD_WAIT)
        _raise_profile_telegram_error(e)

    res = res or {}
    sender.tg_username = res.get("username")
    sender.tg_bio = res.get("bio")
    sender.tg_premium = bool(res.get("premium", False))
    # PROF-06 gap-fix: also refresh the display name from the live account. There is
    # no separate first/last column on Sender — compose them into the single `name`
    # field the SAME way update_sender_profile does. Only overwrite when Telegram
    # actually returned a first_name, mirroring the photo/has_photo "don't zero out
    # on missing data" defensiveness below (a partial/lightweight resync payload must
    # never blank the cached name).
    if res.get("first_name") is not None:
        composed = (
            (res.get("first_name") or "")
            + (" " + res["last_name"] if res.get("last_name") else "")
        ).strip()
        sender.name = composed or sender.name
    photo = res.get("photo")
    if photo is not None:
        sender.tg_photo = photo
        sender.tg_photo_mime = res.get("photo_mime") or "image/jpeg"
    elif res.get("has_photo") is False:
        # Live account has no photo → clear the cache.
        sender.tg_photo = None
        sender.tg_photo_mime = None
    await db.commit()
    await db.refresh(sender)

    resp = _sender_to_response(sender)
    # Honour the service's authoritative has_photo when it reports one without
    # shipping the raw bytes (e.g. a lightweight resync); in prod the derived
    # bool(tg_photo) already matches.
    if res.get("has_photo") is not None:
        resp.has_photo = bool(res.get("has_photo"))
    return resp


# ─── Account 2FA + recovery email (Phase 20 — PROF-05, D-03/D-04) ────────────
# SECURITY (D-03): the 2FA password is a transient request field only — it is
# never written to any DB column here (no `sender.` assignment, no db.commit).


@router.post("/senders/{slug}/2fa")
async def update_sender_2fa(
    slug: str,
    request: TwoFAPasswordUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-05: set (no current_password) or change (with current_password, D-04) the
    account 2FA password via a single stateless ``edit_2fa`` request. Wrong current
    password → 400 PASSWORD_INVALID. The password is NEVER persisted (D-03).

    IMPT-10 (D-06): for an IMPORTED account whose 2FA password was stored (encrypted)
    at import time, a request that OMITS ``current_password`` falls back to the stored,
    decrypted password server-side — so the user need not re-type the 2FA password the
    platform already knows. The decrypted plaintext is used ONLY to build the edit_2fa
    request; it is never returned in the response, never logged, never re-persisted
    (D-07). The reconnect uses the account's own fingerprint (Part B, site edit_2fa)."""
    from app.services.telegram import telegram_service, SessionAuthError
    from app.services.encryption import decrypt_session

    sender = await _load_sender_by_slug(db, ctx, slug)
    # IMPT-10 (D-06): imported-account autofill of the stored 2FA password. Server-side
    # only — never surfaced to the client (D-07).
    current_pw = request.current_password
    if current_pw is None and getattr(sender, "twofa_password_enc", None):
        current_pw = decrypt_session(sender.twofa_password_enc)
    try:
        await telegram_service.edit_2fa(
            sender.slug,
            str(sender.id),
            sender.session_string,
            current_password=current_pw,
            new_password=request.new_password,
            hint=request.hint or "",
            proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error
        _raise_profile_telegram_error(e)
    # D-03: nothing written to the DB — password is transient.
    return {"success": True}


@router.post("/senders/{slug}/2fa/recovery-email")
async def start_sender_recovery_email(
    slug: str,
    request: RecoveryEmailStart,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-05 step 1: start a recovery-email change. Telegram sends a confirmation
    code and this returns EMAIL_CONFIRMATION_SENT + code_length so the UI can prompt.
    The two-request confirm flow lives account-side (see the /confirm endpoint)."""
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)
    try:
        res = await telegram_service.set_recovery_email(
            sender.slug,
            str(sender.id),
            sender.session_string,
            current_password=request.current_password,
            email=str(request.email),
            proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error
        _raise_profile_telegram_error(e)
    # D-03: nothing written to the DB — password is transient.
    return {"code": "EMAIL_CONFIRMATION_SENT", "code_length": (res or {}).get("code_length")}


@router.post("/senders/{slug}/2fa/recovery-email/confirm")
async def confirm_sender_recovery_email(
    slug: str,
    request: RecoveryEmailConfirm,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-05 step 2: submit the emailed confirmation code
    (``ConfirmPasswordEmailRequest``). Wrong / expired code → 400 EMAIL_CODE_INVALID."""
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)
    try:
        await telegram_service.confirm_recovery_email(
            sender.slug,
            str(sender.id),
            sender.session_string,
            code=request.code,
            proxy=sender.proxy,
            fingerprint=sender.client_fingerprint,
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error
        _raise_profile_telegram_error(e)
    # D-03: nothing written to the DB — password is transient.
    return {"success": True}


# ─── Workspace proxy pool CRUD (D-22) ────────────────────────────────────────


@router.get("/workspace/proxies", response_model=ProxyPoolListResponse)
async def list_proxies(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List workspace proxy pool (D-22)."""
    result = await db.execute(
        select(ProxyPool)
        .where(ProxyPool.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy
        .order_by(ProxyPool.created_at.desc())
    )
    proxies = result.scalars().all()
    items = [
        ProxyPoolItem(
            id=p.id,
            host=p.host,
            port=p.port,
            type="socks5",
            username=p.username,
            assigned_to_sender_id=p.assigned_to_sender_id,
        )
        for p in proxies
    ]
    return ProxyPoolListResponse(proxies=items, total=len(items))


@router.post("/workspace/proxies", response_model=ProxyPoolItem, status_code=201)
async def create_proxy(
    request: ProxyPoolCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Add proxy to workspace pool (D-22)."""
    proxy = ProxyPool(
        workspace_id=ctx.workspace_id,
        host=request.host,
        port=request.port,
        username=request.username or "",
        password=request.password,
    )
    db.add(proxy)
    await db.commit()
    await db.refresh(proxy)

    logger.info(
        f"[proxy-pool] created workspace={ctx.workspace_id} "
        f"host={proxy.host} port={proxy.port}"
    )
    return ProxyPoolItem(
        id=proxy.id,
        host=proxy.host,
        port=proxy.port,
        type=request.type,
        username=proxy.username,
        assigned_to_sender_id=proxy.assigned_to_sender_id,
    )


@router.delete("/workspace/proxies/{proxy_id}", status_code=204)
async def delete_proxy(
    proxy_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Remove proxy from workspace pool (D-22)."""
    result = await db.execute(
        select(ProxyPool).where(
            ProxyPool.id == proxy_id,
            ProxyPool.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    proxy = result.scalar_one_or_none()
    if proxy is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROXY_NOT_FOUND", "message": "Proxy not found"},
        )
    await db.delete(proxy)
    await db.commit()
    logger.info(
        f"[proxy-pool] deleted workspace={ctx.workspace_id} proxy_id={proxy_id}"
    )
