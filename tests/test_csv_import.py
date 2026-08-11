"""Unit-тесты для app/services/csv_import.

Покрывает 9 pitfall'ов из RESEARCH §"CSV Import Pitfalls":
BOM, delimiters, cp1251, кавычки, no-header heuristic, и mapping.
"""

import pytest

from app.services.csv_import import (
    apply_import,
    parse_preview,
    resolve_mapping,
    suggest_mapping,
)


# ─── parse_preview ───────────────────────────────────────────────────────────


def test_parse_preview_basic_utf8():
    data = b"phone,name\n+79001234567,John\n+79009998877,Jane\n"
    result = parse_preview(data)
    assert result["columns"] == ["phone", "name"]
    assert result["delimiter"] == ","
    assert result["encoding"] == "utf-8-sig"
    assert len(result["sample_rows"]) == 2
    assert result["sample_rows"][0]["phone"] == "+79001234567"
    assert result["sample_rows"][1]["name"] == "Jane"


def test_parse_preview_strips_utf8_bom():
    # BOM (0xEF 0xBB 0xBF) — Excel "Save As CSV UTF-8" сохраняет с BOM.
    data = "﻿phone,name\n+79001234567,John\n".encode("utf-8")
    result = parse_preview(data)
    # encoding utf-8-sig strip'ит BOM автоматически
    assert result["columns"][0] == "phone"
    assert "﻿" not in result["columns"][0]


def test_parse_preview_detects_semicolon_delimiter():
    data = b"phone;name\n+79001234567;John\n+79009998877;Jane\n"
    result = parse_preview(data)
    assert result["delimiter"] == ";"
    assert result["columns"] == ["phone", "name"]


def test_parse_preview_cp1251_fallback():
    # Russian Excel "Save As CSV (MS-DOS)" → cp1251.
    data = "телефон,имя\n+79001234567,Иван\n".encode("cp1251")
    result = parse_preview(data)
    assert result["encoding"] == "cp1251"
    assert "телефон" in result["columns"]
    assert result["sample_rows"][0]["имя"] == "Иван"


def test_parse_preview_empty_file_raises():
    with pytest.raises(ValueError, match="EMPTY_FILE"):
        parse_preview(b"")


def test_parse_preview_only_header():
    # Файл только с header'ом → пустой sample_rows, но без ошибки.
    result = parse_preview(b"phone,name\n")
    assert result["columns"] == ["phone", "name"]
    assert result["sample_rows"] == []


def test_parse_preview_handles_quoted_field_with_comma():
    # Поле с запятой внутри кавычек.
    data = b'phone,name\n+79001234567,"Smith, John"\n'
    result = parse_preview(data)
    assert result["sample_rows"][0]["name"] == "Smith, John"


def test_parse_preview_max_rows_limit():
    # Файл с 100 строк → возвращает максимум max_rows + header.
    body = b"phone\n" + b"\n".join(
        f"+790000000{i:02d}".encode() for i in range(100)
    )
    result = parse_preview(body, max_rows=10)
    assert len(result["sample_rows"]) == 10


# ─── suggest_mapping ─────────────────────────────────────────────────────────


def test_suggest_mapping_matches_eng_aliases():
    result = suggest_mapping(["phone", "name", "source"])
    assert result == {"0": "phone", "1": "full_name", "2": "source"}


def test_suggest_mapping_matches_ru_aliases():
    result = suggest_mapping(["телефон", "имя", "источник"])
    assert result == {"0": "phone", "1": "full_name", "2": "source"}


def test_suggest_mapping_username_alias():
    result = suggest_mapping(["username", "telegram"])
    # username → username, telegram (alias) → username тоже
    assert result["0"] == "username"


def test_suggest_mapping_empty_for_unknown():
    result = suggest_mapping(["foo", "bar"])
    assert result == {}


def test_suggest_mapping_case_insensitive():
    result = suggest_mapping(["Phone", "NAME"])
    assert result == {"0": "phone", "1": "full_name"}


# ─── apply_import ────────────────────────────────────────────────────────────


def test_apply_import_normalizes_phone():
    data = b"phone,name\n89001234567,John\n+79009998877,Jane\n"
    result = apply_import(
        data, mapping={"0": "phone", "1": "full_name"}, delimiter=",", encoding="utf-8-sig"
    )
    assert result["total"] == 2
    assert result["skipped_invalid"] == 0
    assert len(result["rows_to_insert"]) == 2
    # RU leading-8 → +7
    assert result["rows_to_insert"][0]["phone"] == "+79001234567"
    assert result["rows_to_insert"][0]["full_name"] == "John"
    assert result["rows_to_insert"][1]["phone"] == "+79009998877"


def test_apply_import_skips_invalid_phone():
    data = b"phone,name\n+7abc,John\n+79009998877,Jane\n"
    result = apply_import(
        data, mapping={"0": "phone", "1": "full_name"}, delimiter=",", encoding="utf-8-sig"
    )
    assert result["total"] == 2
    assert result["skipped_invalid"] == 1
    assert result["skipped_invalid_reasons"][0]["reason"] == "invalid_phone"
    # "+7abc" нормализуется в "+7" (одна цифра) → too short → None
    assert len(result["rows_to_insert"]) == 1
    assert result["rows_to_insert"][0]["phone"] == "+79009998877"


def test_apply_import_skips_no_phone_no_username():
    data = b"phone,name\n,John\n+79009998877,Jane\n"
    result = apply_import(
        data, mapping={"0": "phone", "1": "full_name"}, delimiter=",", encoding="utf-8-sig"
    )
    assert result["skipped_invalid"] == 1
    assert result["skipped_invalid_reasons"][0]["reason"] == "no_phone_no_username"
    assert len(result["rows_to_insert"]) == 1


def test_apply_import_mapping_invalid_raises():
    data = b"name,source\nJohn,referral\n"
    # Mapping без phone и без username → MAPPING_INVALID.
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        apply_import(
            data,
            mapping={"0": "full_name", "1": "source"},
            delimiter=",",
            encoding="utf-8-sig",
        )


def test_apply_import_custom_fields_into_jsonb():
    data = b"phone,company,city\n+79001234567,Acme,Moscow\n"
    result = apply_import(
        data,
        mapping={"0": "phone", "1": "custom.company", "2": "custom.city"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert len(result["rows_to_insert"]) == 1
    rec = result["rows_to_insert"][0]
    assert rec["custom"]["company"] == "Acme"
    assert rec["custom"]["city"] == "Moscow"


def test_apply_import_username_strips_at():
    data = b"username\n@johndoe\n"
    result = apply_import(
        data,
        mapping={"0": "username"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert result["rows_to_insert"][0]["username"] == "johndoe"


# ─── mapping key contract (regression: 2026-08-11 name-keyed mapping bug) ────
#
# Root cause: the UI keyed `mapping` by column NAME while the backend contract is
# column INDEX-as-string; apply_import did int(key) and silently `continue`d, so
# every user-picked field was dropped (77 contacts imported with username only).
# See .planning/debug/resolved/csv-contact-mapping-only-username-saved.md


def test_apply_import_accepts_name_keyed_mapping():
    data = "Телефон,Имя,Источник\n+79001234567,иван петров,barter\n".encode("utf-8")
    result = apply_import(
        data,
        mapping={"Телефон": "phone", "Имя": "full_name", "Источник": "source"},
        delimiter=",",
        encoding="utf-8",
    )
    assert result["unresolved_mapping_keys"] == []
    rec = result["rows_to_insert"][0]
    assert rec["phone"] == "+79001234567"
    assert rec["full_name"] == "Иван Петров"
    assert rec["source"] == "barter"


def test_apply_import_name_keyed_mapping_is_case_insensitive():
    data = b"Phone,Full Name\n+79001234567,John Doe\n"
    result = apply_import(
        data,
        mapping={"phone": "phone", "full name": "full_name"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert result["unresolved_mapping_keys"] == []
    assert result["rows_to_insert"][0]["phone"] == "+79001234567"
    assert result["rows_to_insert"][0]["full_name"] == "John Doe"


def test_apply_import_mixed_index_and_name_keys():
    data = b"phone,company\n+79001234567,Acme\n"
    result = apply_import(
        data,
        mapping={"0": "phone", "company": "custom.company"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert result["unresolved_mapping_keys"] == []
    assert result["rows_to_insert"][0]["custom"]["company"] == "Acme"


def test_apply_import_custom_field_via_name_key_round_trips():
    data = "phone,Компания,Город\n+79001234567,Акме,Москва\n".encode("utf-8")
    result = apply_import(
        data,
        mapping={
            "phone": "phone",
            "Компания": "custom.company",
            "Город": "custom.city",
        },
        delimiter=",",
        encoding="utf-8",
    )
    rec = result["rows_to_insert"][0]
    assert rec["custom"] == {"company": "Акме", "city": "Москва"}


def test_apply_import_reports_unresolvable_key_instead_of_silent_drop():
    data = b"phone,name\n+79001234567,John\n"
    result = apply_import(
        data,
        mapping={"0": "phone", "no_such_column": "full_name", "42": "source"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    # phone still imports…
    assert result["rows_to_insert"][0]["phone"] == "+79001234567"
    assert result["rows_to_insert"][0]["full_name"] is None
    # …but the bad keys are surfaced, not swallowed.
    assert sorted(result["unresolved_mapping_keys"]) == ["42", "no_such_column"]


def test_apply_import_reports_unknown_target_field():
    data = b"phone,whatever\n+79001234567,x\n"
    result = apply_import(
        data,
        mapping={"0": "phone", "1": "not_a_field"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert result["unknown_mapping_fields"] == ["1=not_a_field"]
    assert result["rows_to_insert"][0]["phone"] == "+79001234567"


def test_apply_import_raises_when_phone_key_unresolvable():
    """Guard: don't import 77 empty contacts when the phone key matches nothing."""
    data = b"Tel,Name\n+79001234567,John\n"
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        apply_import(
            data,
            mapping={"telephone": "phone"},  # neither an index nor a real column
            delimiter=",",
            encoding="utf-8-sig",
        )


def test_apply_import_duplicate_column_names_resolve_to_first():
    data = b"phone,phone\n+79001234567,+79009998877\n"
    result = apply_import(
        data,
        mapping={"phone": "phone"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert result["rows_to_insert"][0]["phone"] == "+79001234567"


def test_resolve_mapping_prefers_index_over_name():
    # A CSV whose header is literally "1" must not shadow the canonical index key.
    resolved, unresolved, unknown = resolve_mapping({"1": "phone"}, ["1", "name"])
    assert resolved == [(1, "phone")]
    assert unresolved == [] and unknown == []


def test_apply_import_username_only_record_valid():
    # Контакт только с username (без phone) — valid.
    data = b"username,name\n@johndoe,John Doe\n"
    result = apply_import(
        data,
        mapping={"0": "username", "1": "full_name"},
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert len(result["rows_to_insert"]) == 1
    assert result["rows_to_insert"][0]["username"] == "johndoe"
    assert result["rows_to_insert"][0]["phone"] is None
