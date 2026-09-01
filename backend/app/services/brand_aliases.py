"""Прежние имена владельцев торговых марок.

Треть списка сырья заказчика — торговые марки, и на позиции без CAS имя
марки становится единственным якорем поиска. Владельцы марок меняются, а
китайские заводы пишут на своих страницах то имя, под которым продукт знали
раньше.

Проверено на позиции заказчика «Dowsil 556 Cosmetic Grade Fluid». Собственная
страница завода Silibase называется «Replacement product for Dow Corning®556
Cosmetic Grade Fluid», а у Kemi-Works имя прежнего владельца стоит прямо в
адресе страницы: ``Phenyl-Trimethicone-Equivalent-to-DC556``. Запрос, где имя
взято в точную фразу как «Dowsil 556 …», такую страницу не находит вовсе.

Dow объявила переименование в феврале 2018 года и отдельно оговорила, что
описательная часть названия продукта не меняется: меняется только слово
марки. Поэтому подстановка идёт по границе слова и сохраняет остаток имени —
«Dowsil 556 Cosmetic Grade Fluid» превращается в «Dow Corning 556 Cosmetic
Grade Fluid», а не в отдельное новое название.

Таблица — это данные, а не правило: её дополняют по мере того, как в списках
заказчиков встречаются новые марки. Ошибка в ней стоит одного лишнего
названия в поисковой группе, а не неверного вывода о поставщике.
"""

from __future__ import annotations

import re

# Текущее имя владельца марки и имена, под которыми ту же продуктовую линейку
# знали прежде. Связи двусторонние: заказчик приносит и старое имя, и новое.
_BRAND_HISTORY: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Dow переименовала силиконы Dow Corning в DOWSIL в феврале 2018 года.
    ("dow corning", ("DOWSIL",)),
    ("dowsil", ("Dow Corning",)),
    # Degussa вошла в Evonik в 2007 году.
    ("degussa", ("Evonik",)),
    ("evonik", ("Degussa",)),
    # Ciba и Cognis куплены BASF в 2009 и 2010 годах.
    ("ciba", ("BASF",)),
    ("cognis", ("BASF",)),
    # Rohm and Haas куплена Dow в 2009 году.
    ("rohm and haas", ("Dow",)),
    ("rohm & haas", ("Dow",)),
    # Noveon куплена Lubrizol в 2004 году.
    ("noveon", ("Lubrizol",)),
    ("lubrizol", ("Noveon",)),
    # Uniqema куплена Croda у ICI в 2006 году.
    ("uniqema", ("Croda",)),
    ("croda", ("Uniqema",)),
)

# Длинные имена проверяются раньше коротких: иначе «dow» сработал бы внутри
# «dow corning» и дал бы бессмысленное «Rohm and Haas Corning 556».
_ORDERED_BRANDS = tuple(
    sorted(_BRAND_HISTORY, key=lambda item: -len(item[0]))
)

_MAX_ALIASES = 3


def _brand_pattern(brand: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![0-9a-zA-Z]){re.escape(brand)}(?![0-9a-zA-Z])", re.I)


def brand_aliases(name: str | None) -> list[str]:
    """Тот же продукт под прежним или нынешним именем владельца марки.

    Возвращает пустой список, если марка в названии не опознана, — то есть
    почти всегда, потому что таблица намеренно короткая и покрывает только
    переименования, реально встречающиеся в списках заказчиков.
    """
    source = (name or "").strip()
    if not source:
        return []
    aliases: list[str] = []
    seen = {source.casefold()}
    for brand, replacements in _ORDERED_BRANDS:
        pattern = _brand_pattern(brand)
        if not pattern.search(source):
            continue
        for replacement in replacements:
            renamed = pattern.sub(replacement, source, count=1).strip()
            key = renamed.casefold()
            if key in seen:
                continue
            seen.add(key)
            aliases.append(renamed)
            if len(aliases) >= _MAX_ALIASES:
                return aliases
        # Марка в названии одна: продолжать перебор незачем, а по короткой
        # метке вроде «dow» можно ошибочно совпасть повторно.
        break
    return aliases
