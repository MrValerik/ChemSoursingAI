"""Encrypted shared Echemi sender profile, independent of SMTP transport."""
from sqlalchemy.orm import Session
from app.schemas.echemi_sender import EchemiSenderRead, EchemiSenderUpdate
from app.services.integration_settings import effective_email_settings, get_saved_setting, save_setting

CHANNEL = "echemi_sender"


def read_sender(db: Session) -> EchemiSenderRead:
    row, saved = get_saved_setting(db, CHANNEL)
    if row is None:
        email = effective_email_settings(db)[0]
        values = EchemiSenderUpdate(email=email.email_from, company_name=email.email_from_name)
    else:
        values = EchemiSenderUpdate.model_validate(saved)
    return EchemiSenderRead(
        **values.model_dump(), configured=all(values.model_dump().values()),
        source="database" if row is not None else "email_settings",
        updated_at=row.updated_at if row is not None else None,
    )


def update_sender(db: Session, payload: EchemiSenderUpdate, actor_id: int) -> EchemiSenderRead:
    save_setting(db, channel=CHANNEL, enabled=False, payload=payload.model_dump(), actor_id=actor_id)
    return read_sender(db)
