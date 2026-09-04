"""Роль компании, напечатанная полем анкеты, и язык снабжения.

Обход по неопределённым карточкам 3 сентября 2026. Из шести сайтов, о
которых конвейер сказал «не определено» либо промолчал, пять оказались
торговыми компаниями, и все пятеро говорят об этом на своей же странице:

- Shenghe печатает поле «Business Type: Manufacturer Representative /
  Trading Company», а карточка показывала «роль со слов сайта»;
- LANCHEMIE: «we specialize in supplying … organosilicon fine chemicals»,
  склад у порта Шанхая, восемь лет экспортного опыта, а из оборудования —
  ГХ-МС и вискозиметр, то есть только аналитика;
- Wenzhou Blue Dolphin: «we specialize in supplying optical brightening
  agents» — их дело оптические отбеливатели, а найдены они по ментиллактату.

Прежнее правило ждало оборота «we are a trading company» и не видело ни
одного из этих случаев. Поле анкеты сильнее прозы: это выбор из готового
списка, а не рассказ о себе.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_business_type.db")

from app.services.page_facts import (  # noqa: E402
    find_business_type,
    find_trade_facts,
    looks_like_third_party_production_claim,
)


def test_поле_анкеты_называет_торговую_компанию():
    text = "Business Type\nManufacturer Representative / Trading Company\nLocation\nJinan, Shandong, China\n"
    kind, quote = find_business_type(text)
    assert kind == "reseller"
    assert quote == "Business Type Manufacturer Representative / Trading Company"


def test_поле_анкеты_в_одну_строку():
    kind, quote = find_business_type("Business Type: Trading Company\nMobile: +86\n")
    assert kind == "reseller"
    assert quote == "Business Type: Trading Company"


def test_поле_анкеты_называет_завод():
    text = "Business Type:\nManufacturer/Factory\nCompany Introduction\nProduction Capacity\n"
    assert find_business_type(text) == (
        "manufacturer",
        "Business Type: Manufacturer/Factory",
    )


def test_смешанное_значение_читается_как_торговля():
    text = "Business Type\nManufacturer, Trading & service company, Distributor\nExport\n"
    kind, _ = find_business_type(text)
    assert kind == "reseller"


def test_китайская_подпись_поля():
    kind, _ = find_business_type("企业类型：贸易公司\n地址\n浙江杭州\n")
    assert kind == "reseller"


def test_выпадающий_список_полем_не_считается():
    """На форме обратной связи под подписью идёт весь словарь сразу."""
    text = (
        "Business Type\n"
        "Manufacturer/Factory\n"
        "Trading Company\n"
        "Distributor\n"
        "Other\n"
    )
    assert find_business_type(text) is None


def test_вопрос_из_справки_полем_не_считается():
    assert find_business_type("Business Type: are you a trading company?\n") is None


def test_абзац_рядом_с_подписью_значением_не_считается():
    long_value = "We are " + "a very reliable partner " * 6
    assert find_business_type(f"Business Type: {long_value}\n") is None


def test_поле_становится_доказательством_роли():
    text = "Business Type\nTrading Company\nAddress\nShijiazhuang, Hebei, China\n"
    facts = find_trade_facts(text)
    assert facts == {"reseller_role": "Business Type Trading Company"}


def test_поле_завода_доказательством_торговли_не_становится():
    text = "Business Type\nManufacturer/Factory\nPlant Area\n12000 square meters\n"
    assert find_trade_facts(text) == {}


def test_специализация_на_поставке_читается_из_длинного_абзаца():
    """У LANCHEMIE признак стоит в абзаце длиннее строки цитаты."""
    line = (
        "Headquartered in Hangzhou, a city renowned for its innovation and "
        "industrial vitality, we specialize in supplying high-quality "
        "organosilicon and organofluorine fine chemical products, boasting a "
        "comprehensive portfolio including silicone oils, silicone resins, "
        "silicone rubbers, silanes, fluorosilicone oils and perfluoropolyethers."
    )
    facts = find_trade_facts(line)
    assert "reseller_role" in facts
    assert "specialize in supplying" in facts["reseller_role"]
    # Цитата остаётся дословным куском страницы.
    assert facts["reseller_role"] in line


def test_специализация_на_поставке_у_короткого_абзаца():
    line = (
        "At Wenzhou Blue Dolphin New Material Co., Ltd., we specialize in "
        "supplying optical brightening agent solutions for plastics and paper."
    )
    quote = find_trade_facts(line)["reseller_role"]
    assert quote.startswith("we specialize in supplying optical brightening")
    assert quote in line


def test_мы_поставляем_торговой_компанией_не_делает():
    """Так пишет и завод, и по одному этому обороту судить нельзя."""
    assert find_trade_facts("We supply menthyl lactate worldwide.\n") == {}


def test_экспортный_опыт_торговой_компанией_не_делает():
    """Замер по 1681 странице: признак ловил заводы наравне с посредниками.

    У SPR Chemical в той же фразе стоит «with stable mass production».
    """
    assert find_trade_facts(
        "With 10+ years of export experience, our factory covers an area of "
        "20,000 square meters."
    ) == {}
    assert find_trade_facts(
        "With stable mass production and years of export experience serving "
        "global flavor houses."
    ) == {}


def test_контрактное_производство_доказательством_завода_не_служит():
    assert looks_like_third_party_production_claim(
        "Kavya pharma is a Contract Manufacturing Company provides WHO GMP quality"
    )
    assert looks_like_third_party_production_claim(
        "We undertake Third Party Pharmaceutical Contract Manufacturing"
    )
    assert looks_like_third_party_production_claim(
        "Loan licence: you rent space in an already licensed manufacturing unit"
    )
    assert not looks_like_third_party_production_claim(
        "Our factory covers an area of 20,000 square meters"
    )


def test_анкета_площадки_противоречит_роли_производителя():
    """Chuanghai: «Business Type Trading Company» рядом с «500000ton /Year».

    Роль не переворачивается — оба свидетельства заполнил один и тот же
    продавец, — но противоречие должно дойти до закупщика.
    """
    from app.api.supplier_search import (
        QualificationEvidence,
        SupplierQualification,
        _apply_evidence_gates,
    )

    # Ворота не судят о роде и роли по недогруженной странице, поэтому
    # образец должен быть длиннее порога загрузки.
    body = (
        "Caustic soda flakes and pearls for pulp, textile and alumina "
        "industries, packed in 25 kg bags and 1000 kg jumbo bags, shipped "
        "from Tianjin and Qingdao ports.\n"
    ) * 3
    page = (
        "Chuanghai Technology\n"
        "Business Type\n"
        "Trading Company\n"
        "Plant Area\n"
        "500000ton /Year of caustic soda\n"
    ) + body

    qualification = SupplierQualification(
        result_index=0,
        company_name="Chuanghai Technology",
        title_ru="Поставщик",
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
    payload = _apply_evidence_gates(
        qualification,
        [
            {
                "claim_type": "production_capacity",
                "support_status": "supports",
                "claim_value": "мощность",
                "quote": "500000ton /Year of caustic soda",
            }
        ],
        page_url="https://chuanghaitech.en.made-in-china.com/",
        page_text=page,
        fetch_status="completed",
    )

    assert payload["supplier_type"] == "manufacturer"
    assert payload["role_proof"] == "proven"
    assert any("торговой" in flag for flag in payload["red_flags"])
