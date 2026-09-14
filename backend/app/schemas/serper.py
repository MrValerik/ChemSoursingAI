from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SerperBalanceRead(BaseModel):
    status: Literal["ok", "not_configured", "unavailable"]
    remaining_credits: int | None = None
    checked_at: datetime | None = None
