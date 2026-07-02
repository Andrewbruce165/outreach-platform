"""Phase 18 — provider-neutral normalized result types + the provider protocol.

Both `OpenAIProvider` and `AnthropicProvider` translate ONE internal representation
(`system` prompt + `messages[]` + `tools[]` in the OpenAI-ish shape) into their native
request shape, and normalize the native response into a plain `LLMResult`. Callers
(ai_engine / warmup, wired in 18-04) only ever see `LLMResult` — they never touch a
provider-specific object graph (research anti-pattern: do NOT make Anthropic emulate
OpenAI's `.choices[0].message`).
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ToolCall:
    """One tool/function invocation requested by the model.

    `arguments` carries the model-supplied call arguments. Its concrete type is
    provider-native as produced by that provider's `normalize_response`:
      - OpenAI  → a JSON *string* (`function.arguments` is already a str),
      - Anthropic → a *dict* (`tool_use` blocks carry `input` as a parsed dict).
    The wiring layer (18-04) json.loads / passes through as appropriate; both are
    representable here so neither provider is forced to emulate the other.
    """

    id: str
    name: str
    arguments: Any


@dataclass
class LLMResult:
    """Normalized completion result shared across providers.

    finish_reason is kept as the provider's own stop signal on `normalize_response`
    (OpenAI: 'stop'|'length'|'tool_calls'; Anthropic: 'end_turn'|'max_tokens'|
    'tool_use'). Callers that rely on the empty-response 'length' retry can use
    `finish_reason_normalized` for a cross-provider view.
    """

    text: Optional[str]
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: dict = field(default_factory=dict)  # {prompt_tokens, completion_tokens, total_tokens}
    raw: Any = None

    @property
    def finish_reason_normalized(self) -> Optional[str]:
        """Cross-provider finish reason so the existing empty-guard retry logic
        (`finish_reason == 'length'`) works unchanged for either provider.

        Anthropic 'max_tokens' -> 'length'; 'tool_use' -> 'tool_calls';
        'end_turn'/'stop' -> 'stop'. OpenAI values pass through unchanged.
        """
        fr = self.finish_reason
        if fr is None:
            return None
        mapping = {
            "max_tokens": "length",
            "tool_use": "tool_calls",
            "end_turn": "stop",
        }
        return mapping.get(fr, fr)


class LLMProvider(Protocol):
    """Structural protocol every provider adapter satisfies."""

    async def complete(
        self,
        *,
        system: str,
        messages: list,
        tools: Optional[list],
        max_tokens: int,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResult:
        ...
