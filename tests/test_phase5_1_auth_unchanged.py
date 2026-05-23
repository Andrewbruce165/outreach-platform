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
    """HS256 still the single algorithm (Pitfall 3 — ES256 deferred to v2).

    The JWT decode call must list algorithms=["HS256"]. The string "ES256"
    may appear in comments (documentation of the deferred decision) but
    must not appear in executable code.
    """
    src = AUTH_PY.read_text()
    assert 'algorithms=["HS256"]' in src, (
        "HS256 algorithm pinning removed from JWT decode."
    )
    # Strip line comments (# ...) and verify ES256 only lives there.
    code_only = re.sub(r"#.*", "", src)
    assert "ES256" not in code_only, (
        "ES256 found in executable code (not just comments) — Pitfall 3 "
        "migration is v2 work, not 05.1."
    )


def test_auth_py_pitfall_3_documented():
    """Pitfall 3 decision documented inline.

    handoff bundle (plan 05.1-05 lovable-handoff/AGENTS.md) cross-references
    this comment block; both anchors must be present.
    """
    src = AUTH_PY.read_text()
    assert "Pitfall 3" in src, "Pitfall 3 anchor missing from auth.py"
    assert "lovable-handoff" in src, (
        "lovable-handoff cross-reference missing from auth.py — plan 05.1-05 "
        "AGENTS.md links here."
    )
    assert "Phase 05.1 decision" in src, (
        "Phase 05.1 decision marker missing — handoff/research expects it as an anchor."
    )
