"""Wave 0 stubs — Plan 04-04 Task 1 (message_queue.campaign_id + conversations.campaign_id).

Covers CAMP-17 (queue учитывает campaign_id).
Real test bodies в Task 5 (после INSERT conversations rewrite).
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_message_queue_campaign_id_nullable_per_audit_q1():
    """INSERT без campaign_id — успешно (legacy items support)."""
    pytest.skip("Wave 0 stub — Task 5 implements")


async def test_message_queue_campaign_id_fk_set_null_on_delete():
    """DELETE campaign → existing queue items.campaign_id → NULL (D-07)."""
    pytest.skip("Wave 0 stub — Task 5 implements")


async def test_conversations_campaign_id_set_at_first_send():
    """D-05: INSERT в conversations при первой отправке campaign'ской очереди → campaign_id заполнен."""
    pytest.skip("Wave 0 stub — Task 5 implements")


async def test_queue_todo_phase4_resolved():
    """L2: both queue.py TODO markers закрыты (:708 + :849 per B1).

    Точный match строк маркеров вместо substring, чтобы избежать частичных совпадений.
    """
    import app.services.queue as q
    src = open(q.__file__).read()
    # queue.py:708 — INSERT conversations branch
    assert "TODO(phase-4): pull from conversation.campaign_id JOIN" not in src
    # queue.py:849 — enqueue_file branch (B1 revision)
    assert "TODO(phase-4): apply same ai_context_id propagation as enqueue_message" not in src


async def test_enqueue_file_signature_accepts_campaign_id():
    """B1: enqueue_file принимает campaign_id (как enqueue_message)."""
    pytest.skip("Wave 0 stub — Task 5 implements")
