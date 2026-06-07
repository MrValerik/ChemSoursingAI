"""Тесты rule-экстрактора на синтетическом корпусе (golden-примеры).

Корпус: data/synthetic/supplier_replies.jsonl — реалистичные ответы поставщиков
с эталонными значениями ключевых полей. На таком наборе и замеряется точность
извлечения (раздел 5 ТЗ: golden-датасет).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extraction.rule_extractor import extract_with_rules

CORPUS = (
    Path(__file__).resolve().parents[2] / "data" / "synthetic" / "supplier_replies.jsonl"
)

# Поля, по которым сверяем точность.
KEY_FIELDS = ("price", "currency", "incoterm", "moq", "has_coa", "has_tds")


def _load_corpus():
    cases = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def test_corpus_exists():
    assert CORPUS.exists(), f"corpus not found: {CORPUS}"
    assert len(_load_corpus()) >= 5


@pytest.mark.parametrize("case", _load_corpus(), ids=lambda c: c["id"])
def test_extract_key_fields(case):
    got = extract_with_rules(case["text"])
    exp = case["expected"]
    for field_name in KEY_FIELDS:
        assert getattr(got, field_name) == exp[field_name], (
            f"{case['id']} field {field_name}: got {getattr(got, field_name)!r}, "
            f"expected {exp[field_name]!r}"
        )


def test_overall_field_accuracy():
    """Сводная точность по всем полям корпуса — прокси целевой метрики ТЗ."""
    cases = _load_corpus()
    total = hits = 0
    for case in cases:
        got = extract_with_rules(case["text"])
        for field_name in KEY_FIELDS:
            total += 1
            if getattr(got, field_name) == case["expected"][field_name]:
                hits += 1
    accuracy = hits / total
    # Rule-baseline должен уверенно проходить порог; LLM поднимет выше.
    assert accuracy >= 0.9, f"accuracy {accuracy:.2%} below 0.90"
