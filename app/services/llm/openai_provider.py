"""Phase 18 — OpenAI provider adapter.

Translates the internal representation (`system` + `messages[]` + OpenAI-shape
`tools[]`) into `chat.completions.create` params and normalizes the ChatCompletion
into a plain `LLMResult`. Param assembly mirrors ai_engine._build_completion_params
(the existing, battle-tested reasoning-aware shape) so routing the answerer through
this adapter in 18-04 is byte-identical for the default gpt-5-mini path.
"""

from typing import Optional

from openai import AsyncOpenAI

from app.services.llm.base import LLMResult, ToolCall
from app.services.llm.capabilities import (
    clamp_max_tokens,
    is_reasoning_model,
    supports_temperature,
)


def _to_openai_messages(messages: list) -> list:
    """Translate the provider-neutral second-pass assistant turn into OpenAI's
    native shape. The wiring layer (ai_engine second pass, 18-04) appends a neutral
    assistant turn `{"role":"assistant","content","tool_calls":[{id,name,arguments}]}`
    so the SAME messages list can drive either provider. OpenAI expects
    `tool_calls:[{id,type:"function",function:{name,arguments}}]` (arguments a str),
    so reshape any neutral tool_calls found. All other turns pass through unchanged."""
    import json as _json

    out: list = []
    for m in messages:
        tcs = m.get("tool_calls") if isinstance(m, dict) else None
        if tcs and isinstance(tcs, list) and tcs and "function" not in tcs[0]:
            native = []
            for tc in tcs:
                args = tc.get("arguments")
                if not isinstance(args, str):
                    args = _json.dumps(args or {}, ensure_ascii=False)
                native.append({
                    "id": tc.get("id"),
                    "type": "function",
                    "function": {"name": tc.get("name"), "arguments": args},
                })
            new_msg = dict(m)
            new_msg["tool_calls"] = native
            out.append(new_msg)
        else:
            out.append(m)
    return out


class OpenAIProvider:
    """LLMProvider for OpenAI chat.completions."""

    def __init__(self, *, api_key: str, model: str):
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key)

    def build_params(
        self,
        *,
        system: str,
        messages: list,
        tools: Optional[list],
        max_tokens: int,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
    ) -> dict:
        """Assemble chat.completions params.

        - system stays as messages[0] role='system' (OpenAI native).
        - max_completion_tokens = clamped budget (reasoning floor >=4000, D-10).
        - reasoning_effort only for reasoning models (gpt-4o* 400s on it).
        - temperature only when supported (reasoning models 400 on it) AND not None.
        - tools kept in the {type:'function', function:{...}} shape + tool_choice='auto'.
          OpenAI tolerates consecutive same-role turns — no coalescing needed.
        """
        model = self.model
        chat_messages: list = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend(_to_openai_messages(messages))

        params: dict = {
            "model": model,
            "messages": chat_messages,
            "max_completion_tokens": clamp_max_tokens(model, max_tokens),
        }
        if is_reasoning_model(model) and reasoning_effort is not None:
            params["reasoning_effort"] = reasoning_effort
        if supports_temperature(model) and temperature is not None:
            params["temperature"] = temperature
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        return params

    def normalize_response(self, resp) -> LLMResult:
        """ChatCompletion -> LLMResult. OpenAI tool_call.function.arguments is
        already a JSON string; finish_reason ('stop'|'length'|'tool_calls') and
        usage pass through unchanged."""
        choice = resp.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        for tc in (message.tool_calls or []):
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,  # already a JSON string
                )
            )

        usage = {}
        if getattr(resp, "usage", None) is not None:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }

        return LLMResult(
            text=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            raw=resp,
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list,
        tools: Optional[list] = None,
        max_tokens: int = 4000,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMResult:
        params = self.build_params(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        resp = await self.client.chat.completions.create(**params)
        return self.normalize_response(resp)
