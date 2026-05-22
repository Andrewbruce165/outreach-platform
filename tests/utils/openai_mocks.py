"""Phase 4 test util — mock OpenAI Chat Completions response with tool_calls.

Used by tests/test_builtin_tools.py, tests/test_campaign_webhooks.py,
tests/test_custom_tools_wiring.py to patch ai_engine.client.chat.completions.create.
"""

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock


@dataclass
class _MockFunction:
    name: str
    arguments: str  # JSON string


@dataclass
class MockToolCall:
    id: str
    function_name: str
    function_arguments: str  # JSON string

    def __post_init__(self):
        # Build a function-shaped object that mirrors OpenAI SDK
        # (response.choices[0].message.tool_calls[i].function.{name,arguments}).
        self.function = _MockFunction(
            name=self.function_name, arguments=self.function_arguments
        )


@dataclass
class MockResponseMessage:
    content: str | None
    tool_calls: list | None = None


@dataclass
class MockChatChoice:
    message: MockResponseMessage
    finish_reason: str = "stop"


@dataclass
class MockChatResponse:
    choices: list[MockChatChoice]


def make_openai_response(
    *,
    text_content: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
) -> MockChatResponse:
    """Build a mock OpenAI Chat Completions response.

    Args:
        text_content: response_message.content (for Q3 text + tool_call случая).
        tool_calls: list of {"name": "...", "arguments": "{...}"} dicts.
        finish_reason: choice.finish_reason ("stop", "tool_calls").

    Returns: MockChatResponse with single choice.
    """
    mocked_tool_calls = None
    if tool_calls:
        mocked_tool_calls = [
            MockToolCall(
                id=f"call_{i}",
                function_name=tc["name"],
                function_arguments=tc["arguments"],
            )
            for i, tc in enumerate(tool_calls)
        ]
    return MockChatResponse(
        choices=[
            MockChatChoice(
                message=MockResponseMessage(
                    content=text_content, tool_calls=mocked_tool_calls
                ),
                finish_reason=finish_reason,
            )
        ]
    )


def patched_openai_client(monkeypatch, *responses: MockChatResponse):
    """Helper to monkey-patch ai_engine's OpenAI client.chat.completions.create.

    Pass one MockChatResponse for a single call. Pass multiple to get them
    returned in order (useful for two-pass tool-call → final-reply flow).
    """
    queue = list(responses)

    async def fake_create(*args, **kwargs):
        if not queue:
            raise RuntimeError("patched_openai_client: queue exhausted")
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)

    monkeypatch.setattr(
        "app.services.ai_engine.client.chat.completions.create",
        AsyncMock(side_effect=fake_create),
    )
