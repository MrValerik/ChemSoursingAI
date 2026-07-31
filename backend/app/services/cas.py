"""Детерминированный валидатор CAS-номера.

CAS Registry Number имеет формат NNNNNNN-NN-C, где последняя цифра C —
контрольная сумма. Проверка по контрольной сумме отсекает опечатки и
«галлюцинации» до любого обращения к внешним сервисам (раздел 5 ТЗ:
детерминированные валидаторы поверх LLM).
"""

from __future__ import annotations

import re

_CAS_RE = re.compile(r"^(\d{2,7})-(\d{2})-(\d)$")


def normalize_cas(value: str) -> str:
    """Приводит к каноничному виду: убирает пробелы, прочие дефисы оставляет."""
    return value.strip()


def is_valid_cas(value: str) -> bool:
    """True, если строка — синтаксически корректный CAS с верной контрольной цифрой.

    Контрольная цифра = (сумма i*d_i) mod 10, где d_i — цифры без дефисов,
    пронумерованные справа налево начиная с 1 (не считая саму контрольную).
    """
    m = _CAS_RE.match(normalize_cas(value))
    if not m:
        return False
    body = m.group(1) + m.group(2)        # все цифры, кроме контрольной
    check = int(m.group(3))
    total = sum(int(d) * i for i, d in enumerate(reversed(body), start=1))
    return total % 10 == check
