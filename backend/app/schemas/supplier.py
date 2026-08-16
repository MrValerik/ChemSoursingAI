"""Схемы поставщиков и получателей рассылки (разделы 9–10 UI/UX-плана)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Channel, DispatchStatus, SupplierType
from app.services.contacts import looks_like_email


class SupplierCreate(BaseModel):
    """Ручное добавление поставщика (раздел 9: «Добавить вручную»)."""

    company: str = Field(..., min_length=1)
    type: SupplierType | None = None
    country: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    source: str | None = "добавлен вручную"
    reputation: str | None = None
    qualification_status: str = Field(
        default="candidate",
        pattern="^(candidate|under_review|verified|rejected)$",
    )
    evidence_score: int | None = Field(default=None, ge=0, le=100)
    certificates: list[str] | None = None


class SupplierContact(BaseModel):
    """Контакт компании: кому и куда писать."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    offered_substances: list[str] | None = None


class SupplierContactCreate(BaseModel):
    """Контакт, который закупщик вписал руками, открыв сайт компании.

    Поиск доводит до компании и останавливается там, где адрес закрыт
    подменой, спрятан за формой или лежит только в личном кабинете
    площадки. Человек эти преграды проходит: открывает страницу глазами и
    переносит адрес сюда.
    """

    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    whatsapp: str | None = Field(default=None, max_length=64)

    @field_validator("full_name", "email", "whatsapp")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def refusal(self) -> str | None:
        """Почему контакт не годится — словами, понятными закупщику.

        Проверка живёт здесь, а не в валидаторе pydantic: тот отвечает
        422 со служебной обёрткой, и в карточке появлялось «Проверьте
        данные: «Данные»: Value error, Адрес почты введён с ошибкой».
        Человек, переносящий адрес с сайта, должен прочитать одну фразу.
        """
        # Имя без адреса — не канал связи: галочку рассылки по такому
        # контакту всё равно не поставить, а в таблице компания так и
        # останется без связи.
        if not self.email and not self.whatsapp:
            return "Нужен адрес почты или номер WhatsApp"
        if self.email and not looks_like_email(self.email):
            return "Адрес почты введён с ошибкой"
        return None


class SupplierRequestLink(BaseModel):
    rfq_id: int
    name: str
    # Номера может не быть: половина списка заказчика — торговые марки и
    # смеси без CAS, и поиск давно умеет работать по названию. Схема же
    # требовала строку, и весь список поставщиков падал с 500, стоило
    # компании оказаться связанной с таким запросом. На стенде это 22
    # связи с запросом «Dowsil 556», то есть вкладка «Отобранные
    # поставщики» не открывалась вовсе.
    cas: str | None = None


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company: str
    country: str | None
    type: SupplierType | None
    reputation: str | None
    source: str | None
    certificates: list[str] | None
    qualification_status: str
    evidence_score: int | None
    last_checked_at: datetime | None

    # Доступные каналы по контактам менеджеров.
    channels: list[Channel] = Field(default_factory=list)
    # Сами адреса. В таблице отбора их не показать — она и так широка, — но
    # в подробной карточке закупщику нужен точный адрес: он решает, писать
    # ли по нему, и должен видеть, куда именно уйдёт письмо.
    contacts: list[SupplierContact] = Field(default_factory=list)
    contacts_count: int = 0
    # Почему связи нет: «obfuscated» — адрес на странице скрыт подменой,
    # «form» — вместо адреса форма обратной связи. Пусто, если контакт есть
    # или страница ничего об этом не сказала.
    contact_barrier: str | None = None
    request_count: int = 0
    linked_requests: list[SupplierRequestLink] = Field(default_factory=list)
    has_coa: bool = False
    has_tds: bool = False


class RecipientAdd(BaseModel):
    supplier_id: int
    channel: Channel


class RecipientsSelect(BaseModel):
    items: list[RecipientAdd]


class RecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfq_id: int
    supplier_id: int
    channel: Channel
    status: DispatchStatus
    note: str | None
    updated_at: datetime

    supplier_company: str | None = None
