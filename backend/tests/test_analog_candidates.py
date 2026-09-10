"""Подбор аналогов: что принимается на замену, а что отбрасывается.

Проверяется главное продуктовое правило ступени: замена, которую нечем
проверить, замной не считается. Название без цитаты и адреса не
показывается вовсе, номер без подтверждения не подставляется, а само
закупаемое вещество под другим написанием аналогом не является — иначе
закупщик выберет «замену», которая ничего не заменяет.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_analog_candidates.db")

from app.extraction.llm_client import LLMUnavailableError
from app.services import analog_candidates
from app.services.analog_candidates import suggest_analogs


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


def _patch_search(monkeypatch, results: list[dict] | None) -> list[str]:
    queries: list[str] = []

    def _search(query: str, limit: int = 8) -> list[dict]:
        queries.append(query)
        return list(results or [])

    monkeypatch.setattr(analog_candidates, "search_web", _search)
    return queries


def test_candidate_with_a_quote_and_a_confirmed_number(monkeypatch):
    """Обычный случай: замена названа в выдаче, номер есть в тексте."""
    _patch_search(
        monkeypatch,
        _snippets(
            (
                "Preservative alternatives",
                "https://example.test/preservatives",
                "Sodium benzoate (CAS 532-32-1) is used as an alternative "
                "to potassium sorbate in beverages.",
            )
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Sodium benzoate",
                    "cas": "532-32-1",
                    "reason": "Тот же класс консервантов, другая соль кислоты.",
                    "source_url": "https://example.test/preservatives",
                    "quote": "Sodium benzoate (CAS 532-32-1) is used as an "
                    "alternative to potassium sorbate in beverages.",
                }
            ]
        }
    )

    result = suggest_analogs("Potassium sorbate", cas="24634-61-5", llm=llm)

    assert result.search_used is True
    assert result.llm_used is True
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.name == "Sodium benzoate"
    assert candidate.cas == "532-32-1"
    assert candidate.cas_confirmed is True
    assert candidate.source_url == "https://example.test/preservatives"
    assert result.warnings == []


def test_number_absent_from_the_output_is_not_substituted(monkeypatch):
    """Номер, которого нет в выдаче, не подставляется: кандидат остаётся без него."""
    _patch_search(
        monkeypatch,
        _snippets(
            (
                "Thickener alternatives",
                "https://example.test/thickeners",
                "Xanthan gum is commonly replaced by guar gum in cold process "
                "formulations.",
            )
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Guar gum",
                    # Номер настоящий и контрольную сумму проходит, но в
                    # выдаче его нет: модель достроила его по памяти.
                    "cas": "9000-30-0",
                    "reason": "Тот же загуститель, другое сырьё.",
                    "source_url": "https://example.test/thickeners",
                    "quote": "Xanthan gum is commonly replaced by guar gum "
                    "in cold process formulations.",
                }
            ]
        }
    )

    result = suggest_analogs("Xanthan gum", llm=llm)

    assert result.candidates[0].cas is None
    assert result.candidates[0].cas_confirmed is False
    assert any("9000-30-0" in warning for warning in result.warnings)


def test_candidate_without_a_source_is_not_shown(monkeypatch):
    """Замена без цитаты и ссылки не показывается: проверить её нечем."""
    _patch_search(
        monkeypatch,
        _snippets(
            (
                "Solvent alternatives",
                "https://example.test/solvents",
                "Ethyl acetate is a common alternative to acetone.",
            )
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Methyl ethyl ketone",
                    "cas": None,
                    "reason": "Похожий растворитель.",
                    "source_url": "",
                    "quote": "",
                }
            ]
        }
    )

    result = suggest_analogs("Acetone", llm=llm)

    assert result.candidates == []
    assert any("Methyl ethyl ketone" in warning for warning in result.warnings)


def test_the_purchased_substance_itself_is_not_an_analog(monkeypatch):
    """Само закупаемое вещество заменой не является — ни по названию, ни по номеру."""
    _patch_search(
        monkeypatch,
        _snippets(
            (
                "Ascorbic acid",
                "https://example.test/ascorbic",
                "Ascorbic acid (CAS 50-81-7), also sold as vitamin C, "
                "CAS 50-81-7.",
            )
        ),
    )
    llm = _StubLLM(
        {
            "candidates": [
                {
                    "name": "Ascorbic acid",
                    "cas": "50-81-7",
                    "reason": "То же вещество.",
                    "source_url": "https://example.test/ascorbic",
                    "quote": "Ascorbic acid (CAS 50-81-7)",
                },
                {
                    "name": "Vitamin C",
                    "cas": "50-81-7",
                    "reason": "Другое название того же вещества.",
                    "source_url": "https://example.test/ascorbic",
                    "quote": "also sold as vitamin C, CAS 50-81-7",
                },
            ]
        }
    )

    result = suggest_analogs("Ascorbic acid", cas="50-81-7", llm=llm)

    assert result.candidates == []


def test_search_without_results_says_so_instead_of_inventing(monkeypatch):
    """Пустая выдача — честный ответ и предупреждение, а не выдуманная замена."""
    _patch_search(monkeypatch, [])
    llm = _StubLLM({"candidates": [{"name": "Что угодно"}]})

    result = suggest_analogs("Полиэфирполиол для жёсткого ППУ", llm=llm)

    assert result.candidates == []
    assert result.llm_used is False
    assert llm.calls == []
    assert result.warnings


def test_unavailable_model_does_not_break_the_button(monkeypatch):
    """Недоступная модель — предупреждение, а не пустой экран без объяснения."""
    _patch_search(
        monkeypatch,
        _snippets(
            ("Alternatives", "https://example.test/a", "Some alternative text")
        ),
    )

    result = suggest_analogs(
        "Acetone", llm=_StubLLM(LLMUnavailableError("нет слота"))
    )

    assert result.candidates == []
    assert any("Модель недоступна" in warning for warning in result.warnings)


def test_specification_anchors_the_query_for_items_without_a_number(monkeypatch):
    """У позиции без номера запрос идёт по показателям, а не только по названию.

    Именно так выглядит половина списка заказчика: торговая марка или
    описание вместо номера. Запрос по одному названию для них бесполезен.
    """
    queries = _patch_search(monkeypatch, [])

    suggest_analogs(
        "Полиэфирполиол",
        specification="Гидроксильное число 440-460 мг KOH/г",
        llm=_StubLLM({"candidates": []}),
    )

    assert any("440-460" in query for query in queries)


def test_application_narrows_the_search_and_reaches_the_model(monkeypatch):
    """Применение — не пожелание, а критерий подбора.

    Без него выдача по «чем заменить ксантановую камедь» состоит из
    кулинарных сравнений, и первый же боевой прогон 10.09.2026 вернул
    желатин. Применение обязано попасть и в поисковый запрос, и в задание
    модели — иначе отсечь такую замену нечем.
    """
    queries = _patch_search(
        monkeypatch,
        _snippets(("Thickeners", "https://example.test/t", "some text")),
    )
    llm = _StubLLM({"candidates": []})

    suggest_analogs(
        "Ксантановая камедь",
        application="промышленный загуститель для буровых растворов",
        constraints="без животного происхождения",
        llm=llm,
    )

    assert any("промышленный загуститель" in query for query in queries)
    assert "буровых" in llm.calls[0]
    assert "без животного происхождения" in llm.calls[0]


def test_long_application_does_not_leak_into_the_search_string(monkeypatch):
    """В поисковую строку идут первые слова применения, а не весь абзац.

    Замер на проде 10.09.2026: запрос со всей строкой («загуститель
    бурового раствора, температура до 80 °C, минерализация до 200 г/л»)
    не нашёл ни одной страницы — под такую фразу их не существует, — и
    подбор вернул пусто. С коротким «загуститель бурового раствора»
    нашлись полиакриламиды.
    """
    queries = _patch_search(monkeypatch, [])

    suggest_analogs(
        "Ксантановая камедь",
        application=(
            "загуститель бурового раствора, температура до 80 °C, "
            "минерализация до 200 г/л"
        ),
        llm=_StubLLM({"candidates": []}),
    )

    assert not any("минерализация" in query for query in queries)
    assert not any("80" in query for query in queries)
    # Первый запрос остаётся точным: имя в кавычках и слова о замене.
    assert "загуститель" not in queries[0]


def test_candidates_dropped_by_a_constraint_are_named(monkeypatch):
    """Снятое ограничением показывается, а не исчезает молча.

    Замер на проде 10.09.2026: запрет «не пищевые добавки» снял обе
    найденные замены, и закупщик увидел голое «не нашлось» — как будто
    поиск сломался. Он задал запрет и вправе увидеть, что под него попало:
    иначе не понять, смягчать формулировку или искать иначе.
    """
    _patch_search(
        monkeypatch,
        _snippets(("Thickeners", "https://example.test/t", "guar gum, gelatin")),
    )
    llm = _StubLLM(
        {
            "candidates": [],
            "rejected": [
                {"name": "Желатин", "reason": "животного происхождения"}
            ],
        }
    )

    result = suggest_analogs(
        "Ксантановая камедь",
        constraints="без животного происхождения",
        llm=llm,
    )

    assert result.candidates == []
    assert any("Желатин" in warning for warning in result.warnings)
    assert any("животного происхождения" in warning for warning in result.warnings)
    # Пустой список из-за запрета объясняется иначе, чем пустая выдача:
    # в первом случае замены были, во втором их нет вовсе.
    assert any("сняты вашим ограничением" in warning for warning in result.warnings)
