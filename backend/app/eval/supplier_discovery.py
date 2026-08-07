"""Замер поиска контрагентов против эталона известных игроков.

Задача №3 из ТЗ формулирует результат как «список контрагентов с
предварительным статусом», а не список производителей. Поэтому меряются
два разных свойства, и путать их нельзя:

* полнота — сколько известных игроков рынка система вообще нашла;
* точность статуса — кем она их назвала.

До этого замера настройка шла по собственным отчётам системы. Так и
выжили ворота, которые не открылись ни разу за 129 кандидатов: отчёт
говорил «заблокировано», и это выглядело как работа фильтра.

Эталон заведомо неполон — это набор известных игроков, а не рынок
целиком. Поэтому кандидаты вне набора не считаются ошибкой: они выводятся
отдельным списком, чтобы разметить вручную и пополнить эталон.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.services.intermediaries import marketplace_page_kind

DATASET_DIR = Path(__file__).resolve().parent / "datasets"

# Площадки среди игроков не значатся намеренно: их не надо находить, их
# надо не пускать в кандидаты. Пока они лежали в known_players, включение
# отсева читалось замером как падение полноты.
_KINDS = {"manufacturer", "distributor", "trader"}
_CATEGORIES = {"with_cas", "trade_name", "plain_name"}
_CONFIDENCE = {"verified", "industry_knowledge"}

# Откуда игрок попал в эталон. «system_run» — его нашёл наш же поиск, и в
# счёт полноты он не идёт: иначе замер пополняет числитель и знаменатель
# одними и теми же именами и хвалит сам себя. В проверке классификации
# такой игрок участвует наравне с остальными — там вопрос не «нашли ли»,
# а «верно ли назвали роль», и ответ на него от происхождения не зависит.
_DISCOVERY = {"independent", "system_run"}
_DEFAULT_DISCOVERY = "independent"


class DiscoveryEvalError(ValueError):
    """Эталон отсутствует или структурно испорчен."""


def dataset_path(version: str) -> Path:
    return DATASET_DIR / f"supplier_discovery_eval.{version}.json"


def load_dataset(version: str = "v1") -> dict[str, Any]:
    path = dataset_path(version)
    if not path.is_file():
        raise DiscoveryEvalError(
            f"Эталон supplier_discovery_eval.{version}.json не найден."
        )
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("dataset_version") != version:
        raise DiscoveryEvalError(
            "dataset_version внутри файла не совпадает с именем файла."
        )
    substances = dataset.get("substances")
    if not isinstance(substances, list) or not substances:
        raise DiscoveryEvalError("Эталон не содержит ни одного вещества.")
    seen: set[str] = set()
    for substance in substances:
        substance_id = substance.get("id")
        if not isinstance(substance_id, str) or substance_id in seen:
            raise DiscoveryEvalError(
                "У каждого вещества должен быть уникальный строковый id."
            )
        seen.add(substance_id)
        if substance.get("category") not in _CATEGORIES:
            raise DiscoveryEvalError(
                f"{substance_id}: неизвестная категория."
            )
        players = substance.get("known_players")
        if not isinstance(players, list) or not players:
            raise DiscoveryEvalError(
                f"{substance_id}: нет ни одного известного игрока."
            )
        for player in players:
            if player.get("kind") not in _KINDS:
                raise DiscoveryEvalError(
                    f"{substance_id}: неизвестный вид «{player.get('kind')}»."
                )
            if player.get("confidence") not in _CONFIDENCE:
                raise DiscoveryEvalError(
                    f"{substance_id}: не указана достоверность записи."
                )
            if not player.get("country"):
                raise DiscoveryEvalError(
                    f"{substance_id}: у игрока «{player['name']}» нет страны. "
                    "Без неё полнота считается по всему свету, а запрос "
                    "спрашивал одну страну."
                )
            if player.setdefault("discovered_by", _DEFAULT_DISCOVERY) not in (
                _DISCOVERY
            ):
                raise DiscoveryEvalError(
                    f"{substance_id}: у игрока «{player['name']}» неизвестное "
                    "происхождение записи."
                )
    return dataset


# --- сопоставление кандидата с эталоном ---


_SEPARATORS = re.compile(r"[^0-9a-zа-яё一-鿿]+", re.IGNORECASE)
# Юридические хвосты не различают компании, но мешают сравнению.
_LEGAL_TAILS = (
    "coltd", "co", "ltd", "limited", "inc", "llc", "gmbh", "corporation",
    "corp", "group", "company", "plc", "sa", "bv", "pvt",
)


def normalise_name(value: str) -> str:
    """Имя компании без разделителей и юридических хвостов."""
    collapsed = _SEPARATORS.sub("", (value or "")).casefold()
    changed = True
    while changed:
        changed = False
        for tail in _LEGAL_TAILS:
            if collapsed.endswith(tail) and len(collapsed) > len(tail) + 3:
                collapsed = collapsed[: -len(tail)]
                changed = True
    return collapsed


def host_of(url: str) -> str:
    host = (urlparse(url or "").hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def _domain_matches(candidate_url: str, domain: str | None) -> bool:
    if not domain:
        return False
    host = host_of(candidate_url)
    domain = domain.casefold()
    return host == domain or host.endswith("." + domain)


def match_player(candidate: dict, players: list[dict]) -> dict | None:
    """Ищет кандидата среди известных игроков по домену и имени.

    Домен надёжнее имени: страница на собственном сайте компании
    однозначна, а имя на витрине маркетплейса пишут как придётся.
    """
    for player in players:
        if _domain_matches(candidate.get("url", ""), player.get("domain")):
            return player
    haystack = normalise_name(
        f"{candidate.get('company_name') or ''} {candidate.get('title') or ''}"
    )
    if not haystack:
        return None
    for player in players:
        names = [player.get("name", ""), *(player.get("aliases") or [])]
        for name in names:
            needle = normalise_name(name)
            if needle and len(needle) >= _min_match_length(needle):
                if needle in haystack:
                    return player
    return None


def _min_match_length(needle: str) -> int:
    """Сколько знаков должно совпасть, чтобы это не было случайностью.

    Иероглифы плотнее латиницы: «华峰» — уже узнаваемое имя, а любые две
    латинские буквы найдутся где угодно.
    """
    return 2 if any("一" <= char <= "鿿" for char in needle) else 4


# --- отчёт ---


@dataclass
class SubstanceReport:
    substance_id: str
    category: str
    known_total: int
    found: list[tuple[str, str, str]] = field(default_factory=list)
    # Игроки, которых в эталон записал наш же поиск. Роль у них проверяется,
    # но в полноту они не идут — см. _DISCOVERY.
    found_system: list[tuple[str, str, str]] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    unlabelled: list[tuple[str, str]] = field(default_factory=list)
    # Площадки, всё же попавшие в кандидатов: это не потеря полноты, а
    # протечка отсева, и считать её надо отдельно.
    leaked_marketplaces: list[str] = field(default_factory=list)
    # Найденные за пределами страны запроса. Требовать их нельзя, но и
    # прятать не за что: находка полезная, просто сверх заказанного.
    found_abroad: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return len(self.found) / self.known_total if self.known_total else 0.0

    @property
    def correct_kinds(self) -> int:
        return sum(
            expected == actual
            for _, expected, actual in (*self.found, *self.found_system)
        )

    @property
    def judged_kinds(self) -> int:
        return len(self.found) + len(self.found_system)


def score_substance(
    substance: dict, candidates: list[dict]
) -> SubstanceReport:
    """Сверяет кандидатов одного вещества с его известными игроками."""
    players = substance["known_players"]
    # Запрос спрашивал одну страну — по ней и считается полнота. Игроки
    # из других стран остаются в наборе: при поиске по их стране они снова
    # станут ожидаемыми, а найтись могут и сейчас.
    wanted = (substance.get("country") or "").strip().casefold()
    in_country = [
        player
        for player in players
        if not wanted or (player.get("country") or "").casefold() == wanted
    ]
    # Полнота считается только по игрокам, найденным независимо от нас.
    expected = [
        player
        for player in in_country
        if player.get("discovered_by", _DEFAULT_DISCOVERY) != "system_run"
    ]
    report = SubstanceReport(
        substance_id=substance["id"],
        category=substance["category"],
        known_total=len(expected),
    )
    filtered_hosts = {
        (item.get("domain") or "").casefold()
        for item in (substance.get("should_be_filtered") or [])
    }
    matched_names: set[str] = set()
    for candidate in candidates:
        url = str(candidate.get("url") or "")
        host = host_of(url)
        on_platform = host and any(
            host == domain or host.endswith("." + domain)
            for domain in filtered_hosts
            if domain
        )
        # Магазин одной компании на домене площадки протечкой не считается.
        # Продукт держит его прямым источником намеренно: он называет
        # предприятие. Замер считал площадкой любой адрес на домене и
        # записывал в протечки «chemball.cn/factory/zimbir/product.html» —
        # страницу компании, которую сам же код зовёт витриной магазина.
        if on_platform and marketplace_page_kind(url) != "storefront":
            report.leaked_marketplaces.append(host)
            continue
        player = match_player(candidate, players)
        if player is None:
            report.unlabelled.append(
                (
                    str(candidate.get("company_name") or "без имени"),
                    host_of(candidate.get("url", "")),
                )
            )
            continue
        if player["name"] in matched_names:
            continue
        matched_names.add(player["name"])
        if player not in in_country:
            report.found_abroad.append(
                f'{player["name"]} ({player.get("country")})'
            )
            continue
        hit = (
            player["name"],
            player["kind"],
            str(candidate.get("supplier_type") or "unknown"),
        )
        if player.get("discovered_by", _DEFAULT_DISCOVERY) == "system_run":
            report.found_system.append(hit)
        else:
            report.found.append(hit)
    report.missed = [
        player["name"]
        for player in expected
        if player["name"] not in matched_names
    ]
    return report


def format_report(reports: list[SubstanceReport]) -> str:
    """Человекочитаемый отчёт: полнота, статусы и что размечать дальше."""
    lines: list[str] = []
    known = found = correct = judged = leaked = abroad = 0
    for report in reports:
        known += report.known_total
        found += len(report.found)
        correct += report.correct_kinds
        judged += report.judged_kinds
        leaked += len(report.leaked_marketplaces)
        abroad += len(report.found_abroad)
        lines.append(
            f"=== {report.substance_id} [{report.category}] "
            f"найдено {len(report.found)} из {report.known_total}"
        )
        for name, expected, actual in report.found:
            mark = "  " if expected == actual else " !"
            lines.append(f" {mark} {name}: эталон {expected}, система {actual}")
        for name, expected, actual in report.found_system:
            mark = "  " if expected == actual else " !"
            lines.append(
                f" {mark} {name}: эталон {expected}, система {actual} "
                "(в полноту не идёт — запись из нашего же прогона)"
            )
        for name in report.missed:
            lines.append(f"  - не найден: {name}")
        for name, host in report.unlabelled:
            lines.append(f"  ? вне эталона: {name} ({host})")
        for name in report.found_abroad:
            lines.append(f"  + сверх страны запроса: {name}")
        for host in report.leaked_marketplaces:
            lines.append(f"  x площадка в кандидатах: {host}")
    lines.append("")
    lines.append(f"полнота по стране запроса: {found} из {known}")
    lines.append(f"верный статус: {correct} из {judged}")
    lines.append(f"найдено сверх страны: {abroad}")
    lines.append(f"площадок просочилось: {leaked}")
    return "\n".join(lines)
