"""Encrypted shared Echemi sender profile, independent of SMTP transport."""
from sqlalchemy.orm import Session
from app.schemas.echemi_sender import EchemiSenderRead, EchemiSenderUpdate
from app.services.integration_settings import effective_email_settings, get_saved_setting, save_setting

CHANNEL = "echemi_sender"


def read_sender(db: Session, actor_id: int | None = None) -> EchemiSenderRead:
    row, saved = get_saved_setting(db, CHANNEL if actor_id is None else f"{CHANNEL}_{actor_id}")
    email = effective_email_settings(db)[0]
    if row is None:
        if actor_id is None:
            values = EchemiSenderUpdate(email=email.email_from, company_name=email.email_from_name)
        else:
            values = EchemiSenderUpdate(email=email.email_from)
    else:
        # Reply addresses always follow the ordinary mail integration, including
        # existing profiles saved before the field became read-only.
        values = EchemiSenderUpdate.model_validate({**saved, "email": email.email_from})
    return EchemiSenderRead(
        **values.model_dump(), configured=all(getattr(values, key) for key in
            ("email", "company_name", "contact_name", "phone", "country")),
        source="database" if row is not None else ("email_settings" if actor_id is None else "personal"),
        updated_at=row.updated_at if row is not None else None,
    )


def update_sender(db: Session, payload: EchemiSenderUpdate, actor_id: int, *, personal=False) -> EchemiSenderRead:
    save_setting(db, channel=f"{CHANNEL}_{actor_id}" if personal else CHANNEL,
                 enabled=False, payload=payload.model_dump(exclude={"email"}), actor_id=actor_id)
    return read_sender(db, actor_id if personal else None)
