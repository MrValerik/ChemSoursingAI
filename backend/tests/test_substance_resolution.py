"""Опознание вещества по названию: что принимается, а что отбрасывается.

Проверяется главное продуктовое правило модуля: номер, названный моделью,
не становится фактом сам по себе. Он проходит контрольную сумму и должен
дословно присутствовать в выдаче, иначе кандидат остаётся без номера, а
причина попадает в предупреждения.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_substance_resolution.db")

from app.connectors.pubchem import SubstanceInfo
from app.extraction.llm_client import LLMUnavailableError
from app.services import substance_resolution
from app.services.substance_resolution import resolve_substance


class _StubLLM:
    """Отдаёт заранее заданный ответ вместо вызова модели."""

    def __init__(self, payload: dict | Exception):
        self.payload = payload
        self.calls: list[str] = []

    def generate_json(self, *, user_text: str, **_kwargs) -> dict:
        self.calls.append(user_text)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _snippets(*items: tuple[str, str, str]) -> list[dict]:
    return [
        {"title": title, "url": url, "snippet": snippet}
        for title, url, snippet in items
    ]


def _patch_sources(
    monkeypatch,
    *,
    pubchem: SubstanceInfo | None = None,
    results: list[dict] | None = None,
) -> None:
    class _StubPubChem:
        # Опознание вещества ходит в справочник по названию, а не по номеру:
        # verify_cas отсекал бы название на проверке контрольной суммы.
        def lookup_name(self, name: str) -> SubstanceInfo:
            if pubchem is not None:
                return pubchem
            return SubstanceInfo(cas="", found=False, error="not_found")

        def verify_cas(self, cas: str) -> SubstanceInfo:
            if pubchem is not None:
                return pubchem
            return SubstanceInfo(cas=cas, found=False, error="not_found")

    monkeypatch.setattr(substance_resolution, "PubChemConnector", _StubPubChem)
    monkeypatch.setattr(
        substance_resolution,
        "search_web",
        lambda query, limit=8: list(results or []),
    )


def test_pubchem_hit_gives_a_confirmed_number(monkeypatch):
    """Справочник знает название — номер берётся из него и помечен как проверенный."""
    info = SubstanceInfo(
        cas="Pioglitazone",
        found=True,
        cid=4829,
        iupac_name="pioglitazone",
        molecular_formula="C19H20N2O3S",
        synonyms=["Pioglitazone", "111025-46-8", "Actos", "AKOS015896321"],
    )
    _patch_sources(monkeypatch, pubchem=info)

    result = resolve_substance("Pioglitazone", llm=_StubLLM({"candidates": []}))

    assert result.pubchem_used is True
    first = result.candidates[0]
    assert first.source == "pubchem"
    assert first.cas == "111025-46-8"
    assert first.cas_confirmed is True
    # Складской артикул названием вещества не является и в отметки не идёт.
    assert "AKOS015896321" not in first.synonyms
    assert "Actos" in first.synonyms


def test_number_failing_the_checksum_is_not_substituted(monkeypatch):
    """Номер с неверной контрольной цифрой не подставляется, но кандидат остаётся."""
    _patch_sources(
        monkeypatch,
        results=_snippets(
            (
                "Menthyl lactate",
                "https://example.test/ml",
                "Menthyl lactate CAS 59259-38-1 cooling agent",
            )
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Menthyl lactate",
                    "cas": "59259-38-1",
                    "relation": "same",
                    "reason": "Охлаждающий агент",
                    "source_url": "https://example.test/ml",
                    "quote": "Menthyl lactate CAS 59259-38-1 cooling agent",
                }
            ]
        }
    )

    result = resolve_substance("Menthyl lactate", llm=llm)

    assert len(result.candidates) == 1
    assert result.candidates[0].cas is None
    assert result.candidates[0].cas_confirmed is False
    assert any("контрольную сумму" in text for text in result.warnings)


def test_number_absent_from_the_sources_is_rejected(monkeypatch):
    """Верный по форме номер, которого нет в выдаче, считается достроенным."""
    _patch_sources(
        monkeypatch,
        results=_snippets(
            (
                "Zinc ricinoleate",
                "https://example.test/zn",
                "Zinc ricinoleate powder for deodorants",
            )
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Zinc ricinoleate",
                    "cas": "13040-19-2",
                    "relation": "same",
                    "reason": "Соль рицинолевой кислоты",
                    "source_url": "https://example.test/zn",
                    "quote": "Zinc ricinoleate powder for deodorants",
                }
            ]
        }
    )

    result = resolve_substance("Zinc ricinoleate powder", llm=llm)

    assert result.candidates[0].cas is None
    assert any("нет ни в одном источнике" in text for text in result.warnings)


def test_neighbouring_substance_is_kept_as_a_separate_relation(monkeypatch):
    """Соседнее название сохраняется отдельно — оно и есть защита от подмены.

    Заказчик закупил поликватерниум-22 вместо силикон-кватерниума-22 именно
    потому, что названия соседние. Такой кандидат нужен закупщику не меньше
    правильного: он уходит в отрицательный фильтр поиска.
    """
    _patch_sources(
        monkeypatch,
        results=_snippets(
            (
                "Quaternium-18",
                "https://example.test/q18",
                "Quaternium-18 CAS 61789-80-8 is a quaternary ammonium salt",
            ),
            (
                "Silicone Quaternium-18",
                "https://example.test/sq18",
                "Silicone Quaternium-18 is a silicone hair conditioning agent",
            ),
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Silicone Quaternium-18",
                    "cas": None,
                    "relation": "same",
                    "reason": "Исправленное написание INCI",
                    "source_url": "https://example.test/sq18",
                    "quote": "Silicone Quaternium-18 is a silicone hair conditioning agent",
                },
                {
                    "name": "Quaternium-18",
                    "cas": "61789-80-8",
                    "relation": "different",
                    "reason": "Другое вещество: соль четвертичного аммония, не силикон",
                    "source_url": "https://example.test/q18",
                    "quote": "Quaternium-18 CAS 61789-80-8 is a quaternary ammonium salt",
                },
            ]
        }
    )

    result = resolve_substance("Silicon quaternium-18", llm=llm)

    same = [item for item in result.candidates if item.relation == "same"]
    different = [item for item in result.candidates if item.relation == "different"]
    assert [item.name for item in same] == ["Silicone Quaternium-18"]
    # У правильного названия номера нет, у соседнего есть — и это нормально.
    assert same[0].cas is None
    assert different[0].name == "Quaternium-18"
    assert different[0].cas == "61789-80-8"
    assert different[0].cas_confirmed is True


def test_unavailable_model_leaves_the_deterministic_answer(monkeypatch):
    """Недоступная модель не роняет кнопку: остаётся ветка справочника."""
    info = SubstanceInfo(
        cas="Ruxolitinib",
        found=True,
        cid=25126798,
        synonyms=["Ruxolitinib", "941678-49-5"],
    )
    _patch_sources(
        monkeypatch,
        pubchem=info,
        results=_snippets(
            ("Ruxolitinib", "https://example.test/rux", "Ruxolitinib CAS 941678-49-5")
        ),
    )
    llm = _StubLLM(LLMUnavailableError("model down"))

    result = resolve_substance("Ruxolitinib", llm=llm)

    assert result.llm_used is False
    assert [item.source for item in result.candidates] == ["pubchem"]
    assert result.candidates[0].cas == "941678-49-5"
    assert any("Модель недоступна" in text for text in result.warnings)


def test_empty_search_and_empty_reference_explain_themselves(monkeypatch):
    """Пустой ответ объясняется, а не выглядит как молчаливый сбой."""
    _patch_sources(monkeypatch, results=[])

    result = resolve_substance("Совершенно неизвестное название", llm=_StubLLM({}))

    assert result.candidates == []
    assert result.warnings, "пустой результат должен объясняться"


def test_blank_name_does_not_call_anything(monkeypatch):
    """Пустое название не идёт ни в справочник, ни в поиск."""
    called: list[str] = []
    monkeypatch.setattr(
        substance_resolution,
        "search_web",
        lambda query, limit=8: called.append(query) or [],
    )

    result = resolve_substance("   ")

    assert result.candidates == []
    assert called == []


# --- Контракт эндпоинта ---


def test_endpoint_returns_candidates_and_is_open_to_the_auditor(monkeypatch):
    """Опознание только читает внешние источники, поэтому доступно и аудитору."""
    import pytest  # noqa: F401 - импорт локальный: модуль сервисный по большей части
    from fastapi.testclient import TestClient

    from app.api import substances as substances_api
    from app.core.db import engine
    from app.main import app

    monkeypatch.setattr(
        substances_api,
        "resolve_substance",
        lambda name: substance_resolution.SubstanceResolution(
            query=name,
            candidates=[
                substance_resolution.ResolvedName(
                    name="Silicone Quaternium-18",
                    relation="same",
                    reason="Исправленное написание INCI",
                    source="web",
                    source_url="https://example.test/sq18",
                )
            ],
            warnings=["Номер у этого INCI отсутствует"],
            search_used=True,
            llm_used=True,
        ),
    )

    try:
        with TestClient(app) as client:
            token = client.post(
                "/auth/login", json={"username": "auditor", "password": "demo123"}
            ).json()["access_token"]
            response = client.post(
                "/substances/resolve",
                json={"name": "Silicon quaternium-18"},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        engine.dispose()

    assert response.status_code == 200
    body = response.json()
    assert body["candidates"][0]["name"] == "Silicone Quaternium-18"
    assert body["candidates"][0]["cas"] is None
    assert body["warnings"]


def test_endpoint_rejects_an_empty_name(monkeypatch):
    """Пустое название отсекается контрактом, а не сетевым вызовом."""
    from fastapi.testclient import TestClient

    from app.core.db import engine
    from app.main import app

    try:
        with TestClient(app) as client:
            token = client.post(
                "/auth/login", json={"username": "ivanov", "password": "demo123"}
            ).json()["access_token"]
            response = client.post(
                "/substances/resolve",
                json={"name": " "},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        engine.dispose()

    assert response.status_code == 422


# --- провенанс: на нём стоит автозаполнение синонимов ---


def test_model_verdict_never_claims_registry_provenance(monkeypatch):
    """Вывод модели не может выдать себя за запись справочника.

    Форма автоматически отмечает равнозначные названия, и порогом служит
    именно источник: синоним PubChem — запись реестра, кандидат из веба —
    прочтение страницы моделью. Если бы веб-кандидат мог получить
    source="pubchem", форма молча отметила бы догадку модели, а неверный
    синоним в поиске находит настоящих поставщиков не того вещества.
    """
    _patch_sources(
        monkeypatch,
        results=_snippets(
            (
                "Betaine anhydrous",
                "https://example.test/betaine",
                "Betaine anhydrous is the same as glycine betaine",
            ),
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Glycine betaine",
                    "cas": None,
                    "relation": "same",
                    "reason": "Страница называет это тем же веществом",
                    "source_url": "https://example.test/betaine",
                    "quote": "Betaine anhydrous is the same as glycine betaine",
                }
            ]
        }
    )

    result = resolve_substance("Betaine anhydrous", llm=llm)

    assert result.candidates, "кандидат из веба должен остаться в выдаче"
    assert all(item.source == "web" for item in result.candidates)
    # Причина обязательна: форма показывает её закупщику рядом с названием.
    assert all(item.reason for item in result.candidates)


def test_registry_synonyms_are_ready_for_autofill(monkeypatch):
    """Синонимы карточки PubChem пригодны для отметки без правки руками.

    Форма отмечает их автоматически, поэтому в списке не должно быть
    номеров и складских артикулов: закупщик увидел бы их в письме
    поставщику как названия вещества.
    """
    info = SubstanceInfo(
        cas="Betaine",
        found=True,
        cid=247,
        iupac_name="betaine",
        molecular_formula="C5H11NO2",
        synonyms=[
            "Betaine",
            "107-43-7",
            "Glycine betaine",
            "AKOS015896321",
            "NSC 27640",
            "Trimethylglycine",
        ],
    )
    _patch_sources(monkeypatch, pubchem=info)

    result = resolve_substance("Betaine", llm=_StubLLM({"candidates": []}))

    card = result.candidates[0]
    assert card.source == "pubchem"
    assert card.relation == "same"
    assert "Glycine betaine" in card.synonyms
    assert "Trimethylglycine" in card.synonyms
    for junk in ("107-43-7", "AKOS015896321", "NSC 27640"):
        assert junk not in card.synonyms, junk
    # Само предпочтительное название в синонимах не дублируется.
    assert card.name not in card.synonyms


def test_neighbouring_name_never_arrives_as_same(monkeypatch):
    """Другая соль остаётся different и в автозаполнение не попадает."""
    _patch_sources(
        monkeypatch,
        results=_snippets(
            (
                "Betaine hydrochloride",
                "https://example.test/bet-hcl",
                "Betaine hydrochloride CAS 590-46-5 is the salt form",
            ),
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Betaine hydrochloride",
                    "cas": "590-46-5",
                    "relation": "different",
                    "reason": "Гидрохлорид — другое вещество",
                    "source_url": "https://example.test/bet-hcl",
                    "quote": "Betaine hydrochloride CAS 590-46-5 is the salt form",
                }
            ]
        }
    )

    result = resolve_substance("Betaine", llm=llm)

    same = [item for item in result.candidates if item.relation == "same"]
    different = [item for item in result.candidates if item.relation == "different"]
    assert not same
    assert [item.name for item in different] == ["Betaine hydrochloride"]


def test_registry_is_asked_by_name_not_by_number(monkeypatch):
    """Опознание идёт по названию — и должно доходить до сети.

    `verify_cas` начинается с проверки контрольной суммы и на «Betaine»
    отвечает invalid_cas_checksum, не сделав ни одного запроса. Пока
    опознание звало именно его, справочная ветка молчала всегда: карточки
    приходили только из веб-поиска, то есть из прочтения страниц моделью.
    На проде это выглядело как «PubChem опрошен, кандидатов из него нет».
    """
    asked: list[tuple[str, str]] = []

    class _RecordingPubChem:
        def lookup_name(self, name: str) -> SubstanceInfo:
            asked.append(("lookup_name", name))
            return SubstanceInfo(
                cas="",
                found=True,
                cid=247,
                iupac_name="betaine",
                synonyms=["Betaine", "107-43-7", "Trimethylglycine"],
            )

        def verify_cas(self, cas: str) -> SubstanceInfo:
            asked.append(("verify_cas", cas))
            return SubstanceInfo(cas=cas, found=False, error="invalid_cas_checksum")

    monkeypatch.setattr(substance_resolution, "PubChemConnector", _RecordingPubChem)
    monkeypatch.setattr(substance_resolution, "search_web", lambda query, limit=8: [])

    result = resolve_substance("Betaine", llm=_StubLLM({"candidates": []}))

    assert asked and asked[0][0] == "lookup_name", (
        "справочник обязан спрашиваться по названию, иначе ветка мертва"
    )
    registry = [item for item in result.candidates if item.source == "pubchem"]
    assert registry, "карточка из справочника должна появиться"
    assert registry[0].cas == "107-43-7"
    assert "Trimethylglycine" in registry[0].synonyms
