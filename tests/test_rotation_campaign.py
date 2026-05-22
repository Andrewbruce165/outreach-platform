"""Wave 0 stubs — Plan 04-04 Task 1 (rotation.py per-campaign rewrite).

Covers D-06 (rotation.get_or_assign_sender(campaign_id, ...) signature).
Real test bodies в Task 3 (после rotation rewrite).
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_get_or_assign_sender_signature_uses_campaign_id():
    """rotation.get_or_assign_sender принимает campaign_id (not context_id)."""
    pytest.skip("Wave 0 stub — Task 3 implements")


async def test_rotation_picks_from_campaign_senders_only():
    """Sender pool = campaign_senders, не глобально workspace senders."""
    pytest.skip("Wave 0 stub — Task 3 implements")


async def test_rotation_unique_constraint_protects_race():
    """ON CONFLICT idx_cca_campaign_phone — нет дублей при concurrent INSERT."""
    pytest.skip("Wave 0 stub — Task 3 implements")


async def test_rotation_skips_inactive_senders():
    """Sender с auth_status != 'ok' OR lifecycle_status != 'active' — не выбирается."""
    pytest.skip("Wave 0 stub — Task 3 implements")


async def test_rotation_returns_assigned_sender_on_retry():
    """Если cca уже есть — возвращает тот же sender (idempotent)."""
    pytest.skip("Wave 0 stub — Task 3 implements")


async def test_rotation_returns_none_when_no_active_senders():
    """Все sender'ы кампании auth_status='locked' → возвращает None (caller handles)."""
    pytest.skip("Wave 0 stub — Task 3 implements")
