"""Wave 0 stubs — Plan 04-04 Task 1 (template renderer).

Covers CAMP-10 / CAMP-11 (variable substitution).
Real test bodies в Task 2 (TDD GREEN после implementation).
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_render_basic_name():
    """{{name}} resolves to contact.full_name."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_russian_alias_imya():
    """{{имя}} = same as {{name}}."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_with_spaces_inside_braces():
    """{{ name }} works (regex allows internal spaces)."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_username_with_at_prefix():
    """{{username}} = '@' + contact.username if not empty."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_phone():
    """{{phone}} = contact.phone."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_source():
    """{{source}} = contact.source."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_custom_key():
    """{{custom.company}} = contact.custom['company']."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_russian_alias_kompaniya_to_custom_company():
    """{{компания}} → custom.company (C-02 alias table)."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_missing_var_returns_empty_string_plus_warning(caplog):
    """{{not_a_var}} → '' + logger.warning (D-19)."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_missing_custom_key_returns_empty():
    """{{custom.notset}} → '' + warning."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_json_snippet_not_misparsed():
    """Текст содержит JSON-like '{"key": "value"}' — НЕ должен интерпретироваться как переменная."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_case_insensitive_NAME_equals_name():
    """{{NAME}} = {{name}} (re.IGNORECASE)."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_does_not_support_filters():
    """{{name | upper}} — НЕ resolves (strict, no filters per C-03)."""
    pytest.skip("Wave 0 stub — Task 2 implements")


async def test_render_unicode_template_safe():
    """Шаблон с кириллицей + emoji — render не падает."""
    pytest.skip("Wave 0 stub — Task 2 implements")
