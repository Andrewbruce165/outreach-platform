"""Plan 04-04 Task 2: render_template Mustache-style + RU aliases + missing-var fallback.

Tests verify CAMP-10 / CAMP-11 (variable substitution per D-19).
"""

import logging

import pytest

from app.services.template import render_template

pytestmark = pytest.mark.asyncio


async def test_render_basic_name():
    """{{name}} resolves to contact.full_name."""
    contact = {
        "full_name": "Иван",
        "username": "ivan",
        "phone": "+71234567890",
        "source": "lm",
        "custom": {},
    }
    assert render_template("Привет, {{name}}!", contact) == "Привет, Иван!"


async def test_render_russian_alias_imya():
    """{{имя}} = same as {{name}}."""
    contact = {
        "full_name": "Анна",
        "username": None,
        "phone": "+71234567890",
        "source": "",
        "custom": {},
    }
    assert render_template("Привет, {{имя}}!", contact) == "Привет, Анна!"


async def test_render_with_spaces_inside_braces():
    """{{ name }} works (regex allows internal spaces)."""
    contact = {"full_name": "Test", "username": None, "phone": "", "source": "", "custom": {}}
    assert render_template("Hello {{ name }}!", contact) == "Hello Test!"


async def test_render_username_with_at_prefix():
    """{{username}} = '@' + contact.username if not empty."""
    contact = {"full_name": "X", "username": "ivan", "phone": "", "source": "", "custom": {}}
    assert render_template("Hi {{username}}", contact) == "Hi @ivan"


async def test_render_username_already_prefixed():
    """{{username}} не дублирует @ если значение уже с префиксом."""
    contact = {"full_name": "X", "username": "@ivan", "phone": "", "source": "", "custom": {}}
    assert render_template("Hi {{username}}", contact) == "Hi @ivan"


async def test_render_phone():
    """{{phone}} = contact.phone."""
    contact = {"full_name": "", "username": None, "phone": "+71234567890", "source": "", "custom": {}}
    assert render_template("Tel: {{phone}}", contact) == "Tel: +71234567890"


async def test_render_source():
    """{{source}} = contact.source."""
    contact = {"full_name": "X", "username": None, "phone": "", "source": "landing", "custom": {}}
    assert render_template("From {{source}}", contact) == "From landing"


async def test_render_custom_key():
    """{{custom.company}} = contact.custom['company']."""
    contact = {
        "full_name": "X",
        "username": None,
        "phone": "",
        "source": "",
        "custom": {"company": "AGS"},
    }
    assert render_template("Из {{custom.company}}", contact) == "Из AGS"


async def test_render_russian_alias_kompaniya_to_custom_company():
    """{{компания}} → custom.company (C-02 alias table)."""
    contact = {
        "full_name": "X",
        "username": None,
        "phone": "",
        "source": "",
        "custom": {"company": "AGS Foods"},
    }
    assert render_template("Из {{компания}}", contact) == "Из AGS Foods"


async def test_render_missing_var_returns_empty_string_plus_warning(caplog):
    """{{not_a_var}} → '' + logger.warning (D-19)."""
    contact = {"full_name": "X", "username": None, "phone": "", "source": "", "custom": {}}
    with caplog.at_level(logging.WARNING, logger="app.services.template"):
        result = render_template(
            "Hello {{not_a_var}}!",
            contact,
            campaign_id="c1",
            phone="+7",
        )
    # D-19: whitespace around an empty var is collapsed → "Hello!" (not "Hello !").
    assert result == "Hello!"
    assert any("not_a_var" in r.message for r in caplog.records)


async def test_render_missing_custom_key_returns_empty(caplog):
    """{{custom.notset}} → '' + warning."""
    contact = {"full_name": "X", "username": None, "phone": "", "source": "", "custom": {}}
    with caplog.at_level(logging.WARNING, logger="app.services.template"):
        result = render_template("V: {{custom.notset}}", contact)
    # D-19: trailing empty var + surrounding space collapses and is stripped → "V:".
    assert result == "V:"
    assert any("custom.notset" in r.message for r in caplog.records)


async def test_render_json_snippet_not_misparsed():
    """JSON-like '{"key": "value"}' — НЕ интерпретируется как переменная."""
    contact = {"full_name": "X", "username": None, "phone": "", "source": "", "custom": {}}
    template = 'Тут JSON: {"key": "value"} и {{name}}'
    result = render_template(template, contact)
    assert '{"key": "value"}' in result
    assert "X" in result


async def test_render_case_insensitive_NAME_equals_name():
    """{{NAME}} = {{name}} (re.IGNORECASE)."""
    contact = {"full_name": "Test", "username": None, "phone": "", "source": "", "custom": {}}
    assert render_template("Hi {{NAME}}", contact) == "Hi Test"


async def test_render_does_not_support_filters():
    """{{name | upper}} — regex НЕ matches (нет filters per C-03)."""
    contact = {"full_name": "anna", "username": None, "phone": "", "source": "", "custom": {}}
    # `name | upper` содержит пробелы и `|`, что не соответствует regex \w+(\.\w+)?
    result = render_template("Hi {{name | upper}}!", contact)
    # Должен остаться как есть (не заматчилось).
    assert "{{name | upper}}" in result


async def test_render_unicode_template_safe():
    """Шаблон с кириллицей + emoji — render не падает."""
    contact = {
        "full_name": "Иван",
        "username": None,
        "phone": "",
        "source": "",
        "custom": {},
    }
    result = render_template("Привет 🚀 {{name}}, добро пожаловать!", contact)
    assert "Иван" in result
    assert "🚀" in result
