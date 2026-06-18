"""Phone normalization to E.164 format.

RU-centric heuristics + ITU E.164 spec.
Phase 2 (CONT-05): canonical phone storage in `contacts.phone`.

NB: We do NOT use `phonenumbers` library — overkill for v1 (5+ MB data,
slow startup). Pure regex is enough for RU/CIS-focus use cases.
См. .planning/phases/02-tg-accounts-contacts/02-RESEARCH.md §"Phone Normalization — E.164".
"""

import re
from typing import Optional

_NON_DIGIT = re.compile(r"\D+")
_E164_RE = re.compile(r"^\+\d{7,15}$")


def normalize_to_e164(raw: Optional[str]) -> Optional[str]:
    """Normalize phone string to E.164 format (`+XXXXXXXXX`).

    Rules:
    1. Strip all non-digit (preserves whether leading `+` was present in
       original string for the RU heuristic gate; removes everything else).
    2. RU heuristic: 11-digit without leading `+` starting with 8 → replace
       leading 8 with 7. Это покрывает legacy российский формат `89001234567`.
    3. Add leading `+` if missing.
    4. Validate ITU E.164 spec: `+` followed by 7..15 digits.

    Returns None if invalid (empty / not digits / wrong length).

    Examples:
        normalize_to_e164("+79001234567")        → "+79001234567"
        normalize_to_e164("89001234567")          → "+79001234567" (RU leading-8)
        normalize_to_e164("79001234567")          → "+79001234567"
        normalize_to_e164("+7 (900) 123-45-67")   → "+79001234567"
        normalize_to_e164("+380501234567")        → "+380501234567"
        normalize_to_e164("abc")                  → None
        normalize_to_e164("")                     → None
    """
    if not raw:
        return None
    had_plus = raw.lstrip().startswith("+")
    digits = _NON_DIGIT.sub("", raw)
    if not digits:
        return None
    # RU heuristic: применяется только к 11-digit строкам без явного `+`,
    # начинающимся с 8. Это безопасно — Казахстан/+77 идёт через had_plus,
    # либо 12-значное (+7 + 10 цифр после strip = 11) — это разные кейсы.
    if not had_plus and len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    e164 = "+" + digits
    if not _E164_RE.match(e164):
        return None
    return e164


# --- Outreach identity key (migration 025: send by @username) -----------------
# The whole outreach pipeline (rotation assignment, queue item, conversation,
# contacts_cache) keys recipients by a single string stored in the *_phone
# columns. For phone contacts that key is the E.164 phone (`+7…`). For contacts
# that have only a Telegram username, the key is `@username`. The send/resolve
# layer branches on the leading `@` to pick ResolveUsername vs ResolvePhone.


def contact_identity_key(
    phone: Optional[str], username: Optional[str]
) -> Optional[str]:
    """Build the pipeline identity key for a contact.

    Phone wins when present (it's the richer, more stable identity). Otherwise
    fall back to `@username`. Returns None if neither is usable.
    """
    if phone:
        return phone
    if username:
        uname = username.strip().lstrip("@")
        if uname:
            return "@" + uname
    return None


def is_username_key(key: Optional[str]) -> bool:
    """True if the identity key addresses a Telegram username (`@…`)."""
    return bool(key) and key.startswith("@")


def username_from_key(key: str) -> str:
    """Extract the bare username (no leading `@`) from a username identity key."""
    return key.lstrip("@")
