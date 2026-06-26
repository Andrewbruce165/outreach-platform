"""Phase 14 Wave-0 RED scaffold — health-probe miss-counting + suspect rollback.

RESV-01 / D-05 / D-07. These tests are intentionally RED until Wave 2-3 add:
  - a control-probe path on the worker that resolves known-live numbers LIVE
    (bypassing contacts_cache) and counts consecutive misses per checker,
  - degradation on ≥2 consecutive misses → mark checker spam_limited + write a
    sender_restriction_events row,
  - suspect-batch rollback: the degraded checker's not_registered results roll
    back to 'pending' (tg_checked_at cleared), registered results untouched.

Deferred in-body imports of the not-yet-existing helpers keep `--collect-only`
clean (mirrors the Phase 13 13-01 scaffold approach); the helpers/behaviours do
not exist yet, so the test BODIES fail (genuinely RED, real assertions).
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_resolution_state(async_db_session):
    """Delete committed pending contacts / cache rows after each test (see test_checker_cap)."""
    yield
    await async_db_session.execute(text("DELETE FROM contacts_cache"))
    await async_db_session.execute(text("DELETE FROM contacts WHERE tg_status = 'pending'"))
    await async_db_session.commit()


# ─── Control-set / probe miss counting (D-05) ────────────────────────────────


async def test_two_misses_flags(async_db_session, test_workspace, test_checker):
    """≥2 consecutive control-set misses → checker marked spam_limited + audit row.

    D-05: a single miss is stochastic noise; two consecutive control-set misses
    (a known-live number resolving as not_registered twice running) flag the
    checker degraded. The mark MUST go through the Phase-10 restriction infra
    (restriction_status='spam_limited' + a sender_restriction_events row), NEVER
    by nuking auth_status (Pitfall 2).
    """
    # Wave 2 helper — does not exist yet. In-body import keeps collection clean.
    from app.services.contact_check_worker import run_control_probe  # noqa: F401

    checker_id = str(test_checker.id)

    async def _one_miss():
        # A control number (known-live) resolving as not_registered = a miss.
        with patch(
            "app.services.contact_check_worker.checker_service.check_phones",
            new=AsyncMock(
                return_value={
                    "checked": 1,
                    "registered": 0,
                    "not_registered": 1,
                    "flood_wait_hit": False,
                    "results": [{"phone": "+79990000001", "is_registered": False}],
                }
            ),
        ):
            return await run_control_probe(checker_id=checker_id)

    await _one_miss()  # miss #1 — must NOT flag (noise)
    row = (
        await async_db_session.execute(
            text("SELECT restriction_status FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "none", "single miss must not flag (D-05 noise)"

    await _one_miss()  # miss #2 — consecutive → flag

    row = (
        await async_db_session.execute(
            text("SELECT restriction_status FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "spam_limited"

    events = (
        await async_db_session.execute(
            text(
                "SELECT event_type FROM sender_restriction_events "
                "WHERE sender_id = :id ORDER BY created_at DESC"
            ),
            {"id": checker_id},
        )
    ).fetchall()
    assert any(e.event_type == "spam_limited" for e in events)


async def test_single_miss_no_flag(async_db_session, test_workspace, test_checker):
    """One control-set miss is noise (D-05) — checker stays restriction_status='none'."""
    from app.services.contact_check_worker import run_control_probe

    checker_id = str(test_checker.id)
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(
            return_value={
                "checked": 1,
                "registered": 0,
                "not_registered": 1,
                "flood_wait_hit": False,
                "results": [{"phone": "+79990000001", "is_registered": False}],
            }
        ),
    ):
        await run_control_probe(checker_id=checker_id)

    row = (
        await async_db_session.execute(
            text("SELECT restriction_status FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "none"


# ─── Suspect-batch rollback (D-07) ───────────────────────────────────────────


async def test_suspect_rollback_keeps_registered(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Degraded checker: batch not_registered → pending (tg_checked_at cleared);
    registered rows untouched (D-07 / Pitfall 3).

    A throttle produces FALSE NEGATIVES only — never false positives — so the
    `registered` results of a suspect batch are kept while the `not_registered`
    results roll back to `pending` for re-check by another checker.
    """
    # Wave 3 helper — does not exist yet.
    from app.services.contact_check_worker import apply_results_with_confidence

    contacts = await test_contacts_factory(count=2, tg_status="pending")
    reg_contact, notreg_contact = contacts[0], contacts[1]

    summary = {
        "checked": 2,
        "registered": 1,
        "not_registered": 1,
        "flood_wait_hit": False,
        "results": [
            {"phone": reg_contact.phone, "is_registered": True, "telegram_id": 777},
            {"phone": notreg_contact.phone, "is_registered": False},
        ],
    }
    items = [
        type("It", (), {"contact_id": reg_contact.id, "phone": reg_contact.phone, "username": None}),
        type("It", (), {"contact_id": notreg_contact.id, "phone": notreg_contact.phone, "username": None}),
    ]

    # Checker is degraded → not_registered must NOT finalize.
    await apply_results_with_confidence(
        items, summary, checker_id=str(test_checker.id), probe_state="suspect"
    )

    reg_row = (
        await async_db_session.execute(
            text("SELECT tg_status, tg_telegram_id FROM contacts WHERE id = :id"),
            {"id": str(reg_contact.id)},
        )
    ).fetchone()
    assert reg_row.tg_status == "registered"
    assert reg_row.tg_telegram_id == 777

    notreg_row = (
        await async_db_session.execute(
            text(
                "SELECT tg_status, tg_checked_at FROM contacts WHERE id = :id"
            ),
            {"id": str(notreg_contact.id)},
        )
    ).fetchone()
    assert notreg_row.tg_status == "pending", "suspect not_registered must roll back, not finalize"
    assert notreg_row.tg_checked_at is None, "claim timestamp must be cleared for re-check"
