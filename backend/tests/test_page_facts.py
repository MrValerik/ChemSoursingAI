"""Факты, читаемые со страницы без модели.

Замер на бетаине: из пяти загруженных страниц номер присутствовал в тексте
четырёх, а модель не подтвердила совпадение вещества ни на одной. Модели
отдавались первые 4000 символов, тогда как на карточках китайских
поставщиков спецификация стоит ниже маркетингового текста — у одной страницы
номер оказался на позиции 4015.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_page_facts.db")

from app.connectors.web_page import extract_page_text
from app.services.page_facts import (
    build_highlights,
    cas_quote,
    find_cas_numbers,
    find_document_mentions,
    find_ec_numbers,
    find_inchikeys,
    find_molecular_formulas,
    find_purity,
    is_valid_ec,
    looks_like_formula,
    page_cas_match,
    spec_lines,
    substance_facts,
)


# --- поиск номера ---


def test_checksum_separates_a_cas_from_a_lookalike():
    """Контрольная сумма отсекает телефоны и даты, похожие на номер."""
    text = "CAS 107-43-7, тел. 0086-571-8627, партия 2024-05-17"
    assert find_cas_numbers(text) == ["107-43-7"]


def test_position_on_the_page_does_not_matter():
    """Именно из-за позиции терялся номер: обрезка до него не доходила."""
    text = "маркетинговый текст\n" * 500 + "CAS Number: 107-43-7"
    assert text.find("107-43-7") > 4000
    assert page_cas_match(text, "107-43-7") is True


def test_a_word_hyphen_does_not_break_the_number():
    """В списке сырья заказчика так записаны два номера из 323.

    Копипаста из Word и PDF даёт неразрывный дефис U+2011. Оба номера
    верны, и отклонять их — значит терять настоящие запросы.
    """
    assert find_cas_numbers("CAS 112‑70‑9") == ["112-70-9"]
    assert page_cas_match("CAS 27458‑92‑0", "27458-92-0") is True


def test_a_fullwidth_hyphen_does_not_break_the_number():
    """Китайские карточки товара пишут номер полноширинным дефисом."""
    assert page_cas_match("CAS：107－43－7", "107-43-7") is True
    quote = cas_quote("产品 CAS：107－43－7 现货", "107-43-7")
    assert quote is not None and "107" in quote


def test_a_different_substance_is_not_a_match():
    assert page_cas_match("CAS 50-78-2 aspirin", "107-43-7") is False


def test_missing_cas_is_not_a_match():
    assert page_cas_match("CAS 107-43-7", None) is False


# --- цитата ---


def test_quote_is_copied_verbatim_from_the_page():
    """Цитата проверяется вхождением, поэтому её нельзя переформатировать."""
    text = "О компании\nProduct Name: Betaine | CAS Number: 107-43-7\nКонтакты"
    quote = cas_quote(text, "107-43-7")
    assert quote is not None
    assert quote in text


def test_long_line_keeps_the_number_inside_the_quote():
    """Обрезка длинной строки не должна выбрасывать сам номер."""
    line = "x" * 900 + " CAS 107-43-7 " + "y" * 900
    quote = cas_quote(line, "107-43-7")
    assert quote is not None
    assert "107-43-7" in quote
    assert quote in line


# --- спецификация ---


def test_specification_lines_are_picked_over_prose():
    text = (
        "Добро пожаловать на сайт нашей компании\n"
        "Assay: 98% min\n"
        "Мы работаем с 2003 года и ценим каждого клиента\n"
        "CAS No.: 107-43-7\n"
    )
    picked = spec_lines(text)
    assert "Assay: 98% min" in picked
    assert "CAS No.: 107-43-7" in picked
    assert all("Добро пожаловать" not in line for line in picked)


def test_highlights_come_from_the_page_itself():
    """Иначе цитата из подсветки не пройдёт проверку вхождением."""
    text = "шапка\n" * 100 + "CAS No.: 107-43-7\nAssay: 98% min\n" + "подвал\n" * 100
    for line in build_highlights(text, cas="107-43-7"):
        assert line in text


# --- прочие признаки вещества ---


def test_european_number_is_checked_by_its_own_checksum():
    """У бетаина EC 203-490-6; соседнее число контрольную сумму не проходит."""
    assert is_valid_ec("203-490-6") is True
    assert is_valid_ec("203-490-5") is False
    assert find_ec_numbers("EINECS: 203-490-6") == ["203-490-6"]


def test_structure_key_is_recognised():
    """InChIKey однозначнее любого названия и почти не даёт ложных срабатываний."""
    text = "InChIKey: KWIUHFFTVRNATP-UHFFFAOYSA-N"
    assert find_inchikeys(text) == ["KWIUHFFTVRNATP-UHFFFAOYSA-N"]


def test_formula_needs_a_word_that_announces_it():
    """Без указателя формулу не отличить от артикула."""
    assert find_molecular_formulas("Molecular formula: C5H11NO2") == ["C5H11NO2"]
    # Артикул из тех же букв и цифр указателем не назван — и не берётся.
    assert find_molecular_formulas("Модель C5H11NO2 в наличии") == []


def test_a_part_number_is_not_a_formula():
    assert looks_like_formula("C5H11NO2") is True
    # Xx — не символ элемента.
    assert looks_like_formula("Xx12") is False
    # Один элемент формулой вещества на карточке не бывает.
    assert looks_like_formula("C") is False


def test_purity_needs_a_word_that_announces_it():
    assert find_purity("Assay: 98.5%") == ["98.5"]
    # Иначе любая скидка на странице станет чистотой.
    assert find_purity("Скидка 20% до конца месяца") == []


def test_documents_are_found_with_a_verbatim_line():
    text = (
        "О компании\n"
        "We are ISO 9001:2015 certified and follow GMP\n"
        "Certificate of Analysis is provided with every batch\n"
    )
    mentions = find_document_mentions(text)
    assert set(mentions) == {"iso", "gmp", "coa"}
    for line in mentions.values():
        assert line in text


def test_isopropyl_is_not_an_iso_certificate():
    """Без требования цифр выражение ловило бы «isopropyl» и «isomer»."""
    assert find_document_mentions("Isopropyl alcohol, isomer mixture") == {}


def test_substance_facts_collect_everything_at_once():
    text = (
        "Molecular formula: C5H11NO2\n"
        "CAS No.: 107-43-7\n"
        "EINECS: 203-490-6\n"
        "Assay: 98%\n"
    )
    facts = substance_facts(text)
    assert facts["cas"] == ["107-43-7"]
    assert facts["ec"] == ["203-490-6"]
    assert facts["formula"] == ["C5H11NO2"]
    assert facts["purity_percent"] == ["98"]


def test_neighbouring_product_properties_do_not_leak_in():
    """На каталожной странице карточки идут вплотную.

    Замер на en.aobobio.cn: рядом с бетаином лежали формулы C4H9NO2 и
    C5H14ClNO — чужие вещества. Границей служит соседний CAS-номер.
    """
    from app.services.page_facts import substance_neighbourhood

    page = (
        "CAS NO.:56-12-2\n"
        "Molecular formula: C4H9NO2\n"
        "CAS NO.:107-43-7\n"
        "Molecular formula: C5H11NO2\n"
        "CAS NO.:590-46-5\n"
        "Molecular formula: C5H14ClNO\n"
    )
    window = substance_neighbourhood(page, "107-43-7")
    assert "C5H11NO2" in window
    assert "C4H9NO2" not in window
    assert "C5H14ClNO" not in window

    joined = "\n".join(build_highlights(page, cas="107-43-7"))
    assert "C5H11NO2" in joined
    assert "C5H14ClNO" not in joined


def test_a_page_about_one_substance_keeps_its_properties():
    """Ограничение соседним номером не должно съедать обычную карточку."""
    from app.services.page_facts import substance_neighbourhood

    page = "Betaine Anhydrous\nCAS No.: 107-43-7\nMolecular formula: C5H11NO2\n"
    assert "C5H11NO2" in substance_neighbourhood(page, "107-43-7")


def test_highlights_cover_documents_and_properties():
    text = (
        "приветственный текст\n" * 200
        + "CAS No.: 107-43-7\n"
        + "Molecular formula: C5H11NO2\n"
        + "We are ISO 9001 certified\n"
    )
    highlights = build_highlights(text, cas="107-43-7")
    joined = "\n".join(highlights)
    assert "107-43-7" in joined
    assert "C5H11NO2" in joined
    assert "ISO 9001" in joined
    # Каждая строка обязана быть дословной, иначе цитату отклонит проверка.
    for line in highlights:
        assert line in text


# --- имена компаний ---


def test_company_names_are_recognised_by_their_legal_tail():
    """Заводы многотоннажной химии находятся по имени, а не по веществу.

    В прогоне по адипиновой кислоте система нашла торговые дома, тогда как
    рынок держат Shenma и Hualu Hengsheng. Их имена стоят в отраслевых
    сообщениях, и по имени корпоративный сайт находится сразу.
    """
    from app.services.page_facts import find_company_names

    text = (
        "China ShenMa Group Co., Ltd announced an expansion.\n"
        "Shandong Hualu Hengsheng Chemical Co., Ltd operates a plant.\n"
        "adipic acid production increased this year\n"
        "山东华鲁恒升化工股份有限公司 扩产\n"
    )
    names = find_company_names(text)

    assert "China ShenMa Group Co., Ltd" in names
    assert any("Hualu Hengsheng" in name for name in names)
    assert any("有限公司" in name for name in names)
    # Обычное словосочетание компанией не становится.
    assert all("adipic acid production" not in name for name in names)


def test_a_chinese_brand_without_a_legal_tail_is_still_a_name():
    """Отраслевой обзор пишет марку без «有限公司».

    В прогоне 62 по адипиновой кислоте 华鲁恒升 стоял в выдаче и не
    извлекался: регулярка требовала юридического хвоста, а рядом стояло
    только слово о мощности. Из четырёх известных производителей нашёлся
    один.
    """
    from app.services.page_facts import find_company_names

    names = find_company_names("华鲁恒升产能达到 32 万吨，神马 年产 47 万吨")

    assert "华鲁恒升" in names
    assert "神马" in names


def test_a_link_fragment_does_not_glue_itself_to_the_name():
    """«...-1999492502.html Tangshan Zhonghao Co., Ltd» — не имя компании."""
    from app.services.page_facts import find_company_names

    text = (
        "Food-Acidity-Regulators-Adipic-Acid-CAS-124-04-9-1999492502.html "
        "Tangshan Zhonghao Chemical Co., Ltd"
    )
    names = find_company_names(text)

    assert names == ["Tangshan Zhonghao Chemical Co., Ltd"]


def test_market_research_names_are_skipped():
    """«Market Report Corp» — не завод, а издатель отчёта."""
    from app.services.page_facts import find_company_names

    names = find_company_names("Global Market Report Corporation published data")
    assert names == []


# --- разбор HTML ---


def test_table_cells_no_longer_glue_together():
    """В замере получалось «Origin : ChinaCAS Number : 107-43-7».

    Два разных поля спецификации выглядели одним значением, потому что
    между ячейками не было разделителя.
    """
    html = (
        "<table><tr><td>Origin : China</td>"
        "<td>CAS Number : 107-43-7</td></tr></table>"
    )
    _, text = extract_page_text(html, "text/html")
    assert "ChinaCAS" not in text
    assert "107-43-7" in text


def test_schema_org_markup_is_kept():
    """Разметка лежит внутри script и терялась вместе со скриптами."""
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Product","name":"Betaine Anhydrous 98%",'
        '"description":"CAS 107-43-7"}'
        "</script></head><body><p>О компании</p></body></html>"
    )
    _, text = extract_page_text(html, "text/html")
    assert "Betaine Anhydrous 98%" in text
    assert "107-43-7" in text
    # Разметка идёт первой: до конца страницы обрезка может не дойти.
    assert text.index("Betaine Anhydrous 98%") < text.index("О компании")


def test_broken_markup_does_not_lose_the_page():
    html = (
        '<html><head><script type="application/ld+json">{не json</script>'
        "</head><body><p>Основной текст</p></body></html>"
    )
    _, text = extract_page_text(html, "text/html")
    assert "Основной текст" in text


def test_ordinary_scripts_are_still_dropped():
    html = "<html><body><script>var x = 1;</script><p>Текст</p></body></html>"
    _, text = extract_page_text(html, "text/html")
    assert "var x" not in text
    assert "Текст" in text
