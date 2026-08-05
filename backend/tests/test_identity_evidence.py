"""Факты со страницы как доказательства, а не суждения модели.

В замере на бетаине identity получила 0 у всех пяти кандидатов, потолок
балла оказался 45 при пороге короткого списка 70, и допуск стал недостижим
арифметически. Модель при этом не ошибалась злонамеренно: номер просто не
попадал в отдаваемый ей фрагмент страницы.

Здесь два разных рода фактов, и смешивать их нельзя. Номер вещества — факт
о товаре. Упоминание GMP или ISO — факт о том, что написала страница: сайт
продавца сертификат не подтверждает, он о нём только сообщает.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_identity_evidence.db")

from app.api.supplier_search import (
    QualificationEvidence,
    SupplierQualification,
    _apply_evidence_gates,
    _compose_page_text,
    _inject_deterministic_evidence,
)
from app.models.search_trace import SourceDocument


def _qualification(**kw) -> SupplierQualification:
    base = dict(
        result_index=0,
        company_name="Shandong Aobo Biotech",
        title_ru="Поставщик бетаина",
        summary_ru="Китайская компания, заявляет собственное производство.",
        supplier_type="manufacturer",
        cas_status="not_found",
        country_status="claimed",
        gmp_status="not_found",
        iso_status="not_found",
        coa_status="not_found",
        tds_status="not_found",
        confidence=45,
        red_flags=[],
        missing_evidence=[],
        evidence=[],
    )
    base.update(kw)
    return SupplierQualification(**base)


def _source(text: str, doc_id: int = 1) -> SourceDocument:
    source = SourceDocument(
        search_run_id=1,
        agent_run_id=1,
        url="https://en.aobobio.cn/",
        domain="en.aobobio.cn",
        status="completed",
        text_content=text,
    )
    source.id = doc_id
    return source


def _inject(qualifications, cas="107-43-7", source=None):
    _inject_deterministic_evidence(
        qualifications,
        cas=cas,
        source_documents={1: source if source is not None else _source(_PAGE)},
        source_indexes={1: 0},
    )


# Номер стоит за отметкой 4000 — ровно как на реальной странице Aobobio,
# где он оказался на позиции 4015.
_PAGE = "маркетинговый текст о компании\n" * 200 + "CAS No.: 107-43-7\n"


# --- совпадение вещества ---


def test_number_beyond_the_truncation_still_becomes_evidence():
    assert _PAGE.find("107-43-7") > 4000

    qualifications = {0: _qualification()}
    _inject(qualifications)

    evidence = qualifications[0].evidence
    assert [item.claim_type for item in evidence] == ["chemical_identity"]
    # Цитата обязана быть подстрокой сохранённого текста, иначе её отклонит
    # детерминированная проверка.
    assert evidence[0].quote in _PAGE
    assert evidence[0].support_status == "supports"


def test_a_wrong_number_produces_nothing():
    qualifications = {0: _qualification()}
    _inject(qualifications, cas="50-78-2")
    assert qualifications[0].evidence == []


def test_the_model_is_not_overruled_when_it_did_the_work():
    """Своё доказательство модели остаётся единственным: дубли не нужны."""
    own = QualificationEvidence(
        source_document_id=1,
        claim_type="chemical_identity",
        claim_value="CAS совпадает",
        support_status="supports",
        quote="CAS No.: 107-43-7",
    )
    qualifications = {0: _qualification(evidence=[own])}
    _inject(qualifications)
    assert len(qualifications[0].evidence) == 1


def test_a_quote_too_short_for_the_contract_is_skipped():
    """Контракт требует цитату не короче пяти символов.

    Строка страницы вполне может быть короче: на прогоне по адипиновой
    кислоте строка вида «TDS» уронила весь этап оценки ошибкой проверки
    схемы. Пропустить один факт дешевле, чем потерять прогон.
    """
    page = "о компании\n" * 50 + "TDS\nGMP\n"
    qualifications = {0: _qualification()}
    _inject(qualifications, cas="107-43-7", source=_source(page))

    for item in qualifications[0].evidence:
        assert len(item.quote) >= 5


def test_a_failed_page_yields_nothing():
    source = _source("")
    source.status = "failed"
    qualifications = {0: _qualification()}
    _inject(qualifications, source=source)
    assert qualifications[0].evidence == []


# --- документы и сертификаты ---


def test_certificate_mentions_become_evidence():
    """Требование подтверждающего документа в шортлисте зависело от того,
    заметит ли модель упоминание. Теперь оно читается со страницы."""
    page = (
        "о компании\n" * 200
        + "We are ISO 9001:2015 certified and follow GMP\n"
        + "Certificate of Analysis is provided with every batch\n"
    )
    qualifications = {0: _qualification()}
    _inject(qualifications, source=_source(page))

    types = {item.claim_type for item in qualifications[0].evidence}
    assert {"gmp", "iso", "coa"} <= types
    for item in qualifications[0].evidence:
        assert item.quote in page


def test_a_mention_raises_the_status_only_to_claimed():
    """Страница продавца сертификат не подтверждает — она о нём сообщает."""
    payload = _apply_evidence_gates(
        _qualification(iso_status="not_found"),
        [
            {
                "claim_type": "iso",
                "support_status": "supports",
                "quote_verified": True,
            }
        ],
    )
    assert payload["iso_status"] == "claimed"


def test_a_page_without_certificates_adds_nothing():
    qualifications = {0: _qualification()}
    _inject(qualifications, source=_source("Isopropyl alcohol, isomer mixture"))
    assert qualifications[0].evidence == []


# --- ворота ---


def test_gate_raises_the_status_the_model_missed():
    """Ворота работают в обе стороны: цитата и снимает статус, и ставит."""
    payload = _apply_evidence_gates(
        _qualification(cas_status="not_found"),
        [
            {
                "claim_type": "chemical_identity",
                "support_status": "supports",
                "quote_verified": True,
            }
        ],
    )
    assert payload["cas_status"] == "confirmed"


def test_gate_still_removes_an_unsupported_status():
    payload = _apply_evidence_gates(
        _qualification(cas_status="confirmed"),
        [],
    )
    assert payload["cas_status"] == "not_found"


# --- состав фрагмента для модели ---


def test_specification_goes_before_the_start_of_the_page():
    text = "шапка сайта\n" * 100 + "CAS No.: 107-43-7\n"
    composed = _compose_page_text(text, ["CAS No.: 107-43-7"], 400)
    assert composed.startswith("CAS No.: 107-43-7")
    assert "шапка сайта" in composed


def test_the_budget_does_not_grow():
    text = "x" * 10000
    composed = _compose_page_text(text, ["важная строка"], 500)
    assert len(composed) <= 500


def test_without_highlights_behaviour_is_unchanged():
    text = "y" * 10000
    assert _compose_page_text(text, [], 500) == text[:500]
