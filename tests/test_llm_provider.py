"""Wave-0 RED scaffold — provider adapter translation, both directions (LLMP-11).

Targets `app.services.llm.openai_provider.OpenAIProvider` and
`app.services.llm.anthropic_provider.AnthropicProvider` (NOT yet built). Deferred imports
keep --collect-only clean. Behavioural assertions FAIL now (RED) and pass once plan 18-02
lands the adapters.

The adapter's job: translate ONE internal representation (system prompt + messages[] +
tools[] in the OpenAI-ish shape) into each provider's NATIVE request shape, and normalise
the response into a plain LLMResult{text, tool_calls[], finish_reason, usage}. Do NOT make
Anthropic emulate OpenAI's object graph (research anti-pattern).

Translation map (research §Pattern 1):
  - system: OpenAI messages[0] role=system  vs  Anthropic top-level system= param
  - tools:  OpenAI {type:function, function:{name,...}}  vs  Anthropic {name, description, input_schema}
  - max tokens: OpenAI max_completion_tokens (reasoning)  vs  Anthropic max_tokens (required)
  - roles MUST alternate for Anthropic (research line 153) — the debounce case produces
    consecutive same-role turns from get_conversation_history and MUST be coalesced.
"""

import pytest

pytestmark = pytest.mark.asyncio


# Internal representation shared by the translation tests.
_SYSTEM = "You are a helpful sales agent."
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "mark_as_lead",
            "description": "Mark this conversation as a qualified lead.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    }
]


async def test_openai_adapter_builds_native_params():
    """Internal {system, messages, tools} → OpenAI chat.completions params:
    system stays as messages[0].role=='system', tools keep the {type:'function',
    function:{...}} wrapper, and a reasoning model uses max_completion_tokens."""
    from app.services.llm.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test", model="gpt-5-mini")
    params = provider.build_params(
        system=_SYSTEM,
        messages=[{"role": "user", "content": "Привет"}],
        tools=_TOOLS,
        max_tokens=4000,
    )

    assert params["messages"][0]["role"] == "system"
    assert params["messages"][0]["content"] == _SYSTEM
    # Tools keep the OpenAI function wrapper.
    assert params["tools"][0]["type"] == "function"
    assert params["tools"][0]["function"]["name"] == "mark_as_lead"
    # Reasoning model → max_completion_tokens (not max_tokens).
    assert "max_completion_tokens" in params
    assert "max_tokens" not in params


async def test_anthropic_adapter_builds_native_params():
    """Same internal input → Anthropic messages params: top-level system= (NOT a system
    message), max_tokens present (required), tools shaped {name, description, input_schema}
    with NO type:'function' wrapper, and NO temperature key when temperature is None."""
    from app.services.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-test", model="claude-sonnet-4-5")
    params = provider.build_params(
        system=_SYSTEM,
        messages=[{"role": "user", "content": "Привет"}],
        tools=_TOOLS,
        max_tokens=4000,
        temperature=None,
    )

    # System is a top-level param, not a message role.
    assert params["system"] == _SYSTEM
    assert all(m["role"] != "system" for m in params["messages"])
    # max_tokens is required and present.
    assert params["max_tokens"] == 4000
    # Tools use Anthropic's input_schema shape, no function wrapper.
    tool = params["tools"][0]
    assert tool["name"] == "mark_as_lead"
    assert "input_schema" in tool
    assert "type" not in tool  # no {type:'function'} wrapper
    assert "function" not in tool
    # temperature omitted entirely when None.
    assert "temperature" not in params


async def test_anthropic_thinking_excludes_temperature():
    """Anthropic rejects any request where `thinking` is enabled AND `temperature`
    is set to something other than 1 (400 invalid_request_error, hit in 18-05 live
    UAT: reasoning_effort='medium' + temperature=0.4 -> 400, contact never got a
    reply). When reasoning_effort maps to a budget > 0, thinking must be enabled
    and temperature must be omitted entirely, even if the caller passed one."""
    from app.services.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-test", model="claude-sonnet-4-5")
    params = provider.build_params(
        system=_SYSTEM,
        messages=[{"role": "user", "content": "Привет"}],
        tools=None,
        max_tokens=4000,
        temperature=0.4,
        reasoning_effort="medium",
    )

    assert "thinking" in params
    assert params["thinking"]["type"] == "enabled"
    assert "temperature" not in params


async def test_anthropic_coalesces_consecutive_same_role():
    """The debounce case: get_conversation_history can produce two inbound (user) turns
    in a row. Anthropic 400s on non-alternating roles (research line 153), so the adapter
    MUST coalesce consecutive same-role turns into strictly alternating messages.

    Input internal messages: user 'a', user 'b', assistant 'c', user 'd'.
    Expected Anthropic messages handed to messages.create: strictly alternating
    user/assistant, the two leading user turns merged into one containing BOTH 'a' and 'b'
    joined by '\\n\\n', final list = [user, assistant, user] (length 3)."""
    from unittest.mock import AsyncMock
    from app.services.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-test", model="claude-sonnet-4-5")

    internal_messages = [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"},
    ]

    # Capture the params handed to the native client.
    mock_create = AsyncMock(return_value=_fake_anthropic_message(text="ok"))
    provider.client.messages.create = mock_create

    await provider.complete(
        system=_SYSTEM, messages=internal_messages, tools=[], max_tokens=4000
    )

    sent_messages = mock_create.call_args.kwargs["messages"]

    # Strictly alternating: no two consecutive entries share a role.
    roles = [m["role"] for m in sent_messages]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles
    # The two leading user turns merged into one containing BOTH 'a' and 'b'.
    assert len(sent_messages) == 3
    assert roles == ["user", "assistant", "user"]
    first_content = _as_text(sent_messages[0]["content"])
    assert "a" in first_content and "b" in first_content
    assert "\n\n" in first_content


async def test_anthropic_normalizes_response():
    """A fake Anthropic Message (text block + tool_use block, stop_reason='tool_use',
    usage input/output tokens) normalises to LLMResult{text, tool_calls[], finish_reason,
    usage}."""
    from app.services.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-test", model="claude-sonnet-4-5")

    fake = _fake_anthropic_message(
        text="Здравствуйте!",
        tool_use={"name": "mark_as_lead", "input": {"reason": "interested"}},
        stop_reason="tool_use",
        input_tokens=120,
        output_tokens=42,
    )

    result = provider.normalize_response(fake)

    assert result.text == "Здравствуйте!"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "mark_as_lead"
    assert result.tool_calls[0].arguments == {"reason": "interested"}
    assert result.finish_reason == "tool_use"
    # Usage normalised to the common shape (Anthropic input/output → prompt/completion).
    assert result.usage["prompt_tokens"] == 120
    assert result.usage["completion_tokens"] == 42


# ─── local fakes (no anthropic Message ctor needed) ──────────────────────────

def _as_text(content):
    """Anthropic message content may be a str or a list of content blocks."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            parts.append(block.get("text", "") or str(block.get("content", "")))
        else:
            parts.append(getattr(block, "text", "") or "")
    return "\n\n".join(parts)


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeAnthropicMessage:
    def __init__(self, content, stop_reason, usage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


def _fake_anthropic_message(
    *, text=None, tool_use=None, stop_reason="end_turn",
    input_tokens=0, output_tokens=0,
):
    blocks = []
    if text is not None:
        blocks.append(_Block(type="text", text=text))
    if tool_use is not None:
        blocks.append(
            _Block(type="tool_use", id="toolu_1",
                   name=tool_use["name"], input=tool_use["input"])
        )
    return _FakeAnthropicMessage(
        content=blocks,
        stop_reason=stop_reason,
        usage=_Usage(input_tokens, output_tokens),
    )
