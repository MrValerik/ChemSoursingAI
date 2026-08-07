"""Роль производителя привязывается к веществу, а витрина — к компании.

Оба правила выведены из разбора прогона по эпоксидированному соевому маслу.
Перепродавец получил допуск в короткий список с баллом 92, а крупнейший в
мире производитель этого вещества не мог быть найден в принципе.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_role_binding.db")

from app.api.supplier_search import (
    QualificationEvidence,
    _evidence_rejection_reason,
)
from app.models.search_trace import SourceDocument
from app.services.intermediaries import (
    is_intermediary,
    marketplace_page_kind,
    split_by_intermediary,
)
from app.services.page_facts import (
    looks_like_role_keyword_stuffing,
    looks_like_third_party_production_claim,
    mentions_substance,
)

# Дословные строки из прогонов.
TNJ_QUOTE = (
    "TNJ Chemical, China leading Epoxidized Soybean Oil ESBO CAS 8013-07-8 "
    "suppliers, factory & manufacturers."
)
FOODCHEM_QUOTE = "Our Gelatin Factory"
REAL_QUOTE = "Our plant produces 100,000 tons of epoxidized soybean oil per year"


# --- перечисление ролей ---


def test_role_listing_is_not_a_claim():
    """Тот же шаблон стоит на каждой из тысяч товарных страниц каталога."""
    assert looks_like_role_keyword_stuffing(TNJ_QUOTE) is True


def test_a_real_production_claim_survives():
    assert looks_like_role_keyword_stuffing(REAL_QUOTE) is False
    assert looks_like_role_keyword_stuffing("We are a manufacturer of ESBO") is False


def test_a_partner_factory_is_not_the_candidates_factory():
    quote = (
        "Adipic Acid CAS 124-04-9: On-Spot QC in our APPROVED associated "
        "production bases"
    )
    assert looks_like_third_party_production_claim(quote) is True


def test_singular_and_plural_count_once():
    """Иначе «supplier and suppliers» сошло бы за три роли."""
    assert looks_like_role_keyword_stuffing("supplier, suppliers, supplier") is False


# --- заголовок страницы ---


def test_a_page_title_does_not_prove_production():
    """«Manufacturer» в теге title написан для поисковика.

    На прогоне 72 Shandong Aojin получил статус производителя по строке
    «China Adipic Acid Manufacturer and Supplier | AOJIN». На той же
    странице он перечисляет марки, которые перепродаёт: Hualu, Huafeng,
    Shenma. Сама оценка это заметила и написала в красных флагах, но
    статус всё равно поставила.
    """
    from app.services.page_facts import looks_like_page_title

    assert looks_like_page_title("China Adipic Acid Manufacturer and Supplier | AOJIN")
    assert looks_like_page_title(
        "Behenyl dimethylamine, DMA22 factory and manufacturers | Kerui Chemicals"
    )


def test_an_ordinary_sentence_is_not_a_title():
    """Правило узкое: именные фразы без разделителя не трогаем.

    Иначе вместе с перекупщиками отсеются настоящие заводы — у Anhui
    Liwei роль доказывалась именно такой строкой.
    """
    from app.services.page_facts import looks_like_page_title

    assert not looks_like_page_title(
        "Octadecyl-Behenyl Dimethyl Amine Manufacturer in China"
    )
    assert not looks_like_page_title("We produce adipic acid at our own plant")
    assert not looks_like_page_title("年产 20000 吨己二酸生产线")


def test_a_plain_hyphen_separates_a_title_too():
    """Один недостающий символ пропускал половину заголовков.

    В списке разделителей были « – » и « — », но не обычный дефис с
    пробелами — а им отбита шапка «Zinc Ricinoleate Manufacturer &
    Supplier in China - Echo Chemtech Co., Ltd.». Замер по 69 принятым
    цитатам о роли: дефис отсекает пять, все до одной — шапки товарных
    страниц.
    """
    from app.services.page_facts import looks_like_page_title

    assert looks_like_page_title(
        "Zinc Ricinoleate Manufacturer & Supplier in China - Echo Chemtech Co., Ltd."
    )
    assert looks_like_page_title(
        "Top Zinc Ricinoleate Exporter from China - High-Quality Wholesale Solutions"
    )


def test_a_verifiable_detail_survives_the_wider_separator():
    """Строка с выпуском или площадкой утверждает факт, а не рекламирует.

    Оговорка нужна именно из-за обычного дефиса: он часто стоит внутри
    обычного предложения, а не только в теге title.
    """
    from app.services.page_facts import looks_like_page_title

    assert not looks_like_page_title(
        "Our own factory in Shandong - annual capacity is 20,000 tons"
    )


def test_the_same_boilerplate_with_an_article_is_caught():
    """«a Leading Manufacturer» — тот же оборот, что «one of the leading»."""
    from app.services.page_facts import looks_like_leading_supplier_boilerplate

    assert looks_like_leading_supplier_boilerplate(
        "a high quality product from Simson Pharma Limited, a Leading Manufacturer"
    )
    assert looks_like_leading_supplier_boilerplate(
        "Foshan Chancheng Chang Jiang Plastic Additives Co., Ltd. is one of the "
        "most reliable manufacturers and suppliers of epoxidized soybean oil"
    )


def test_an_invitation_to_buy_does_not_prove_a_factory():
    """Единственная опора статуса у Henan GP — призыв купить.

    «Welcome to buy bulk adipic acid from our factory» — ошибка
    классификации, подтверждённая эталоном: эталон знает Henan GP как
    дистрибьютора, а адрес на той же странице — 29-й этаж бизнес-центра.
    Замер по 69 принятым цитатам о роли: 11 срабатываний, все — один
    шаблон в шести написаниях.
    """
    from app.services.page_facts import looks_like_purchase_invitation

    assert looks_like_purchase_invitation(
        "welcome to buy bulk adipic acid from our factory."
    )
    assert looks_like_purchase_invitation(
        "buy bulk epoxidized soybean oil esbo from our factory"
    )


def test_a_real_claim_is_not_an_invitation():
    from app.services.page_facts import looks_like_purchase_invitation

    assert not looks_like_purchase_invitation(
        "Douwin has own adipic acid factory with advanced production facility"
    )
    assert not looks_like_purchase_invitation(
        "welcome to buy from our factory with an annual capacity of 20,000 tons"
    )


def test_a_separator_without_a_role_word_is_not_a_title():
    from app.services.page_facts import looks_like_page_title

    assert not looks_like_page_title("Adipic acid | 99.7% | 25 kg bag")


# --- самохарактеристика ---


def test_leading_supplier_boilerplate_is_not_production():
    """Формула, по которой трейдер прошёл в короткий список.

    Tianjin Gnee: «is one of the leading manufacturers and suppliers of
    99% behenyl dimethyl amine … in China». Предложение связное, вещество
    названо — прежние проверки его пропускали. Адрес компании при этом
    25-й этаж бизнес-центра в Аньяне.
    """
    from app.services.page_facts import looks_like_leading_supplier_boilerplate

    assert looks_like_leading_supplier_boilerplate(
        "Tianjin Gnee Biotech Co., Ltd. is one of the leading manufacturers "
        "and suppliers of 99% behenyl dimethyl amine white color cas "
        "93164-85-3 in China."
    )
    assert looks_like_leading_supplier_boilerplate(
        "We are a professional manufacturer and supplier of adipic acid"
    )


def test_the_same_words_with_a_verifiable_detail_pass():
    """Мощность или своя площадка рядом превращают похвальбу в утверждение."""
    from app.services.page_facts import looks_like_leading_supplier_boilerplate

    assert not looks_like_leading_supplier_boilerplate(
        "One of the leading manufacturers of adipic acid with an annual "
        "capacity of 20,000 tons"
    )
    assert not looks_like_leading_supplier_boilerplate(
        "One of the leading suppliers, we have our own factory in Shandong"
    )


def test_a_plain_production_claim_is_untouched():
    from app.services.page_facts import looks_like_leading_supplier_boilerplate

    assert not looks_like_leading_supplier_boilerplate(
        "Octadecyl-Behenyl Dimethyl Amine Manufacturer in China"
    )
    assert not looks_like_leading_supplier_boilerplate("年产 20000 吨己二酸")


# --- привязка к веществу ---


def test_a_factory_for_another_substance_is_not_evidence():
    """Завод настоящий, вещество другое."""
    assert (
        mentions_substance(
            FOODCHEM_QUOTE, cas="107-43-7", names=["Betaine"]
        )
        is False
    )


def test_the_number_binds_across_languages():
    """На китайской странице названия нет, а номер есть."""
    assert mentions_substance(
        "环氧大豆油 8013-07-8 生产厂家", cas="8013-07-8", names=["Epoxidized soybean oil"]
    )


def test_a_synonym_binds_too():
    assert mentions_substance("We produce ESBO", cas=None, names=["ESBO"])


def test_the_same_name_written_apart_still_binds():
    """Заявка пишет слитно, страница — раздельно. Вещество одно.

    На прогоне 59 по Behenyldimethylamine отклонились все пять цитат о
    роли производителя, включая «Behenyl dimethylamine, CAS No.
    21542-96-1, DMA22 factory and manufacturers». Роль не подтвердилась
    ни у одного кандидата, и короткий список не мог открыться — а без
    номера так выглядят 170 позиций из списка заказчика.
    """
    names = ["Behenyldimethylamine"]
    for quote in (
        "Octadecyl-Behenyl Dimethyl Amine Manufacturer in China",
        "Behenyl dimethylamine， CAS No. 21542-96-1， DMA22 factory",
        "As an accredited Behenyl Dimethyl Amine factory",
    ):
        assert mentions_substance(quote, cas=None, names=names), quote


def test_a_reordered_systematic_name_is_a_known_gap():
    """«N,N-Dimethyl Behenylamine» — то же вещество, но порядок частей иной.

    Сопоставление подстрокой этого не берёт, и честнее это записать, чем
    делать вид, что случай закрыт. Лечится не кодом, а полем «Другие
    названия того же вещества»: закупщик или агент кладёт туда вариант,
    и он становится якорем наравне с основным.
    """
    quote = "N,N-Dimethyl Behenylamine production line"
    assert mentions_substance(quote, cas=None, names=["Behenyldimethylamine"]) is False
    assert mentions_substance(
        quote, cas=None, names=["Behenyldimethylamine", "N,N-Dimethyl Behenylamine"]
    )


def test_dropping_separators_does_not_bind_a_different_substance():
    """Убрали разделители — не значит «совпадает что угодно»."""
    assert (
        mentions_substance(
            "We manufacture Cetearyl alcohol",
            cas=None,
            names=["Behenyldimethylamine"],
        )
        is False
    )


def test_the_number_binds_through_unicode_dashes():
    """Китайская вёрстка пишет номер длинным тире."""
    assert mentions_substance(
        "己二酸 124–04–9 生产厂家", cas="124-04-9", names=["Adipic acid"]
    )


# --- проверка доказательства целиком ---


def _source(text: str) -> SourceDocument:
    source = SourceDocument(
        search_run_id=1,
        agent_run_id=1,
        url="https://www.tnjchem.com/x",
        domain="tnjchem.com",
        status="completed",
        text_content=text,
    )
    source.id = 1
    return source


def _reason(quote: str, claim_type: str = "manufacturer_role") -> str | None:
    return _evidence_rejection_reason(
        QualificationEvidence(
            source_document_id=1,
            claim_type=claim_type,
            claim_value="манufacturer",
            support_status="supports",
            quote=quote,
        ),
        result_index=0,
        source_documents={1: _source(quote)},
        source_indexes={1: 0},
        cas="8013-07-8",
        names=["Epoxidized soybean oil", "ESBO"],
    )


def test_the_trader_boilerplate_is_rejected():
    """Именно эта строка дала перепродавцу 92 балла и допуск."""
    assert _reason(TNJ_QUOTE) is not None


def test_a_genuine_production_claim_is_accepted():
    assert _reason(REAL_QUOTE) is None


def test_a_third_party_production_claim_is_rejected():
    quote = (
        "Epoxidized Soybean Oil ESBO CAS 8013-07-8 is inspected in our "
        "associated production bases"
    )
    assert _reason(quote) is not None


def test_other_claim_types_are_untouched():
    """Правило про роль не должно мешать доказывать страну или документы."""
    assert _reason("Guangzhou, China", claim_type="country") is None


# --- витрина против магазина компании ---


def test_a_company_storefront_is_not_a_marketplace_listing():
    """Крупнейший производитель ESBO собственного сайта не имеет вовсе."""
    assert marketplace_page_kind("https://xjleso.en.made-in-china.com/") == "storefront"
    assert marketplace_page_kind("https://m.made-in-china.com/company-xjleso/") == "storefront"
    assert marketplace_page_kind("https://jieheng.lookchem.com/products/x.html") == "storefront"


def test_a_listing_page_stays_a_listing():
    assert marketplace_page_kind(
        "https://www.made-in-china.com/products-search/hot-china-products/esbo.html"
    ) == "listing"
    assert marketplace_page_kind("https://www.lookchem.cn/cas_8013-07-8.html") == "listing"
    assert marketplace_page_kind("https://china.guidechem.com/cas/2014.html") == "listing"
    assert marketplace_page_kind("https://www.chemball.cn/search/chemical_list") == "listing"
    assert marketplace_page_kind("https://www.linkedin.com/company/x") == "listing"


def test_a_product_card_under_a_shop_path_is_not_a_shop():
    """Метка магазина сама по себе компанию не называет.

    На echemi путь «/supplier/» ведёт и в магазин, и в карточку товара:
    «/supplier/pd2105281021-zinc-diricinoleate.html» — номер позиции и имя
    вещества, никакой компании. То же на made-in-china с
    «/factory/aspirin-drug.html». Оба уходили от отсева как магазины.

    Различие в форме: магазин — каталог, товар — отдельная страница сразу
    за меткой. Замер по 12 сохранённым адресам: 8 магазинов, 4 товарных.
    """
    assert marketplace_page_kind(
        "https://www.echemi.com/supplier/pd2105281021-zinc-diricinoleate.html"
    ) == "listing"
    assert marketplace_page_kind(
        "https://ru.made-in-china.com/factory/aspirin-drug.html"
    ) == "listing"


def test_a_shop_catalogue_and_its_pages_stay_a_storefront():
    """Оговорка не должна съесть настоящие магазины."""
    assert marketplace_page_kind("https://www.chemball.com/factory/qrrybz/") == "storefront"
    assert marketplace_page_kind(
        "https://www.made-in-china.com/showroom/fusil2019/"
    ) == "storefront"
    assert marketplace_page_kind(
        "https://www.chemball.cn/factory/zimbir/product.html"
    ) == "storefront"
    assert marketplace_page_kind(
        "https://m.chemball.com/factory/nqenqi/connect"
    ) == "storefront"


def test_a_chemball_factory_page_is_a_storefront():
    assert marketplace_page_kind(
        "https://www.chemball.cn/factory/qrrybz/product/124-04-9.html"
    ) == "storefront"


def test_language_and_mirror_prefixes_are_not_company_names():
    """«en», «m», «chinese» площадка выдаёт себе, а не продавцу."""
    for url in (
        "https://www.made-in-china.com/x.html",
        "https://m.made-in-china.com/x.html",
        "https://chinese.alibaba.com/x.html",
    ):
        assert marketplace_page_kind(url) == "listing"


def test_a_chinese_mobile_prefix_is_not_a_company_name():
    """Замер по 4-хлорфенолу: торговая страница площадки прошла в кандидаты.

    `wap` — стандартная мобильная версия китайского сайта, а не имя
    продавца, и без него адрес читался как магазин компании.
    """
    assert marketplace_page_kind(
        "https://wap.guidechem.com/trade/4-chlorophenol-id3916146.html"
    ) == "listing"
    for prefix in ("3g", "touch", "mip", "h5"):
        assert marketplace_page_kind(f"https://{prefix}.guidechem.com/x.html") == "listing"


def test_a_storefront_survives_the_split():
    domains = {"made-in-china.com", "lookchem.com"}
    results = [
        {"url": "https://xjleso.en.made-in-china.com/"},
        {"url": "https://www.made-in-china.com/products-search/esbo.html"},
        {"url": "https://www.gz-xjl.com/"},
    ]
    direct, intermediaries = split_by_intermediary(results, domains)

    assert [r["url"] for r in direct] == [
        "https://xjleso.en.made-in-china.com/",
        "https://www.gz-xjl.com/",
    ]
    assert len(intermediaries) == 1
    # Сама принадлежность площадке при этом не отменяется.
    assert is_intermediary("https://xjleso.en.made-in-china.com/", domains)
