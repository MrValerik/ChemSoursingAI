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
    country_adjustment: int
    hard_exclusion: bool
    shortlist_eligible: bool

    def to_dict(self) -> dict:
        return asdict(self)


# Штраф за доказанную чужую страну. Закупщик называет страну не пожеланием,
# а условием: ему нужен поставщик именно там. До этого несовпадение просто
# не приносило десяти баллов, и карточка оставалась почти такой же
# привлекательной, как верная.
#
# Замер по 219 карточкам прогонов 280-320: у 45 страна доказанно чужая, и
# их медиана 50 против 55 у подтверждённых, p90 — 73 против 86. Otto Chemie
# из Индии набрала 81 балл в поиске по Китаю, Pfizer — 75, Dr. Reddy's — 75,
# и две такие карточки дошли до короткого списка.
#
# Двадцать пять баллов ставят их ниже даже карточек с неизвестной страной
# (медиана 36): «доказанно не там» хуже, чем «неизвестно где». Это штраф, а
# не исключение: у мирового производителя завод бывает и в нужной стране
# при штаб-квартире в другой, и прятать такую находку нельзя — её место
# ниже, а не за пределами списка.
COUNTRY_MISMATCH_PENALTY = -25

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


# Причина, по которой кандидат по запросу на аналог не попадает в короткий
# список. Короткий список означает «можно запрашивать цену как у подходящего
# поставщика», а аналог до сравнения свойств человеком подходящим не является.
ANALOG_NEEDS_REVIEW_FLAG = (
    "Запрос ищет аналог: равнозначность состава и свойств эталону "
    "автоматически не доказана и требует решения специалиста"
)


def score_supplier(
    assessment: dict,
    evidence: list[dict],
    *,
    identification_method: str = "cas",
) -> SupplierScore:
    """Детерминированная оценка кандидата.

    `identification_method="analog"` закрывает короткий список: поиск
    аналога ищет замену эталону, и подтверждённая цитата означает там
    «продукт со схожей функцией найден», а не «вещество то же». Раньше
    это правило жило только в тексте промпта — то есть держалось на
    добросовестности модели, — и кандидат по аналогу мог набрать 70+
    баллов и уйти в короткий список как готовый ответ.
    """
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
            country_adjustment=0,
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
    country_adjustment = (
        COUNTRY_MISMATCH_PENALTY
        if assessment.get("country_status") == "mismatch"
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
            + volume_adjustment
            + country_adjustment,
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
        # Короткий список — обещание безопасности, а не подборка похожего.
        # Компания, о которой доказано, что она в другой стране, ответом на
        # запрос по этой стране быть не может. Две такие в него уже попали.
        and assessment.get("country_status") != "mismatch"
        # Балл кандидату по аналогу сохраняется: он честно отражает
        # найденные доказательства. Закрыт именно короткий список —
        # решение, а не оценка.
        and identification_method != "analog"
    )
    return SupplierScore(
        total=total,
        identity=identity,
        supplier_role=supplier_role,
        country=country,
        documents=documents,
        evidence_quality=evidence_quality,
        country_adjustment=country_adjustment,
        volume_adjustment=volume_adjustment,
        hard_exclusion=False,
        shortlist_eligible=shortlist_eligible,
    )
