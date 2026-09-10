"""Опознание вещества по названию: номер CAS и правильное написание.

Зачем. Закупщик присылает позиции названиями, а не номерами. Замер на семи
реальных позициях заказчика (19.08.2026, запуски 289-297): у всех семи
идентификация осталась `unverified`, потому что сопоставление «название ->
CAS» в конвейере не выполнялось вовсе — PubChem вызывался по номеру, которого
на входе нет. В поиск уходила введённая строка как есть, включая опечатку
«Silicon quaternium-18» вместо «Silicone», и уходила в кавычках, то есть в
самом жёстком режиме.

Что делает этот модуль. По названию возвращает список кандидатов: правильное
написание, номер и — отдельно — соседние названия, которые обозначают другое
вещество. Выбор остаётся за человеком: модуль ничего не подставляет молча.

Почему соседние названия важны не меньше правильных. Ровно на этом заказчик
потерял два месяца: закупили поликватерниум-22 вместо силикон-кватерниума-22,
потому что названия соседние. У неправильного варианта при этом обычно есть
настоящий номер, а у правильного номера может не быть совсем — так устроены
INCI-названия функционализированных силиконов. Поэтому «похоже, но другое»
показывается наравне с «то же самое» и уходит в отрицательный фильтр поиска.

Правила доказательности здесь те же, что во всём проекте. Номер, названный
моделью, принимается только если он проходит контрольную сумму и дословно
присутствует в процитированном фрагменте. Непрошедший номер не выбрасывается
молча: кандидат остаётся без номера, а причина попадает в предупреждения.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.connectors.pubchem import PubChemConnector
from app.connectors.web_search import (
    SearchProviderNotConfigured,
    SearchSourceBlocked,
    UnknownSearchProvider,
    search_web,
)
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.services.cas import is_valid_cas, normalize_cas
from app.services.stoichiometry import compare_names

logger = logging.getLogger(__name__)

# Номер в свободном тексте: две-семь цифр, две цифры, одна контрольная.
_CAS_PATTERN = re.compile(r"\b\d{2,7}-\d{2}-\d\b")

_CYRILLIC_PATTERN = re.compile(r"[Ѐ-ӿ]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")


def has_cyrillic(text: str) -> bool:
    """Название написано по-русски хотя бы частично.

    Проверять это нужно до поиска, а не после. Рынок, на котором система ищет
    поставщиков, русского написания не знает: 10.09.2026 «Дигидроксимоноацетат
    алюминия» ушёл дословно и в кавычках во все девять запросов прогона, и все
    девять вернули пустую выдачу. Прогон при этом упал с сообщением про
    ограничение доступа к поисковику — неверный диагноз вдобавок к пустому
    результату.
    """
    return bool(_CYRILLIC_PATTERN.search(text))


def _is_international(name: str) -> bool:
    """Название годится для внешнего поиска: латиница и никакой кириллицы."""
    return bool(_LATIN_PATTERN.search(name)) and not has_cyrillic(name)


# Сколько названий показывать. Список выбирают глазами, и длинный список
# выбирать труднее, чем короткий: он превращается в ту же выдачу поисковика,
# от которой мы уходим.
_MAX_CANDIDATES = 8
# Сколько синонимов PubChem доносить до формы. Полный список у популярных
# веществ уходит за сотню строк и состоит в основном из складских артикулов.
_MAX_SYNONYMS = 12
# Восемь на запрос вместо шести. Потолок кандидатов и раньше был восемь, а
# приходило по три: моделью выбирать было не из чего — на пяти запросах
# набиралось около десятка фрагментов, и половина из них про одно и то же.
_SEARCH_RESULTS_PER_QUERY = 8

_SYSTEM_PROMPT = """Ты помогаешь специалисту по закупкам химического сырья
опознать вещество по названию, которое он ввёл.

Тебе дают введённое название и фрагменты веб-выдачи. Верни список кандидатов.

Каждый кандидат — это одно название вещества, и у него есть отношение к
запросу:
- "same": это то же самое вещество, что запросил специалист. Сюда же —
  исправленное написание и общепринятое название вместо торгового.
- "different": название очень похоже на запрошенное, но обозначает ДРУГОЕ
  вещество. Такие кандидаты особенно важны: специалист использует их как
  отрицательный фильтр, чтобы не закупить не то.

Правила:
- Во введённом названии может быть опечатка. Если в выдаче встречается
  общепринятое написание, отличающееся от введённого, верни его как "same" и
  объясни разницу в reason. Опечатку не повторяй.
- Номер CAS указывай, только если он есть в приведённых фрагментах. Если
  номера в тексте нет — ставь null. Не восстанавливай номер по памяти.
- В поле quote приведи дословный фрагмент из выдачи, где видно название и
  номер. Не переписывай его своими словами.
- source_url — адрес того фрагмента, который ты процитировал.
- reason — одно предложение по-русски: почему это то же вещество или чем оно
  отличается.
- У смесей, полимеров и INCI-названий номера может не быть в принципе. Это
  нормальный ответ, а не ошибка: верни кандидата без номера.
- Не выдумывай названия, которых нет во фрагментах.
- Верни все различающиеся написания, которые встретились во фрагментах, а не
  два-три самых очевидных. Систематическое, торговое, INCI, сокращённое,
  написание через дефис и без — для закупщика это разные ключи поиска, и
  выбирает он глазами. Меньше пяти возвращай только тогда, когда во
  фрагментах их правда меньше.
- Если введённое название написано по-русски, обязательно верни международное
  написание — то, под которым вещество продают на внешнем рынке (английское,
  INCI или систематическое). Ставь его первым кандидатом с relation "same".
  Русское написание кандидатом не возвращай: по нему поставщиков не найти.
  Международного написания нет во фрагментах — верни кандидатов без него, но
  не переводи название сам."""

_TRANSLATION_SYSTEM_PROMPT = """Специалист по закупкам ввёл название
химического вещества по-русски. Назови, как это вещество называется в
международной номенклатуре — по-английски, латиницей.

Это перевод названия, а не поиск фактов. Русское химическое название
собрано из тех же морфем, что и английское: «дигидрокси-» — dihydroxy-,
«моноацетат» — monoacetate, «алюминия» — aluminium. Разбери название и
собери английское.

Правила:
- Не называй номер CAS. Совсем. Номер проверяется отдельно и по источнику;
  названный по памяти, он уводит закупку к другому веществу.
- Не транслитерируй. «Digidroksimonoatsetat» — не название вещества, по
  нему ничего не найти. Нужен химический термин.
- Дай до трёх вариантов написания, от самого употребительного к редкому:
  систематическое, торговое или INCI, если они есть.
- reason — одно предложение по-русски: как разобрано название.
- Не уверен в разборе — верни пустой список. Пустой ответ честнее
  выдуманного названия."""

_TRANSLATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["names"],
    "properties": {
        "names": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "reason"],
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "reason": {"type": "string", "maxLength": 300},
                },
            },
        }
    },
}

_RESOLUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": _MAX_CANDIDATES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "cas",
                    "relation",
                    "reason",
                    "source_url",
                    "quote",
                ],
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "cas": {"type": ["string", "null"], "maxLength": 20},
                    "relation": {
                        "type": "string",
                        "enum": ["same", "different"],
                    },
                    "reason": {"type": "string", "maxLength": 300},
                    "source_url": {"type": "string", "maxLength": 500},
                    "quote": {"type": "string", "maxLength": 400},
                },
            },
        }
    },
}


@dataclass
class ResolvedName:
    """Один кандидат: как называется, какой номер и откуда это известно."""

    name: str
    relation: str
    cas: str | None = None
    reason: str = ""
    # "pubchem" — справочник, "web" — прочтение страницы, "translation" —
    # название собрано разбором русского термина и в выдаче не встретилось.
    # Третий вид слабее двух первых, и слабость его видна в карточке: он
    # существует потому, что искать поставщиков по русскому названию нельзя,
    # а не потому, что источник его подтвердил.
    source: str = "web"
    source_url: str | None = None
    quote: str | None = None
    # Номер прошёл контрольную сумму и подтверждён источником, а не назван
    # моделью по памяти. Интерфейс показывает разницу, а не усредняет её.
    cas_confirmed: bool = False
    synonyms: list[str] = field(default_factory=list)
    # Брутто-формула из справочника по этому номеру. Показывается рядом с
    # номером: «C4H7AlO5» под названием, в котором закупщик написал «моно»,
    # видно сразу, а рейтингу не видно ничего.
    formula: str | None = None
    # Расхождение числительных между введённым названием и справочным.
    # Строка объяснения или None. Кандидат при этом остаётся в списке:
    # решает человек, а система обязана назвать, что заметила.
    formula_conflict: str | None = None
    # Самый надёжный из найденных вариантов: по нему форма ищет по умолчанию.
    # Отмечается ровно один кандидат, и человек волен выбрать другой — но
    # выбор «ничего не выбрано» приводил к поиску по русскому написанию.
    recommended: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "relation": self.relation,
            "cas": self.cas,
            "reason": self.reason,
            "source": self.source,
            "source_url": self.source_url,
            "quote": self.quote,
            "cas_confirmed": self.cas_confirmed,
            "synonyms": self.synonyms,
            "recommended": self.recommended,
            "formula": self.formula,
            "formula_conflict": self.formula_conflict,
        }


@dataclass
class SubstanceResolution:
    """Результат опознания: кандидаты плюс честный отчёт о том, что не вышло."""

    query: str
    candidates: list[ResolvedName] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pubchem_used: bool = False
    search_used: bool = False
    llm_used: bool = False

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "candidates": [item.as_dict() for item in self.candidates],
            "warnings": self.warnings,
            "pubchem_used": self.pubchem_used,
            "search_used": self.search_used,
            "llm_used": self.llm_used,
        }


def _cas_from_synonyms(synonyms: list[str]) -> str | None:
    """Достаёт номер из синонимов PubChem.

    PubChem кладёт номер в общий список синонимов обычной строкой. Берём
    первый, прошедший контрольную сумму: справочник иногда приводит и
    устаревшие номера, и номера родственных солей.
    """
    for value in synonyms:
        candidate = normalize_cas(value.strip())
        if is_valid_cas(candidate):
            return candidate
    return None


def _readable_synonyms(synonyms: list[str], *, skip: str | None) -> list[str]:
    """Оставляет человекочитаемые названия, выбрасывая номера и артикулы."""
    seen: set[str] = set()
    result: list[str] = []
    for value in synonyms:
        name = value.strip()
        if not name or len(name) > 120:
            continue
        if _CAS_PATTERN.fullmatch(name):
            continue
        # Складские артикулы вида "AKOS015896321" или "NSC 27640" названием
        # вещества не являются и в отрицательный фильтр не годятся.
        if re.fullmatch(r"[A-Z]{2,6}[\s-]?\d{3,}", name):
            continue
        key = name.casefold()
        if key in seen or (skip and key == skip.casefold()):
            continue
        seen.add(key)
        result.append(name)
        if len(result) >= _MAX_SYNONYMS:
            break
    return result


def _lookup_pubchem(name: str, resolution: SubstanceResolution) -> None:
    """Детерминированная ветка: справочник знает название — берём как есть."""
    connector = PubChemConnector()
    try:
        # Именно поиск по названию: verify_cas отсекает всё, что не является
        # номером, ещё до сети, и справочная ветка опознания молчала всегда.
        info = connector.lookup_name(name)
    except Exception as exc:  # noqa: BLE001 - справочник не должен ронять кнопку
        logger.warning("PubChem lookup failed for %r: %s", name, exc)
        resolution.warnings.append(
            "Справочник PubChem не ответил; показаны только веб-источники."
        )
        return

    resolution.pubchem_used = True
    if not info.found:
        # Отсутствие в PubChem ничего не говорит о существовании вещества:
        # смесей, полимеров и INCI-названий там нет по определению.
        return

    cas = _cas_from_synonyms(info.synonyms)
    preferred = next(
        (
            value.strip()
            for value in info.synonyms
            if value.strip() and not _CAS_PATTERN.fullmatch(value.strip())
        ),
        info.iupac_name or name,
    )
    resolution.candidates.append(
        ResolvedName(
            name=preferred,
            relation="same",
            cas=cas,
            cas_confirmed=cas is not None,
            reason=(
                "Название найдено в справочнике PubChem"
                + (f", формула {info.molecular_formula}" if info.molecular_formula else "")
                + "."
            ),
            source="pubchem",
            source_url=(
                f"https://pubchem.ncbi.nlm.nih.gov/compound/{info.cid}"
                if info.cid
                else None
            ),
            quote=None,
            synonyms=_readable_synonyms(info.synonyms, skip=preferred),
        )
    )


def _collect_snippets(name: str, resolution: SubstanceResolution) -> list[dict]:
    """Три выдачи: точная, свободная и охота за соседними названиями.

    Кавычки нужны первому запросу: они дают страницы именно этого написания.
    Но на опечатке кавычки же и вредят — поисковик перестаёт предлагать
    исправление. Замер на «Silicon quaternium-18»: в кавычках выдача состоит
    из страниц с той же опечаткой, и правильное «Silicone» в неё не попадает
    вовсе. Поэтому второй запрос идёт без кавычек, и исправление приходит от
    самого поисковика.

    Третий запрос ищет то, что человеку нужнее всего, а выдача сама не
    показывает: чем запрошенное вещество отличается от соседнего по названию.

    У русского написания к ним добавляются ещё два, и они здесь главные.
    Английские формулировки на русском названии не работают: страниц, где
    рядом стоят «Дигидроксимоноацетат алюминия» и «CAS number», в сети нет.
    Мост между написаниями строит русскоязычная выдача — справочники и
    каталоги, где русское название приведено вместе с международным.
    """
    queries = [
        f'"{name}" CAS number',
        f"{name} INCI chemical name synonyms",
        f"{name} vs similar name different substance CAS",
    ]
    if has_cyrillic(name):
        queries = [
            f"{name} как называется на международном рынке",
            f"{name} международное название английское название CAS",
            *queries,
        ]
    snippets: list[dict] = []
    seen_urls: set[str] = set()
    for query in queries:
        try:
            results = search_web(query, limit=_SEARCH_RESULTS_PER_QUERY)
        except (
            SearchProviderNotConfigured,
            UnknownSearchProvider,
        ) as exc:
            resolution.warnings.append(f"Поиск не настроен: {exc}")
            return snippets
        except SearchSourceBlocked:
            resolution.warnings.append(
                "Поисковый источник ответил блокировкой; часть выдачи не получена."
            )
            continue
        except Exception as exc:  # noqa: BLE001 - сеть не должна ронять кнопку
            logger.warning("Search failed for %r: %s", query, exc)
            resolution.warnings.append("Поисковый источник недоступен.")
            continue
        resolution.search_used = True
        for item in results:
            url = (item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            snippets.append(
                {
                    "url": url,
                    "title": (item.get("title") or "").strip()[:200],
                    "snippet": (item.get("snippet") or "").strip()[:400],
                }
            )
    return snippets


def _accept_llm_candidate(
    raw: dict,
    snippets: list[dict],
    resolution: SubstanceResolution,
) -> ResolvedName | None:
    """Пропускает кандидата только через детерминированную проверку.

    Модель здесь — интерпретатор недоверенного текста, а не источник фактов.
    Номер принимается, если он проходит контрольную сумму и дословно есть в
    выдаче. Иначе кандидат остаётся, но без номера: название модель прочитала
    в тексте, а номер могла достроить по памяти.
    """
    name = (raw.get("name") or "").strip()
    if not name:
        return None
    relation = raw.get("relation")
    if relation not in {"same", "different"}:
        return None

    quote = (raw.get("quote") or "").strip() or None
    source_url = (raw.get("source_url") or "").strip() or None
    haystack = " ".join(
        [quote or ""]
        + [f"{item['title']} {item['snippet']} {item['url']}" for item in snippets]
    )

    cas_raw = (raw.get("cas") or "").strip()
    cas: str | None = None
    confirmed = False
    if cas_raw:
        normalized = normalize_cas(cas_raw)
        if not is_valid_cas(normalized):
            resolution.warnings.append(
                f"Для «{name}» предложен номер {cas_raw}: не проходит "
                "контрольную сумму, поэтому не подставлен."
            )
        elif normalized not in haystack.replace("‑", "-"):
            resolution.warnings.append(
                f"Для «{name}» предложен номер {normalized}, но его нет ни в "
                "одном источнике выдачи. Номер не подставлен — проверьте вручную."
            )
        else:
            cas = normalized
            confirmed = True

    return ResolvedName(
        name=name,
        relation=relation,
        cas=cas,
        cas_confirmed=confirmed,
        reason=(raw.get("reason") or "").strip(),
        source="web",
        source_url=source_url,
        quote=quote,
    )


def _merge(candidates: list[ResolvedName]) -> list[ResolvedName]:
    """Схлопывает одинаковые названия, оставляя более доказанный вариант.

    Порядок сохраняется: подтверждённое справочником идёт первым, потому что
    именно его чаще всего и выбирают.
    """
    merged: dict[str, ResolvedName] = {}
    for item in candidates:
        key = item.name.casefold()
        current = merged.get(key)
        if current is None:
            merged[key] = item
            continue
        # Номер, подтверждённый источником, вытесняет его отсутствие.
        if item.cas_confirmed and not current.cas_confirmed:
            item.synonyms = item.synonyms or current.synonyms
            merged[key] = item
        elif not current.synonyms and item.synonyms:
            current.synonyms = item.synonyms
    return list(merged.values())[:_MAX_CANDIDATES]


def _annotate_formulas(resolution: SubstanceResolution) -> None:
    """Достаёт из справочника формулу по номеру и сверяет состав с названием.

    Подтверждённый номер до сих пор считался признаком надёжности. На
    «Дигидроксимоноацетат алюминия» это и вышло боком: номер 142-03-0
    подтверждён страницей честно, только он от соседней соли — ацетатов
    два вместо одного. Рейтинг видел подтверждённый номер и ставил
    отметку «самый надёжный вариант» на другое вещество.

    Сверяются названия, а не формула: посчитать ацетатные группы в
    C4H7AlO5, не зная строения, нельзя. Формула нужна человеку — её и
    показываем рядом с номером.
    """
    connector = PubChemConnector()
    seen: dict[str, tuple[str | None, str | None]] = {}
    for item in resolution.candidates:
        if not item.cas or not item.cas_confirmed:
            continue
        if item.cas not in seen:
            try:
                info = connector.verify_cas(item.cas)
            except Exception as exc:  # noqa: BLE001 - справочник не роняет кнопку
                logger.warning("PubChem verify failed for %s: %s", item.cas, exc)
                seen[item.cas] = (None, None)
            else:
                seen[item.cas] = (
                    (info.molecular_formula, info.iupac_name)
                    if info.found
                    else (None, None)
                )
        formula, iupac = seen[item.cas]
        item.formula = formula
        if not iupac or item.relation != "same":
            # У соседнего названия расхождение состава — не находка, а
            # определение: карточка «НЕ подходит» затем и показана, что
            # это другое вещество. Прогон 10.09.2026 по алюминиевой соли
            # дал три предупреждения, из них два — про такие карточки.
            continue
        # Сверяется введённое человеком название, а не название кандидата:
        # кандидат мог приехать уже подменённым, и сравнение его с самим
        # собой ничего бы не поймало.
        conflict = compare_names(resolution.query, iupac)
        if conflict is None:
            continue
        item.formula_conflict = conflict
        resolution.warnings.append(
            f"«{item.name}» (CAS {item.cas}) — состав не сходится с тем, что "
            f"вы назвали: {conflict}. Номер подтверждён источником, но, "
            "похоже, он от соседней соли. Проверьте перед поиском."
        )


def _translate_to_international(
    query: str,
    resolution: SubstanceResolution,
    client: LLMClient,
) -> list[dict]:
    """Разбирает русское химическое название и собирает международное.

    Вторая ступень, и она включается, только когда первая не нашла в выдаче
    ни одного латинского названия. Это не обход правила доказательности, а
    признание того, что якорь поиска и факт о веществе — разные вещи. Факты
    (номер, роль поставщика, документы) по-прежнему берутся только из
    источников. Название же нужно, чтобы вообще было что спросить у
    китайского рынка: русскую строку он не знает, и без латиницы запрос
    уходит заведомо пустым.
    """
    try:
        raw = client.generate_json(
            system_prompt=_TRANSLATION_SYSTEM_PROMPT,
            user_text=json.dumps({"entered_name": query}, ensure_ascii=False),
            schema_name="substance_international_name",
            json_schema=_TRANSLATION_SCHEMA,
            max_tokens=500,
        )
    except LLMUnavailableError as exc:
        logger.warning("LLM unavailable while translating %r: %s", query, exc)
        return []
    except Exception as exc:  # noqa: BLE001 - кнопка не должна падать целиком
        logger.warning("Translation failed for %r: %s", query, exc)
        return []

    proposals: list[dict] = []
    for item in raw.get("names") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        # Транслитерация сюда доходить не должна, но проверить дешевле, чем
        # объяснять потом, почему поиск ушёл по «Digidroksimonoatsetat».
        if not name or not _is_international(name):
            continue
        proposals.append({"name": name, "reason": (item.get("reason") or "").strip()})
    if not proposals:
        resolution.warnings.append(
            "Международное написание собрать не удалось. Впишите его в "
            "«Другие названия того же вещества»: по русскому названию поиск "
            "поставщиков возвращает пустую выдачу."
        )
    return proposals


def _confirm_translated_name(
    proposal: dict,
    resolution: SubstanceResolution,
) -> ResolvedName:
    """Проверяет собранное название поиском и признаётся, если не вышло.

    Название, найденное на живой странице, — обычный веб-кандидат со
    ссылкой и цитатой. Не найденное остаётся в списке, но с источником
    `translation`: искать по нему всё равно лучше, чем по русскому, а
    закупщик видит, что за этим названием пока не стоит ни одной страницы.
    """
    name = proposal["name"]
    reason = proposal["reason"]
    try:
        results = search_web(f'"{name}" CAS', limit=_SEARCH_RESULTS_PER_QUERY)
    except Exception as exc:  # noqa: BLE001 - сеть не должна ронять кнопку
        logger.warning("Confirmation search failed for %r: %s", name, exc)
        results = []

    for item in results:
        haystack = " ".join(
            [
                (item.get("title") or ""),
                (item.get("snippet") or ""),
                (item.get("url") or ""),
            ]
        )
        if name.casefold() not in haystack.casefold():
            continue
        snippet = (item.get("snippet") or "").strip()[:400]
        cas = next(
            (
                normalize_cas(found)
                for found in _CAS_PATTERN.findall(haystack)
                if is_valid_cas(normalize_cas(found))
            ),
            None,
        )
        return ResolvedName(
            name=name,
            relation="same",
            cas=cas,
            cas_confirmed=cas is not None,
            reason=(
                f"{reason} Название подтверждено страницей выдачи."
                if reason
                else "Международное написание подтверждено страницей выдачи."
            ),
            source="web",
            source_url=(item.get("url") or "").strip() or None,
            quote=snippet or None,
        )

    resolution.warnings.append(
        f"«{name}» — международное написание, собранное разбором русского "
        "названия. Ни одна страница выдачи его не подтвердила: проверьте "
        "перед рассылкой поставщикам."
    )
    return ResolvedName(
        name=name,
        relation="same",
        cas=None,
        reason=(
            f"{reason} Источником не подтверждено."
            if reason
            else "Собрано разбором русского названия; источником не подтверждено."
        ),
        source="translation",
        source_url=None,
        quote=None,
    )


def _add_international_fallback(
    query: str,
    resolution: SubstanceResolution,
    client: LLMClient,
) -> None:
    """Даёт русскому вводу латинский якорь, когда выдача его не дала.

    Без этого «Дигидроксимоноацетат алюминия» оставался вовсе без варианта,
    по которому можно спросить китайский рынок, и закупщик упирался в
    предложение вписать название руками — то есть в работу, ради снятия
    которой систему и делают.
    """
    for proposal in _translate_to_international(query, resolution, client)[:2]:
        candidate = _confirm_translated_name(proposal, resolution)
        resolution.candidates.append(candidate)
    resolution.candidates = _merge(resolution.candidates)


def _reliability(item: ResolvedName, *, needs_international: bool) -> int:
    """Насколько кандидату можно доверять как якорю поиска.

    Международное написание здесь не украшение, а условие работоспособности:
    по русскому названию внешняя выдача пуста, каким бы доказанным оно ни
    было. Поэтому на русском вводе оно весит больше справочника и номера
    вместе, а без него кандидат в рекомендацию не попадает вовсе.
    """
    if item.relation != "same":
        return 0
    if needs_international and not _is_international(item.name):
        return 0
    # Состав не сошёлся с названием — рекомендации не будет, каким бы
    # подтверждённым ни был номер. Именно подтверждённость номера и завела
    # отметку «самый надёжный вариант» на соседнюю соль.
    if item.formula_conflict:
        return 0
    score = 8
    # Название без страницы за спиной — последнее средство. Оно уверенно
    # обходит русское написание, по которому искать нечем, и уверенно
    # проигрывает любому подтверждённому варианту.
    if item.source == "translation":
        return score - 4
    if item.source == "pubchem":
        score += 4
    if item.cas_confirmed:
        score += 3
    if item.quote:
        score += 1
    if item.synonyms:
        score += 1
    return score


def _mark_recommended(resolution: SubstanceResolution) -> None:
    """Отмечает один вариант как самый надёжный из найденных.

    Отметка — не решение за человека: список остаётся, выбрать можно любой
    вариант. Но у русского ввода вариант «не выбрано» означал поиск по
    русскому написанию, а он всегда пустой, и цена молчания здесь выше цены
    подсказки.
    """
    needs_international = has_cyrillic(resolution.query)
    ranked = [
        (_reliability(item, needs_international=needs_international), index, item)
        for index, item in enumerate(resolution.candidates)
    ]
    ranked = [row for row in ranked if row[0] > 0]
    if not ranked:
        return
    ranked.sort(key=lambda row: (-row[0], row[1]))
    ranked[0][2].recommended = True


def resolve_substance(name: str, *, llm: LLMClient | None = None) -> SubstanceResolution:
    """Опознаёт вещество по названию и возвращает кандидатов для выбора.

    Ничего не подставляет молча: результат — список, из которого выбирает
    человек. Пустой список тоже допустимый ответ, и он честнее выдуманного
    номера.

    Единственное, что модуль решает сам, — какой из найденных вариантов
    надёжнее прочих (`recommended`). Это подсказка, а не выбор: она нужна
    русскому вводу, где «не выбрано» означает поиск по написанию, которого
    внешний рынок не знает.

    Русский ввод без латинского варианта в выдаче не остаётся ни с чем:
    включается вторая ступень, которая собирает международное название
    разбором русского термина и проверяет его поиском. Спрашивать китайский
    рынок по-русски нечем, и «не нашлось» как конечный ответ означало бы
    вернуть закупщику ровно ту работу, ради которой он пришёл.
    """
    resolution = _resolve(name, llm=llm)
    _annotate_formulas(resolution)
    # Кандидат с расхождением состава якорем не считается: без этого
    # «Aluminum diacetate hydroxide» закрывал бы дорогу запасной ступени
    # и позиция оставалась бы с названием соседней соли.
    if has_cyrillic(resolution.query) and not any(
        _is_international(item.name)
        and item.relation == "same"
        and not item.formula_conflict
        for item in resolution.candidates
    ):
        _add_international_fallback(
            resolution.query, resolution, llm or LLMClient()
        )
    _mark_recommended(resolution)
    return resolution


def _resolve(name: str, *, llm: LLMClient | None = None) -> SubstanceResolution:
    query = name.strip()
    resolution = SubstanceResolution(query=query)
    if not query:
        return resolution

    _lookup_pubchem(query, resolution)
    snippets = _collect_snippets(query, resolution)

    if not snippets:
        if not resolution.candidates and not resolution.warnings:
            resolution.warnings.append(
                "Ни справочник, ни поиск не дали названий. Введите номер вручную "
                "или уточните название."
            )
        return resolution

    client = llm or LLMClient()
    user_text = json.dumps(
        {"entered_name": query, "search_results": snippets},
        ensure_ascii=False,
    )
    try:
        raw = client.generate_json(
            system_prompt=_SYSTEM_PROMPT,
            user_text=user_text,
            schema_name="substance_resolution",
            json_schema=_RESOLUTION_SCHEMA,
            max_tokens=1200,
        )
        resolution.llm_used = True
    except LLMUnavailableError as exc:
        # Модель недоступна — остаётся детерминированная ветка PubChem.
        # Это хуже полного ответа, но лучше пустого экрана без объяснения.
        logger.warning("LLM unavailable while resolving %r: %s", query, exc)
        resolution.warnings.append(
            "Модель недоступна: показано только то, что нашлось в справочнике."
        )
        return resolution
    except Exception as exc:  # noqa: BLE001 - кнопка не должна падать целиком
        logger.warning("Resolution failed for %r: %s", query, exc)
        resolution.warnings.append("Не удалось разобрать выдачу; попробуйте ещё раз.")
        return resolution

    for item in raw.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        accepted = _accept_llm_candidate(item, snippets, resolution)
        if accepted is not None:
            resolution.candidates.append(accepted)

    resolution.candidates = _merge(resolution.candidates)
    if not resolution.candidates:
        resolution.warnings.append(
            "Подходящих названий в выдаче не нашлось. Проверьте написание."
        )
    return resolution
