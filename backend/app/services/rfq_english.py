"""Подготовка проверенного английского текста для внешнего RFQ."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from app.models.rfq import RFQ
from app.services.communication_language import (
    english_text_uses_latin_script,
    text_has_forbidden_external_script,
)
from app.services.rfq_builder import RFQInput, build_rfq
from app.services.text_translation import LLMTranslationConnector, TranslationError


_TRANSLATABLE_FIELDS = (
    "analog_reference",
    "specification",
    "purity",
    "application",
    "volume",
)


class RFQEnglishPreparationError(ValueError):
    """Внешний RFQ нельзя безопасно подготовить на английском."""


def _source_hash(rfq: RFQ, *, external_name: str) -> str:
    payload = {
        "name": external_name,
        "cas": rfq.cas,
        "identification_method": rfq.identification_method,
        "analog_variations": list(rfq.analog_variations or []),
        "incoterms": list(rfq.incoterms or []),
        "target_price": str(rfq.target_price) if rfq.target_price is not None else None,
        "currency": rfq.currency,
        **{field: getattr(rfq, field) for field in _TRANSLATABLE_FIELDS},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _needs_translation(value: str | None) -> bool:
    return text_has_forbidden_external_script(value)


def _cached_text_is_current(rfq: RFQ, source_hash: str) -> bool:
    return bool(
        rfq.rfq_generated_source_hash == source_hash
        and rfq.rfq_generated_subject_en
        and rfq.rfq_generated_body_en
        and english_text_uses_latin_script(
            f"{rfq.rfq_generated_subject_en}\n{rfq.rfq_generated_body_en}"
        )
    )


def _input_from_rfq(rfq: RFQ, *, external_name: str) -> RFQInput:
    return RFQInput(
        cas=rfq.cas,
        name=external_name,
        identification_method=rfq.identification_method,
        analog_reference=rfq.analog_reference,
        analog_variations=list(rfq.analog_variations or []),
        specification=rfq.specification,
        incoterms=list(rfq.incoterms or []),
        purity=rfq.purity,
        application=rfq.application,
        volume=rfq.volume,
        target_price=float(rfq.target_price) if rfq.target_price else None,
        currency=rfq.currency or "USD",
    )


def prepare_rfq_english(
    rfq: RFQ,
    *,
    external_name: str,
    translator: LLMTranslationConnector | None = None,
) -> bool:
    """Переводит только внешние динамические поля и кеширует готовое письмо.

    Исходные поля RFQ не меняются. Возвращает ``True``, когда кеш был обновлён.
    Ручной черновик здесь не исправляется: его должен поправить пользователь.
    """
    if rfq.identification_method == "analog":
        return False
    if rfq.rfq_subject_override or rfq.rfq_body_override:
        if not (
            rfq.rfq_subject_override
            and rfq.rfq_body_override
            and english_text_uses_latin_script(
                f"{rfq.rfq_subject_override}\n{rfq.rfq_body_override}"
            )
        ):
            raise RFQEnglishPreparationError(
                "Ручной RFQ должен быть полностью на английском языке. "
                "Исправьте или сбросьте черновик."
            )
        return False
    if _needs_translation(external_name):
        raise RFQEnglishPreparationError(
            "Для RFQ без подтверждённого международного названия выберите "
            "английское название вещества."
        )
    if any(_needs_translation(code) for code in rfq.incoterms or []):
        raise RFQEnglishPreparationError(
            "Пользовательский базис поставки для внешнего RFQ укажите латиницей."
        )

    source_hash = _source_hash(rfq, external_name=external_name)
    if _cached_text_is_current(rfq, source_hash):
        return False

    data = _input_from_rfq(rfq, external_name=external_name)
    translation_service = translator
    updates: dict[str, Any] = {}
    try:
        for field in _TRANSLATABLE_FIELDS:
            value = getattr(data, field)
            if _needs_translation(value):
                if translation_service is None:
                    translation_service = LLMTranslationConnector()
                updates[field] = translation_service.translate(
                    value or "",
                    source_language="auto",
                    target_language="en",
                )
    except TranslationError as exc:
        raise RFQEnglishPreparationError(str(exc)) from exc

    translated = replace(data, **updates)
    result = build_rfq(translated, strict=False)
    if not english_text_uses_latin_script(f"{result['subject']}\n{result['body']}"):
        raise RFQEnglishPreparationError(
            "Не удалось сформировать RFQ полностью на английском языке."
        )
    rfq.rfq_generated_subject_en = result["subject"]
    rfq.rfq_generated_body_en = result["body"]
    rfq.rfq_generated_source_hash = source_hash
    return True


def cached_rfq_english(rfq: RFQ, *, external_name: str) -> tuple[str, str] | None:
    """Возвращает только актуальный и прошедший проверку английский кеш."""
    source_hash = _source_hash(rfq, external_name=external_name)
    if not _cached_text_is_current(rfq, source_hash):
        return None
    return rfq.rfq_generated_subject_en or "", rfq.rfq_generated_body_en or ""


def rfq_english_is_ready(rfq: RFQ, *, external_name: str) -> bool:
    """Проверяет готовность без обращения к модели и без изменения записи."""
    if rfq.identification_method == "analog":
        return True
    if rfq.rfq_subject_override or rfq.rfq_body_override:
        return bool(
            rfq.rfq_subject_override
            and rfq.rfq_body_override
            and english_text_uses_latin_script(
                f"{rfq.rfq_subject_override}\n{rfq.rfq_body_override}"
            )
        )
    if _needs_translation(external_name):
        return False
    if any(_needs_translation(code) for code in rfq.incoterms or []):
        return False
    if not any(
        _needs_translation(getattr(rfq, field)) for field in _TRANSLATABLE_FIELDS
    ):
        return True
    return cached_rfq_english(rfq, external_name=external_name) is not None


def safe_rfq_input(rfq: RFQ, *, external_name: str) -> RFQInput:
    """Не допускает исходную кириллицу даже в неподготовленный предпросмотр."""
    data = _input_from_rfq(rfq, external_name=external_name)
    updates = {
        field: "English translation required"
        for field in _TRANSLATABLE_FIELDS
        if _needs_translation(getattr(data, field))
    }
    if _needs_translation(data.name):
        updates["name"] = "Requested substance" if data.cas else "Requested product"
    if any(_needs_translation(code) for code in data.incoterms):
        updates["incoterms"] = [
            "CUSTOM" if _needs_translation(code) else code
            for code in data.incoterms
        ]
    return replace(data, **updates)
