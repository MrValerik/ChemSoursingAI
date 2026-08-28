"""Безопасное выделение новой реплики из процитированной Email-цепочки.

Оригинал письма всегда хранится целиком в ``communications.body``. Эта функция
готовит только машинный вход: иначе процитированный RFQ покупателя выглядит для
экстрактора как утверждение поставщика о цене, Incoterm и документах.
"""

from __future__ import annotations

import re


_QUOTED_HISTORY_MARKERS = (
    re.compile(r"(?im)^\s*-{2,}\s*original message\s*-{2,}\s*$"),
    re.compile(r"(?im)^\s*on\s+.+\s+wrote:\s*$"),
    re.compile(r"(?im)^\s*(?:from|sent|to|subject)\s*:\s*.+$"),
    re.compile(r"(?im)^\s*(?:发件人|发送时间|收件人|主题)\s*[：:]\s*.+$"),
)


def latest_reply_text(text: str) -> str:
    """Возвращает только новую верхнюю реплику, не меняя сохранённый оригинал."""
    source = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cut_at = len(source)
    for marker in _QUOTED_HISTORY_MARKERS:
        match = marker.search(source)
        if match is not None:
            cut_at = min(cut_at, match.start())

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
