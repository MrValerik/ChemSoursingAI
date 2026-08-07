"""Роль торговой компании тоже бывает доказана.

Роль производителя мы доказывать научились, а всё недоказанное падало в
«не определён». Посредник, который прямым текстом называет себя
посредником, попадал туда наравне с компанией, о которой не известно
ничего — а закупщику нужны и производители, и не производители, и
разница между ними и есть ответ.

Замер по сохранённым прогонам 214–252: признак срабатывает на пяти
карточках, все пять согласны с эталоном, ни одна не спорит с
доказательством производства.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_trade_facts.db")

from app.services.page_facts import find_trade_facts
from app.services.supplier_scoring import score_supplier


def _claim(claim_type: str, support: str = "supports") -> dict:
    return {
        "claim_type": claim_type,
        "support_status": support,
        "quote_verified": True,
    }


def _qualification(**overrides):
    from app.api.supplier_search import SupplierQualification

    fields = {
        "result_index": 0,
        "company_name": "Некто",
        "title_ru": "Оценка",
        "summary_ru": "Описание",
        "supplier_type": "manufacturer",
        "cas_status": "confirmed",
        "country_status": "claimed",
        "gmp_status": "not_found",
        "iso_status": "not_found",
        "coa_status": "not_found",
        "tds_status": "not_found",
        "confidence": 0,
        "red_flags": [],
        "missing_evidence": [],
        "evidence": [],
    }
    fields.update(overrides)
    return SupplierQualification(**fields)


# --- чтение страницы ---


def test_foreign_trade_self_description_is_read():
    """Строка Shandong Aojin, из-за которой правило и появилось."""
    text = (
        "Founded in 2009, Shandong Aojin Chemical Technology Co., Ltd. is a "
        "comprehensive enterprise integrating chemical product import and "
        "export, domestic trade, and supply chain services."
    )
    facts = find_trade_facts(text)
    assert "import and export" in facts["reseller_role"]


def test_authorised_distributor_is_read():
    text = "About\nWe are the authorized distributor of BASF in Poland\n"
    assert "reseller_role" in find_trade_facts(text)


def test_definite_article_is_read():
    text = "Henan GP is the professional and comprehensive distributor in China"
    assert "reseller_role" in find_trade_facts(text)


def test_a_faq_question_is_not_a_statement():
    """«Вы завод или торговая компания?» — в ответе значится завод.

    На странице Anhui Sunhere слова «trading company» стоят в вопросе
    FAQ, а ответ гласит «We are a manufacturer of pharmaceutical
    excipients». Ранняя, широкая версия признака ловила именно вопрос.
    """
    text = (
        "1.Q:Are you a factory or trading company? A:We are a manufacturer "
        "of pharmaceutical excipients."
    )
    assert find_trade_facts(text) == {}


def test_a_form_dropdown_is_not_a_statement():
    """«经销商» как пункт списка «тип организации» — мебель страницы."""
    text = "单位性质 高校研究所生物公司医药公司经销商其他 联系人 QQ E-mail"
    assert find_trade_facts(text) == {}


def test_a_plain_manufacturer_page_yields_nothing():
    text = "We have our own factory in Shandong and an annual capacity of 20,000 tons"
    assert find_trade_facts(text) == {}


# --- вывод о роли ---


def test_a_self_declared_trader_is_called_a_distributor():
    from app.api.supplier_search import _apply_evidence_gates

    payload = _apply_evidence_gates(
        _qualification(company_name="Shandong Aojin Chemical Technology"),
        [_claim("chemical_identity"), _claim("reseller_role")],
    )

    assert payload["supplier_type"] == "distributor"


def test_production_proof_outweighs_a_trading_arm():
    """У завода бывает и торговое подразделение — завод остаётся заводом."""
    from app.api.supplier_search import _apply_evidence_gates

    payload = _apply_evidence_gates(
        _qualification(),
        [
            _claim("chemical_identity"),
            _claim("production_site"),
            _claim("reseller_role"),
        ],
    )

    assert payload["supplier_type"] == "manufacturer"


def test_without_the_claim_the_status_stays_unknown():
    """Правило снимает «не определён» только по доказательству."""
    from app.api.supplier_search import _apply_evidence_gates

    payload = _apply_evidence_gates(
        _qualification(), [_claim("chemical_identity"), _claim("coa")]
    )

    assert payload["supplier_type"] == "unknown"


def test_a_proven_distributor_scores_its_role():
    """Доказанный посредник стоит больше недоказанного, но меньше завода."""
    assessment = {"supplier_type": "distributor", "cas_status": "confirmed"}
    evidence = [_claim("chemical_identity"), _claim("reseller_role")]

    assert score_supplier(assessment, evidence).supplier_role == 5


def test_a_trader_still_does_not_reach_the_shortlist_on_role_alone():
    assessment = {"supplier_type": "distributor", "cas_status": "confirmed"}
    evidence = [
        _claim("chemical_identity"),
        _claim("reseller_role"),
        _claim("country"),
    ]

    assert score_supplier(assessment, evidence).shortlist_eligible is False
