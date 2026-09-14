"""Личные настройки, доступные независимо от роли."""

from pydantic import BaseModel, ConfigDict, StrictBool


class UserPreferences(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    auto_dispatch_after_search: StrictBool
