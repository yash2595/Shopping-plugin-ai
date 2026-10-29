from datetime import datetime
from app.schemas.common import BaseSchema
from typing import Literal
from datetime import date
from pydantic import field_validator


class RegisterResponse(BaseSchema):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class EraseShoppingData(BaseSchema):
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def require_explicit_true(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class UsageRead(BaseSchema):
    day: date
    resets_at: datetime
    ai_requests: int
    ai_limit: int
    ai_remaining: int
    speech_requests: int
    speech_limit: int
    speech_remaining: int
    processing_provider: Literal["mock", "local", "chat"]
    cloud_fallback_enabled: bool
    measurement: Literal["admitted_requests_not_tokens_or_billed_cost"] = "admitted_requests_not_tokens_or_billed_cost"
