"""Phase 18 — Anthropic (Claude) provider adapter.

Translates the internal representation (`system` + OpenAI-shape `messages[]` +
OpenAI-shape `tools[]`) into `messages.create` params and normalizes the native
Message into a plain `LLMResult`. Does NOT make Claude emulate OpenAI's object
graph (research anti-pattern) — `normalize_response` returns Anthropic-native
values (tool_use `input` stays a dict, `stop_reason` passes through); the shared
`LLMResult.finish_reason_normalized` gives callers a cross-provider view.

CRITICAL — role alternation: the 3–5 min debounce means a contact routinely sends
2+ inbound messages in a row, and get_conversation_history maps each to a separate
{"role":"user"} turn. Anthropic messages.create() returns 400 invalid_request_error
on non-alternating roles (research line 153). `_coalesce_roles` merges consecutive
same-role plain-text turns BEFORE the call so debounced multi-turn dialogs never 400.
OpenAI tolerates the same input, so only this adapter coalesces.
"""

from typing import Optional

from anthropic import AsyncAnthropic

from app.services.llm.base import LLMResult, ToolCall
from app.services.llm.capabilities import (
    anthropic_uses_adaptive_thinking,
    clamp_max_tokens,
    effort_to_anthropic_level,
    effort_to_budget,
)


def _to_anthropic_messages(messages: list) -> list:
    """Translate the provider-neutral second-pass turns into Anthropic content blocks.

    The wiring layer (18-04) appends a neutral assistant turn
    `{"role":"assistant","content":str,"tool_calls":[{id,name,arguments}]}` and
    neutral tool-result turns `{"role":"tool","tool_call_id","content"}`. Anthropic
    represents these as `tool_use` blocks on an assistant turn and `tool_result`
    blocks on a user turn respectively. Plain turns (no tool_calls / not role='tool')
    pass through unchanged so the normal dialogue path is untouched."""
    out: list = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else None
        tcs = m.get("tool_calls") if isinstance(m, dict) else None
        if role == "assistant" and tcs:
            blocks: list = []
            text = m.get("content")
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in tcs:
                args = tc.get("arguments")
                if isinstance(args, str):
                    import json as _json
                    try:
                        args = _json.loads(args or "{}")
                    except Exception:
                        args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": tc.get("name"),
                    "input": args or {},
                })
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id"),
                    "content": m.get("content", ""),
                }],
            })
        else:
            out.append(m)
    return out


def _coalesce_roles(messages: list) -> list:
    """Merge consecutive same-role messages so the final list strictly alternates
    user/assistant (Anthropic alternation constraint, research line 153).

    Only plain-string `content` turns are string-joined (with "\\n\\n"). Turns whose
    content is a list of content blocks (tool_result / tool_use, produced by the
    tool-block translation) are left untouched — they are never string-joined.
    """
    coalesced: list = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if (
            coalesced
            and coalesced[-1]["role"] == role
            and isinstance(coalesced[-1].get("content"), str)
            and isinstance(content, str)
        ):
            coalesced[-1]["content"] = coalesced[-1]["content"] + "\n\n" + content
        else:
            # copy so we never mutate the caller's dicts when merging later
            coalesced.append({"role": role, "content": content})
    return coalesced


def _translate_tools(tools: Optional[list]) -> list:
    """OpenAI {type:'function', function:{name, description, parameters}} ->
    Anthropic {name, description, input_schema}. Strips the function wrapper."""
    if not tools:
        return []
    out = []
    for t in tools:
        fn = t.get("function", t)  # tolerate already-flat shape
        out.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return out


class AnthropicProvider:
    """LLMProvider for Anthropic messages.create."""

    def __init__(self, *, api_key: str, model: str):
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key)

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
        """Assemble messages.create params.

        - system= is a TOP-LEVEL param (NOT a message) — Anthropic native.
        - max_tokens is REQUIRED and always present (clamped).
        - messages are role-coalesced to guarantee strict alternation.
        - tools reshaped to {name, description, input_schema} (no function wrapper).
        - temperature omitted entirely when None (0.0–1.0 range otherwise), and ALWAYS
          omitted whenever thinking is active in either shape (Anthropic 400s if
          temperature != 1 while thinking is on).
        - Claude-5-generation models (+ Opus 4.7/4.8): thinking={"type":"adaptive"} +
          a TOP-LEVEL `effort` param (low/medium/high) — no budget_tokens support.
        - Older models: thinking={"type":"enabled","budget_tokens":b} only when effort
          budget > 0 (manual budget; omitted for 'minimal'/None — Pitfall 1/2, budget
          < max_tokens).
        """
        clamped = clamp_max_tokens(self.model, max_tokens)

        # System messages, if any slipped into the list, are stripped: system is top-level.
        non_system = [m for m in messages if m.get("role") != "system"]
        # Translate provider-neutral tool turns (assistant tool_calls / role='tool')
        # into Anthropic tool_use / tool_result content blocks before coalescing.
        translated = _to_anthropic_messages(non_system)
        coalesced = _coalesce_roles(translated)

        params: dict = {
            "model": self.model,
            "max_tokens": clamped,
            "system": system,
            "messages": coalesced,
        }
        translated_tools = _translate_tools(tools)
        if translated_tools:
            params["tools"] = translated_tools
        # Anthropic rejects temperature != 1 whenever thinking is enabled (either shape),
        # so temperature and thinking are mutually exclusive (mirrors the OpenAI
        # reasoning-model exclusion, D-09).
        if anthropic_uses_adaptive_thinking(self.model):
            level = effort_to_anthropic_level(reasoning_effort)
            if level:
                params["thinking"] = {"type": "adaptive"}
                params["effort"] = level  # top-level, sibling of `thinking` — NOT nested
            elif temperature is not None:
                params["temperature"] = temperature
        else:
            budget = effort_to_budget(reasoning_effort, clamped)
            if budget > 0:
                params["thinking"] = {"type": "enabled", "budget_tokens": budget}
            elif temperature is not None:
                params["temperature"] = temperature
        return params

    def normalize_response(self, resp) -> LLMResult:
        """Anthropic Message -> LLMResult.

        text = concat of text blocks; tool_calls from tool_use blocks (arguments =
        the native `input` DICT — NOT json-dumped, so callers get a parsed object);
        finish_reason = raw stop_reason (pass-through; use
        LLMResult.finish_reason_normalized for the cross-provider view); usage maps
        input_tokens->prompt_tokens, output_tokens->completion_tokens.
        """
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in (resp.content or []):
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=getattr(block, "input", {}),  # native dict
                    )
                )

        usage = {}
        if getattr(resp, "usage", None) is not None:
            in_tok = getattr(resp.usage, "input_tokens", 0) or 0
            out_tok = getattr(resp.usage, "output_tokens", 0) or 0
            usage = {
                "prompt_tokens": in_tok,
                "completion_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
            }

        return LLMResult(
            text="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            finish_reason=getattr(resp, "stop_reason", None),
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
        resp = await self.client.messages.create(**params)
        return self.normalize_response(resp)
