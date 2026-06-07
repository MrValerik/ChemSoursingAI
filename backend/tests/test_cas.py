"""Тесты детерминированного валидатора CAS (контрольная сумма)."""

from app.services.cas import is_valid_cas


def test_valid_known_cas():
    # Реальные вещества с корректной контрольной цифрой.
    assert is_valid_cas("50-78-2")     # аспирин
    assert is_valid_cas("58-08-2")     # кофеин
    assert is_valid_cas("7732-18-5")   # вода
    assert is_valid_cas("67-64-1")     # ацетон
    assert is_valid_cas("64-17-5")     # этанол


def test_invalid_checksum():
    # Тот же формат, но неверная контрольная цифра.
    assert not is_valid_cas("50-78-3")
    assert not is_valid_cas("7732-18-4")


def test_malformed():
    assert not is_valid_cas("")
    assert not is_valid_cas("abc")
    assert not is_valid_cas("50782")
    assert not is_valid_cas("50-78")
    assert not is_valid_cas("50-78-")


def test_whitespace_tolerated():
    assert is_valid_cas("  50-78-2  ")
