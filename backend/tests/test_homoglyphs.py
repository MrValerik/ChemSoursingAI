"""Кириллица, набранная вместо латиницы, роняла поиск целиком.

Запрос #31 «С18-С22 fatty alcohol» дал ноль результатов на все восемь
запросов, и система списала это на блокировку поисковика. На деле «С» в
«С18-С22» — кириллическая U+0421: выглядит как латинская, но для
поисковика это другая строка, а имя стоит в кавычках точной фразой.
Проверено — с латинской буквой тот же запрос находит поставщиков.

Половина списка сырья набрана в Word и Excel, где раскладка
переключается на полуслове, так что случай не единичный.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_homoglyphs.db")

from app.services.homoglyphs import fix_lookalikes, has_lookalikes


def test_the_name_that_broke_request_31():
    assert fix_lookalikes("С18-С22 fatty alcohol") == "C18-C22 fatty alcohol"
    assert has_lookalikes("С18-С22 fatty alcohol") is True


def test_a_latin_name_is_untouched():
    for value in ("C18-C22 fatty alcohol", "Adipic acid", "DOWSIL 556"):
        assert fix_lookalikes(value) == value
        assert has_lookalikes(value) is False


def test_a_russian_name_is_untouched():
    """Иначе мы сломаем половину справочника."""
    for value in (
        "Ацетилсалициловая кислота",
        "Эпоксидированное соевое масло",
        "Карбомер",
    ):
        assert fix_lookalikes(value) == value
        assert has_lookalikes(value) is False


def test_a_russian_word_with_digits_is_untouched():
    """«ГОСТ2016» — настоящее русское слово, а не смешанное."""
    assert fix_lookalikes("ГОСТ2016") == "ГОСТ2016"
    assert fix_lookalikes("Марка А2") == "Марка А2"


def test_mixed_words_are_fixed_one_by_one():
    """Правится слово, а не строка целиком."""
    assert fix_lookalikes("Кислота Сtearyl alcohol") == "Кислота Ctearyl alcohol"


def test_a_cyrillic_letter_with_a_digit_is_fixed_next_to_latin_words():
    """Случай «С18»: латинских букв в самом слове нет, решает соседство.

    «С22 fatty alcohol» — опечатка, «Марка А2» — русское обозначение
    марки. На вид они неразличимы, поэтому смотрим на строку целиком.
    """
    assert fix_lookalikes("С22 fatty alcohol") == "C22 fatty alcohol"
    assert fix_lookalikes("Р2О5 grade") == "P2O5 grade"
    # Без латинского соседства обозначение остаётся русским.
    assert fix_lookalikes("С22") == "С22"


def test_empty_input_survives():
    assert fix_lookalikes("") == ""
    assert fix_lookalikes(None) is None
    assert has_lookalikes("") is False
