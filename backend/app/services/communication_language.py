"""Детерминированная проверка письменности внешней переписки."""

from __future__ import annotations

import re


_CYRILLIC_CHAR_RE = re.compile(r"[А-Яа-яЁё]")
_CYRILLIC_WORD_RE = re.compile(r"[А-Яа-яЁё]{2,}")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def message_language_matches(value: str, language: str) -> bool:
    """Проверяет письменность локально, не раскрывая текст внешнему детектору."""
    if language == "ru":
        words = _CYRILLIC_WORD_RE.findall(value)
        return len(words) >= 3 and sum(map(len, words)) >= 8
    if language == "zh":
        return len(_HAN_RE.findall(value)) >= 4

    words = _LATIN_WORD_RE.findall(value)
    return (
        len(words) >= 3
        and sum(map(len, words)) >= 8
        and _CYRILLIC_CHAR_RE.search(value) is None
        and _HAN_RE.search(value) is None
    )
