"""Сверка числительных в названии вещества: моно-, ди-, три-.

Зачем. 10.09.2026 опознание по запросу «Дигидроксимоноацетат алюминия»
вернуло «Aluminum diacetate hydroxide» с номером 142-03-0, пометило его
как то же самое вещество и поставило отметку «самый надёжный вариант» —
потому что номер был подтверждён страницей. Номер и правда подтверждён,
только он от соседней соли:

    7360-44-3   Al(OH)2(CH3COO)    два гидроксила, один ацетат
    142-03-0    Al(OH)(CH3COO)2    один гидроксил, два ацетата

Разница видна прямо в названии — «дигидрокси-моно-ацетат» против
«diacetate hydroxide», — и её видит человек, если ему показать оба
названия рядом. Алгоритму она не видна вовсе: строки разные, номер
подтверждён, отношение «то же вещество» назвала модель.

Что делает модуль. Достаёт из названия пары «числительное + корень» и
сравнивает два названия по общим корням. Ацетатов два против одного —
конфликт. Корней не пересеклось — молчим: молчание здесь честнее
догадки, потому что способов назвать вещество больше, чем таблица
корней.

Чего модуль не делает. Не разбирает брутто-формулу: посчитать в C4H7AlO5
число ацетатных групп нельзя, не зная строения. Формула показывается
человеку как есть, а сверяются названия — введённое и то, которое даёт
справочник.
"""

from __future__ import annotations

import re

# Числительные, которыми в названиях солей и оксидов считают группы.
# Русские и латинские вперемешку: сравниваются как раз введённое русское
# название и систематическое английское из справочника.
_MULTIPLIERS: dict[str, int] = {
    "моно": 1,
    "mono": 1,
    "ди": 2,
    "di": 2,
    "бис": 2,
    "bis": 2,
    "три": 3,
    "tri": 3,
    "тетра": 4,
    "tetra": 4,
    "пента": 5,
    "penta": 5,
    "гекса": 6,
    "hexa": 6,
}

# Корни, по которым сравнение осмысленно: это группы, которые считают.
# Таблица намеренно короткая. Каждый корень здесь — обещание, что число
# перед ним означает количество групп, а не часть слова; на длинном
# списке такое обещание перестаёт держаться.
_ROOTS: dict[str, str] = {
    "ацетат": "acetate",
    "acetate": "acetate",
    "гидрокси": "hydroxide",
    "гидроксид": "hydroxide",
    "hydroxide": "hydroxide",
    "hydroxy": "hydroxide",
    "хлорид": "chloride",
    "chloride": "chloride",
    "сульфат": "sulfate",
    "sulfate": "sulfate",
    "sulphate": "sulfate",
    "фосфат": "phosphate",
    "phosphate": "phosphate",
    "нитрат": "nitrate",
    "nitrate": "nitrate",
    "оксид": "oxide",
    "oxide": "oxide",
    "стеарат": "stearate",
    "stearate": "stearate",
}

_MULTIPLIER_ALTERNATIVES = "|".join(
    sorted(_MULTIPLIERS, key=len, reverse=True)
)
_ROOT_ALTERNATIVES = "|".join(sorted(_ROOTS, key=len, reverse=True))
# Числительное вплотную к корню: «дигидрокси», «diacetate». Пробел и дефис
# между ними допускаются — справочники пишут и «di-acetate».
_PAIR = re.compile(
    rf"({_MULTIPLIER_ALTERNATIVES})[\s-]?({_ROOT_ALTERNATIVES})",
    re.IGNORECASE,
)
# Корень без числительного означает одну группу: «acetate hydroxide» — это
# один ацетат и один гидроксил.
_BARE_ROOT = re.compile(rf"({_ROOT_ALTERNATIVES})", re.IGNORECASE)


def group_counts(name: str) -> dict[str, int]:
    """Считает группы в названии: {"acetate": 2, "hydroxide": 1}.

    Числительное перед корнем задаёт количество, корень без числительного
    означает одну группу. Повторный корень не складывается, а берётся по
    первому вхождению: «diacetate ... acetate» — это одна и та же группа,
    названная дважды, а не три ацетата.
    """
    text = name.casefold()
    counts: dict[str, int] = {}
    consumed: list[tuple[int, int]] = []
    for match in _PAIR.finditer(text):
        root = _ROOTS[match.group(2).casefold()]
        counts.setdefault(root, _MULTIPLIERS[match.group(1).casefold()])
        consumed.append(match.span())
    for match in _BARE_ROOT.finditer(text):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        root = _ROOTS[match.group(1).casefold()]
        counts.setdefault(root, 1)
    return counts


def composition_agrees(entered: str, reference: str) -> bool | None:
    """Сошёлся ли состав: True — да, False — нет, None — сравнивать нечего.

    Три ответа, а не два. «Неизвестно» и «сошлось» — разные вещи, и на
    русском вводе разница решающая: 10.09.2026 отметку получило «Acetic
    acid, aluminum salt, hydrate (2:1:1)», где состав записан отношением, а
    не приставками. Расхождения модуль не нашёл, потому что и сравнивать
    было нечего, — и «нечего сравнивать» прошло за «сошлось».
    """
    left = group_counts(entered)
    right = group_counts(reference)
    shared = set(left) & set(right)
    if not shared:
        return None
    return all(left[root] == right[root] for root in shared)


def compare_names(entered: str, reference: str) -> str | None:
    """Сравнивает числительные двух названий одного вещества.

    Возвращает объяснение расхождения по-русски или None, если расхождения
    нет либо сравнивать нечего. None — обычный ответ: пересечься корни
    должны сами, натягивать сравнение модуль не станет.
    """
    left = group_counts(entered)
    right = group_counts(reference)
    shared = set(left) & set(right)
    if not shared:
        return None
    differences = [
        (root, left[root], right[root]) for root in sorted(shared) if left[root] != right[root]
    ]
    if not differences:
        return None
    russian = {
        "acetate": "ацетатных групп",
        "hydroxide": "гидроксильных групп",
        "chloride": "хлоридных групп",
        "sulfate": "сульфатных групп",
        "phosphate": "фосфатных групп",
        "nitrate": "нитратных групп",
        "oxide": "кислородных групп",
        "stearate": "стеаратных групп",
    }
    parts = [
        f"{russian.get(root, root)}: у вас {mine}, у найденного {theirs}"
        for root, mine, theirs in differences
    ]
    return "; ".join(parts)
