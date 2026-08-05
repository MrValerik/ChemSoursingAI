"""Коннектор верификации вещества по CAS через PubChem PUG REST.

PubChem — бесплатный открытый API без ключа. По CAS-номеру (передаётся как
имя) получаем CID, далее — наименование, синонимы, формулу и молекулярную массу.
Echemi в продукте остаётся заглушкой в UI под будущую интеграцию.

Документация API: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import sleep

import httpx

from app.core.config import get_settings
from app.services.cas import is_valid_cas, normalize_cas
from app.services.domain_rate_limit import (
    defer_domain,
    reserve_slot,
    retry_after_seconds,
)

# 404 у PubChem означает «вещество не найдено» и троттлингом не является.
_THROTTLED_STATUSES = {429, 503}
# Пауза, когда сервис ограничил нас, но Retry-After не прислал.
_THROTTLED_BACKOFF_S = 10.0
# Сколько раз пытаться при троттлинге, считая первую попытку.
_THROTTLE_ATTEMPTS = 2


@dataclass
class SubstanceInfo:
    """Результат верификации вещества."""

    cas: str
    found: bool
    cid: int | None = None
    iupac_name: str | None = None
    molecular_formula: str | None = None
    molecular_weight: float | None = None
    synonyms: list[str] = field(default_factory=list)
    source: str = "pubchem"
    error: str | None = None

    @property
    def outcome(self) -> str:
        """Почему проверка не удалась — это три разных факта, не один.

        `found=False` возвращается при опечатке в номере, при отсутствии
        вещества в PubChem и при недоступности самого PubChem. Первое —
        точно ошибка ввода, второе ничего не говорит о существовании
        вещества (смесей и промышленных продуктов там нет), третье вообще
        не факт о веществе, а факт о нас. Показывать их одинаково —
        значит выдавать сбой сети за отсутствие вещества.
        """
        if self.found:
            return "confirmed"
        if self.error == "invalid_cas_checksum":
            return "invalid_checksum"
        if self.error == "not_found":
            return "not_found"
        return "unavailable"

    def as_dict(self) -> dict:
        return {
            "cas": self.cas,
            "found": self.found,
            "outcome": self.outcome,
            "cid": self.cid,
            "iupac_name": self.iupac_name,
            "molecular_formula": self.molecular_formula,
            "molecular_weight": self.molecular_weight,
            "synonyms": self.synonyms[:20],
            "source": self.source,
            "error": self.error,
        }


class PubChemConnector:
    """Тонкий адаптер над PUG REST. Сетевые ошибки не пробрасываются наружу —
    возвращается SubstanceInfo(found=False, error=...)."""

    def __init__(self, base_url: str | None = None, timeout_s: float = 15.0) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.pubchem_base_url).rstrip("/")
        self.timeout_s = timeout_s

    def verify_cas(self, cas: str) -> SubstanceInfo:
        """Верифицирует CAS: сначала контрольная сумма, затем запрос к PubChem."""
        cas = normalize_cas(cas)

        # 1. Детерминированная проверка — отсекаем мусор без сетевого вызова.
        if not is_valid_cas(cas):
            return SubstanceInfo(cas=cas, found=False, error="invalid_cas_checksum")

        # 2. CAS -> CID (CAS передаётся как name).
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                cid = self._fetch_cid(client, cas)
                if cid is None:
                    return SubstanceInfo(cas=cas, found=False, error="not_found")
                props = self._fetch_properties(client, cid)
                synonyms = self._fetch_synonyms(client, cid)
        except httpx.HTTPError as exc:
            return SubstanceInfo(cas=cas, found=False, error=f"http_error: {exc}")

        return SubstanceInfo(
            cas=cas,
            found=True,
            cid=cid,
            iupac_name=props.get("IUPACName"),
            molecular_formula=props.get("MolecularFormula"),
            molecular_weight=_to_float(props.get("MolecularWeight")),
            synonyms=synonyms,
        )

    # --- внутренние запросы ---

    def _get(self, client: httpx.Client, url: str) -> httpx.Response:
        """Запрос с соблюдением общего лимита PubChem.

        PubChem просит не превышать пять запросов в секунду на организацию и
        сам применяет динамический троттлинг, отвечая 503. Лимит считается по
        исходящему IP, поэтому пауза общая для всех worker-процессов: каждый
        поиск обращается сюда трижды.
        """
        for attempt in range(_THROTTLE_ATTEMPTS):
            wait = reserve_slot(url)
            if wait > 0:
                sleep(wait)
            response = client.get(url)
            if response.status_code not in _THROTTLED_STATUSES:
                return response
            delay = retry_after_seconds(response.headers.get("Retry-After"))
            defer_domain(url, delay or _THROTTLED_BACKOFF_S)
            # Троттлинг — состояние на секунды, а не свойство вещества.
            # Без повтора пачка заведённых подряд запросов навсегда осталась
            # бы непроверенной: закупщик заводит их списком, и первые три
            # выбирают лимит на всех. Замер: три запроса разом дали два
            # «сервис недоступен», по одному те же вещества подтвердились.
            if attempt + 1 < _THROTTLE_ATTEMPTS:
                sleep(min(delay or _THROTTLED_BACKOFF_S, _THROTTLED_BACKOFF_S))
        return response

    def _fetch_cid(self, client: httpx.Client, cas: str) -> int | None:
        url = f"{self.base_url}/compound/name/{cas}/cids/JSON"
        resp = self._get(client, url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        cids = resp.json().get("IdentifierList", {}).get("CID", [])
        return cids[0] if cids else None

    def _fetch_properties(self, client: httpx.Client, cid: int) -> dict:
        props = "IUPACName,MolecularFormula,MolecularWeight"
        url = f"{self.base_url}/compound/cid/{cid}/property/{props}/JSON"
        resp = self._get(client, url)
        resp.raise_for_status()
        table = resp.json().get("PropertyTable", {}).get("Properties", [{}])
        return table[0] if table else {}

    def _fetch_synonyms(self, client: httpx.Client, cid: int) -> list[str]:
        url = f"{self.base_url}/compound/cid/{cid}/synonyms/JSON"
        resp = self._get(client, url)
        if resp.status_code != 200:
            return []
        info = resp.json().get("InformationList", {}).get("Information", [{}])
        return info[0].get("Synonym", []) if info else []


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
