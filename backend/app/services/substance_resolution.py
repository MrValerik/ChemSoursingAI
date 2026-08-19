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

logger = logging.getLogger(__name__)

# Номер в свободном тексте: две-семь цифр, две цифры, одна контрольная.
_CAS_PATTERN = re.compile(r"\b\d{2,7}-\d{2}-\d\b")

# Сколько названий показывать. Список выбирают глазами, и длинный список
# выбирать труднее, чем короткий: он превращается в ту же выдачу поисковика,
# от которой мы уходим.
_MAX_CANDIDATES = 8
# Сколько синонимов PubChem доносить до формы. Полный список у популярных
# веществ уходит за сотню строк и состоит в основном из складских артикулов.
_MAX_SYNONYMS = 12
_SEARCH_RESULTS_PER_QUERY = 6

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
- Номер CAS указывай, только если он есть в приведённых фрагментах. Если
  номера в тексте нет — ставь null. Не восстанавливай номер по памяти.
- В поле quote приведи дословный фрагмент из выдачи, где видно название и
  номер. Не переписывай его своими словами.
- source_url — адрес того фрагмента, который ты процитировал.
- reason — одно предложение по-русски: почему это то же вещество или чем оно
  отличается.
- У смесей, полимеров и INCI-названий номера может не быть в принципе. Это
  нормальный ответ, а не ошибка: верни кандидата без номера.
- Не выдумывай названия, которых нет во фрагментах."""

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
    source: str = "web"
    source_url: str | None = None
    quote: str | None = None
    # Номер прошёл контрольную сумму и подтверждён источником, а не назван
    # моделью по памяти. Интерфейс показывает разницу, а не усредняет её.
    cas_confirmed: bool = False
    synonyms: list[str] = field(default_factory=list)

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
        info = connector.verify_cas(name)
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
    """Две выдачи: номер по названию и разбор соседних названий."""
    queries = [
        f'"{name}" CAS number',
        f'"{name}" INCI chemical name synonyms',
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


def resolve_substance(name: str, *, llm: LLMClient | None = None) -> SubstanceResolution:
    """Опознаёт вещество по названию и возвращает кандидатов для выбора.

    Ничего не подставляет автоматически: результат — список, из которого
    выбирает человек. Пустой список тоже допустимый ответ, и он честнее
    выдуманного номера.
    """
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
