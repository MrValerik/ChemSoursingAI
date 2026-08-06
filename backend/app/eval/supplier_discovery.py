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

DATASET_DIR = Path(__file__).resolve().parent / "datasets"

_KINDS = {"manufacturer", "distributor", "trader", "marketplace"}
_CATEGORIES = {"with_cas", "trade_name", "plain_name"}
_CONFIDENCE = {"verified", "industry_knowledge"}


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
    missed: list[str] = field(default_factory=list)
    unlabelled: list[tuple[str, str]] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return len(self.found) / self.known_total if self.known_total else 0.0

    @property
    def correct_kinds(self) -> int:
        return sum(expected == actual for _, expected, actual in self.found)


def score_substance(
    substance: dict, candidates: list[dict]
) -> SubstanceReport:
    """Сверяет кандидатов одного вещества с его известными игроками."""
    players = substance["known_players"]
    report = SubstanceReport(
        substance_id=substance["id"],
        category=substance["category"],
        known_total=len(players),
    )
    matched_names: set[str] = set()
    for candidate in candidates:
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
        report.found.append(
            (
                player["name"],
                player["kind"],
                str(candidate.get("supplier_type") or "unknown"),
            )
        )
    report.missed = [
        player["name"]
        for player in players
        if player["name"] not in matched_names
    ]
    return report


def format_report(reports: list[SubstanceReport]) -> str:
    """Человекочитаемый отчёт: полнота, статусы и что размечать дальше."""
    lines: list[str] = []
    known = found = correct = 0
    for report in reports:
        known += report.known_total
        found += len(report.found)
        correct += report.correct_kinds
        lines.append(
            f"=== {report.substance_id} [{report.category}] "
            f"найдено {len(report.found)} из {report.known_total}"
        )
        for name, expected, actual in report.found:
            mark = "  " if expected == actual else " !"
            lines.append(f" {mark} {name}: эталон {expected}, система {actual}")
        for name in report.missed:
            lines.append(f"  - не найден: {name}")
        for name, host in report.unlabelled:
            lines.append(f"  ? вне эталона: {name} ({host})")
    lines.append("")
    lines.append(f"полнота: {found} из {known}")
    lines.append(f"верный статус: {correct} из {found}")
    return "\n".join(lines)
