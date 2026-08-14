"""Unit tests for `app.utils.phone.sanitize_username`.

Guard for the send-time resolve ladder (bug send-ignores-import-username,
2026-08-14): tier-2 may now resolve the handle the operator DECLARED at CSV
import time, so a garbage value must be rejected BEFORE it costs a
ResolveUsername call. Rejection means "skip tier-2", never "not registered".
"""
import pytest

from app.utils.phone import sanitize_username


@pytest.mark.parametrize("raw,expected", [
    ("Wirbelwind84", "Wirbelwind84"),
    ("@Wirbelwind84", "Wirbelwind84"),
    ("  @fp_gt  ", "fp_gt"),
    ("gansteroid", "gansteroid"),
    ("Oleg_Y1", "Oleg_Y1"),
    ("mirandd1966", "mirandd1966"),
    ("a" * 32, "a" * 32),
])
def test_accepts_plausible_handles(raw, expected):
    assert sanitize_username(raw) == expected


@pytest.mark.parametrize("raw", [
    None,
    "",
    "   ",
    "@",
    "@   ",
    # CSV/ETL placeholders — prod folder 6595094a literally stored "None".
    "None", "none", "NONE", "null", "NULL", "nil", "nan", "undefined", "N/A", "-",
    # Not plausible handles.
    "+79222272580",
    "1starts_with_digit",
    "_leading_underscore",
    "has space",
    "has-dash",
    "with.dot",
    "почта",
    "ab",           # too short
    "a" * 33,       # too long
])
def test_rejects_garbage(raw):
    assert sanitize_username(raw) is None


def test_rejection_is_not_a_verdict():
    """Documented semantics: a None return is 'skip the tier-2 attempt'. The caller
    (resolve_contact) must still run tier-3 — asserted end-to-end in
    tests/test_send.py::test_garbage_declared_username_skips_tier2_not_finalized."""
    assert sanitize_username("None") is None
