"""Пустой ответ хуже площадки.

Расширение реестра посредников на тринадцать доменов увело в отсев по
21-30 ссылок на прогон. Карбомер остался с мусором вроде sohu.com и
cnblogs.com, а Dowsil 556 — вовсе без кандидатов, хотя до этого давал
пятерых. Отсев бережёт бюджет загрузки, но не ценой нулевого результата.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_intermediary_fallback.db")

from app.services.intermediaries import split_by_intermediary

DOMAINS = {"made-in-china.com", "app17.com", "kitairu.net"}


def _result(url: str) -> dict:
    return {"url": url, "title": url, "snippet": ""}


def test_platforms_are_still_set_aside_when_there_is_enough_direct():
    direct, platforms = split_by_intermediary(
        [
            _result("https://plant-one.cn/adipic"),
            _result("https://plant-two.cn/adipic"),
            _result("https://www.app17.com/supply/1.html"),
        ],
        DOMAINS,
    )

    assert [item["url"] for item in direct] == [
        "https://plant-one.cn/adipic",
        "https://plant-two.cn/adipic",
    ]
    assert len(platforms) == 1


def test_a_company_shop_inside_a_platform_is_not_set_aside():
    """Магазин называет предприятие — он остаётся прямым источником."""
    direct, platforms = split_by_intermediary(
        [_result("https://megawidechem.en.made-in-china.com/product/x.html")],
        DOMAINS,
    )

    assert len(direct) == 1
    assert platforms == []


def test_everything_filtered_leaves_nothing_to_rank():
    """Свойство, из-за которого Dowsil остался без кандидатов."""
    direct, platforms = split_by_intermediary(
        [
            _result("https://www.app17.com/supply/1.html"),
            _result("https://kitairu.net/category/2.html"),
        ],
        DOMAINS,
    )

    assert direct == []
    assert len(platforms) == 2
