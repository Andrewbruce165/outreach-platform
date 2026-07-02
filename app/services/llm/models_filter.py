"""Phase 18 — server-side family/capability filter for the live /v1/models list (D-08).

Pure functions, no I/O, no SDK imports. The provider `models.list()` endpoints return
EVERY model on the account (embeddings, whisper, tts, dall-e, realtime, transcribe,
moderation, deprecated instruct, …). The Settings UI must only offer chat-with-tools
compatible models, so the raw ids are filtered here before they reach the client.

D-08 family whitelist:
  - OpenAI: gpt-4o* / gpt-4.* / gpt-4-* / gpt-5* / o1 / o3 / o4  — MINUS anything whose id
    names a non-chat capability (embedding / whisper / tts / dall-e / realtime / audio /
    transcribe / image / moderation / search / instruct).
  - Anthropic: claude-* (every returned claude model is a chat-with-tools model).

`capabilities.filter_chat_models(provider, model_ids)` (the name the RED tests import) is a
thin alias over `filter_models` so there is exactly one filter implementation.
"""

import re

# D-08: chat-with-tools families only. Keep gpt-4o/gpt-4.x/gpt-4-x/gpt-5/o1/o3/o4 …
_OPENAI_KEEP = re.compile(r"^(gpt-4o|gpt-4\.|gpt-4-|gpt-5|o1|o3|o4)", re.I)
# … then drop any id that names a non-chat / non-tool-capable capability.
_OPENAI_DROP = re.compile(
    r"(embedding|whisper|tts|dall-e|dalle|realtime|audio|transcribe|image|moderation|search|instruct)",
    re.I,
)
_ANTHROPIC_KEEP = re.compile(r"^claude-", re.I)


def filter_models(model_ids: list[str], *, provider: str) -> list[str]:
    """Return only chat-with-tools-compatible model ids for `provider` (D-08).

    Order is preserved. `gpt-4o-realtime-preview`/`gpt-4o-transcribe` match _OPENAI_KEEP
    (they start `gpt-4o`) but are caught by _OPENAI_DROP, so they drop as intended.
    """
    out: list[str] = []
    for mid in model_ids:
        if provider == "anthropic":
            if _ANTHROPIC_KEEP.match(mid):
                out.append(mid)
        else:  # openai (the platform default provider)
            if _OPENAI_KEEP.match(mid) and not _OPENAI_DROP.search(mid):
                out.append(mid)
    return out
