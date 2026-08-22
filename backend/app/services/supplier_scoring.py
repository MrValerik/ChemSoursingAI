"""Deterministic, explainable scoring of supplier evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SupplierScore:
    total: int
    identity: int
    supplier_role: int
    country: int
    documents: int
    evidence_quality: int
    volume_adjustment: int
    hard_exclusion: bool
    shortlist_eligible: bool

    def to_dict(self) -> dict:
        return asdict(self)


# Признаки, которые трейдеру выдать труднее, чем заявить «мы завод».
# Сертификат выдаёт внешний орган, CoA и TDS относятся к партии, а
# мощность и производственная площадка — проверяемые детали, которые
# перекупщик о себе обычно не пишет.
#
# Одних документов мало. Замер по 129 кандидатам: упоминание CoA, TDS,
# ISO или GMP есть лишь у 34%, и подтверждённых среди них ноль — сайт
# продавца сертификат не подтверждает. А у заказчика документы приходят
# перепиской за 2–4 дня после контакта. Требовать их при поиске значит
# закрывать список для заводов со скупым сайтом.
CORROBORATING_CLAIMS = frozenset(
    {"gmp", "iso", "coa", "tds", "production_capacity", "production_site"}
)

# Причина, по которой кандидат не попал в короткий список: закупщик должен
# видеть, чего именно не хватило, а не пустую строку.
SELF_DECLARED_ONLY_FLAG = (
    "Статус производителя держится только на заявлении самой компании: "
    "нет ни сертификата, ни документа на партию, ни данных о мощности "
    "или собственной площадке"
)


def score_supplier(assessment: dict, evidence: list[dict]) -> SupplierScore:
    supported = {
        item["claim_type"]
        for item in evidence
        if item.get("support_status") == "supports"
        and item.get("quote_verified") is True
    }
    contradicted = {
        item["claim_type"]
        for item in evidence
        if item.get("support_status") == "contradicts"
        and item.get("quote_verified") is True
    }
    hard_exclusion = (
        "chemical_identity" in contradicted
        or assessment.get("cas_status") == "mismatch"
    )
    if hard_exclusion:
        return SupplierScore(
            total=0,
            identity=0,
            supplier_role=0,
            country=0,
            documents=0,
            evidence_quality=0,
            volume_adjustment=0,
            hard_exclusion=True,
            shortlist_eligible=False,
        )

    identity = 35 if "chemical_identity" in supported else 0
    supplier_type = assessment.get("supplier_type")
    supplier_role = (
        25
        if supplier_type == "manufacturer" and "manufacturer_role" in supported
        else 5
        if supplier_type == "distributor"
        and {"manufacturer_role", "reseller_role"} & supported
        else 0
    )
    # Подтверждение страны стоит баллов только тогда, когда подтверждена
    # искомая страна. Раньше здесь проверялось лишь наличие claim, и
    # доказательство «India» с цитатой «+91 8767360663» приносило те же 10
    # баллов в поиске по Китаю: так Simson Pharma набрала 63 и попала в
    # запрос #30. Значение claim сверяют ворота, сюда приходит готовый
    # статус.
    country = (
        10
        if "country" in supported and assessment.get("country_status") != "mismatch"
        else 0
    )
    documents = sum(
        weight
        for claim_type, weight in (
            ("gmp", 5),
            ("iso", 4),
            ("coa", 3),
            ("tds", 3),
            ("production_capacity", 5),
            ("production_site", 3),
        )
        if claim_type in supported
    )
    evidence_quality = 0
    if evidence and all(item.get("quote_verified") is True for item in evidence):
        evidence_quality += 10
    if len(supported) >= 3:
        evidence_quality += 5

    volume = assessment.get("volume_compatibility") or {}
    volume_status = volume.get("status")
    has_requested_volume = bool(volume.get("requested_volume_raw"))
    volume_adjustment = (
        -20
        if volume_status == "incompatible"
        else -5
        if volume_status == "unknown" and has_requested_volume
        else 0
    )
    total = max(
        0,
        min(
            100,
            identity
            + supplier_role
            + country
            + documents
            + evidence_quality
            + volume_adjustment,
        ),
    )
    # Роль производителя почти всегда подтверждается цитатой с сайта самого
    # продавца: «we are a dedicated manufacturer» пишет и завод, и перекупщик.
    # Для короткого списка этого мало — нужна вторая опора, которую трейдеру
    # выдать труднее: сертификат от внешнего органа или предъявленный документ
    # на партию.
    corroboration = supported & CORROBORATING_CLAIMS
    shortlist_eligible = (
        total >= 70
        and supplier_type == "manufacturer"
        and "chemical_identity" in supported
        and "manufacturer_role" in supported
        and bool(corroboration)
        and volume_status != "incompatible"
    )
    return SupplierScore(
        total=total,
        identity=identity,
        supplier_role=supplier_role,
        country=country,
        documents=documents,
        evidence_quality=evidence_quality,
        volume_adjustment=volume_adjustment,
        hard_exclusion=False,
        shortlist_eligible=shortlist_eligible,
    )
