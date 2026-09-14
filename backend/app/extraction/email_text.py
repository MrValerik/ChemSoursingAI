"""Безопасное выделение новой реплики из процитированной Email-цепочки.

Оригинал письма всегда хранится целиком в ``communications.body``. Эта функция
готовит только машинный вход: иначе процитированный RFQ покупателя выглядит для
экстрактора как утверждение поставщика о цене, Incoterm и документах.
"""

from __future__ import annotations

import re


_QUOTED_HISTORY_MARKERS = (
    re.compile(r"(?im)^\s*-{2,}\s*original message\s*-{2,}\s*$"),
    re.compile(r"(?im)^\s*-{2,}\s*forwarded message\s*-{2,}\s*$"),
    re.compile(r"(?m)^\s*_{5,}\s*$"),
    re.compile(r"(?im)^\s*on\s+.+\s+wrote:\s*$"),
    re.compile(r"(?ims)^\s*on\s+[^\n]*(?:\n[^\n]*){0,3}\bwrote:\s*$"),
    re.compile(
        r"(?im)^\s*.*<[^>\n]+>\s+"
        r"(?:wrote|написал(?:а|и)?|написал\(а\))\s*:\s*$"
    ),
    re.compile(
        r"(?im)^\s*(?:from|sent|to|subject|от|отправлено|кому|тема)"
        r"\s*:\s*.+$"
    ),
    re.compile(r"(?im)^\s*(?:发件人|发送时间|收件人|主题)\s*[：:]\s*.+$"),
)
_STRONG_QUOTED_HISTORY_MARKERS = _QUOTED_HISTORY_MARKERS[:6]
_QUOTED_HEADER_PATTERN = re.compile(
    r"(?im)^\s*(from|sent|to|subject|от|отправлено|кому|тема|"
    r"发件人|发送时间|收件人|主题)\s*[：:]\s*.+$"
)
_PARTICIPANT_HEADER_NAMES = {"from", "to", "от", "кому", "发件人", "收件人"}


def _normalized_email_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _quoted_history_start(source: str) -> int | None:
    starts = [
        match.start()
        for marker in _QUOTED_HISTORY_MARKERS
        if (match := marker.search(source)) is not None
    ]
    return min(starts) if starts else None


def _strict_quoted_history_start(source: str) -> int | None:
    starts = [
        match.start()
        for marker in _STRONG_QUOTED_HISTORY_MARKERS
        if (match := marker.search(source)) is not None
    ]
    header_matches = list(_QUOTED_HEADER_PATTERN.finditer(source))
    for first in header_matches:
        nearby = [
            match
            for match in header_matches
            if first.start() <= match.start() <= first.start() + 1200
        ]
        names = {match.group(1).casefold() for match in nearby}
        if len(names) >= 2 and names & _PARTICIPANT_HEADER_NAMES:
            starts.append(first.start())
    return min(starts) if starts else None


def quoted_history_text(text: str) -> str:
    """Возвращает только явно отделённую цитируемую Email-историю."""

    source = _normalized_email_text(text)
    start = _strict_quoted_history_start(source)
    if start is not None:
        return source[start:].strip()

    # Некоторые мобильные клиенты оставляют только хвост из строк ``>`` без
    # заголовка ``On ... wrote``. Считаем историей лишь непрерывный хвост,
    # чтобы адрес из новой реплики не стал сигналом идентичности.
    lines = source.splitlines()
    index = len(lines) - 1
    saw_quoted_line = False
    while index >= 0:
        stripped = lines[index].lstrip()
        if stripped.startswith(">"):
            saw_quoted_line = True
            index -= 1
            continue
        if not stripped and saw_quoted_line:
            index -= 1
            continue
        break
    if not saw_quoted_line:
        return ""
    return "\n".join(lines[index + 1 :]).strip()


def latest_reply_text(text: str) -> str:
    """Возвращает только новую верхнюю реплику, не меняя сохранённый оригинал."""
    source = _normalized_email_text(text)
    cut_at = _quoted_history_start(source)
    if cut_at is None:
        cut_at = len(source)

    latest = source[:cut_at].strip()
    if not latest:
        latest = source.strip()

    # Gmail и некоторые мобильные клиенты не добавляют заголовок, но ставят
    # ``>`` перед каждой строкой старого письма. Убираем только хвостовой блок,
    # чтобы символ сравнения внутри спецификации не потерялся.
    lines = latest.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith(">")):
        lines.pop()
    return "\n".join(lines).strip()
