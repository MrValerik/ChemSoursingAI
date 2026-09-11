"""Нормализация текста, который система показывает или отправляет поставщику."""

from __future__ import annotations

import re


_MARKDOWN_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+")
_MARKDOWN_BULLET_RE = re.compile(r"(?m)^[ \t]*[*+][ \t]+")
_MARKDOWN_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.*?\S)\1", re.DOTALL)
_MARKDOWN_ITALIC_RE = re.compile(r"(?<!\w)([*_])(?=\S)([^\n]*?\S)\1(?!\w)")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")
_LEADING_SUBJECT_RE = re.compile(
    r"\A[ \t]*(?:subject(?: line)?|тема(?: письма)?|主题)"
    r"[ \t]*(?:[:：]|[-–—])[ \t]*[^\n]*(?:\n+|\Z)",
    re.IGNORECASE,
)
_TRAILING_TEST_NOTE_RE = re.compile(
    r"(?:\s*(?:(?:please\s+note\s+that|note|обратите\s+внимание|примечание)"
    r"\s*[:：-]?\s*)?(?:"
    r"this\s+is\s+(?:a\s+)?test(?:ing)?\s+message|"
    r"this\s+is\s+(?:a\s+)?simulated\s+(?:message|conversation)|"
    r"this\s+message\s+(?:is|was)\s+(?:generated|created)\s+"
    r"(?:for\s+testing(?:\s+purposes)?|in\s+test\s+mode)|"
    r"for\s+testing\s+purposes\s+only|"
    r"это\s+тестовое\s+сообщение|"
    r"это\s+(?:только\s+)?(?:тест|симуляция)(?:\s+переписки)?|"
    r"(?:это\s+)?сообщение\s+(?:создано|сгенерировано|предназначено)\s+"
    r"(?:только\s+)?(?:для\s+тестирования|в\s+тестовом\s+режиме)|"
    r"сообщение\s+не\s+будет\s+отправлено|"
    r"这是(?:一条)?测试消息|本消息仅用于测试"
    r")[.!。]*\s*)+\Z",
    re.IGNORECASE,
)
_TRAILING_SIGNATURE_RE = re.compile(
    r"(?:\n+|[ \t]+)(?:(?:best|kind|warm)\s+regards|sincerely)[,!]?"
    r"(?:[ \t]+[^\n]{0,80})?(?:[ \t]*\n[^\n]{0,80}){0,3}[.!]?[ \t]*\Z",
    re.IGNORECASE,
)
_TRAILING_PLACEHOLDER_RE = re.compile(
    r"(?:\n+|[ \t]+)(?:"
    r"\[\s*(?:your\s+)?(?:name|company(?:\s+name)?|position|title)\s*\]|"
    r"<\s*(?:your\s+)?(?:name|company(?:\s+name)?|position|title)\s*>"
    r")[.!]?[ \t]*\Z",
    re.IGNORECASE,
)
_TRAILING_EMPTY_COURTESY_RE = re.compile(
    r"(?:\s+(?:thank\s+you|thanks|looking\s+forward\s+to\s+your\s+"
    r"(?:prompt\s+)?(?:reply|response)))[.!]?[ \t]*\Z",
    re.IGNORECASE,
)


def plain_text_supplier_message(value: str) -> str:
    """Удаляет разметку, служебные пометки и вымышленные подписи модели."""
    text = value.strip().replace("```", "").replace("`", "")
    text = _MARKDOWN_HEADING_RE.sub("", text)
    text = _MARKDOWN_BULLET_RE.sub("", text)
    text = _MARKDOWN_BOLD_RE.sub(r"\2", text)
    text = _MARKDOWN_ITALIC_RE.sub(r"\2", text)
    text = text.replace("*", "")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text).strip()
    text = _LEADING_SUBJECT_RE.sub("", text).strip()
    text = _TRAILING_TEST_NOTE_RE.sub("", text).strip()
    text = _TRAILING_SIGNATURE_RE.sub("", text).strip()
    text = _TRAILING_PLACEHOLDER_RE.sub("", text).strip()
    text = _TRAILING_EMPTY_COURTESY_RE.sub("", text).strip()
    return _EXCESS_BLANK_LINES_RE.sub("\n\n", text).strip()
