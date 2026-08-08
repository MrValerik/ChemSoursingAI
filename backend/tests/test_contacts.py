"""Способы связи со страницы — то, ради чего затевается поиск.

Роль контрагента по странице чаще всего недоказуема: замер по 136
сохранённым карточкам дал один номер государственной лицензии и шесть
упоминаний выпуска или площадки. Зато почта, телефон или мессенджер
нашлись у 92 из тех же 136, а ссылка на раздел «контакты» — у 125.

Точный ответ «завод вы или посредник» приходит перепиской, и ТЗ называет
Echemi, почту и WhatsApp каналами рассылки запросов. Дело поиска —
довести до компании и дать, куда написать.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_contacts.db")

from app.connectors.web_page import find_contact_links
from app.services.contacts import find_contacts, has_contacts


# --- чтение контактов ---


def test_email_and_phone_are_read():
    text = (
        "Contact us\n"
        "E-mail: Jessica@gpcchem.com\n"
        "Tel: +86-371-89916809\n"
    )
    contacts = find_contacts(text)
    assert contacts["emails"] == ["Jessica@gpcchem.com"]
    assert contacts["phones"] == ["+86-371-89916809"]


def test_a_chinese_mobile_is_read():
    assert "phones" in find_contacts("联系电话 13131671519")


def test_messengers_are_read():
    text = "WhatsApp: +8615365418683\nWeChat: sunhere2020\nSkype: live.chemsales"
    contacts = find_contacts(text)
    assert contacts["whatsapp"] == ["+8615365418683"]
    assert contacts["wechat"] == ["sunhere2020"]
    assert contacts["skype"] == ["live.chemsales"]


def test_tracker_and_template_mail_is_dropped():
    """Почта систем аналитики стоит на тысячах сайтов и связи не даёт."""
    text = "abc123@sentry.wixpress.com support@example.com info@yourdomain.com"
    assert find_contacts(text) == {}


def test_a_neighbouring_label_is_not_a_value():
    """В блоке контактов подписи идут вплотную.

    У Zhejiang Jiaao в Skype попадало «E-mail»: жадная регулярка приняла
    следующую подпись за значение.
    """
    contacts = find_contacts("Skype: E-mail: inquiry@jiaaohuanbao.com")
    assert "skype" not in contacts
    assert contacts["emails"] == ["inquiry@jiaaohuanbao.com"]


def test_duplicates_collapse_and_the_list_is_capped():
    text = "\n".join(["sales@x.cn"] * 3 + [f"a{i}@x.cn" for i in range(9)])
    assert len(find_contacts(text)["emails"]) == 5


def test_a_page_without_contacts_yields_nothing():
    assert find_contacts("Adipic acid 99.7%, 25 kg bag") == {}
    assert has_contacts({}) is False
    assert has_contacts({"skype": ["x"]}) is False
    assert has_contacts({"emails": ["a@b.cn"]}) is True


# --- ссылка на раздел «контакты» ---


def test_a_contact_link_is_found_by_href():
    html = '<a href="/contact-us.html">Reach out</a><a href="/products">Goods</a>'
    links = find_contact_links(html, "https://example.cn/product/x.html")
    assert links == ("https://example.cn/contact-us.html",)


def test_a_contact_link_is_found_by_its_label():
    html = '<a href="/lxwm/">联系我们</a>'
    assert find_contact_links(html, "https://example.cn/") == (
        "https://example.cn/lxwm/",
    )


def test_mail_and_phone_links_are_not_pages():
    html = '<a href="mailto:a@b.cn">Contact</a><a href="tel:+8610">Contact</a>'
    assert find_contact_links(html, "https://example.cn/") == ()


def test_a_product_section_is_not_a_contact_page():
    html = '<a href="/product/contact-lens">Contact lens</a>'
    assert find_contact_links(html, "https://example.cn/") == ()


def test_broken_markup_does_not_raise():
    assert find_contact_links("<a href=", "https://example.cn/") == ()
