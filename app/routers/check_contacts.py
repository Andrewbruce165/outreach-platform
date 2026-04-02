"""
POST /api/v1/check-contacts

Bulk phone-number verification via a dedicated checker Telegram account.
Results are cached in contacts_cache so main sender accounts can skip
ResolvePhoneRequest for unregistered numbers, protecting them from Telegram bans.
"""
import re
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Sender
from app.routers.auth import verify_api_key
from app.services.checker import checker_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/check-contacts", tags=["check-contacts"])

PHONE_RE = re.compile(r"^\+\d{7,15}$")


# === Schemas ===

class CheckContactsRequest(BaseModel):
    phones: List[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Список номеров для проверки (формат: +7XXXXXXXXXX). Максимум 20 за раз.",
    )
    checker_slug: str = Field(..., description="Slug checker-аккаунта (role='checker')")

    @field_validator("phones")
    @classmethod
    def validate_phone_format(cls, v: List[str]) -> List[str]:
        for phone in v:
            if not PHONE_RE.match(phone):
                raise ValueError(
                    f"Invalid phone format: '{phone}'. Expected international format, e.g. +79001234567"
                )
        return v


class PhoneCheckResult(BaseModel):
    phone: str
    is_registered: bool
    telegram_id: Optional[int] = None
    from_cache: bool


class CheckContactsResponse(BaseModel):
    checked: int
    registered: int
    not_registered: int
    flood_wait_hit: bool
    results: List[PhoneCheckResult]


# === Endpoint ===

@router.post("", response_model=CheckContactsResponse)
async def check_contacts(
    request: CheckContactsRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """
    Check whether phone numbers are registered in Telegram.

    Uses a dedicated checker account (role='checker') to call ResolvePhoneRequest.
    Results are cached in contacts_cache. Subsequent calls for the same numbers
    return immediately from cache.

    Main sender accounts consult this cache before sending — numbers with
    is_registered=false are rejected without any Telegram API call, protecting
    main accounts from spam detection.
    """
    # 1. Resolve checker account
    result = await db.execute(
        select(Sender).where(
            Sender.slug == request.checker_slug,
            Sender.role == "checker",
            Sender.is_active.is_(True),
        )
    )
    checker = result.scalar_one_or_none()

    if checker is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Checker account '{request.checker_slug}' not found or inactive. "
                         "Make sure the sender exists with role='checker' and is_active=true.",
                "code": "CHECKER_NOT_FOUND",
            },
        )

    logger.info(
        f"[check-contacts] Starting batch: checker={request.checker_slug}, "
        f"phones={len(request.phones)}"
    )

    # 2. Run batch check (Lock is held inside checker_service.check_phones)
    summary = await checker_service.check_phones(
        checker_id=str(checker.id),
        checker_slug=checker.slug,
        encrypted_session=checker.session_string,
        phones=request.phones,
        proxy=checker.proxy,
    )

    return CheckContactsResponse(
        checked=summary["checked"],
        registered=summary["registered"],
        not_registered=summary["not_registered"],
        flood_wait_hit=summary["flood_wait_hit"],
        results=[PhoneCheckResult(**r) for r in summary["results"]],
    )
