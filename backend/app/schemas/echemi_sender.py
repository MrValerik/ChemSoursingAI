"""Contact data for Echemi forms; no credentials or delivery switches."""
import re
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EchemiSenderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(default="", max_length=80)
    company_name: str = Field(default="", max_length=120)
    contact_name: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=2)

    @field_validator("email", "company_name", "contact_name", "phone", "country", mode="before")
    @classmethod
    def clean(cls, value):
        if not isinstance(value, str):
            raise ValueError("Ожидается текст")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Управляющие символы недопустимы")
        return value.strip()

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        if value and not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", value):
            raise ValueError("Укажите email без имени и угловых скобок")
        return value

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value):
        if not value:
            return value
        if re.search(r"[^0-9+() -]", value):
            raise ValueError("Телефон должен содержать международный код и цифры")
        value = re.sub(r"[() -]", "", value)
        if not value.startswith("+"):
            value = "+" + value
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
            raise ValueError("Укажите международный телефон: + и от 8 до 15 цифр")
        return value

    @field_validator("country")
    @classmethod
    def country_format(cls, value):
        value = value.upper()
        if value and not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("Укажите двухбуквенный код страны, например RU")
        return value


class EchemiSenderRead(EchemiSenderUpdate):
    configured: bool
    source: str
    updated_at: datetime | None = None
