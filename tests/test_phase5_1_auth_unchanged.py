"""Phase 05.1 — verify app/utils/auth.py is FUNCTIONALLY unchanged.

This guards against accidentally regressing the HS256 / python-jose path
while editing comments. The full auth surface continues to be covered by
tests/test_auth_dep.py and tests/test_auth_api_key_cache.py.

References:
  - RESEARCH §"Common Pitfalls" Pitfall 2 — python-jose deprecated, PyJWT
    migration deferred to v2.
  - RESEARCH §"Common Pitfalls" Pitfall 3 — Supabase HS256 vs ES256 default;
    must pin to HS256 in dashboard; ES256/JWKS migration deferred to v2.
  - RESEARCH §"Out-of-scope reminders" — NO library/algorithm migration in 05.1.
"""
import pathlib
import re


AUTH_PY = pathlib.Path("app/utils/auth.py")


def test_auth_py_still_imports_jose():
    """python-jose import path preserved (Pitfall 2 — migration deferred to v2)."""
    src = AUTH_PY.read_text()
    assert "from jose import jwt" in src, (
        "python-jose import removed — Pitfall 2 migration is v2 work, not 05.1."
    )


def test_auth_py_still_uses_hs256_only():
    """JWT algorithms: ES256 (JWKS) primary + HS256 fallback.

    Supersedes the original Pitfall-3 "HS256-only" stance: the Phase 05.1-DEBUG
    (2026-05-23) rewrite migrated to Supabase ES256 verification via JWKS, keeping
    an HS256 fallback for legacy projects. Both algorithms must therefore appear
    in EXECUTABLE code (not just comments).
    """
    src = AUTH_PY.read_text()
    code_only = re.sub(r"#.*", "", src)
    assert '["ES256"]' in code_only, (
        "ES256 verification missing from executable code — Supabase ES256/JWKS "
        "migration (Phase 05.1-DEBUG) expected."
    )
    assert '["HS256"]' in code_only, (
        "HS256 fallback for legacy projects removed from JWT decode."
    )


def test_auth_py_pitfall_3_documented():
    """The Supabase HS256→ES256 decision is documented inline.

    The original Pitfall-3 "defer ES256 to v2" stance was superseded when Supabase
    made ES256 the default (Oct 2025). auth.py now documents the ES256/JWKS
    verification it implements; the anchors below pin that documentation.
    """
    src = AUTH_PY.read_text()
    assert "ES256" in src, "ES256 decision documentation missing from auth.py"
    assert "JWKS" in src, "JWKS verification documentation missing from auth.py"
    assert "Phase 05.1" in src, (
        "Phase 05.1 decision marker missing — research/handoff expects it as an anchor."
    )
