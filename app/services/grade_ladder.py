"""Phase 22 — shared new-chat grade-ladder resolver.

The SINGLE source of truth for resolving a workspace's account grade ladder.
Imported by the queue rewrite (22-03), the settings API (22-02), the warmup
budget (22-05) and the sender API (22-04) so every reader agrees on budgets and
step timings.

Ladder shape: a 3-element list of (chats_per_day, step_days) for levels 1, 2, 3.
`step_days` is how many days at that level before the account may promote to the
next; level 3 is permanent so its step is None (D-17).

Code-defaults (D-16): a workspace with NO sender_grade_settings row resolves to
LADDER_DEFAULTS — byte-identical behaviour to the unconfigured platform default.
"""

from __future__ import annotations

from sqlalchemy import text

# (chats_per_day, step_days) for levels 1, 2, 3. Level 3 step None (D-17: permanent).
# type: list[tuple[int, int | None]]
LADDER_DEFAULTS = [(5, 30), (9, 30), (13, None)]


def resolve_ladder(row) -> list[tuple[int, int | None]]:
    """Resolve a sender_grade_settings row (or None) to the 3-level ladder.

    `row` may be a mapping / Row / ORM object exposing the five ladder columns,
    or None. None (missing row) → LADDER_DEFAULTS (D-16 code-defaults).
    """
    if row is None:
        return list(LADDER_DEFAULTS)

    def _get(key: str):
        # Support Row/mapping (row["k"]) and ORM/attr (row.k) access.
        try:
            return row[key]
        except (TypeError, KeyError, IndexError):
            return getattr(row, key)

    return [
        (int(_get("level1_chats_per_day")), int(_get("level1_step_days"))),
        (int(_get("level2_chats_per_day")), int(_get("level2_step_days"))),
        (int(_get("level3_chats_per_day")), None),  # D-17: level 3 permanent
    ]


def _clamp_level(level: int) -> int:
    """Clamp a grade level into the valid 1..3 range."""
    if level < 1:
        return 1
    if level > 3:
        return 3
    return level


def budget_for_level(ladder: list[tuple[int, int | None]], level: int) -> int:
    """Return the new-chats-per-day budget for `level` (clamped to 1..3)."""
    return ladder[_clamp_level(level) - 1][0]


def step_days_for_level(ladder: list[tuple[int, int | None]], level: int) -> int | None:
    """Return the days-at-level before promotion for `level` (None = permanent)."""
    return ladder[_clamp_level(level) - 1][1]


async def load_ladder(db, workspace_id) -> list[tuple[int, int | None]]:
    """Load the resolved ladder for a workspace via an AsyncSession.

    SELECT the ladder columns for `workspace_id`; a missing row → LADDER_DEFAULTS.
    Bind params only (no string interpolation).
    """
    result = await db.execute(
        text(
            "SELECT level1_chats_per_day, level1_step_days, "
            "level2_chats_per_day, level2_step_days, level3_chats_per_day "
            "FROM sender_grade_settings WHERE workspace_id = :wid"
        ),
        {"wid": str(workspace_id)},
    )
    row = result.mappings().first()
    return resolve_ladder(row)
