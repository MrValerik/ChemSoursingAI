"""Сборка безопасного контекста промпта для операций над RFQ."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt import PromptTemplate, RfqAiSetting


def get_active_prompt_text(db: Session, kind: str) -> str | None:
    """Возвращает первый активный системный промпт указанного назначения."""
    prompt = db.scalar(
        select(PromptTemplate)
        .where(PromptTemplate.kind == kind, PromptTemplate.is_active.is_(True))
        .order_by(PromptTemplate.id)
        .limit(1)
    )
    return prompt.system_prompt if prompt else None


def get_rfq_prompt_context(
    db: Session, rfq_id: int, kind: str = "extraction"
) -> tuple[str | None, str | None]:
    setting = db.get(RfqAiSetting, rfq_id)
    instructions = setting.additional_instructions if setting else None
    prompt = None
    if setting and setting.prompt_template_id:
        candidate = db.get(PromptTemplate, setting.prompt_template_id)
        if candidate and candidate.is_active and candidate.kind == kind:
            prompt = candidate
    if prompt is None:
        return get_active_prompt_text(db, kind), (
            instructions.strip() if instructions and instructions.strip() else None
        )
    return (
        prompt.system_prompt if prompt else None,
        instructions.strip() if instructions and instructions.strip() else None,
    )
