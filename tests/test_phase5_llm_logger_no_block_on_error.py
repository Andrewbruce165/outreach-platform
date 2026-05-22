"""Phase 5 ANLX-05 — llm_logger never-raise contract + prompt-leak guard.

Covers tests 5, 6, 9 from plan 05-03 behaviour list:
  - SQLAlchemyError on session open → swallowed, warning logged, no raise.
  - Any other Exception type → swallowed, warning logged, no raise.
  - T-05-03-PROMPT-LEAK: sensitive prompt content MUST NOT appear in
    application logs (only in llm_calls.prompt JSONB column).
"""

import logging
from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services.llm_logger import log_llm_call

pytestmark = pytest.mark.asyncio


# ── Test 5: SQLAlchemyError swallowed, warning logged ────────────────────────


async def test_db_error_does_not_raise(test_conversation_factory, caplog):
    """SQLAlchemyError on AsyncSessionLocal — swallowed, no raise."""
    conv = await test_conversation_factory()

    with patch(
        "app.services.llm_logger.AsyncSessionLocal",
        side_effect=SQLAlchemyError("simulated DB failure"),
    ):
        # MUST NOT raise
        await log_llm_call(
            workspace_id=conv["workspace_id"],
            conversation_id=conv["id"],
            model="gpt-4o-mini",
            prompt={},
            response=None,
            latency_ms=10,
        )

    assert any(
        "llm_calls INSERT failed" in r.message or "unexpected error" in r.message
        for r in caplog.records
    )


# ── Test 6: any Exception type swallowed ─────────────────────────────────────


async def test_unexpected_error_does_not_raise(test_conversation_factory, caplog):
    """RuntimeError (or any non-SQLAlchemy exception) — also swallowed."""
    conv = await test_conversation_factory()
    with patch(
        "app.services.llm_logger.AsyncSessionLocal",
        side_effect=RuntimeError("unexpected"),
    ):
        await log_llm_call(
            workspace_id=conv["workspace_id"],
            conversation_id=conv["id"],
            model="gpt-4o-mini",
            prompt={},
            response=None,
            latency_ms=10,
        )
    assert any("unexpected error" in r.message for r in caplog.records)


# ── Test 9: T-05-03-PROMPT-LEAK — prompt content NOT in app logs ─────────────


async def test_sensitive_prompt_content_not_in_logs(
    test_conversation_factory, caplog
):
    """Trigger error path so warning is emitted; verify prompt content absent."""
    conv = await test_conversation_factory()
    secret_prompt = {
        "messages": [
            {"role": "system", "content": "SECRET_FAQ_FRAGMENT_XYZ_DO_NOT_LOG"},
            {"role": "user", "content": "SECRET_USER_PRIVATE_INFO_ABC"},
        ],
    }
    with patch(
        "app.services.llm_logger.AsyncSessionLocal",
        side_effect=SQLAlchemyError("triggered"),
    ):
        with caplog.at_level(logging.WARNING):
            await log_llm_call(
                workspace_id=conv["workspace_id"],
                conversation_id=conv["id"],
                model="gpt-4o-mini",
                prompt=secret_prompt,
                response=None,
                latency_ms=10,
            )
    all_log_text = " ".join(r.message for r in caplog.records)
    assert "SECRET_FAQ_FRAGMENT_XYZ_DO_NOT_LOG" not in all_log_text
    assert "SECRET_USER_PRIVATE_INFO_ABC" not in all_log_text
