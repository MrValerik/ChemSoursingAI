import os

# Ранжирование живёт в API-модуле, который при импорте создаёт engine.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_supplier_sources.db")

from app.services.supplier_sources import (
    analog_product_description,
    build_search_queries,
    is_india,
    minimum_query_count,
    source_kind,
    source_priority,
)


def test_plan_no_longer_reserves_places_for_a_marketplace():
    """Два запроса из плана раньше уходили на витрину ECHEMI.

    Карточка на площадке не подтверждает производство, а в режиме «все
    продавцы» площадка находится обычным запросом. Места в плане нужнее для
    поиска самих компаний.
    """
    queries = build_search_queries(
        cas="50-78-2",
        name="Aspirin",
        country="China",
        ai_query='"Aspirin" manufacturer China',
    )

    assert queries, "план не должен опустеть"
    assert not any(query.startswith("site:echemi.com") for query in queries)


def test_a_long_name_is_not_locked_into_an_exact_phrase():
    """Точная фраза длиннее трёх слов не встречается в вебе целиком.

    Замер на «C18-C22 methacrylic acid pentaerythrityl ester»: в кавычках
    ноль результатов во всех странах, без кавычек — восемь. Длинные
    описательные названия бывают как раз у веществ без CAS-номера, то есть
    ровно у тех, ради которых ветка поиска по названию и создавалась.
    """
    long_name = "C18-C22 methacrylic acid pentaerythrityl ester"
    queries = build_search_queries(
        cas=None, name=long_name, country="Китай", ai_query=None
    )

    assert queries
    assert all(f'"{long_name}"' not in query for query in queries)
    assert any(long_name in query for query in queries)


def test_a_short_name_stays_an_exact_phrase():
    """Короткое название в кавычках отсекает шум и находится."""
    queries = build_search_queries(
        cas=None, name="Cocamidopropyl betaine", country="Китай", ai_query=None
    )
    assert any('"Cocamidopropyl betaine"' in query for query in queries)


def test_a_long_name_still_gets_its_own_query_beside_short_synonyms():
    """Иначе длинное название вообще не участвует в поиске."""
    queries = build_search_queries(
        cas=None,
        name="C18-C22 methacrylic acid pentaerythrityl ester",
        country="Китай",
        ai_query=None,
        synonyms=["pentaerythrityl tetramethacrylate"],
    )
    assert any('"pentaerythrityl tetramethacrylate"' in q for q in queries)
    assert any(q.startswith("C18-C22 methacrylic acid") for q in queries)


def test_medium_product_uses_the_product_name_in_every_cas_query():
    """Широкий CAS без названия поднимал декларации состава электроники."""
    queries = build_search_queries(
        cas="7631-86-9",
        name="Colloidal silicon dioxide (fumed silica; Aerosil grade)",
        country="Китай",
        ai_query=None,
    )

    assert queries
    assert all("Colloidal silicon dioxide" in query for query in queries)
    assert any("fumed silica; Aerosil grade" in query for query in queries)


def test_unspecified_grade_comment_is_not_sent_to_the_search_engine():
    queries = build_search_queries(
        cas="9003-11-6",
        name="Poloxamer (grade not specified)",
        country="Китай",
        ai_query=None,
    )

    assert queries
    assert all("grade not specified" not in query for query in queries)
    assert all("Poloxamer" in query for query in queries)


def test_country_is_named_in_the_language_of_the_query():
    """Пользователь выбирает страну по-русски, но «Китай» внутри
    английского запроса ищет хуже, чем «China»."""
    queries = build_search_queries(
        cas="107-43-7", name="Betaine", country="Китай", ai_query=None
    )
    joined = " ".join(queries)
    assert "China" in joined
    assert "Китай" not in joined


def test_russia_asks_in_russian():
    """Раньше рынка России в плане не было вовсе."""
    queries = build_search_queries(
        cas="107-43-7", name="Betaine", country="Россия", ai_query=None
    )
    joined = " ".join(queries)
    assert "производитель" in joined
    assert "site:.ru" in joined


def test_the_plan_asks_who_produces_not_only_who_sells():
    """«Manufacturer» и «factory» — маркетинг, их пишет и перекупщик.

    Слова выпуска пишет только тот, у кого выпуск есть. Замер на адипиновой
    кислоте, где рынок держат Shenma, Hualu Hengsheng и Ляоянский НПЗ:
    покупательский запрос не нашёл ни одного, производственные — троих.
    """
    queries = build_search_queries(
        cas="124-04-9", name="Adipic acid", country="Китай", ai_query=None
    )
    joined = " ".join(queries)

    assert "annual output" in joined or "annual capacity" in joined
    # Язык рынка добавляет компании, которых нет в англоязычной выдаче:
    # тот же приём по-китайски вывел Lubrizol и Tinci по карбомеру.
    assert "产能" in joined


def test_russia_asks_about_output_in_russian():
    queries = build_search_queries(
        cas="124-04-9", name="Adipic acid", country="Россия", ai_query=None
    )
    joined = " ".join(queries)
    assert "тонн в год" in joined or "производственная мощность" in joined


def test_one_query_drops_the_number():
    """Номер сужает выдачу до страниц, где он напечатан.

    Крупный производитель может его не печатать: запрос по одному названию
    находит Hairma, крупнейшего в мире изготовителя эпоксидированного
    соевого масла, а тот же запрос с номером — никого.
    """
    queries = build_search_queries(
        cas="8013-07-8",
        name="Epoxidized soybean oil",
        country="Китай",
        ai_query=None,
    )
    without_cas = [q for q in queries if "8013-07-8" not in q]
    assert without_cas, "хотя бы один запрос должен идти без номера"
    assert any("Epoxidized soybean oil" in q for q in without_cas)


def test_the_query_without_the_number_is_in_the_mandatory_head():
    """План обрезается по бюджету, и хвост до выполнения не доживает.

    У карбомера в заявке стоит 9003-01-4 — полиакриловая кислота, а
    косметический грейд рынок продаёт под 9007-20-9 и марками 940, 980.
    Каждый запрос с номером в точных кавычках отсекал рынок целиком: ни
    одного из семи известных поставщиков за три прогона. Значит заход без
    номера обязан попадать в голову плана, а не стоять после неё.
    """
    queries = build_search_queries(
        cas="9003-01-4",
        name="Carbomer",
        country="Китай",
        ai_query=None,
    )
    first_without_cas = next(
        index for index, query in enumerate(queries) if "9003-01-4" not in query
    )
    assert first_without_cas < 4, queries[:5]


def test_marketplace_no_longer_outranks_a_company_site():
    """Площадка получала восемь баллов и обгоняла сайт завода."""
    assert source_priority("echemi", "Китай") == source_priority("web", "Китай")
    # Отраслевой реестр остаётся приоритетным: он подтверждает производителя.
    assert source_priority("india_registry", "Индия") > source_priority(
        "india_web", "Индия"
    )


def test_india_plan_uses_export_and_regulatory_sources():
    queries = build_search_queries(
        cas="50-78-2",
        name="Aspirin",
        country="Индия",
        ai_query=None,
    )

    assert is_india("Индия")
    required = queries[: minimum_query_count("India")]
    assert any("site:chemexcil.in" in query for query in required)
    assert any("site:cdsco.gov.in" in query for query in required)
    assert any("site:pharmexcil.com" in query for query in required)
    # Сужение по зоне остаётся в плане, но не в обязательной части:
    # замер дал site:.in ноль результатов, тогда как реестры подтверждают
    # изготовителя. Тратить на зону место в начале плана незачем.
    assert any("site:.in" in query for query in queries)


def test_supplier_source_classification():
    assert (
        source_kind("https://www.echemi.com/shop-us123/index.html")
        == "echemi"
    )
    assert source_kind("https://chemexcil.in/members") == "india_registry"
    assert source_kind("https://examplechem.in/product") == "india_web"
    assert source_kind("https://example.com/product") == "web"


def test_distributors_and_reference_sites_are_not_treated_as_manufacturers():
    from app.services.supplier_sources import is_non_manufacturer_domain

    # Дистрибьюторы реактивов и продавцы стандартов: страница либо закрыта
    # защитой, либо всё равно отбраковывается как посредник.
    assert is_non_manufacturer_domain("https://www.sigmaaldrich.com/US/en/product/1")
    assert is_non_manufacturer_domain("https://store.usp.org/product/1044006")
    assert is_non_manufacturer_domain("https://www.thermofisher.com/order/x")
    assert is_non_manufacturer_domain("https://www.sigmaaldrich.cn/CN/en/product/1")
    assert is_non_manufacturer_domain("https://assets.thermofisher.cn/sds/1")
    # Справочники и маркетплейсы.
    assert is_non_manufacturer_domain("https://en.wikipedia.org/wiki/Aspirin")
    assert is_non_manufacturer_domain("https://www.chemicalbook.com/x.htm")
    assert is_non_manufacturer_domain("https://www.alibaba.com/product/x")
    assert is_non_manufacturer_domain("https://www.linkedin.com/company/x")
    assert is_non_manufacturer_domain("https://www.chem960.com/cas/124049/")
    assert is_non_manufacturer_domain("https://www.chemball.cn/search/chemical_list")
    assert is_non_manufacturer_domain("https://hpvchemicals.oecd.org/report.pdf")
    # Настоящие сайты производителей и Echemi остаются доступными.
    assert not is_non_manufacturer_domain("https://www.fengchengroup.com/")
    assert not is_non_manufacturer_domain("https://www.echemi.com/produce/x.html")
    assert not is_non_manufacturer_domain("https://hebei-chem.cn/aspirin")
    # Карточка конкретного завода на площадке остаётся доступной.
    assert not is_non_manufacturer_domain(
        "https://www.chemball.cn/factory/hualu/product/124-04-9.html"
    )


def test_ranking_drops_non_manufacturer_domains_before_fetching():
    from app.api.supplier_search import _rank_results

    results = [
        {"title": "Sigma", "url": "https://www.sigmaaldrich.com/p/1", "snippet": ""},
        {"title": "USP", "url": "https://store.usp.org/product/1044006", "snippet": ""},
        {"title": "Завод", "url": "https://www.fengchengroup.com/", "snippet": ""},
    ]
    ranked = _rank_results(results, "Китай", 10)
    urls = [item["url"] for item in ranked]
    assert urls == ["https://www.fengchengroup.com/"]


def test_industry_catalogs_do_not_displace_the_original_manufacturer():
    from app.api.supplier_search import _rank_results

    results = [
        {
            "title": "Cellactose 80 marketplace listing",
            "url": "https://www.cphi-online.com/product/cellactose-80",
            "snippet": "Manufactured by MEGGLE",
        },
        {
            "title": "Cellactose 80",
            "url": "https://www.meggle-excipients.com/products/cellactose-80",
            "snippet": "Official product information",
        },
    ]

    ranked = _rank_results(results, "Китай", 10)
    assert [item["url"] for item in ranked] == [
        "https://www.meggle-excipients.com/products/cellactose-80"
    ]


def test_ranking_opens_a_company_product_page_before_an_unrelated_pdf():
    """Регрессия по лёгкой адипиновой кислоте из списка заказчика."""
    from app.api.supplier_search import _rank_results

    results = [
        {
            "title": "Phosphate No 1 safety data sheet",
            "url": "https://reference.example.cn/files/124-04-9.pdf",
            "snippet": "CAS 124-04-9",
        },
        {
            "title": "Adipic Acid - Shandong Hualu Hengsheng",
            "url": "https://hualu-hengsheng.com/products/adipic-acid.html",
            "snippet": "Adipic Acid manufactured in China, production capacity 500000 t/y",
        },
    ]

    ranked = _rank_results(results, "Китай", 10)
    assert [item["url"] for item in ranked] == [
        "https://hualu-hengsheng.com/products/adipic-acid.html",
        "https://reference.example.cn/files/124-04-9.pdf",
    ]


def test_material_declaration_does_not_rank_as_a_chemical_manufacturer():
    """Поле Supplier Information в декларации полупроводника — не завод SiO2."""
    from app.api.supplier_search import _rank_results

    results = [
        {
            "title": "Material Composition Declaration Supplier Information",
            "url": "https://semiconductor.example.cn/material/7631-86-9.pdf",
            "snippet": "Manufacturer NXP; silicon dioxide CAS 7631-86-9",
        },
        {
            "title": "Fumed Silica Aerosil 200",
            "url": "https://silica.example.com/products/fumed-silica.html",
            "snippet": "China factory producing fumed silica CAS 7631-86-9",
        },
    ]

    ranked = _rank_results(results, "Китай", 10)
    assert ranked[0]["url"] == "https://silica.example.com/products/fumed-silica.html"


def test_a_search_result_with_a_different_valid_cas_is_not_fetched():
    """Регрессия: по SiO2 вторым результатом был D-пантенол 81-13-0."""
    from app.api.supplier_search import _rank_results

    results = [
        {
            "title": "D-Panthenol 81-13-0",
            "url": "https://unrelated.example.cn/product/d-panthenol.html",
            "snippet": "D-Panthenol CAS 81-13-0 manufacturer",
        },
        {
            "title": "Professional Fumed Silica Manufacturer",
            "url": "https://silica.example.com/",
            "snippet": "Colloidal silicon dioxide CAS 7631-86-9",
        },
        {
            "title": "Fumed silica product range",
            "url": "https://silica-without-cas.example.com/",
            "snippet": "Hydrophilic fumed silica manufacturer",
        },
    ]

    ranked = _rank_results(results, "Китай", 10, cas="7631-86-9")
    urls = [item["url"] for item in ranked]
    assert "https://unrelated.example.cn/product/d-panthenol.html" not in urls
    assert "https://silica.example.com/" in urls
    assert "https://silica-without-cas.example.com/" in urls


def test_a_factory_storefront_survives_the_complete_ranking_path():
    from app.api.supplier_search import _rank_results

    url = "https://www.chemball.cn/factory/hualu/product/124-04-9.html"
    ranked = _rank_results(
        [{"title": "Завод", "url": url, "snippet": "己二酸生产厂家"}],
        "Китай",
        10,
    )
    assert [item["url"] for item in ranked] == [url]


def test_analog_queries_search_for_equivalent_not_only_the_original_brand():
    queries = build_search_queries(
        cas=None,
        name="Silicone Elastomer Blend",
        country="Китай",
        ai_query=None,
        identification_method="analog",
        analog_reference="DOWSIL 9045",
        specification="cyclopentasiloxane dimethicone crosspolymer",
    )

    assert queries
    assert any("DOWSIL 9045" in query for query in queries)
    assert any("equivalent" in query for query in queries)
    assert any("替代品" in query for query in queries)
    assert any("INCI composition TDS" in query for query in queries)
    assert any(
        '"silicone elastomer blend" cyclopentasiloxane' in query.casefold()
        and "DOWSIL" not in query
        for query in queries
    )


def test_full_trade_name_still_yields_a_functional_analog_query():
    full_name = "DOWSIL 5-7113 Silicone Quat Microemulsion"

    assert analog_product_description(full_name, full_name) == (
        "Silicone Quat Microemulsion"
    )
    queries = build_search_queries(
        cas=None,
        name=full_name,
        country="Китай",
        ai_query=None,
        identification_method="analog",
        analog_reference=full_name,
        specification="INCI Silicone Quaternium-16 (and) Undeceth-11",
    )

    assert any(
        "Silicone Quat Microemulsion" in query
        and "Silicone Quaternium-16" in query
        and "DOWSIL" not in query
        for query in queries
    )


def test_ambiguous_sulfate_input_gets_a_separate_salt_query():
    queries = build_search_queries(
        cas=None,
        name="Amikacin (free base or sulfate form not separated)",
        country="Индия",
        ai_query=None,
        identification_method="spec",
    )

    assert any('"Amikacin sulfate"' in query for query in queries)
    assert all("not separated" not in query for query in queries)


def test_specification_mode_searches_by_function_without_the_trade_name():
    queries = build_search_queries(
        cas=None,
        name="Augeo commercial solvent",
        country="Китай",
        ai_query=None,
        identification_method="spec",
        specification="isopropylidene glycerol solvent for air care",
    )

    assert any(
        "isopropylidene glycerol solvent for air care" in query
        and "Augeo" not in query
        for query in queries
    )


def test_unspecified_citrate_cation_does_not_invent_a_salt():
    queries = build_search_queries(
        cas=None,
        name="Citrate / citric acid salt (cation not specified)",
        country="Китай",
        ai_query=None,
        identification_method="spec",
    )

    assert all("sodium citrate" not in query.casefold() for query in queries)
    assert all("potassium citrate" not in query.casefold() for query in queries)


def test_our_own_query_without_the_number_survives_the_anchor_rule():
    """Правило якоря надзирает за моделью, а не за нами.

    Запрос без номера собирался и тут же выбрасывался: якорем при наличии
    CAS был сам номер, а этот запрос идёт без него намеренно. То есть с
    момента появления он не работал ни разу — на карбомере и на
    эпоксидированном соевом масле в том числе.
    """
    from app.api.supplier_search import (
        SubstanceIdentity,
        SupplierSearchRequest,
        _fallback_search_plan,
        _merge_search_plans,
    )

    data = SupplierSearchRequest(name="Carbomer", cas="9003-01-4", country="Китай")
    identity = SubstanceIdentity(
        status="verified", canonical_name="Carbomer", search_names=["Carbomer"]
    )
    fallback = _fallback_search_plan(data, identity)
    merged, _ = _merge_search_plans(data, [], fallback)

    without_number = [item for item in merged if "9003-01-4" not in item.query]
    assert without_number, [item.query for item in merged]
    assert any("Carbomer" in item.query for item in without_number)


def test_a_model_query_without_the_anchor_is_still_rejected():
    """Модель без якоря уводит план в сторону — надзор остаётся."""
    from app.api.supplier_search import (
        SearchPlanItem,
        SubstanceIdentity,
        SupplierSearchRequest,
        _fallback_search_plan,
        _merge_search_plans,
    )

    data = SupplierSearchRequest(name="Carbomer", cas="9003-01-4", country="Китай")
    identity = SubstanceIdentity(
        status="verified", canonical_name="Carbomer", search_names=["Carbomer"]
    )
    stray = SearchPlanItem(
        query="polyacrylate thickener suppliers worldwide",
        language="en",
        purpose="manufacturer",
        source_type="web",
        priority=3,
    )
    merged, rejected = _merge_search_plans(
        data, [stray], _fallback_search_plan(data, identity)
    )

    assert rejected == 1
    assert all(item.query != stray.query for item in merged)


def test_composition_only_query_survives_the_plan_safety_filter():
    from app.api.supplier_search import (
        SubstanceIdentity,
        SupplierSearchRequest,
        _fallback_search_plan,
        _merge_search_plans,
    )

    data = SupplierSearchRequest(
        name="ABIL 45 ME",
        country="Китай",
        identification_method="analog",
        analog_reference="ABIL 45 ME",
        specification=(
            "INCI Silicone Quaternium-22, Polyglyceryl-3 Caprate, "
            "Dipropylene Glycol, Cocamidopropyl Betaine"
        ),
    )
    identity = SubstanceIdentity(
        status="unverified",
        canonical_name=data.name,
        search_names=[data.name],
        substance_type="trade_name",
    )
    fallback = _fallback_search_plan(data, identity)
    merged, rejected_count = _merge_search_plans(data, [], fallback)

    assert rejected_count == 0
    assert any(
        item.query.startswith("Silicone Quaternium-22")
        and "ABIL" not in item.query
        for item in merged
    )


def test_a_name_with_a_range_is_never_an_exact_phrase():
    """Диапазон в названии убивает выдачу, сколько бы слов ни было.

    Замер по запросу #31 «C18-C22 fatty alcohol», четыре пары запросов,
    различие только в кавычках: в кавычках найден один результат на все
    четыре формы, без кавычек — тридцать девять. Единственная находка в
    кавычках оказалась сводным перечнем, а без них пришли «Behenyl
    Alcohol (Docosanol, C22) Supplier», «unsaturated fatty alcohol C18»,
    «STEARYL ALCOHOL (C18)» — тот же товар под другими написаниями.

    Название здесь трёхсловное, то есть под прежний порог по числу слов
    оно проходило и в кавычки попадало.
    """
    name = "C18-C22 fatty alcohol"
    queries = build_search_queries(
        cas=None, name=name, country="Китай", ai_query=None
    )

    assert queries
    assert all(f'"{name}"' not in query for query in queries)
    # Само название из поиска при этом не пропадает.
    assert any(name in query for query in queries)


def test_other_range_spellings_are_caught_too():
    """Продавцы пишут диапазон как попало — правило должно ловить все."""
    for name in ("C16-18 fatty alcohol", "Alcohol 12-14", "С20–С22 alcohol"):
        queries = build_search_queries(
            cas=None, name=name, country="Китай", ai_query=None
        )
        assert all(f'"{name}"' not in query for query in queries), name


def test_a_short_name_without_a_range_keeps_its_quotes():
    """Правило адресное: обычные названия кавычек не теряют."""
    for name in ("Cocamidopropyl betaine", "Adipic acid", "Stearyl alcohol"):
        queries = build_search_queries(
            cas=None, name=name, country="Китай", ai_query=None
        )
        assert any(f'"{name}"' in query for query in queries), name
