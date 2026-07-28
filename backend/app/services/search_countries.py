"""Allowed markets for automated supplier search."""

from __future__ import annotations

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
