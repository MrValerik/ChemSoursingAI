"""Страница, которая спорит сама с собой, завод не доказывает.

Ручная проверка восьми карточек «производитель» 3 сентября 2026: верны
оказались две, ещё две с оговорками, четыре неверны.

- Dalian Handom: на той же странице, где «our factory covers an area of
  30 000 square meters», написано «is a 10-year-experienced supplier of
  Organic Chemicals», а компания основана в 2013 году ради экспорта
  витаминов. Карточка вышла «производитель, доказано» — из-за правила,
  которое открывает раздел «о компании».
- LeapChem: «Strategic Sourcing. Our network consists of 6 500 suppliers»
  рядом с цифрой мощности.
- Roma Pharma доказывала себя цитатой «leading pioglitazone hydrochloride
  15mg tablet manufacturer»: вещество названо верно, но изготовитель
  таблетки субстанцию покупает, а не производит.
- Suzhou Springchem, наоборот, настоящий завод, и видно это по документам:
  лицензия на производство и разрешение на выбросы провинции Чжэцзян.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_role_contradiction.db")

from app.api.supplier_search import (  # noqa: E402
    SupplierQualification,
    _apply_evidence_gates,
    _evidence_rejection_reason,
    QualificationEvidence,
)
from app.models.search_trace import SourceDocument  # noqa: E402
from app.services.page_facts import (  # noqa: E402
    find_production_facts,
    find_production_permits,
    find_sourcing_network,
    find_trading_identity,
    looks_like_dosage_form_claim,
)

FILLER = (
    "Organic chemicals for pharmaceutical, food and personal care industries, "
    "packed in 25 kg drums and shipped worldwide from Dalian and Qingdao.\n"
) * 3

HANDOM_PROFILE = (
    "DALIAN HANDOM CHEMICALS CO.,LTD.\n"
    "Dalian Handom Chemicals Co.,Ltd is a 10-year-experienced supplier of "
    "Organic Chemicals from northeast of China.\n"
    "With 10+ years of export experience, our factory covers an area of "
    "30,000 square meters.\n"
) + FILLER


def _qualification(**kw) -> SupplierQualification:
    base = dict(
        result_index=0,
        company_name="Dalian Handom Chemicals",
        title_ru="Поставщик ментиллактата",
        summary_ru="Заявляет собственное производство.",
        supplier_type="manufacturer",
        page_kind="company_site",
        cas_status="not_found",
        country_status="claimed",
        gmp_status="not_found",
        iso_status="not_found",
        coa_status="not_found",
        tds_status="not_found",
        confidence=60,
        red_flags=[],
        missing_evidence=[],
        evidence=[],
    )
    base.update(kw)
    return SupplierQualification(**base)


def _gate(page: str, profile: str, evidence: list[dict]) -> dict:
    return _apply_evidence_gates(
        _qualification(),
        evidence,
        page_url="https://www.handomchemicals.com/l-menthyl-lactate-supplier/",
        page_text=page,
        profile_text=profile,
        fetch_status="completed",
    )


SITE_EVIDENCE = [
    {
        "claim_type": "production_site",
        "support_status": "supports",
        "claim_value": "площадка",
        "quote": "our factory covers an area of 30,000 square meters",
    }
]


def test_поставщик_и_завод_на_одной_странице_доказательством_не_считаются():
    payload = _gate(FILLER, HANDOM_PROFILE, SITE_EVIDENCE)
    assert payload["role_proof"] == "contradicted"
    assert payload["supplier_type"] == "unknown"
    assert any("поставщиком" in flag for flag in payload["red_flags"])


def test_без_встречного_свидетельства_площадка_остаётся_доказательством():
    profile = (
        "Kemengda owns two wholly-owned production bases for specialty "
        "surfactants in Xinjin District.\n"
    ) + FILLER
    payload = _gate(FILLER, profile, SITE_EVIDENCE)
    assert payload["role_proof"] == "proven"
    assert payload["supplier_type"] == "manufacturer"


def test_разрешение_на_производство_снимает_спор():
    """Торговая компания лицензии на производство физически не держит."""
    profile = HANDOM_PROFILE + "Safety Production License and Certificate of Work Safety\n"
    payload = _gate(FILLER, profile, SITE_EVIDENCE)
    assert payload["role_proof"] == "proven"
    assert payload["supplier_type"] == "manufacturer"


def test_закупочная_сеть_спорит_и_с_цифрой_мощности():
    profile = (
        "Strategic Sourcing. Our network consists of 6,500 suppliers "
        "delivers high-quality products across 32 industries.\n"
    ) + FILLER
    payload = _gate(
        FILLER,
        profile,
        [
            {
                "claim_type": "production_capacity",
                "support_status": "supports",
                "claim_value": "мощность",
                "quote": "100MT/year",
            }
        ],
    )
    assert payload["role_proof"] == "contradicted"


def test_общий_поставщик_с_цифрой_мощности_не_спорит():
    """Цифра мощности сильнее общего «поставщик такого-то»."""
    payload = _gate(
        FILLER,
        HANDOM_PROFILE,
        [
            {
                "claim_type": "production_capacity",
                "support_status": "supports",
                "claim_value": "мощность",
                "quote": "annual capacity 5,000 tons",
            }
        ],
    )
    assert payload["role_proof"] == "proven"


def test_готовая_форма_доказательством_о_веществе_не_служит():
    assert looks_like_dosage_form_claim(
        "Roma Pharma is the leading pioglitazone hydrochloride 15mg tablet manufacturer"
    )
    assert looks_like_dosage_form_claim(
        "We manufacture Pharmaceutical Tablets, Capsules and Syrups"
    )
    assert not looks_like_dosage_form_claim(
        "Annual capacity of pioglitazone hydrochloride API is 12 tons"
    )


def test_цитата_о_таблетке_отклоняется_воротами_доказательств():
    source = SourceDocument(
        search_run_id=1,
        agent_run_id=1,
        url="https://www.romapharma.co.in/about",
        domain="www.romapharma.co.in",
        status="completed",
        text_content=(
            "Roma Pharma is the leading pioglitazone hydrochloride 15mg tablet "
            "manufacturer in India.\n"
        ),
    )
    source.id = 1
    reason = _evidence_rejection_reason(
        QualificationEvidence(
            source_document_id=1,
            claim_type="manufacturer_role",
            claim_value="производитель",
            support_status="supports",
            quote=(
                "Roma Pharma is the leading pioglitazone hydrochloride 15mg "
                "tablet manufacturer in India"
            ),
        ),
        result_index=0,
        source_documents={1: source},
        source_indexes={1: 0},
        cas=None,
        names=["Pioglitazone"],
    )
    assert reason == "цитата о готовой форме, а не о веществе"


def test_разрешение_становится_цитатой_о_производстве():
    text = (
        "We got all approvals of Work Safety: Safety Production License and "
        "Certificate of Work Safety Standardization.\n"
        "We got the Environmental Protection Approval: Pollution-Discharge "
        "Permit of Zhejiang Province.\n"
    )
    assert "Safety Production License" in find_production_permits(text)
    facts = find_production_facts(text)
    assert "Safety Production License" in facts["production_site"]


def test_подпись_поля_цитируется_вместе_со_значением():
    """Цитата «Factory Address:» ничего не доказывает читателю."""
    text = (
        "Factory Address:\n"
        "No.381 Hongtu Road, Chemical Industrial Park, Feidong County, Hefei\n"
    )
    quote = find_production_facts(text)["production_site"]
    assert "Hongtu Road" in quote


def test_торговая_личность_и_сеть_читаются_дословно():
    identity = find_trading_identity(HANDOM_PROFILE)
    # Оборот отрезается по границе предложения, а точка стоит внутри
    # «Co.,Ltd» — цитата от этого остаётся дословной и читаемой.
    assert identity == (
        "Ltd is a 10-year-experienced supplier of Organic Chemicals from "
        "northeast of China"
    )
    assert identity in HANDOM_PROFILE
    network = find_sourcing_network(
        "Strategic Sourcing. Our network consists of 6,500 suppliers.\n"
    )
    assert "Sourcing" in network


def test_производство_под_чужой_маркой_как_род_занятий_спорит():
    """Asterisk: «Top Third Party Pharma Manufacturing Company in Chandigarh»."""
    from app.services.page_facts import find_contract_identity

    profile = (
        "Asterisk Healthcare India (P) Ltd is the most trusted manufacturer of "
        "Pharmaceutical Tablets and Capsules.\n"
        "Our team makes us the Top Third Party Pharma Manufacturing Company in "
        "Chandigarh.\n"
    ) + FILLER
    assert find_contract_identity(profile)
    payload = _gate(
        FILLER,
        profile,
        [
            {
                "claim_type": "production_site",
                "support_status": "supports",
                "claim_value": "площадка",
                "quote": "Manufacturing Plant address",
            }
        ],
    )
    assert payload["role_proof"] == "contradicted"


def test_контрактные_заказы_настоящего_завода_роль_не_снимают():
    """Обычное «contract manufacturing» берёт и завод: по нему судить нельзя."""
    from app.services.page_facts import find_contract_identity

    assert not find_contract_identity(
        "Besides our own products we also offer contract manufacturing services."
    )
