"""H5 — LLM opener paraphrase (C&C mass-ban remediation).

`AIEngine.paraphrase_opener` produces a meaning-preserving visible variant of a
campaign opener so a campaign does not send N byte-identical openers (the top
clustering signal in the mass ban). It must FAIL OPEN — any empty/failed
generation returns the original opener so enqueue is never blocked.

Provider is stubbed exactly like the follow-up-ping tests
(monkeypatch get_provider → a stub whose .complete returns an LLMResult), so no
network / DB is touched (workspace_id=None takes the platform-fallback branch).
"""
import pytest

import app.services.ai_engine as ai_engine_mod
from app.services.ai_engine import ai_engine
from app.services.llm.base import LLMResult

pytestmark = pytest.mark.asyncio


def _stub(monkeypatch, *, text=None, raises=False):
    class _StubProvider:
        async def complete(self, **kwargs):
            if raises:
                raise RuntimeError("boom")
            return LLMResult(text=text, finish_reason="stop")

    monkeypatch.setattr(ai_engine_mod, "get_provider", lambda cfg: _StubProvider())


async def test_paraphrase_returns_llm_text(monkeypatch):
    _stub(monkeypatch, text="Добрый день! Актуально ли финансирование импорта?")
    out = await ai_engine.paraphrase_opener(None, None, "Здравствуйте! Актуален вопрос финансирования импорта?")
    assert out == "Добрый день! Актуально ли финансирование импорта?"


async def test_paraphrase_fail_open_on_error(monkeypatch):
    _stub(monkeypatch, raises=True)
    original = "Здравствуйте! Актуален вопрос финансирования импорта?"
    out = await ai_engine.paraphrase_opener(None, None, original)
    assert out == original, "an LLM error must fall open to the original opener"


async def test_paraphrase_empty_result_returns_original(monkeypatch):
    _stub(monkeypatch, text="")
    original = "Здравствуйте! Актуален вопрос финансирования импорта?"
    out = await ai_engine.paraphrase_opener(None, None, original)
    assert out == original, "an empty generation must fall open to the original opener"


async def test_paraphrase_strips_wrapping_quotes(monkeypatch):
    _stub(monkeypatch, text='"Добрый день! Интересно финансирование импорта?"')
    out = await ai_engine.paraphrase_opener(None, None, "Здравствуйте!")
    assert out == "Добрый день! Интересно финансирование импорта?"


async def test_paraphrase_blank_input_shortcircuits(monkeypatch):
    called = {"n": 0}

    class _Boom:
        async def complete(self, **kwargs):
            called["n"] += 1
            return LLMResult(text="x", finish_reason="stop")

    monkeypatch.setattr(ai_engine_mod, "get_provider", lambda cfg: _Boom())
    out = await ai_engine.paraphrase_opener(None, None, "   ")
    assert out == "   "
    assert called["n"] == 0, "blank input must not call the LLM"
