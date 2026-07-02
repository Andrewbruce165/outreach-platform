"""Wave-0 RED scaffold — key-level error taxonomy for D-06 fallback (LLMP-06).

Targets `app.services.llm.resolve.is_key_level_error` (NOT yet built). Deferred imports
keep --collect-only clean. Behavioural assertions FAIL now (RED) and pass once plan 18-02
lands the classifier.

D-06: runtime KEY-level errors (invalid key / permission / quota) → fall back to the
platform OpenAI default and flag the key invalid. Transient 429 rate-limits and 5xx must
NOT trigger a provider swap (Pitfall 6 — that would leak client traffic onto the platform
bill and mask the real load problem).

Constructing real SDK exceptions is awkward (they need httpx request/response objects), so
these tests use small fakes exposing the attributes the classifier inspects (`.code`,
`.status_code`) plus isinstance against the real SDK exception classes where feasible.
"""

import pytest


class _FakeErr:
    """Minimal stand-in exposing the attributes is_key_level_error inspects."""

    def __init__(self, code=None, status_code=None):
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


def test_key_level_errors_true():
    """True for: OpenAI AuthenticationError, PermissionDeniedError, RateLimitError with
    code 'insufficient_quota'; Anthropic AuthenticationError, PermissionDeniedError, and a
    402 billing APIStatusError."""
    import openai
    import anthropic
    from app.services.llm.resolve import is_key_level_error

    # OpenAI auth / permission → key-level (construct via __new__ to skip SDK ctor args).
    assert is_key_level_error(openai.AuthenticationError.__new__(openai.AuthenticationError)) is True
    assert is_key_level_error(openai.PermissionDeniedError.__new__(openai.PermissionDeniedError)) is True

    # OpenAI RateLimitError ONLY when code == 'insufficient_quota'.
    quota = openai.RateLimitError.__new__(openai.RateLimitError)
    quota.code = "insufficient_quota"
    assert is_key_level_error(quota) is True

    # Anthropic auth / permission → key-level.
    assert is_key_level_error(
        anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
    ) is True
    assert is_key_level_error(
        anthropic.PermissionDeniedError.__new__(anthropic.PermissionDeniedError)
    ) is True

    # Anthropic 402 billing APIStatusError → key-level.
    billing = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    billing.status_code = 402
    assert is_key_level_error(billing) is True


def test_transient_errors_false():
    """False for: a plain OpenAI RateLimitError (no insufficient_quota), a 500
    APIStatusError, and APIConnectionError — these degrade/retry, they do NOT flip
    the workspace to the platform key (Pitfall 6)."""
    import openai
    import anthropic
    from app.services.llm.resolve import is_key_level_error

    # Transient 429 (no insufficient_quota) → NOT key-level.
    transient = openai.RateLimitError.__new__(openai.RateLimitError)
    transient.code = None
    assert is_key_level_error(transient) is False

    # 5xx server error → NOT key-level.
    server_err = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    server_err.status_code = 500
    assert is_key_level_error(server_err) is False

    # Connection error → NOT key-level.
    assert is_key_level_error(
        openai.APIConnectionError.__new__(openai.APIConnectionError)
    ) is False

    # A completely unrelated exception is never key-level.
    assert is_key_level_error(ValueError("nope")) is False
