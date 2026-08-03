"""Происхождение значения поля карточки вещества.

Зачем. Часть данных о веществе приходит из PubChem, часть находит ИИ-агент,
часть вводит человек. Внешне они неразличимы, и без пометки догадка агента
через месяц выглядит как справочные данные. Инвариант дорожной карты —
«отсутствующее доказательство не превращается в положительный факт» —
держится именно на том, что источник хранится рядом со значением.

Провенанс ведётся по полям, а не по карточке целиком: у одного вещества
название может прийти из PubChem, а применение — от агента.
"""

from __future__ import annotations

from typing import Literal

FieldSource = Literal["pubchem", "ai_agent", "human", "catalog"]

# Подписи для интерфейса. «Поиск от ИИ-агента» — не «со слов модели»:
# агент действительно ищет, а не придумывает, и подпись должна называть
# способ добычи, а не бросать тень на результат.
SOURCE_LABELS: dict[str, str] = {
    "pubchem": "Проверено по PubChem",
    "ai_agent": "Поиск от ИИ-агента",
    "human": "Указано специалистом",
    "catalog": "Из справочника компании",
}

# Источники, которые считаются внешним подтверждением. Данные агента сюда
# не входят: их проверяет человек, а не независимый источник.
VERIFYING_SOURCES = frozenset({"pubchem", "catalog"})


def is_verified_source(source: str | None) -> bool:
    """True, если значение подтверждено чем-то помимо ИИ-агента."""
    return source in VERIFYING_SOURCES


def source_label(source: str | None) -> str | None:
    """Подпись для интерфейса; неизвестный источник не подписывается."""
    return SOURCE_LABELS.get(source or "")


def merge_sources(
    current: dict[str, str] | None,
    updates: dict[str, str | None],
) -> dict[str, str]:
    """Обновляет карту источников, выбрасывая снятые значения.

    Значение None означает «поле очищено» — источник тоже уходит, иначе
    в карте останется ссылка на происхождение того, чего больше нет.
    """
    merged = dict(current or {})
    for field, source in updates.items():
        if source is None:
            merged.pop(field, None)
        else:
            merged[field] = source
    return merged
