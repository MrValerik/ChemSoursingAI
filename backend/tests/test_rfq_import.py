"""Разбор списка позиций из XLSX/CSV.

Файл приходит от закупщика, а не из нашей выгрузки: кодировка чужая,
заголовки называются как придётся, единицы записаны словом, а часть ячеек
осталась формулами. Разбор обязан быть предсказуемым на всём этом и не
превращать одну плохую строку в отказ от файла.
"""

import csv
import io

import pytest

from app.services.rfq_import import (
    MAX_ROWS,
    RfqImportError,
    parse_import_file,
)


def _csv(rows: list[list[str]], *, encoding: str = "utf-8", delimiter: str = ",") -> bytes:
    # Через csv.writer, а не join: настоящий файл берёт в кавычки значение
    # с разделителем внутри, и «98,5» в CSV с запятой остаётся одним полем.
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode(encoding)


def _xlsx(rows: list[list[object]]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


_HEADER = ["Название", "CAS", "Объём", "Единица", "Чистота", "Incoterms", "Страны"]


def _row(preview, number: int):
    return next(item for item in preview.rows if item.row == number)


# --- штатный разбор ---


def test_plain_csv_is_read_row_by_row():
    payload = _csv(
        [
            _HEADER,
            ["Бетаин", "107-43-7", "500", "кг", "99", "FOB;DAP", "Китай"],
            ["Глицин", "56-40-6", "2", "т", "98,5", "CIP", "Индия;Китай"],
        ]
    )
    preview = parse_import_file("list.csv", payload)

    assert preview.to_dict()["total_rows"] == 2
    assert preview.to_dict()["importable_rows"] == 2

    first = _row(preview, 2)
    assert first.values["name"] == "Бетаин"
    assert first.values["cas"] == "107-43-7"
    assert first.values["volume"] == "500 kg"
    assert first.values["purity"] == "min 99%"
    assert first.values["incoterms"] == ["FOB", "DAP"]
    assert first.values["search_countries"] == ["Китай"]
    assert first.values["identification_method"] == "cas"

    second = _row(preview, 3)
    assert second.values["volume"] == "2 t"
    # Запятая как десятичный разделитель — норма в русской раскладке.
    assert second.values["purity"] == "min 98.5%"
    assert second.values["search_countries"] == ["Индия", "Китай"]


def test_xlsx_is_read_the_same_way():
    payload = _xlsx(
        [
            _HEADER,
            ["Бетаин", "107-43-7", 500, "кг", 99, "FOB", "Китай"],
        ]
    )
    preview = parse_import_file("list.xlsx", payload)

    row = _row(preview, 2)
    # openpyxl отдаёт число как 500.0 — в письме поставщику это выглядело бы
    # небрежностью, поэтому целое остаётся целым.
    assert row.values["volume"] == "500 kg"
    assert row.values["purity"] == "min 99%"


def test_semicolon_delimiter_and_cp1251_are_understood():
    """Excel на русской Windows пишет именно так."""
    payload = _csv(
        [
            ["Название", "CAS", "Объём", "Единица"],
            ["Бетаин", "107-43-7", "500", "кг"],
        ],
        encoding="cp1251",
        delimiter=";",
    )
    preview = parse_import_file("list.csv", payload)
    assert _row(preview, 2).values["name"] == "Бетаин"


def test_english_headers_work_too():
    payload = _csv(
        [
            ["Name", "CAS No", "Quantity", "Unit"],
            ["Betaine", "107-43-7", "500", "kg"],
        ]
    )
    preview = parse_import_file("list.csv", payload)
    assert _row(preview, 2).values["name"] == "Betaine"
    assert _row(preview, 2).values["volume"] == "500 kg"


# --- одна плохая строка не роняет файл ---


def test_bad_row_does_not_hide_the_others():
    payload = _csv(
        [
            _HEADER,
            ["Бетаин", "107-43-7", "500", "кг", "", "", ""],
            ["Глицин", "56-40-7", "1", "кг", "", "", ""],
            ["Мочевина", "57-13-6", "3", "т", "", "", ""],
        ]
    )
    preview = parse_import_file("list.csv", payload)

    assert preview.to_dict()["total_rows"] == 3
    assert preview.to_dict()["importable_rows"] == 2
    broken = _row(preview, 3)
    assert not broken.importable
    error = broken.errors[0]
    # Номер строки, поле и причина — иначе файл правится вслепую.
    assert error.row == 3
    assert error.field == "cas"
    assert "56-40-6" in error.message, "верная контрольная цифра должна быть названа"


def test_row_without_name_is_rejected_with_a_reason():
    payload = _csv([_HEADER, ["", "107-43-7", "500", "кг", "", "", ""]])
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert not row.importable
    assert row.errors[0].field == "name"


# --- единицы ---


def test_missing_unit_is_an_error_not_a_guess():
    """Без единицы неизвестен порядок величины закупки."""
    payload = _csv([["Название", "Объём"], ["Бетаин", "500"]])
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert not row.importable
    assert row.errors[0].field == "unit"


def test_unit_inside_the_value_is_understood():
    """«500 кг» одной ячейкой встречается чаще двух аккуратных столбцов."""
    payload = _csv([["Название", "Объём"], ["Бетаин", "500 кг"]])
    preview = parse_import_file("list.csv", payload)
    assert _row(preview, 2).values["volume"] == "500 kg"


def test_unknown_unit_is_refused():
    payload = _csv([["Название", "Объём", "Единица"], ["Бетаин", "500", "вагон"]])
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert not row.importable
    assert row.errors[0].field == "unit"


def test_convertible_unit_is_converted_out_loud():
    """Пересчёт возможен, но молчать о нём нельзя: число меняется."""
    payload = _csv([["Название", "Объём", "Единица"], ["Бетаин", "100", "lb"]])
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert row.importable
    assert row.values["volume"] == "45.359237 kg"
    assert "100 lb" in row.warnings[0].message
    assert "45.359237 kg" in row.warnings[0].message


# --- формулы ---


def test_csv_formula_is_never_executed():
    payload = _csv(
        [
            ["Название", "CAS", "Объём", "Единица"],
            ["=1+1", "107-43-7", "500", "кг"],
            ["Бетаин", "107-43-7", "=SUM(A1:A9)", "кг"],
        ]
    )
    preview = parse_import_file("list.csv", payload)

    formula_name = _row(preview, 2)
    assert not formula_name.importable
    assert formula_name.errors[0].field == "name"

    formula_volume = _row(preview, 3)
    assert not formula_volume.importable
    assert formula_volume.errors[0].field == "volume"


def test_xlsx_formula_without_a_cached_value_does_not_crash():
    """Книга, которую не открывали в Excel, посчитанных значений не хранит."""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Название", "Объём", "Единица"])
    sheet.append(["Бетаин", "=100*5", "кг"])
    stream = io.BytesIO()
    book.save(stream)

    preview = parse_import_file("list.xlsx", stream.getvalue())

    # Значение не выдумано и не вычислено. Пустая ячейка на месте формулы
    # выглядит как потеря колонки, поэтому названы конкретные адреса.
    assert preview.file_warnings, "молчать о непрочитанной формуле нельзя"
    warning = preview.file_warnings[0].message
    assert "B2" in warning
    assert "Excel" in warning
    assert "volume" not in _row(preview, 2).values


# --- колонки ---


def test_unknown_columns_are_reported_before_being_dropped():
    payload = _csv(
        [
            ["Название", "Склад", "Артикул"],
            ["Бетаин", "А-12", "SKU-9"],
        ]
    )
    preview = parse_import_file("list.csv", payload)

    assert preview.ignored_columns == ["Склад", "Артикул"]
    assert preview.file_warnings, "молча выбрасывать колонку нельзя"
    assert "Склад" in preview.file_warnings[0].message


def test_file_without_a_name_column_is_refused_with_the_headers_listed():
    payload = _csv([["Склад", "Артикул"], ["А-12", "SKU-9"]])
    with pytest.raises(RfqImportError) as exc:
        parse_import_file("list.csv", payload)
    assert "Склад" in str(exc.value)


def test_duplicate_column_does_not_overwrite_the_first():
    payload = _csv(
        [
            ["Название", "Цена", "Price"],
            ["Бетаин", "10", "99"],
        ]
    )
    preview = parse_import_file("list.csv", payload)
    assert _row(preview, 2).values["target_price"] == 10.0
    assert "Price" in preview.ignored_columns


# --- строки и дубликаты ---


def test_blank_lines_are_separators_not_errors():
    payload = _csv(
        [
            ["Название", "CAS"],
            ["Бетаин", "107-43-7"],
            ["", ""],
            ["Глицин", "56-40-6"],
        ]
    )
    preview = parse_import_file("list.csv", payload)
    assert [row.row for row in preview.rows] == [2, 4]


def test_leading_blank_lines_before_the_header_are_skipped():
    payload = _csv([["", ""], ["Название", "CAS"], ["Бетаин", "107-43-7"]])
    preview = parse_import_file("list.csv", payload)
    # Нумерация остаётся такой же, как в Excel у закупщика.
    assert [row.row for row in preview.rows] == [3]


def test_duplicate_row_warns_and_names_the_first_one():
    payload = _csv(
        [
            ["Название", "CAS"],
            ["Бетаин", "107-43-7"],
            ["бетаин", "107-43-7"],
        ]
    )
    preview = parse_import_file("list.csv", payload)
    duplicate = _row(preview, 3)
    # Дубликат не ошибка: закупщик мог сознательно завести две позиции.
    assert duplicate.importable
    assert "строке 2" in duplicate.warnings[0].message


def test_row_limit_is_reported_not_silently_cut():
    rows = [["Название", "CAS"]]
    rows += [[f"Вещество {i}", ""] for i in range(MAX_ROWS + 5)]
    preview = parse_import_file("list.csv", _csv(rows))
    assert len(preview.rows) == MAX_ROWS
    assert str(MAX_ROWS) in preview.file_warnings[0].message


# --- отказы файла целиком ---


def test_empty_file_is_refused():
    with pytest.raises(RfqImportError):
        parse_import_file("list.csv", b"")


def test_header_without_rows_is_refused():
    with pytest.raises(RfqImportError):
        parse_import_file("list.csv", _csv([["Название", "CAS"]]))


def test_unsupported_extension_is_refused():
    with pytest.raises(RfqImportError) as exc:
        parse_import_file("list.pdf", b"%PDF-1.7")
    assert ".xlsx" in str(exc.value)


def test_old_xls_gets_an_actionable_message():
    with pytest.raises(RfqImportError) as exc:
        parse_import_file("list.xlsx", b"not a zip at all")
    assert ".xls" in str(exc.value)


def test_oversized_file_is_refused():
    from app.services.rfq_import import MAX_FILE_BYTES

    with pytest.raises(RfqImportError) as exc:
        parse_import_file("list.csv", b"x" * (MAX_FILE_BYTES + 1))
    assert "МБ" in str(exc.value)


# --- необязательные поля ---


def test_unsupported_incoterm_and_country_are_skipped_with_a_warning():
    payload = _csv(
        [
            ["Название", "Incoterms", "Страны"],
            ["Бетаин", "FOB;DDP", "Китай;Бразилия"],
        ]
    )
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert row.importable, "неподдержанный базис не делает строку негодной"
    assert row.values["incoterms"] == ["FOB"]
    assert row.values["search_countries"] == ["Китай"]
    assert len(row.warnings) == 2


def test_currency_and_grade_land_in_the_card_fields():
    payload = _csv(
        [
            ["Название", "Чистота", "Грейд", "Цена", "Валюта", "Синонимы", "Комментарий"],
            ["Бетаин", "99", "Industrial grade", "12.5", "eur", "Glycine betaine; TMG", "брали в Казани"],
        ]
    )
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert row.values["purity"] == "min 99%, Industrial grade"
    assert row.values["target_price"] == 12.5
    assert row.values["currency"] == "EUR"
    assert row.values["confirmed_synonyms"] == ["Glycine betaine", "TMG"]
    assert row.values["specialist_comment"] == "брали в Казани"


def test_unknown_currency_keeps_the_row_and_explains():
    payload = _csv([["Название", "Валюта"], ["Бетаин", "GBP"]])
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert row.importable
    assert "currency" not in row.values
    assert "GBP" in row.warnings[0].message


def test_row_without_cas_is_identified_by_specification():
    payload = _csv(
        [
            ["Название", "Спецификация"],
            ["Загуститель для шампуня", "Вязкость 4000-6000 сП"],
        ]
    )
    preview = parse_import_file("list.csv", payload)
    row = _row(preview, 2)
    assert row.importable
    assert row.values["identification_method"] == "spec"
    assert row.values["specification"] == "Вязкость 4000-6000 сП"
