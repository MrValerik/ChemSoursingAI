"""Allowed markets for automated supplier search."""

from __future__ import annotations

import re

SEARCH_COUNTRIES = ("Россия", "Китай", "Индия")

_COUNTRY_ALIASES = {
    "россия": "Россия",
    "russia": "Россия",
    "russian federation": "Россия",
    "ru": "Россия",
    "китай": "Китай",
    "china": "Китай",
    "cn": "Китай",
    "prc": "Китай",
    "индия": "Индия",
    "india": "Индия",
    "in": "Индия",
}


def normalize_search_country(value: str) -> str:
    """Return the canonical Russian label or reject an unsupported market."""
    country = value.strip()
    canonical = _COUNTRY_ALIASES.get(country.casefold())
    if canonical is None:
        allowed = ", ".join(SEARCH_COUNTRIES)
        raise ValueError(f"Доступные страны поиска: {allowed}")
    return canonical


# Название страны в свободной строке: русское, английское и китайское
# написание одной страны. Значение claim_value пишет модель, и пишет
# по-разному: «China», «Китай», «中国-辽宁», «Zibo City, Shandong Province,
# China». Замер по 873 сохранённым подтверждениям страны: 43 разных
# написания, 90% из них — Китай в одной из четырёх форм.
#
# Список не претендует на полноту мира: в нём страны, которые встретились
# в сохранённых прогонах, и обычные торговые партнёры по химии. Незнакомая
# страна просто не опознаётся, и тогда правило молчит — см.
# mentioned_countries.
_COUNTRY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Россия", r"\bросси\w*|\bроссийск\w*|\brussian?\b|俄罗斯"),
    ("Китай", r"\bкита[йея]\w*|\bchina\b|\bchinese\b|\bprc\b|中国|中华"),
    ("Индия", r"\bинди[яию]\w*|\bindian?\b|印度"),
    ("США", r"\bсша\b|\bu\.?s\.?a\.?\b|\bunited states\b|\bамерик\w*"),
    ("Германия", r"\bгермани\w*|\bgermany\b|\bdeutschland\b"),
    ("Испания", r"\bиспани\w*|\bspain\b|\bspanish\b"),
    ("Турция", r"\bтурци\w*|\bturkey\b|\bturkish\b|\btürkiye\b"),
    ("Япония", r"\bяпони\w*|\bjapan\b|日本"),
    ("Корея", r"\bкоре[яию]\w*|\bkorea\b|한국|韩国"),
    ("Сингапур", r"\bсингапур\w*|\bsingapore\b|新加坡"),
    ("Малайзия", r"\bмалайзи\w*|\bmalaysia\b"),
    ("Филиппины", r"\bфилиппин\w*|\bphilippines\b"),
    ("Таиланд", r"\bтаиланд\w*|\bthailand\b"),
    ("Вьетнам", r"\bвьетнам\w*|\bvietnam\b"),
    ("Тайвань", r"\bтайван\w*|\btaiwan\b|台湾"),
    ("Великобритания", r"\bвеликобритани\w*|\bunited kingdom\b|\bengland\b"),
    ("Франция", r"\bфранци\w*|\bfrance\b"),
    ("Италия", r"\bитали\w*|\bitaly\b"),
    ("Нидерланды", r"\bнидерланд\w*|\bnetherlands\b|\bholland\b"),
    ("Польша", r"\bпольш\w*|\bpoland\b"),
    ("Бразилия", r"\bбразили\w*|\bbrazil\b"),
    ("Украина", r"\bукраин\w*|\bukraine\b"),
    ("Беларусь", r"\bбеларус\w*|\bбелорус\w*|\bbelarus\b"),
    ("Казахстан", r"\bказахстан\w*|\bkazakhstan\b"),
    ("Швейцария", r"\bшвейцари\w*|\bswitzerland\b"),
    ("Израиль", r"\bизраил\w*|\bisrael\b"),
    ("Пакистан", r"\bпакистан\w*|\bpakistan\b"),
    ("Индонезия", r"\bиндонези\w*|\bindonesia\b"),
)

_COUNTRY_MATCHERS = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _COUNTRY_PATTERNS
)


def mentioned_countries(text: str) -> frozenset[str]:
    """Страны, названные в строке. Пустое множество — ни одной не узнали."""
    if not text:
        return frozenset()
    return frozenset(
        name for name, matcher in _COUNTRY_MATCHERS if matcher.search(text)
    )


def contradicts_search_country(claim_value: str, search_country: str) -> bool:
    """Названа ли в подтверждении только другая страна, не та, где искали.

    Правило умеет запрещать и не умеет подтверждать. Оно молчит, когда
    страну в строке узнать не удалось («likely», «Ningbo», «imported»), и
    когда искомая страна в строке всё-таки названа: «США/Китай» и
    «Сингапур (компания), Китай (происхождение)» — это про компанию с
    двумя адресами, а не про чужую страну.
    """
    wanted = mentioned_countries(search_country)
    if not wanted:
        return False
    found = mentioned_countries(claim_value)
    if not found:
        return False
    return not (found & wanted)
