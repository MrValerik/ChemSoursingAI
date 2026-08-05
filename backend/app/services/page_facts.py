"""Факты, которые читаются со страницы без обращения к модели.

Зачем. В замере на бетаине модель не подтвердила совпадение вещества ни на
одной из пяти страниц, хотя CAS-номер присутствовал в тексте четырёх. Причина
не в модели: странице отдавались первые 4000 символов, а номер на карточках
китайских поставщиков стоит в таблице спецификации ниже — у одной страницы на
позиции 4015, то есть промахнулись на пятнадцать символов.

Порядок текста на странице не связан с его ценностью, поэтому любой предел
рано или поздно окажется не на том месте. Наличие номера — не суждение, а
проверяемый факт: он ищется регулярным выражением по полному тексту, до
всякой обрезки, и подтверждается контрольной суммой.

Строки здесь всегда возвращаются дословно. Это обязательное условие: цитата
проверяется вхождением в сохранённый текст страницы, и переформатированная
строка проверку не пройдёт.
"""

from __future__ import annotations

import re

from app.services.cas import is_valid_cas, normalize_cas

# CAS-номер: до семи цифр, две цифры, контрольная. Границы слова отсекают
# куски телефонов и артикулов, контрольная сумма — почти весь остальной шум.
# Дефис допускается любой: китайские карточки товара пишут номер через
# полноширинный, копипаста из Word — через неразрывный. Минус стоит в конце
# класса, иначе он читается как диапазон.
_DASHES = "‐‑‒–—―−－-"
_DASH = f"[{_DASHES}]"
_CAS_RE = re.compile(
    rf"(?<![\d{_DASHES}])(\d{{2,7}}{_DASH}\d{{2}}{_DASH}\d)(?![\d{_DASHES}])"
)

# Европейский номер EINECS/EC: три-три-одна цифра с контрольной суммой.
# Полезен как вторая точка опоры: у вещества он свой, и расхождение с
# CAS-номером означает, что на странице описано другое вещество.
_EC_RE = re.compile(r"(?<![\d-])([2-5]\d{2}-\d{3}-\d)(?![\d-])")

# InChIKey — отпечаток структуры, 14-10-1 заглавных букв. Совпадений по
# случайности практически не даёт и однозначнее любого названия.
_INCHIKEY_RE = re.compile(r"\b([A-Z]{14}-[A-Z]{10}-[A-Z])\b")

# Молекулярная формула распознаётся только рядом со словом-указателем:
# сама по себе последовательность заглавных букв с цифрами слишком часто
# оказывается артикулом или кодом упаковки.
_FORMULA_RE = re.compile(
    r"(?:molecular\s+formula|chemical\s+formula|formula|分子式|формула)"
    r"\s*[:：]?\s*([A-Z][A-Za-z0-9]{1,30})",
    re.IGNORECASE,
)

# Чистота: число с процентом рядом со словом-показателем. Без указателя
# процент на странице чаще всего означает скидку или долю рынка.
_PURITY_RE = re.compile(
    r"(?:assay|purity|content|min\.?|содержание|чистота|含量|纯度)"
    r"[^\n%]{0,24}?(\d{2,3}(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)

# Символы химических элементов — для проверки того, что найденное похоже
# на формулу, а не на артикул вида «AB12X».
_ELEMENTS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co "
    "Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb "
    "Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re "
    "Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu".split()
)
_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")

# Упоминания документов и сертификатов. Требование цифр у ISO не случайно:
# без них выражение ловит «isopropyl» и «isomer».
_DOCUMENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "gmp": (r"\bc?GMP\b", r"\bНПП\b", r"\bGMP\s*认证"),
    "iso": (r"\bISO\s*\d{4,5}(?::\d{4})?\b", r"\bISO\s*认证"),
    "coa": (
        r"\bCoA\b",
        r"\bC\.?o\.?A\.?\b",
        r"certificate\s+of\s+analysis",
        r"质检报告",
        r"分析证书",
        r"паспорт\s+качества",
    ),
    "tds": (
        r"\bTDS\b",
        r"technical\s+data\s+sheet",
        r"技术数据表",
        r"техническ\w*\s+специфик\w*",
    ),
}
_DOCUMENT_RE = {
    claim: re.compile("|".join(patterns), re.IGNORECASE)
    for claim, patterns in _DOCUMENT_PATTERNS.items()
}

# Признаки строки спецификации: по ним карточка товара отличается от
# маркетингового текста вокруг неё.
_SPEC_MARKERS = (
    "cas",
    "assay",
    "purity",
    "content",
    "grade",
    "appearance",
    "specification",
    "standard",
    "moq",
    "package",
    "einecs",
    "molecular",
    "формула",
    "чистота",
    "содержание",
    "规格",
    "含量",
    "纯度",
    "分子式",
)

# Сколько строк подсветки имеет смысл ставить перед текстом страницы.
# Больше — вытесняет саму страницу из того же бюджета символов.
_MAX_HIGHLIGHT_LINES = 12
_MAX_LINE_CHARS = 300


def find_cas_numbers(text: str) -> list[str]:
    """Все синтаксически верные CAS-номера страницы, без повторов.

    Контрольная сумма отсекает даты, телефоны и артикулы, случайно похожие
    на номер. Порядок сохраняется: первым идёт тот, что выше по странице.
    """
    found: list[str] = []
    seen: set[str] = set()
    for match in _CAS_RE.finditer(text or ""):
        # Приводим к обычному дефису: иначе номер с китайской страницы не
        # сравнится с номером из запроса, хотя это одно и то же.
        candidate = normalize_cas(match.group(1))
        if candidate in seen:
            continue
        seen.add(candidate)
        if is_valid_cas(candidate):
            found.append(candidate)
    return found


def is_valid_ec(value: str) -> bool:
    """Контрольная сумма номера EINECS/EC: сумма i*d по модулю 11."""
    digits = value.replace("-", "")
    if len(digits) != 7 or not digits.isdigit():
        return False
    total = sum(int(d) * i for i, d in enumerate(digits[:6], start=1))
    return total % 11 == int(digits[6])


def find_ec_numbers(text: str) -> list[str]:
    """Европейские номера вещества, прошедшие контрольную сумму."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _EC_RE.finditer(text or ""):
        candidate = match.group(1)
        if candidate not in seen:
            seen.add(candidate)
            if is_valid_ec(candidate):
                found.append(candidate)
    return found


def find_inchikeys(text: str) -> list[str]:
    """Отпечатки структуры. Совпадение по ним сильнее совпадения по имени."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _INCHIKEY_RE.finditer(text or ""):
        key = match.group(1)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def looks_like_formula(value: str) -> bool:
    """Состоит ли строка только из символов настоящих элементов."""
    if not value or not value[0].isupper():
        return False
    position = 0
    symbols = 0
    for match in _FORMULA_TOKEN_RE.finditer(value):
        if match.start() != position:
            return False
        position = match.end()
        if match.group(1) not in _ELEMENTS:
            return False
        symbols += 1
    return position == len(value) and symbols >= 2


def find_molecular_formulas(text: str) -> list[str]:
    """Формулы, названные на странице словом-указателем.

    Без указателя отличить формулу от артикула нельзя: «C5H11NO2» и
    «AB12X» для регулярного выражения выглядят одинаково.
    """
    found: list[str] = []
    seen: set[str] = set()
    for match in _FORMULA_RE.finditer(text or ""):
        candidate = match.group(1)
        if candidate not in seen and looks_like_formula(candidate):
            seen.add(candidate)
            found.append(candidate)
    return found


def find_purity(text: str) -> list[str]:
    """Заявленная чистота в процентах."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _PURITY_RE.finditer(text or ""):
        value = match.group(1).replace(",", ".")
        if value not in seen:
            seen.add(value)
            found.append(value)
    return found


# Слова роли. Когда их три и больше подряд в одной строке, это не
# утверждение, а перечисление ключевых слов для поисковика: «China leading
# ... suppliers, factory & manufacturers» стоит на каждой товарной странице
# каталога и о конкретном товаре не говорит ничего.
_ROLE_WORDS = (
    "supplier",
    "suppliers",
    "manufacturer",
    "manufacturers",
    "factory",
    "factories",
    "exporter",
    "exporters",
    "trader",
    "traders",
    "distributor",
    "distributors",
    "wholesaler",
    "wholesalers",
    "producer",
    "producers",
    "vendor",
    "vendors",
)
_ROLE_WORD_RE = re.compile(
    r"\b(" + "|".join(_ROLE_WORDS) + r")\b", re.IGNORECASE
)
_MIN_STUFFED_ROLES = 3

# Формулировки, которые прямо отделяют продавца от производства. Они могут
# стоять рядом с точным CAS и выглядеть убедительно для модели, но говорят о
# партнёрском/контрактном заводе, а не о собственном изготовлении кандидата.
_THIRD_PARTY_PRODUCTION_MARKERS = (
    "associated production base",
    "associated production bases",
    "partner factory",
    "partner factories",
    "partner manufacturer",
    "manufacturing partner",
    "contract manufacturer",
    "cooperating factory",
    "sourced from manufacturers",
    "from our manufacturer",
    "manufacturer / supplier / principals",
    "партнерский завод",
    "партнёрский завод",
    "контрактный производитель",
    "合作工厂",
    "合作生产基地",
    "代工厂",
)


def looks_like_role_keyword_stuffing(quote: str) -> bool:
    """Перечисление ролей вместо утверждения о производстве.

    Замер на эпоксидированном соевом масле: перепродавец получил допуск в
    короткий список на строке «China leading Epoxidized Soybean Oil ESBO
    CAS 8013-07-8 suppliers, factory & manufacturers». Строка дословная,
    вещество в ней названо — и всё же она ничего не утверждает: тот же
    шаблон стоит на каждой из тысяч товарных страниц этого сайта.
    """
    found = {match.group(1).casefold() for match in _ROLE_WORD_RE.finditer(quote or "")}
    # Единственное и множественное число одной роли считаем за одну.
    roles = {word.rstrip("s") for word in found}
    return len(roles) >= _MIN_STUFFED_ROLES


def looks_like_third_party_production_claim(quote: str) -> bool:
    """Цитата ссылается на чужое производство, а не на завод кандидата."""
    lowered = (quote or "").casefold()
    return any(marker in lowered for marker in _THIRD_PARTY_PRODUCTION_MARKERS)


def mentions_substance(quote: str, *, cas: str | None, names: list[str]) -> bool:
    """Говорит ли цитата об искомом веществе, а не о компании вообще.

    «У нас свой завод» подтверждает, что компания что-то производит. Что
    она производит именно это вещество — не подтверждает. На бетаине
    доказательством роли служила строка «Our Gelatin Factory»: завод
    настоящий, вещество другое.
    """
    text = (quote or "").casefold()
    if not text:
        return False
    if cas and normalize_cas(cas).casefold() in text:
        return True
    return any(name.strip().casefold() in text for name in names if name.strip())


def find_document_mentions(text: str) -> dict[str, str]:
    """Упоминания GMP, ISO, CoA и TDS с дословной строкой страницы.

    Найденное здесь означает ровно одно: страница так написала. Это не
    независимое подтверждение, поэтому статус может быть только
    «заявлено» — сертификат проверяется документом, а не сайтом продавца.
    """
    mentions: dict[str, str] = {}
    lines = (text or "").splitlines()
    for claim, pattern in _DOCUMENT_RE.items():
        for raw in lines:
            line = raw.strip()
            if not line or len(line) > _MAX_LINE_CHARS:
                continue
            if pattern.search(line):
                mentions[claim] = line
                break
    return mentions


def cas_quote(text: str, cas: str) -> str | None:
    """Дословная строка страницы, содержащая искомый номер.

    Ищем не подстрокой, а разбором: на странице номер может быть написан
    другим дефисом, и тогда прямое сравнение строк его не найдёт.
    """
    target = normalize_cas(cas or "")
    if not target or not text:
        return None
    for line in text.splitlines():
        if any(
            normalize_cas(match.group(1)) == target
            for match in _CAS_RE.finditer(line)
        ):
            return _trimmed(line.strip(), target)
    return None


def _trimmed(line: str, needle: str) -> str | None:
    """Обрезает длинную строку, оставляя искомое внутри цитаты."""
    if not line:
        return None
    if len(line) <= _MAX_LINE_CHARS:
        return line
    position = line.find(needle)
    if position < 0:
        return line[:_MAX_LINE_CHARS]
    start = max(0, position - _MAX_LINE_CHARS // 2)
    return line[start : start + _MAX_LINE_CHARS]


def quote_for(text: str, needle: str) -> str | None:
    """Дословная строка страницы, содержащая указанное значение.

    Возвращается именно строка, а не окно символов: строка читается
    человеком в карточке доказательства и проходит проверку вхождением.
    """
    target = needle or ""
    if not target or not text:
        return None
    for line in text.splitlines():
        if target in line:
            stripped = line.strip()
            if stripped:
                # Длинную строку обрезаем вокруг совпадения, чтобы искомое
                # осталось внутри цитаты, а она — подстрокой текста.
                return _trimmed(stripped, target)
    return None


# Показатель без значения — это подпись из соседней ячейки таблицы, а не
# факт. На chemicalbook такие строки шли отдельно: «Molecular Formula:»,
# «Molecular Weight:», «| CAS number» — три из пяти строк подсветки.
_VALUE_RE = re.compile(r"[0-9A-Za-zА-Яа-я一-鿿]")


def _has_value(line: str) -> bool:
    """Есть ли за подписью что-то, кроме самой подписи.

    Строка таблицы отличается от её заголовка наличием цифр: «CAS number |
    Molecular Formula» — это шапка, «3253-41-6 | C21H28O8» — данные. Для
    строк вида «подпись: значение» такого требования нет: «Appearance:
    white powder» — законное значение без единой цифры.
    """
    for separator in (":", "："):
        _, found, tail = line.partition(separator)
        if found and _VALUE_RE.search(tail) and len(tail.strip()) >= 2:
            return True
    if "|" in line:
        _, _, tail = line.partition("|")
        return bool(tail.strip()) and any(char.isdigit() for char in line)
    return False


def spec_lines(text: str) -> list[str]:
    """Строки, похожие на спецификацию товара.

    Ищем не по вёрстке, а по содержанию: строка со знаком-разделителем,
    словом из словаря показателей и непустым значением после него. Такие
    строки плотнее прозы по фактам на символ.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or len(line) > _MAX_LINE_CHARS:
            continue
        if not _has_value(line):
            continue
        lowered = line.casefold()
        if not any(marker in lowered for marker in _SPEC_MARKERS):
            continue
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def substance_facts(text: str) -> dict[str, list[str]]:
    """Свойства вещества, читаемые со страницы без модели.

    Это описание того, что продают, а не оценка продавца: номера, формула
    и чистота либо написаны на странице, либо нет.
    """
    return {
        "cas": find_cas_numbers(text),
        "ec": find_ec_numbers(text),
        "inchikey": find_inchikeys(text),
        "formula": find_molecular_formulas(text),
        "purity_percent": find_purity(text),
    }


# Предел окрестности на случай страницы про одно вещество, где другого
# номера дальше просто нет.
_NEIGHBOURHOOD_LINES = 25


def substance_neighbourhood(text: str, cas: str | None) -> str:
    """Часть страницы, описывающая именно искомое вещество.

    Каталог поставщика перечисляет десятки веществ подряд, и формула из
    соседней карточки относится к другому товару: на странице Aobobio рядом
    с бетаином лежали C4H9NO2 и C5H14ClNO — чужие вещества.

    Границей служит соседний CAS-номер, а не число строк: именно он
    отделяет одну карточку товара от другой, как бы плотно они ни шли.
    """
    target = normalize_cas(cas or "")
    if not target or not text:
        return ""
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if target in line), None)
    if index is None:
        return ""

    def foreign_cas(line: str) -> bool:
        return any(value != target for value in find_cas_numbers(line))

    # Назад окно тянется только до чужого номера — и тогда не тянется
    # вовсе: свойства идут после своего номера, значит всё между чужим
    # номером и нашим описывает чужой товар.
    start = index
    while start > 0 and index - start < _NEIGHBOURHOOD_LINES:
        if foreign_cas(lines[start - 1]):
            start = index
            break
        start -= 1
    end = index
    limit = len(lines) - 1
    while end < limit and end - index < _NEIGHBOURHOOD_LINES:
        if foreign_cas(lines[end + 1]):
            break
        end += 1
    return "\n".join(lines[start : end + 1])


def build_highlights(text: str, *, cas: str | None) -> list[str]:
    """Строки, которые стоит показать модели раньше начала страницы.

    Порядок отражает ценность: искомый номер, затем опознавательные признаки
    рядом с ним, затем упоминания документов, затем прочая спецификация.

    Признаки берутся сначала из окрестности номера и только потом со всей
    страницы: на каталожной странице формула из соседней карточки описывает
    другой товар.

    Собираются только из самого текста, поэтому цитата из подсветки
    проверяется так же, как цитата из любого другого места страницы.
    """
    highlights: list[str] = []
    seen: set[str] = set()

    def add(line: str | None) -> None:
        if line and line not in seen and len(highlights) < _MAX_HIGHLIGHT_LINES:
            seen.add(line)
            highlights.append(line)

    if cas:
        add(cas_quote(text, cas))

    # Есть окрестность — берём признаки только из неё. Проход по всей
    # странице «на всякий случай» вернул бы ровно тот шум, ради которого
    # окрестность и вводилась.
    neighbourhood = substance_neighbourhood(text, cas)
    scope = neighbourhood or text
    facts = substance_facts(scope)
    for key in ("inchikey", "ec", "formula", "purity_percent"):
        for value in facts[key][:2]:
            add(quote_for(scope, value))

    for line in find_document_mentions(text).values():
        add(line)

    for line in spec_lines(neighbourhood or text):
        add(line)
    return highlights


def page_cas_match(text: str, cas: str | None) -> bool:
    """Есть ли искомый номер на странице — по полному тексту, до обрезки."""
    if not cas:
        return False
    return normalize_cas(cas) in find_cas_numbers(text or "")
