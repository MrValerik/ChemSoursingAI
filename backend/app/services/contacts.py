"""Способы связи, читаемые со страницы без обращения к модели.

Зачем. Роль контрагента по странице чаще всего недоказуема: замер по 136
сохранённым карточкам дал ровно один номер государственной лицензии и
шесть упоминаний выпуска или площадки. Зато способ связи есть почти
всегда — почта, телефон, WhatsApp нашлись у 92 карточек из тех же 136.

Смысл смещается соответственно. Поиск не обязан выносить окончательный
вердикт о роли: он обязан найти компанию и дать, куда написать, а точный
ответ «завод вы или посредник» приходит перепиской. ТЗ прямо называет
Echemi, почту и WhatsApp каналами рассылки запросов.

Строки возвращаются как есть, без нормализации номеров: телефон нужен
человеку, а не автомату, и любое «исправление» здесь способно испортить
рабочий номер.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Почта систем аналитики, шаблонов и хостингов. Такие адреса стоят в
# разметке тысяч сайтов и к поставщику отношения не имеют.
_JUNK_MAIL_RE = re.compile(
    r"(sentry\.|example\.|@2x|\.png|\.jpg|\.gif|wixpress|godaddy|cloudflare"
    r"|sentry-next|@sentry|domain\.com|yourdomain|email\.com|test@)",
    re.IGNORECASE,
)

# Международный номер, китайский городской и китайский мобильный.
_PHONE_RE = re.compile(
    r"(\+\d{1,3}[\s\-()]{0,3}\d{1,4}[\s\-()]{0,3}\d{3,4}[\s\-]?\d{3,4}"
    r"|\b0\d{2,3}-\d{7,8}\b"
    r"|\b1[3-9]\d{9}\b)"
)

_WECHAT_RE = re.compile(
    r"(?:wechat|weixin|微信)\s*(?:id)?\s*[:：]?\s*([A-Za-z0-9_-]{5,20})",
    re.IGNORECASE,
)
_WHATSAPP_RE = re.compile(
    r"(?:whats\s?app)\s*[:：]?\s*(\+?[\d][\d\s\-()]{7,19})",
    re.IGNORECASE,
)
_SKYPE_RE = re.compile(
    r"skype\s*(?:id)?\s*[:：]?\s*([A-Za-z0-9._:-]{5,32})", re.IGNORECASE
)

# Сколько адресов одного вида имеет смысл сохранять. Больше — это уже не
# контакты компании, а список рассылки или каталог чужих продавцов.
_MAX_PER_KIND = 5

# Подписи соседних полей. В блоке контактов они идут вплотную, и жадная
# регулярка принимает следующую подпись за значение: у Zhejiang Jiaao в
# Skype попадало «E-mail».
_LABEL_ONLY = frozenset(
    {
        "email", "e-mail", "mail", "tel", "telephone", "phone", "fax",
        "mobile", "address", "contact", "whatsapp", "wechat", "skype",
        "qq", "website", "web", "name", "company",
    }
)


# Переход строчной буквы в заглавную внутри доменной зоны. При извлечении
# текста соседние слова слипаются, и адрес выходит вида
# «inquiry@gneebio.comPhone»: регулярка честно приняла «comPhone» за зону.
_GLUED_TAIL_RE = re.compile(r"([a-z])([A-Z])")


def _trim_glued_tail(email: str) -> str:
    """Отрезает слово, прилипшее к доменной зоне."""
    local, _, domain = email.rpartition("@")
    if not local or "." not in domain:
        return email
    head, _, tld = domain.rpartition(".")
    match = _GLUED_TAIL_RE.search(tld)
    if match is None:
        return email
    return f"{local}@{head}.{tld[: match.start() + 1]}"


def _clean(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        item = value.strip().strip(".,;:)（）")
        if not item or item.casefold() in _LABEL_ONLY:
            continue
        if item not in seen:
            seen.append(item)
    return seen[:_MAX_PER_KIND]


def find_contacts(text: str) -> dict[str, list[str]]:
    """Почта, телефоны и мессенджеры со страницы, дословно.

    Возвращается только то, что нашлось: пустые виды в словарь не
    попадают, чтобы карточка не пестрела пустыми полями.
    """
    body = text or ""
    if not body:
        return {}

    emails = _clean(
        [
            _trim_glued_tail(value)
            for value in _EMAIL_RE.findall(body)
            if not _JUNK_MAIL_RE.search(value)
        ]
    )
    phones = _clean(_PHONE_RE.findall(body))
    wechat = _clean(_WECHAT_RE.findall(body))
    whatsapp = _clean(_WHATSAPP_RE.findall(body))
    skype = _clean(_SKYPE_RE.findall(body))

    found = {
        "emails": emails,
        "phones": phones,
        "wechat": wechat,
        "whatsapp": whatsapp,
        "skype": skype,
    }
    return {kind: values for kind, values in found.items() if values}


def has_contacts(contacts: dict[str, list[str]] | None) -> bool:
    """Есть ли хоть один способ написать или позвонить."""
    if not contacts:
        return False
    return any(contacts.get(kind) for kind in ("emails", "phones", "whatsapp", "wechat"))
