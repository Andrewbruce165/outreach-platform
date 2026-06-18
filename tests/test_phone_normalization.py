"""Unit-тесты для app/utils/phone.normalize_to_e164.

Phase 2 (CONT-05): нормализация phone в каноническую E.164 форму перед
записью в contacts.phone и применением UNIQUE (workspace_id, phone).

См. .planning/phases/02-tg-accounts-contacts/02-RESEARCH.md §"Phone Normalization — E.164"
для полного списка edge-кейсов.
"""

import pytest

from app.utils.phone import (
    normalize_to_e164,
    contact_identity_key,
    is_username_key,
    username_from_key,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Каноническая RU
        ("+79001234567", "+79001234567"),
        # RU leading-8 heuristic (популярный российский формат)
        ("89001234567", "+79001234567"),
        # RU без + (юзер ввёл "просто цифры")
        ("79001234567", "+79001234567"),
        # Форматирование с +
        ("+7 (900) 123-45-67", "+79001234567"),
        # Форматирование + RU leading-8
        ("8 (900) 123-45-67", "+79001234567"),
        # Украина — НЕ применяем RU heuristic
        ("+380501234567", "+380501234567"),
        # Казахстан — 11 digits starting with +77 (НЕ ломаем)
        ("+77001234567", "+77001234567"),
        # США с форматированием
        ("+1 415 555 1212", "+14155551212"),
        # Различные мусорные / некорректные значения
        ("abc", None),
        ("", None),
        ("   ", None),
        # Too short (< 7 digits после плюса)
        ("+1234", None),
        # Too long (> 15 digits)
        ("+1234567890123456", None),
        # None on input
        (None, None),
        # +0 — too short
        ("+0", None),
    ],
)
def test_normalize_to_e164(raw, expected):
    assert normalize_to_e164(raw) == expected


# --- Outreach identity key (migration 025: send by @username) -----------------


@pytest.mark.parametrize(
    "phone,username,expected",
    [
        # Phone wins when present.
        ("+79001234567", None, "+79001234567"),
        ("+79001234567", "roman", "+79001234567"),
        # Username-only → '@username'.
        (None, "roman", "@roman"),
        # Leading '@' in stored username is tolerated (not double-prefixed).
        (None, "@roman", "@roman"),
        # Whitespace trimmed.
        (None, "  roman  ", "@roman"),
        # Neither → None.
        (None, None, None),
        ("", "", None),
        (None, "@", None),
    ],
)
def test_contact_identity_key(phone, username, expected):
    assert contact_identity_key(phone, username) == expected


@pytest.mark.parametrize(
    "key,expected",
    [
        ("@roman", True),
        ("+79001234567", False),
        ("", False),
        (None, False),
    ],
)
def test_is_username_key(key, expected):
    assert is_username_key(key) == expected


def test_username_from_key():
    assert username_from_key("@roman") == "roman"
    assert username_from_key("roman") == "roman"
