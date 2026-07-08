"""Phase 22 (D-16) — per-workspace new-chat grade-ladder settings API.

Cloned from the warmup-settings pattern (app/routers/warmup.py::GET/PUT /settings):
a GET that resolves the workspace ladder to code-defaults when no row exists, and
an idempotent PUT that upserts the fixed 3-level ladder scoped to the caller's
workspace. Green-corridor soft warnings mirror the senders.py rate-limit pattern
(200 + warnings[] for out-of-recommended values; Pydantic bounds are the hard cap).

The ladder is FIXED at 3 levels (D-16 — no add/remove). Level 3 is permanent, so
it carries a chats-per-day budget but NO step-days (D-17). Every query is scoped
by `ctx.workspace_id` from auth_dep — a workspace can never read or write another
tenant's ladder (T-22-04).
"""

import logging
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import WarningItem
from app.services.grade_ladder import resolve_ladder
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["grade-settings"])

# Green-corridor soft caps (D-16). Values beyond these return a warning but 200.
# The hard cap is enforced by the Pydantic Field bounds (le=100 chats / le=365 days).
SOFT_CAP_CHATS = 13
SOFT_STEP_DAYS_MIN = 30


class GradeLadderUpdate(BaseModel):
    """PUT body — the fixed 3-level ladder (D-16). Level 3 has no step (D-17).

    Pydantic `ge/le` bounds are the HARD cap (422 on breach); the green-corridor
    soft warnings are computed separately in `_validate_ladder`.
    """
    level1_chats_per_day: int = Field(5, ge=1, le=100)
    level1_step_days: int = Field(30, ge=1, le=365)
    level2_chats_per_day: int = Field(9, ge=1, le=100)
    level2_step_days: int = Field(30, ge=1, le=365)
    level3_chats_per_day: int = Field(13, ge=1, le=100)


def _validate_ladder(body: GradeLadderUpdate) -> List[WarningItem]:
    """Green-corridor soft warnings (D-16), worded like senders.py rate warnings.

    A WarningItem is appended when a level's chats/day exceeds the recommended
    ceiling (SOFT_CAP_CHATS) or a step is shorter than the recommended minimum
    (SOFT_STEP_DAYS_MIN). Hard bounds are already enforced by Pydantic.
    """
    warnings: List[WarningItem] = []

    chats = {
        "level1_chats_per_day": body.level1_chats_per_day,
        "level2_chats_per_day": body.level2_chats_per_day,
        "level3_chats_per_day": body.level3_chats_per_day,
    }
    for field, val in chats.items():
        if val > SOFT_CAP_CHATS:
            warnings.append(
                WarningItem(field=field, value=val, recommended_max=SOFT_CAP_CHATS)
            )

    steps = {
        "level1_step_days": body.level1_step_days,
        "level2_step_days": body.level2_step_days,
    }
    for field, val in steps.items():
        if val < SOFT_STEP_DAYS_MIN:
            # For step-days the recommendation is a floor; surface it as the
            # recommended_max slot so the UI shows the green-corridor edge.
            warnings.append(
                WarningItem(field=field, value=val, recommended_max=SOFT_STEP_DAYS_MIN)
            )

    return warnings


def _shape(ladder) -> dict:
    """Shape a resolved 3-level ladder for the API response.

    `ladder` is the list of (chats_per_day, step_days) from resolve_ladder;
    level 3's step_days is None (D-17 permanent top level).
    """
    return {
        "levels": [
            {"level": i + 1, "chats_per_day": chats, "step_days": step}
            for i, (chats, step) in enumerate(ladder)
        ],
        "recommended": {
            "max_chats_per_day": SOFT_CAP_CHATS,
            "min_step_days": SOFT_STEP_DAYS_MIN,
        },
    }


@router.get("/sender-grade-settings")
async def get_grade_settings_endpoint(
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Workspace grade ladder — resolves to code-defaults 5/30, 9/30, 13 when no row (D-16).

    Scoped by `ctx.workspace_id`; a missing row returns the resolved code-default
    ladder — byte-identical to the unconfigured platform default.
    """
    result = await db.execute(
        text(
            """
            SELECT level1_chats_per_day, level1_step_days,
                   level2_chats_per_day, level2_step_days, level3_chats_per_day
            FROM sender_grade_settings
            WHERE workspace_id = :wid
            """
        ),
        {"wid": str(ctx.workspace_id)},
    )
    row = result.mappings().first()
    return _shape(resolve_ladder(row))


@router.put("/sender-grade-settings")
async def update_grade_settings_endpoint(
    body: GradeLadderUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Idempotent upsert of the workspace grade ladder (D-16), workspace-scoped.

    INSERT ... ON CONFLICT (workspace_id) DO UPDATE with bind params only. Returns
    {status, settings: <resolved>, warnings: [...]} — 200 even on soft breaches;
    Pydantic bounds already rejected any hard-cap breach with 422.
    """
    wid = str(ctx.workspace_id)
    warnings = _validate_ladder(body)

    await db.execute(
        text(
            """
            INSERT INTO sender_grade_settings
                (workspace_id, level1_chats_per_day, level1_step_days,
                 level2_chats_per_day, level2_step_days, level3_chats_per_day,
                 updated_at)
            VALUES
                (:wid, :l1c, :l1s, :l2c, :l2s, :l3c, NOW())
            ON CONFLICT (workspace_id) DO UPDATE SET
                level1_chats_per_day = EXCLUDED.level1_chats_per_day,
                level1_step_days     = EXCLUDED.level1_step_days,
                level2_chats_per_day = EXCLUDED.level2_chats_per_day,
                level2_step_days     = EXCLUDED.level2_step_days,
                level3_chats_per_day = EXCLUDED.level3_chats_per_day,
                updated_at           = NOW()
            """
        ),
        {
            "wid": wid,
            "l1c": body.level1_chats_per_day,
            "l1s": body.level1_step_days,
            "l2c": body.level2_chats_per_day,
            "l2s": body.level2_step_days,
            "l3c": body.level3_chats_per_day,
        },
    )
    await db.commit()

    logger.info(f"📊 Grade ladder обновлена (ws={wid[:8]})")

    resolved = resolve_ladder(
        {
            "level1_chats_per_day": body.level1_chats_per_day,
            "level1_step_days": body.level1_step_days,
            "level2_chats_per_day": body.level2_chats_per_day,
            "level2_step_days": body.level2_step_days,
            "level3_chats_per_day": body.level3_chats_per_day,
        }
    )
    return {"status": "saved", "settings": _shape(resolved), "warnings": warnings}
