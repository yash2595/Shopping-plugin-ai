"""Configuration is explicit: no network, environment loading or clients on import."""

import os
from typing import Literal

from pydantic import Field

from shopping_ai.models import Contract


class AIConfig(Contract):
    provider: Literal["mock", "gemini"] = "mock"
    model: str | None = Field(default=None, min_length=1, max_length=100)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_context_chars: int = Field(default=24000, ge=1000, le=100000)
    max_snapshot_age_seconds: int = Field(default=120, ge=1, le=3600)
    max_steps: int = Field(default=8, ge=1, le=30)
    max_output_tokens: int = Field(default=1000, ge=100, le=4000)

    @classmethod
    def from_env(cls) -> "AIConfig":
        return cls(
            provider=os.environ.get("AI_PROVIDER", "mock"),
            model=os.environ.get("AI_MODEL") or None,
        )
