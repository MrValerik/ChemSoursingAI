"""Эталон поиска контрагентов и замер по нему.

Настройка шла по отчётам самой системы, и так выжили ворота, которые не
открылись ни разу за 129 кандидатов: отчёт говорил «заблокировано», и это
выглядело как работа фильтра, а не как отказ.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_discovery_eval.db")

import pytest

from app.eval.supplier_discovery import (
    DiscoveryEvalError,
    load_dataset,
    match_player,
    normalise_name,
    score_substance,
)


# --- целостность эталона ---


def test_dataset_loads_and_covers_all_three_categories():
    dataset = load_dataset("v1")
    categories = {s["category"] for s in dataset["substances"]}
    assert categories == {"with_cas", "trade_name", "plain_name"}


def test_every_player_carries_a_kind_and_a_confidence():
    for substance in load_dataset("v1")["substances"]:
        for player in substance["known_players"]:
            assert player["kind"] in {
                "manufacturer",
                "distributor",
                "trader",
                "marketplace",
            }
            assert player["confidence"] in {"verified", "industry_knowledge"}


def test_a_missing_version_is_reported():
    with pytest.raises(DiscoveryEvalError):
        load_dataset("v-нет-такой")


# --- сопоставление ---


def test_legal_tails_do_not_break_the_match():
    assert normalise_name("Shandong Kerui Chemicals Co., Ltd.") == normalise_name(
        "Shandong Kerui Chemicals"
    )


def test_the_domain_wins_over_the_name():
    """На витринах имя пишут как придётся, домен однозначен."""
    players = [
        {"name": "BLIT Chemical", "aliases": [], "domain": "blitchem.com",
         "kind": "manufacturer"},
    ]
    candidate = {
        "company_name": "неразборчивое имя",
        "url": "https://www.blitchem.com/behenyl-dimethylamine-dma22/",
    }
    assert match_player(candidate, players)["name"] == "BLIT Chemical"


def test_an_alias_matches_when_there_is_no_domain():
    players = [
        {"name": "Chongqing Huafeng Chemical", "aliases": ["华峰"],
         "domain": None, "kind": "manufacturer"},
    ]
    candidate = {"company_name": "重庆华峰化工有限公司", "url": "https://example.cn/a"}
    assert match_player(candidate, players) is not None


def test_a_stranger_is_not_matched():
    players = [
        {"name": "BLIT Chemical", "aliases": [], "domain": "blitchem.com",
         "kind": "manufacturer"},
    ]
    candidate = {"company_name": "Sigma-Aldrich", "url": "https://sigmaaldrich.com/x"}
    assert match_player(candidate, players) is None


# --- счёт ---


def test_recall_and_kind_accuracy_are_counted_separately():
    """Найти и правильно назвать — разные свойства, и мерить их надо порознь."""
    substance = {
        "id": "проба",
        "category": "with_cas",
        "known_players": [
            {"name": "Завод", "aliases": [], "domain": "factory.cn",
             "kind": "manufacturer", "confidence": "verified"},
            {"name": "Дистрибьютор", "aliases": [], "domain": "dist.com",
             "kind": "distributor", "confidence": "verified"},
            {"name": "Ненайденный", "aliases": [], "domain": "missing.cn",
             "kind": "manufacturer", "confidence": "verified"},
        ],
    }
    candidates = [
        {"company_name": "Завод", "url": "https://factory.cn/p",
         "supplier_type": "manufacturer"},
        {"company_name": "Дистрибьютор", "url": "https://dist.com/p",
         "supplier_type": "unknown"},
        {"company_name": "Кто-то ещё", "url": "https://other.com/p",
         "supplier_type": "distributor"},
    ]

    report = score_substance(substance, candidates)

    assert len(report.found) == 2
    assert report.missed == ["Ненайденный"]
    assert report.correct_kinds == 1
    assert report.unlabelled == [("Кто-то ещё", "other.com")]


def test_a_candidate_outside_the_set_is_not_an_error():
    """Эталон неполон намеренно: чужак идёт в очередь на разметку."""
    substance = {
        "id": "проба",
        "category": "plain_name",
        "known_players": [
            {"name": "Завод", "aliases": [], "domain": "factory.cn",
             "kind": "manufacturer", "confidence": "verified"},
        ],
    }
    report = score_substance(
        substance,
        [{"company_name": "Новичок", "url": "https://newcomer.cn/p"}],
    )

    assert report.found == []
    assert report.unlabelled == [("Новичок", "newcomer.cn")]
