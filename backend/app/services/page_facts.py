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
from decimal import Decimal, InvalidOperation

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

# Контракт доказательства требует цитату не короче пяти символов, и это
# разумно: «TDS» само по себе ничего не доказывает. Строка страницы вполне
# может оказаться такой короткой — на прогоне по адипиновой кислоте это
# уронило весь этап оценки ошибкой проверки схемы.
MIN_QUOTE_CHARS = 5


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


# Разделители, которыми в заголовке страницы отбивают имя бренда:
# «China Adipic Acid Manufacturer and Supplier | AOJIN».
#
# Обычный дефис с пробелами добавлен 7 августа 2026: им отбита половина
# найденных заголовков — «Zinc Ricinoleate Manufacturer & Supplier in China
# - Echo Chemtech Co., Ltd.», — а правило их пропускало из-за одного
# отсутствующего символа. Замер по 69 принятым цитатам о роли: отсекает
# пять, все до одной — шапки товарных страниц.
_TITLE_SEPARATORS = ("|", "｜", " – ", " — ", " - ", " :: ", " » ")
_ROLE_NOUNS = (
    "manufacturer",
    "manufacturers",
    "supplier",
    "suppliers",
    "factory",
    "factories",
    # Слова продавца, а не завода. Стоят в шапках того же вида, и по одной
    # из них — «Top Zinc Ricinoleate Exporter from China - Wholesale
    # Solutions» — компания получала статус производителя. Список читает
    # только запрет на заголовок, и только для заявления о производстве,
    # так что признак торговой роли он не заденет.
    "exporter",
    "exporters",
    "wholesaler",
    "wholesalers",
    "производитель",
    "поставщик",
    "生产厂家",
    "厂家",
    "供应商",
)


def looks_like_page_title(quote: str) -> bool:
    """Заголовок страницы с именем бренда после разделителя.

    «China Adipic Acid Manufacturer and Supplier | AOJIN» — это тег title,
    написанный для поисковика. Слово «Manufacturer» там стоит потому, что
    его ищут, а не потому, что у компании есть завод: на той же странице
    Shandong Aojin перечисляет марки, которые перепродаёт — Hualu, Huafeng,
    Shenma. Проверка по эталону показала, что дистрибьютор получил статус
    производителя именно по такой строке.

    Правило намеренно узкое. Обычная фраза «Octadecyl-Behenyl Dimethyl
    Amine Manufacturer in China» под него не подпадает: разделителя нет, а
    отбрасывать все именные конструкции значило бы терять настоящие заводы.
    """
    text = (quote or "").strip()
    if not text:
        return False
    if not any(separator in text for separator in _TITLE_SEPARATORS):
        return False
    low = text.casefold()
    if not any(noun in low for noun in _ROLE_NOUNS):
        return False
    # Та же оговорка, что и у рекламных шапок: строка с годовым выпуском
    # или собственной площадкой утверждает факт, даже если набрана как
    # заголовок. Без неё расширенный набор разделителей отбрасывал бы
    # «Our own factory in Shandong - 20,000 tons per year».
    return not (_CAPACITY_RE.search(text) or _PLANT_RE.search(text))


# Приглашение купить. Слово «завод» в нём стоит как довод в продаже, а не
# как утверждение о себе, и шаблон одинаков на тысячах товарных страниц.
_BUY_INVITATION = (
    "welcome to buy",
    "welcome to wholesale",
    "welcome to order",
    "welcome to purchase",
    "feel free to buy",
    "buy bulk",
    "buy cheap",
    "buy discount",
    "欢迎购买",
    "欢迎订购",
)


def looks_like_purchase_invitation(quote: str) -> bool:
    """«Welcome to buy bulk adipic acid from our factory» — это призыв купить.

    Единственное, на чём держался статус производителя у Henan GP —
    подтверждённая эталоном ошибка классификации. Завода за строкой нет:
    адрес компании на той же странице — 29-й этаж бизнес-центра.

    Замер по 69 принятым цитатам о роли: правило отсекает 11, и все они —
    один и тот же шаблон в шести написаниях. Оговорка та же, что у шапок:
    строка с годовым выпуском или собственной площадкой проходит.
    """
    low = (quote or "").casefold()
    if not any(marker in low for marker in _BUY_INVITATION):
        return False
    return not (_CAPACITY_RE.search(quote or "") or _PLANT_RE.search(quote or ""))


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


def _collapsed(value: str) -> str:
    """Название без разделителей: пробелов, дефисов, скобок и запятых.

    Одно и то же вещество пишут и слитно, и раздельно: заявка говорит
    «Behenyldimethylamine», страница — «Behenyl Dimethyl Amine». Для
    поиска это одно слово, для сравнения подстрокой — разные.
    """
    return _NAME_SEPARATORS_RE.sub("", value or "").casefold()


# _DASHES заканчивается обычным дефисом, поэтому идёт последним: внутри
# класса дефис между символами читается как диапазон.
_NAME_SEPARATORS_RE = re.compile(rf"[\s,;·'’`()\[\]{_DASHES}]+")


def mentions_substance(quote: str, *, cas: str | None, names: list[str]) -> bool:
    """Говорит ли цитата об искомом веществе, а не о компании вообще.

    «У нас свой завод» подтверждает, что компания что-то производит. Что
    она производит именно это вещество — не подтверждает. На бетаине
    доказательством роли служила строка «Our Gelatin Factory»: завод
    настоящий, вещество другое.

    Разделители при сравнении убираются. Без этого запрос без номера почти
    всегда терял роль производителя: на Behenyldimethylamine отклонились
    все пять цитат, включая «Behenyl dimethylamine, CAS No. 21542-96-1,
    DMA22 factory and manufacturers» — вещество там названо дважды.
    """
    text = (quote or "").casefold()
    if not text:
        return False
    if cas and page_cas_match(quote, cas):
        return True
    collapsed_text = _collapsed(quote)
    for name in names:
        cleaned = name.strip()
        if not cleaned:
            continue
        if cleaned.casefold() in text:
            return True
        collapsed_name = _collapsed(cleaned)
        if collapsed_name and collapsed_name in collapsed_text:
            return True
    return False


# Юридические окончания названий компаний. По ним имя завода отличается от
# обычного словосочетания: «Shandong Hualu Hengsheng Chemical Co., Ltd.» —
# компания, «adipic acid production» — нет.
_COMPANY_TAILS = (
    r"Co\.,?\s*Ltd\.?",
    r"Co\.,?\s*Limited",
    r"Company\s+Limited",
    r"Corporation",
    r"Corp\.?",
    r"Group\s+Co\.",
    r"Chemical\s+Co\.",
    r"Petrochemical",
    r"Holdings?",
    r"Inc\.?",
    r"GmbH",
    r"S\.?A\.?S\.?",
    r"LLC",
)
_COMPANY_RE = re.compile(
    r"\b([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,5}\s+(?:"
    + "|".join(_COMPANY_TAILS)
    + r"))",
)
# Китайское юридическое лицо: «山东华鲁恒升化工股份有限公司».
_COMPANY_CN_RE = re.compile(r"[一-鿿]{2,12}(?:有限公司|股份有限公司|集团)")

# Слова, после которых «компания» оказывается разделом сайта, а не заводом.
_NOT_A_COMPANY = (
    "market report",
    "research",
    "consulting",
    "news",
    "database",
    # Торговый дом и так попадает в выдачу обычными запросами. Второй
    # заход существует ради заводов, которых там нет.
    "贸易",
    "trading",
    # Рядом со словом о мощности стоит не только имя завода: «产品产能»,
    # «行业产能», «染物综合排放标准». На прогонах 67 и 68 такие обрывки
    # заняли слоты второго захода.
    "标准",
    "行业",
    "市场",
    "产品",
    "技术",
    "工艺",
    "价格",
    "应用",
    "全球",
    "国内",
)
_MAX_COMPANY_NAMES = 20
# Отраслевой обзор называет завод одной маркой, без «有限公司»: «华鲁恒升
# 产能», «神马 年产». Замер по адипиновой кислоте: 华鲁恒升 стоял в выдаче
# и не извлекался, потому что юридического хвоста рядом не было.
_PRODUCTION_WORDS = "产能|年产|生产企业|生产厂家|龙头企业|装置"
_COMPANY_CN_BRAND_RE = re.compile(
    rf"([一-鿿]{{2,8}})\s*(?:{_PRODUCTION_WORDS})"
)
# Куски ссылок и имена файлов попадали в имя компании целиком:
# «Food-Acidity-Regulators-...-1999492502.html Tangshan Zhonghao Co., Ltd».
_LOOKS_LIKE_URL_PART = re.compile(r"\.(?:html?|php|aspx)\b|://|\d{6,}")
_MAX_NAME_TOKEN_CHARS = 24


def _cleaned_company_name(name: str) -> str | None:
    """Отбрасывает служебный мусор, приклеившийся к имени слева."""
    tokens = [
        token
        for token in name.split()
        if not _LOOKS_LIKE_URL_PART.search(token)
        and len(token) <= _MAX_NAME_TOKEN_CHARS
    ]
    cleaned = " ".join(tokens).strip(" .,-")
    return cleaned or None


def find_company_names(text: str) -> list[str]:
    """Названия компаний, упомянутые в тексте.

    Зачем. Заводы многотоннажной химии не оптимизируют страницы под запрос
    «вещество + manufacturer»: у них корпоративные сайты, а не карточки
    товара. В прогоне по адипиновой кислоте система нашла торговые дома,
    тогда как рынок держат Shenma, Hualu Hengsheng и Ляоянский НПЗ — их
    имена стоят в отраслевых обзорах.

    Поэтому обзор полезен не как источник поставщика, а как источник
    имён: по имени компании её собственный сайт находится сразу.
    """
    found: list[str] = []
    seen: set[str] = set()
    # Марки рядом со словами о мощности идут первыми: это имена из
    # отраслевых обзоров, то есть ровно те заводы, которые обычными
    # запросами не находятся. Раньше они стояли последними и обрезались
    # лимитом — на адипиновой кислоте второй заход ушёл к торговым домам,
    # а 华鲁恒升 из того же текста остался неспрошенным.
    for pattern in (_COMPANY_CN_BRAND_RE, _COMPANY_RE, _COMPANY_CN_RE):
        for match in pattern.finditer(text or ""):
            # У марки без юридического хвоста берётся первая группа: сам
            # производственный термин частью имени не является.
            raw = match.group(1) if pattern is _COMPANY_CN_BRAND_RE else match.group(0)
            name = _cleaned_company_name(" ".join((raw or "").split()))
            if not name:
                continue
            key = name.casefold()
            # Иероглифическое имя плотнее латинского: «华鲁恒升» — четыре знака.
            minimum = 2 if _COMPANY_CN_RE.search(name) or pattern is _COMPANY_CN_BRAND_RE else 6
            if key in seen or len(name) < minimum:
                continue
            if any(word in key for word in _NOT_A_COMPANY):
                continue
            seen.add(key)
            found.append(name)
            if len(found) >= _MAX_COMPANY_NAMES:
                return found
    return found


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
            if len(line) < MIN_QUOTE_CHARS:
                continue
            if pattern.search(line):
                mentions[claim] = line
                break
    return mentions


# Мощность и производственная база. Это то, что завод о себе пишет, а
# перекупщик обычно не пишет: цифру годового выпуска, площадь цеха, год
# пуска линии. В поисковых запросах эти слова уже показали себя — они
# доводили до настоящих производителей там, где «manufacturer» приводил
# к торговым домам. Здесь они читаются со страницы как факт.
_CAPACITY_RE = re.compile(
    r"("
    r"年产\s*[\d.,]+|产能[^。\n]{0,20}?[\d.,]+\s*(?:万吨|吨|吨/年)"
    r"|[\d.,]+\s*(?:万吨|吨/年)"
    r"|annual\s+(?:production\s+)?(?:capacity|output|production)\s*"
    r"(?:of|is|:)?\s*[\d.,]+"
    r"|[\d.,]+\s*(?:metric\s+)?(?:tons?|tonnes?|mt)\s*(?:per|/|a)\s*(?:year|annum)"
    r"|production\s+capacity\s*(?:of|is|:)?\s*[\d.,]+"
    r")",
    re.IGNORECASE,
)
# Собственная производственная площадка: не цифра, но и не заявление
# «мы производитель» — это проверяемая деталь.
_PLANT_RE = re.compile(
    r"(生产基地|生产车间|自有工厂|我们的工厂"
    r"|own\s+(?:factory|plant|production\s+base)"
    r"|production\s+base|manufacturing\s+base"
    r"|(?:factory|plant)\s+(?:covers|area|address|located)"
    r"|covers\s+an\s+area\s+of)",
    re.IGNORECASE,
)


# Самая ходовая самохарактеристика торгового сайта. Роли в ней столько
# же, сколько в слове «лучший»: это позиционирование, а не факт.
_LEADING_BOILERPLATE = (
    "one of the leading",
    "one of the top",
    "one of the best",
    "one of the largest",
    "one of the most professional",
    "one of the professional",
    # Найдены замером 7 августа 2026 среди принятых цитат о роли: те же
    # обороты, что и выше, но мимо списка из-за артикля или прилагательного.
    "one of the most reliable",
    "one of the most trusted",
    "one of the most experienced",
    "a leading manufacturer",
    "a leading supplier",
    "leading manufacturer and supplier",
    "leading manufacturers and suppliers",
    "professional manufacturer and supplier",
    "professional manufacturers and suppliers",
    "国内领先",
    "领先的生产",
    "知名生产厂家",
    "один из ведущих",
)


def looks_like_leading_supplier_boilerplate(quote: str) -> bool:
    """«Один из ведущих производителей и поставщиков X» — не доказательство.

    Tianjin Gnee прошёл в короткий список по строке «Tianjin Gnee Biotech
    Co., Ltd. is one of the leading manufacturers and suppliers of 99%
    behenyl dimethyl amine … in China». Предложение связное, вещество
    названо, номер приведён — прежние проверки его пропускают. Факта о
    производстве в нём нет: адрес компании — 25-й этаж бизнес-центра.

    Если рядом стоит проверяемая деталь — годовой выпуск или собственная
    площадка, — это уже утверждение о производстве, и оно проходит.
    """
    low = (quote or "").casefold()
    if not low:
        return False
    if not any(marker in low for marker in _LEADING_BOILERPLATE):
        return False
    return not (_CAPACITY_RE.search(quote) or _PLANT_RE.search(quote))


# Самоописание торговой компании. Требуется именно подлежащее «мы» или
# «компания есть», иначе слово ловится где угодно: в выпадающем списке
# «тип организации» на форме обратной связи и в вопросе FAQ «вы завод или
# торговая компания?» — там оно стоит в вопросе, а в ответе значится завод.
_TRADE_RE = re.compile(
    r"(authoris?ed\s+(?:distributor|agent|dealer)"
    r"|official\s+distributor|exclusive\s+distributor"
    r"|(?:we\s+are|is)\s+(?:an?|the)\s+(?:\w+\s+){0,3}"
    r"(?:distributor|trader|trading\s+company|trading\s+house)"
    r"|(?:import\s+and\s+export|domestic\s+trade)[^.]{0,80}"
    r"(?:supply\s+chain|domestic\s+trade|distribution)"
    r"|授权代理商|一级代理商|独家代理"
    r"|我们是[^。]{0,20}(?:经销商|代理商|贸易))",
    re.IGNORECASE,
)
# Строка вопроса, а не утверждения.
_TRADE_QUESTION_RE = re.compile(r"(are\s+you|\?\s*A\s*[:：]|Q\s*[:：])", re.IGNORECASE)


# Обороты отраслевого обзора. Одного слова «market» мало: оно стоит и на
# обычной товарной странице.
_MARKET_TEXT_RE = re.compile(
    r"(market\s+(?:size|share|report|analysis|research|outlook|overview|forecast)"
    r"|\bCAGR\b|forecast\s+period|market\s+is\s+(?:projected|expected|estimated)"
    r"|key\s+players|competitive\s+landscape"
    r"|объ[её]м\s+рынка|анализ\s+рынка|市场规模|市场分析|行业报告)",
    re.IGNORECASE,
)
# Раздел сайта, где лежат статьи, а не карточки товара.
_ARTICLE_PATH_RE = re.compile(
    r"/(report|reports|news|blog|article|articles|insight|insights|press|market)"
    r"[-/]",
    re.IGNORECASE,
)
_MIN_MARKET_PHRASES = 2


def looks_like_market_report(url: str, text: str) -> bool:
    """Страница про рынок, а не про компанию.

    Отчёт «potassium sorbate market» на straitsresearch.com перечислял
    ведущих игроков, модель взяла оттуда имя Henan GP Chemicals, а
    контакты снялись со страницы — и в реестре появился «Henan GP» с
    почтой исследовательского агентства. Письмо по ней ушло бы не тому.

    Требуются оба признака сразу. Замер по 1305 сохранённым страницам:
    вместе они дают 7 попаданий, и все семь — настоящие обзоры рынка. По
    одним оборотам сработало бы ещё 21, среди них живые заводы: у
    Shandong Xinjiangye, который стоит в эталоне производителем, таких
    оборотов шесть.
    """
    if not _ARTICLE_PATH_RE.search(url or ""):
        return False
    return len(_MARKET_TEXT_RE.findall(text or "")) >= _MIN_MARKET_PHRASES


def find_trade_facts(text: str) -> dict[str, str]:
    """Прямое заявление о перепродаже, дословной строкой страницы.

    Зачем. Роль производителя мы доказывать умеем, а роль торговой
    компании — нет, и всё недоказанное падало в «не определён». Закупщику
    такой ответ бесполезен вдвойне: он не знает ни того, что перед ним
    завод, ни того, что перед ним посредник, хотя страница говорит об
    этом прямым текстом.

    Замер по сохранённым прогонам 214–252: признак срабатывает на пяти
    карточках, все пять согласны с эталоном, ни одна не спорит с
    доказательством производства. Выборка узкая — это одна компания,
    Shandong Aojin, — поэтому вывод из факта делается только там, где
    производство не доказано.
    """
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not (MIN_QUOTE_CHARS <= len(line) <= _MAX_LINE_CHARS):
            continue
        if _TRADE_QUESTION_RE.search(line):
            continue
        if _TRADE_RE.search(line):
            return {"reseller_role": line}
    return {}


# Адрес в промышленной зоне. Завод стоит в промзоне или отдельным
# корпусом — так пишут все руководства по проверке поставщиков.
_PLANT_ADDRESS_RE = re.compile(
    r"(工业园|化工园|经济开发区|工业区|产业园|高新区"
    r"|industrial\s+(?:park|zone|area|estate)"
    r"|(?:economic|technological)\s+development\s+zone"
    r"|chemical\s+(?:park|industrial))",
    re.IGNORECASE,
)
# Адрес в бизнес-центре: этаж, комната, офис, башня. Посредник сидит в
# офисе, и это единственный признак, которого на этих страницах много.
_OFFICE_ADDRESS_RE = re.compile(
    r"(\d+(?:st|nd|rd|th)\s+floor|floor\s*\d+|room\s*\d+|suite\s*\d+"
    r"|\bplaza\b|\bbusiness\s+(?:center|centre|building)\b"
    r"|写字楼|大厦|商务楼|办公楼)",
    re.IGNORECASE,
)


def find_address_facts(text: str) -> dict[str, str]:
    """Офисный адрес компании — дословной строкой страницы.

    Производство по этим страницам доказывать нечем: замер по 136
    карточкам прогонов 214–264 дал ровно один номер государственной
    лицензии и шесть упоминаний выпуска или площадки. Зато опровергать
    есть чем — офисный адрес нашёлся у 62 карточек, и у 27 из 50 нынешних
    «производителей» он стоит при полном отсутствии производственных
    фактов: Henan GP на 29-м этаже, Echo Chemtech в комнате 1602,
    Shanghai Douwin на 16-м.

    Возвращается только офисный адрес и только тогда, когда на странице
    нигде нет признаков промзоны: у завода в промышленном парке вполне
    бывает и номер корпуса, и это не повод его понижать.
    """
    body = text or ""
    if _PLANT_ADDRESS_RE.search(body):
        return {}
    for raw in body.splitlines():
        line = raw.strip()
        if not (MIN_QUOTE_CHARS <= len(line) <= _MAX_LINE_CHARS):
            continue
        if _OFFICE_ADDRESS_RE.search(line):
            return {"office_address": line}
    return {}


def find_production_facts(text: str) -> dict[str, str]:
    """Мощность и производственная база с дословной строкой страницы.

    Служит второй опорой короткого списка вместо упоминания документа.
    Замер по 129 кандидатам: хоть какое-то упоминание CoA, TDS, ISO или
    GMP есть лишь у 34%, и ни одно из них не подтверждено — по нашему же
    правилу сайт продавца сертификат не подтверждает. При этом документы
    у заказчика приходят перепиской за 2–4 дня после контакта, то есть
    требовать их на этапе поиска значит требовать несуществующего.

    Привязки к веществу здесь не требуется: за неё отвечают отдельные
    доказательства идентичности и роли, а мощность говорит о компании.
    """
    facts: dict[str, str] = {}
    for claim, pattern in (
        ("production_capacity", _CAPACITY_RE),
        ("production_site", _PLANT_RE),
    ):
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or len(line) > _MAX_LINE_CHARS:
                continue
            if len(line) < MIN_QUOTE_CHARS:
                continue
            if pattern.search(line):
                facts[claim] = line
                break
    return facts


# Фасовка и минимальный заказ читаются только рядом с явным словом-маркером.
# Иначе строка «USD 10/kg» превращает единицу цены в упаковку 10 kg. Единицы
# приводятся к граммам или миллилитрам; массу в объём без подтверждённой
# плотности не пересчитываем.
_SUPPLY_QUANTITY_RE = re.compile(
    r"(?<![/\w])(?P<value>\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*"
    r"(?P<unit>metric\s+tons?|tonnes?|tons?|kilograms?|milligrams?|grams?|"
    r"cubic\s+met(?:er|re)s?|millilit(?:er|re)s?|lit(?:er|re)s?|"
    r"m[³3]|mt|kgs?|kg|mgs?|mg|g|ml|ltr|l)\b",
    re.IGNORECASE,
)
_SHARED_UNIT_RANGE_RE = re.compile(
    r"(?<![/\w])(?P<minimum>\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*"
    r"(?:-|–|—|to|до)\s*"
    r"(?P<maximum>\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?)\s*"
    r"(?P<unit>metric\s+tons?|tonnes?|tons?|kilograms?|milligrams?|grams?|"
    r"cubic\s+met(?:er|re)s?|millilit(?:er|re)s?|lit(?:er|re)s?|"
    r"m[³3]|mt|kgs?|kg|mgs?|mg|g|ml|ltr|l)\b",
    re.IGNORECASE,
)
_PACKAGING_MARKER_RE = re.compile(
    r"(pack(?:age|aging|ing|\s+size|\s+sizes)?|available\s+sizes?|"
    r"фасовк\w*|упаковк\w*|包装|包裝)",
    re.IGNORECASE,
)
_MOQ_MARKER_RE = re.compile(
    r"(\bMOQ\b|minimum\s+order(?:\s+quantity)?|минимальн\w*\s+заказ|"
    r"мин\.?\s*заказ|起订量|最小起订量)",
    re.IGNORECASE,
)
_ORDER_RANGE_MARKER_RE = re.compile(
    r"(order\s+(?:quantity\s+)?range|available\s+order\s+quantity|"
    r"диапазон\w*\s+заказ|объ[её]м\w*\s+заказ)",
    re.IGNORECASE,
)
_LAB_CATALOG_RE = re.compile(
    r"(research\s+use\s+only|for\s+laboratory\s+use|laboratory\s+reagent|"
    r"analytical\s+standard|лабораторн\w*\s+реактив|только\s+для\s+исследован|"
    r"仅供科研|实验室试剂)",
    re.IGNORECASE,
)
_UNIT_FACTORS: dict[str, tuple[str, Decimal, str]] = {
    "mg": ("mass", Decimal("0.001"), "g"),
    "g": ("mass", Decimal("1"), "g"),
    "kg": ("mass", Decimal("1000"), "g"),
    "mt": ("mass", Decimal("1000000"), "g"),
    "t": ("mass", Decimal("1000000"), "g"),
    "ml": ("volume", Decimal("1"), "mL"),
    "l": ("volume", Decimal("1000"), "mL"),
    "m3": ("volume", Decimal("1000000"), "mL"),
}


def _unit_key(value: str) -> str | None:
    unit = " ".join((value or "").casefold().replace("³", "3").split())
    if unit in {"mg", "mgs", "milligram", "milligrams"}:
        return "mg"
    if unit in {"g", "gram", "grams"}:
        return "g"
    if unit in {"kg", "kgs", "kilogram", "kilograms"}:
        return "kg"
    if unit in {
        "mt",
        "t",
        "ton",
        "tons",
        "tonne",
        "tonnes",
        "metric ton",
        "metric tons",
    }:
        return "mt"
    if unit in {"ml", "milliliter", "milliliters", "millilitre", "millilitres"}:
        return "ml"
    if unit in {"l", "ltr", "liter", "liters", "litre", "litres"}:
        return "l"
    if unit in {"m3", "cubic meter", "cubic meters", "cubic metre", "cubic metres"}:
        return "m3"
    return None


def _decimal_number(value: str) -> Decimal | None:
    compact = (value or "").replace("\u00a0", "").replace(" ", "")
    if not compact:
        return None
    if "," in compact and "." in compact:
        decimal_mark = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands_mark = "." if decimal_mark == "," else ","
        compact = compact.replace(thousands_mark, "").replace(decimal_mark, ".")
    elif compact.count(",") == 1:
        left, right = compact.split(",")
        compact = left + right if len(right) == 3 else f"{left}.{right}"
    try:
        number = Decimal(compact)
    except InvalidOperation:
        return None
    return number if number > 0 else None


def _quantity(value: str, unit: str, *, quote: str) -> dict | None:
    number = _decimal_number(value)
    key = _unit_key(unit)
    if number is None or key is None:
        return None
    dimension, factor, normalized_unit = _UNIT_FACTORS[key]
    normalized = number * factor
    return {
        "raw": f"{value.strip()} {unit.strip()}",
        "normalized_value": float(normalized),
        "normalized_unit": normalized_unit,
        "dimension": dimension,
        "quote": quote,
    }


def _line_quantities(line: str) -> list[dict]:
    quantities: list[dict] = []
    seen: set[tuple[str, float]] = set()
    for match in _SUPPLY_QUANTITY_RE.finditer(line):
        item = _quantity(match.group("value"), match.group("unit"), quote=line)
        if item is None:
            continue
        key = (item["dimension"], item["normalized_value"])
        if key not in seen:
            seen.add(key)
            quantities.append(item)
    return quantities


def _shared_unit_range(line: str) -> tuple[dict, dict] | None:
    match = _SHARED_UNIT_RANGE_RE.search(line)
    if match is None:
        return None
    minimum = _quantity(match.group("minimum"), match.group("unit"), quote=line)
    maximum = _quantity(match.group("maximum"), match.group("unit"), quote=line)
    if minimum is None or maximum is None:
        return None
    return minimum, maximum


def find_supply_volume_facts(text: str) -> dict:
    """Фасовки, MOQ, диапазоны и лабораторные признаки с цитатами страницы."""
    packages: list[dict] = []
    moqs: list[dict] = []
    ranges: list[dict] = []
    lab_signals: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not (MIN_QUOTE_CHARS <= len(line) <= _MAX_LINE_CHARS):
            continue
        if _LAB_CATALOG_RE.search(line) and line not in lab_signals:
            lab_signals.append(line)
        marker = (
            _PACKAGING_MARKER_RE.search(line)
            or _MOQ_MARKER_RE.search(line)
            or _ORDER_RANGE_MARKER_RE.search(line)
        )
        if marker is None:
            continue
        quantities = _line_quantities(line)
        shared_range = _shared_unit_range(line)
        if _ORDER_RANGE_MARKER_RE.search(line):
            endpoints = list(shared_range or ()) or quantities[:2]
            if (
                len(endpoints) >= 2
                and endpoints[0]["dimension"] == endpoints[1]["dimension"]
            ):
                ordered = sorted(
                    endpoints[:2], key=lambda item: item["normalized_value"]
                )
                ranges.append(
                    {
                        "minimum": ordered[0],
                        "maximum": ordered[1],
                        "quote": line,
                    }
                )
                continue
        if _MOQ_MARKER_RE.search(line) and quantities:
            moqs.extend(quantities)
        if _PACKAGING_MARKER_RE.search(line) and quantities:
            packages.extend(quantities)
    return {
        "packaging": packages,
        "moq": moqs,
        "order_ranges": ranges,
        "lab_catalog_signals": lab_signals[:3],
    }


def _requested_quantity(value: str | None) -> dict | None:
    if not value:
        return None
    parsed = _line_quantities(value.strip())
    return parsed[0] if len(parsed) == 1 else None


def assess_supply_volume(
    text: str,
    requested_volume: str | None,
    *,
    source_url: str,
    industrial_mass_kg: float = 20,
    industrial_volume_l: float = 20,
) -> dict:
    """Сопоставляет потребность RFQ только с фактами первичной страницы."""
    requested = _requested_quantity(requested_volume)
    facts = find_supply_volume_facts(text)
    result = {
        "status": "unknown",
        "requested_volume": requested,
        "requested_volume_raw": requested_volume,
        "found_packaging": facts["packaging"],
        "moqs": facts["moq"],
        "moq": facts["moq"][0] if facts["moq"] else None,
        "order_ranges": facts["order_ranges"],
        "order_range": facts["order_ranges"][0] if facts["order_ranges"] else None,
        "lab_catalog_signals": facts["lab_catalog_signals"],
        "source_url": source_url,
        "quote": None,
        "reason": "На первичной странице не найдены подтверждённые фасовка, диапазон заказа или MOQ.",
    }
    if requested is None:
        result["reason"] = (
            "Требуемый объём не указан или не нормализуется в поддерживаемую единицу массы/объёма."
        )
        return result

    dimension = requested["dimension"]
    target = Decimal(str(requested["normalized_value"]))
    industrial_base_quantity = {
        "mass": Decimal(str(industrial_mass_kg)) * Decimal("1000"),
        "volume": Decimal(str(industrial_volume_l)) * Decimal("1000"),
    }
    decisions: list[tuple[str, str, str]] = []
    for order_range in facts["order_ranges"]:
        minimum = order_range["minimum"]
        maximum = order_range["maximum"]
        if minimum["dimension"] != dimension or maximum["dimension"] != dimension:
            continue
        lower = Decimal(str(minimum["normalized_value"]))
        upper = Decimal(str(maximum["normalized_value"]))
        status = "compatible" if lower <= target <= upper else "incompatible"
        decisions.append(
            (status, order_range["quote"], "подтверждённый диапазон заказа")
        )

    for moq in facts["moq"]:
        if moq["dimension"] != dimension:
            continue
        minimum = Decimal(str(moq["normalized_value"]))
        industrial_floor = industrial_base_quantity[dimension]
        if target >= industrial_floor:
            # MOQ лабораторного магазина в 20 g не доказывает способность
            # поставить 500 kg. Промышленный MOQ (20 kg/L и выше), напротив,
            # подтверждает масштаб даже когда его минимум выше потребности RFQ.
            if minimum >= industrial_floor:
                decisions.append(
                    ("compatible", moq["quote"], "подтверждённый промышленный MOQ")
                )
        else:
            status = "compatible" if target >= minimum else "incompatible"
            decisions.append((status, moq["quote"], "подтверждённый MOQ"))

    comparable_packages = [
        item for item in facts["packaging"] if item["dimension"] == dimension
    ]
    if comparable_packages:
        largest = max(comparable_packages, key=lambda item: item["normalized_value"])
        largest_value = Decimal(str(largest["normalized_value"]))
        threshold = min(target, industrial_base_quantity[dimension])
        status = "compatible" if largest_value >= threshold else "incompatible"
        decisions.append((status, largest["quote"], "подтверждённая фасовка"))

    statuses = {item[0] for item in decisions}
    if not decisions:
        if facts["packaging"] or facts["moq"] or facts["order_ranges"]:
            result["reason"] = (
                "На странице есть количественные данные, но их единицы нельзя безопасно сравнить с потребностью RFQ."
            )
        return result
    if len(statuses) > 1:
        result["quote"] = decisions[0][1]
        result["reason"] = (
            "Фасовка, диапазон заказа и MOQ дают противоречащие выводы; требуется ручная проверка."
        )
        return result

    status, quote, basis = decisions[0]
    result["status"] = status
    result["quote"] = quote
    if status == "compatible":
        result["reason"] = f"Потребность совместима: {basis} покрывает требуемый объём."
    else:
        lab_note = (
            " Найдены признаки лабораторного каталога."
            if facts["lab_catalog_signals"]
            else ""
        )
        result["reason"] = (
            f"Потребность несовместима: {basis} не покрывает требуемый "
            f"объём.{lab_note}"
        )
    return result


# Многоточие, которым модель сокращает длинную цитату.
_ELLIPSIS_RE = re.compile(r"\.{3}|…")
# Минимальная длина куска, который имеет смысл проверять отдельно: на
# коротком обрывке совпадение случайно.
_MIN_QUOTE_FRAGMENT = 12


def _comparable(value: str) -> str:
    """Текст без разницы в пробелах: перенос строки и пробел равны."""
    return " ".join((value or "").split())


def quote_is_on_page(quote: str, page_text: str) -> bool:
    """Есть ли цитата на странице — с поправкой на оформление, но не на смысл.

    Требование дословности держит всю систему доказательств, и ослаблять
    его нельзя. Но половина отказов оказалась не выдумкой модели, а
    разницей в наборе: из 67 отклонённых цитат 15 отличались только
    пробелами, 3 — краевыми знаками, 16 были сокращены многоточием, и все
    их куски стояли на странице. Терялись при этом и нужные факты, вроде
    «Factory Site Yudu County, Ganzhou, Jiangxi, China».

    Каждое слово по-прежнему обязано быть на странице: у сокращённой
    цитаты проверяется каждый кусок отдельно. Выдуманного текста это не
    пропускает — остальные 32 отказа остались отказами.
    """
    page = _comparable(page_text)
    if not page:
        return False
    cleaned = _comparable(quote).strip(" .,:;«»\"'")
    if not cleaned:
        return False
    if cleaned in page:
        return True
    fragments = [
        part.strip(" .,:;«»\"'")
        for part in _ELLIPSIS_RE.split(cleaned)
    ]
    fragments = [part for part in fragments if len(part) >= _MIN_QUOTE_FRAGMENT]
    return bool(fragments) and all(part in page for part in fragments)


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
    """Обрезает длинную строку, оставляя искомое внутри цитаты.

    Слишком короткая строка не возвращается вовсе: контракт доказательства
    её не примет, и вместо пропуска одного факта упадёт весь этап.
    """
    if not line or len(line) < MIN_QUOTE_CHARS:
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
