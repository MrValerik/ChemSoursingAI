"""Подбор веществ-аналогов: чем можно заменить названную позицию.

Зачем отдельная ступень. Запрос «найди аналог» без выбора вещества
перекладывает решение на поставщика: он присылает то, что считает заменой
сам, и закупщик узнаёт об этом из ответа. Между «нужна замена» и «ищем
поставщиков» не хватало шага, на котором замену называют по имени и
проверяют. Этот модуль делает первый шаг: возвращает список веществ с
обоснованием, из которого выбирает человек.

Правила доказательности те же, что во всём проекте. Модель здесь —
интерпретатор веб-выдачи, а не источник фактов: у кандидата обязана быть
цитата и адрес страницы. Номер CAS принимается, только если он проходит
контрольную сумму и дословно присутствует в выдаче; иначе кандидат
остаётся без номера, а причина попадает в предупреждения.

Ничего не подставляется молча и ничего не создаётся: модуль возвращает
кандидатов, а запросы по ним заводит закупщик отдельным действием. Пустой
список — допустимый ответ, и он честнее выдуманного названия.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.connectors.web_search import (
    SearchProviderNotConfigured,
    SearchSourceBlocked,
    UnknownSearchProvider,
    search_web,
)
from app.extraction.llm_client import LLMClient, LLMUnavailableError
from app.services.cas import is_valid_cas, normalize_cas

logger = logging.getLogger(__name__)

# Сколько аналогов показывать. Список выбирают глазами, и длинный выбирать
# труднее короткого: он превращается в ту же выдачу поисковика, от которой
# мы уходим.
MAX_CANDIDATES = 8
_SEARCH_RESULTS_PER_QUERY = 6

_SYSTEM_PROMPT = """Ты помогаешь специалисту по закупкам химического сырья
подобрать замену веществу, которое он закупает.

Тебе дают закупаемую позицию и фрагменты веб-выдачи. Верни список веществ,
которыми эту позицию можно заменить.

Аналог — это ДРУГОЕ вещество или другой продукт, выполняющий ту же функцию.
Иное написание того же вещества, его синоним или торговое название того же
самого продукта аналогом не являются: такие варианты не возвращай.

Правила:
- Возвращай только то, что названо во фрагментах выдачи. Не добавляй
  вещества по памяти: специалист не сможет проверить такую замену.
- В поле quote приведи дословный фрагмент, где видно предлагаемое вещество и
  связь с закупаемым. Не пересказывай его своими словами.
- source_url — адрес того фрагмента, который ты процитировал.
- Номер CAS указывай, только если он есть в приведённых фрагментах. Если
  номера в тексте нет — ставь null. Не восстанавливай номер по памяти.
- reason — одно-два предложения по-русски: чем это вещество заменяет
  закупаемое и в чём отличается. Если из выдачи видны ограничения замены
  (другая дозировка, другая форма, другой класс), назови их.
- У смесей, полимеров и INCI-названий номера может не быть в принципе. Это
  нормальный ответ, а не ошибка: верни кандидата без номера."""

_ANALOG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_CANDIDATES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "cas", "reason", "source_url", "quote"],
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "cas": {"type": ["string", "null"], "maxLength": 20},
                    "reason": {"type": "string", "maxLength": 400},
                    "source_url": {"type": "string", "maxLength": 500},
                    "quote": {"type": "string", "maxLength": 400},
                },
            },
        }
    },
}


@dataclass
class AnalogCandidate:
    """Один аналог: что предлагается вместо позиции и откуда это известно."""

    name: str
    cas: str | None = None
    cas_confirmed: bool = False
    reason: str = ""
    quote: str | None = None
    source_url: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "cas": self.cas,
            "cas_confirmed": self.cas_confirmed,
            "reason": self.reason,
            "quote": self.quote,
            "source_url": self.source_url,
        }


@dataclass
class AnalogSuggestion:
    """Результат подбора: кандидаты плюс честный отчёт о том, что не вышло."""

    query: str
    candidates: list[AnalogCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    search_used: bool = False
    llm_used: bool = False

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "candidates": [item.as_dict() for item in self.candidates],
            "warnings": self.warnings,
            "search_used": self.search_used,
            "llm_used": self.llm_used,
        }


def _queries(name: str, cas: str | None, specification: str | None) -> list[str]:
    """Три выдачи: перечни замен, отраслевой разговор и подбор по функции.

    Первый запрос ищет то, как замену называют производители и дистрибьюторы:
    «alternative to», «replacement for», countertype-перечни. Второй — как о
    ней говорят в отрасли, включая сравнения «X vs Y». Третий нужен позициям
    без номера: там заменяемое описывается функцией и показателями, а не
    названием, и запрос по названию для них бесполезен.
    """
    queries = [
        f'"{name}" (alternative OR replacement OR substitute) chemical',
        f"{name} vs alternative raw material comparison",
    ]
    if cas:
        queries.append(f"{cas} {name} substitute equivalent chemical")
    elif specification:
        # Показатели важнее прилагательных: замену подбирают по ним.
        queries.append(f"{name} {specification[:120]} alternative raw material")
    else:
        queries.append(f"{name} functional alternative raw material")
    return queries


def _collect_snippets(
    name: str,
    cas: str | None,
    specification: str | None,
    suggestion: AnalogSuggestion,
) -> list[dict]:
    snippets: list[dict] = []
    seen_urls: set[str] = set()
    for query in _queries(name, cas, specification):
        try:
            results = search_web(query, limit=_SEARCH_RESULTS_PER_QUERY)
        except (SearchProviderNotConfigured, UnknownSearchProvider) as exc:
            suggestion.warnings.append(f"Поиск не настроен: {exc}")
            return snippets
        except SearchSourceBlocked:
            suggestion.warnings.append(
                "Поисковый источник ответил блокировкой; часть выдачи не получена."
            )
            continue
        except Exception as exc:  # noqa: BLE001 - сеть не должна ронять кнопку
            logger.warning("Analog search failed for %r: %s", query, exc)
            suggestion.warnings.append("Поисковый источник недоступен.")
            continue
        suggestion.search_used = True
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


def _accept(
    raw: dict,
    snippets: list[dict],
    *,
    source_name: str,
    source_cas: str | None,
    suggestion: AnalogSuggestion,
) -> AnalogCandidate | None:
    """Пропускает кандидата через детерминированную проверку.

    Отсекается главная ошибка этой ступени — вернуть само закупаемое
    вещество под другим написанием. Такой «аналог» ничего не заменяет, но
    выглядит как готовый ответ и съедает место в коротком списке.
    """
    name = (raw.get("name") or "").strip()
    if not name:
        return None
    if name.casefold() == source_name.strip().casefold():
        return None

    quote = (raw.get("quote") or "").strip() or None
    source_url = (raw.get("source_url") or "").strip() or None
    if not quote or not source_url:
        # Без цитаты и адреса замену нечем проверить, а на слово здесь
        # верить дороже всего: ошибка вскрывается на производстве.
        suggestion.warnings.append(
            f"«{name}» предложен без ссылки на источник — не показан."
        )
        return None

    haystack = " ".join(
        [quote]
        + [f"{item['title']} {item['snippet']} {item['url']}" for item in snippets]
    ).replace("‑", "-")

    cas_raw = (raw.get("cas") or "").strip()
    cas: str | None = None
    confirmed = False
    if cas_raw:
        normalized = normalize_cas(cas_raw)
        if not is_valid_cas(normalized):
            suggestion.warnings.append(
                f"Для «{name}» предложен номер {cas_raw}: не проходит "
                "контрольную сумму, поэтому не подставлен."
            )
        elif normalized not in haystack:
            suggestion.warnings.append(
                f"Для «{name}» предложен номер {normalized}, но его нет ни в "
                "одном источнике выдачи. Номер не подставлен — проверьте вручную."
            )
        elif source_cas and normalize_cas(normalized) == normalize_cas(source_cas):
            # Тот же номер — то же вещество, как бы оно ни называлось.
            return None
        else:
            cas = normalized
            confirmed = True

    return AnalogCandidate(
        name=name,
        cas=cas,
        cas_confirmed=confirmed,
        reason=(raw.get("reason") or "").strip(),
        quote=quote,
        source_url=source_url,
    )


def _merge(candidates: list[AnalogCandidate]) -> list[AnalogCandidate]:
    """Схлопывает одинаковые названия, оставляя более доказанный вариант."""
    merged: dict[str, AnalogCandidate] = {}
    for item in candidates:
        key = item.name.casefold()
        current = merged.get(key)
        if current is None:
            merged[key] = item
        elif item.cas_confirmed and not current.cas_confirmed:
            merged[key] = item
    return list(merged.values())[:MAX_CANDIDATES]


def suggest_analogs(
    name: str,
    *,
    cas: str | None = None,
    specification: str | None = None,
    llm: LLMClient | None = None,
) -> AnalogSuggestion:
    """Подбирает вещества, которыми можно заменить позицию.

    Ничего не создаёт и ничего не выбирает: результат — список, из которого
    отмечает человек. Пустой список тоже ответ, и он честнее выдуманного.
    """
    query = (name or "").strip()
    suggestion = AnalogSuggestion(query=query)
    if not query:
        return suggestion

    snippets = _collect_snippets(query, cas, specification, suggestion)
    if not snippets:
        if not suggestion.warnings:
            suggestion.warnings.append(
                "Поиск не дал страниц о заменах этой позиции. "
                "Уточните название или спецификацию."
            )
        return suggestion

    client = llm or LLMClient()
    user_text = json.dumps(
        {
            "purchased_item": {
                "name": query,
                "cas": cas,
                "specification": (specification or "")[:600] or None,
            },
            "search_results": snippets,
        },
        ensure_ascii=False,
    )
    try:
        raw = client.generate_json(
            system_prompt=_SYSTEM_PROMPT,
            user_text=user_text,
            schema_name="analog_candidates",
            json_schema=_ANALOG_SCHEMA,
            max_tokens=1400,
        )
        suggestion.llm_used = True
    except LLMUnavailableError as exc:
        logger.warning("LLM unavailable while suggesting analogs for %r: %s", query, exc)
        suggestion.warnings.append(
            "Модель недоступна: подбор не выполнен. Повторите позже."
        )
        return suggestion
    except Exception as exc:  # noqa: BLE001 - кнопка не должна падать целиком
        logger.warning("Analog suggestion failed for %r: %s", query, exc)
        suggestion.warnings.append("Не удалось разобрать выдачу; попробуйте ещё раз.")
        return suggestion

    for item in raw.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        accepted = _accept(
            item,
            snippets,
            source_name=query,
            source_cas=cas,
            suggestion=suggestion,
        )
        if accepted is not None:
            suggestion.candidates.append(accepted)

    suggestion.candidates = _merge(suggestion.candidates)
    if not suggestion.candidates:
        suggestion.warnings.append(
            "Замен в выдаче не нашлось. Это бывает у продуктов, которые "
            "выпускают по одной рецептуре: тогда искать нужно сам продукт."
        )
    return suggestion
