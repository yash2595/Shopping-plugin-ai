"""Explicit, validated settings. Imports never load files or open connections."""

import os
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from shopping_ai.models import Contract


class AIConfig(Contract):
    provider: Literal["mock", "gemini"] = "mock"
    model: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    timeout_seconds: float = Field(default=30, gt=0, le=120, strict=True, allow_inf_nan=False)
    max_context_chars: int = Field(default=24000, ge=1000, le=100000, strict=True)
    max_snapshot_age_seconds: int = Field(default=120, ge=1, le=3600, strict=True)
    search_timeout_seconds: int = Field(default=120, ge=1, le=600, strict=True)
    max_steps: int = Field(default=8, ge=1, le=30, strict=True)
    max_output_tokens: int = Field(default=1000, ge=100, le=4000, strict=True)

    max_concurrent_calls: int = Field(default=8, ge=1, le=256, strict=True)
    capacity_wait_seconds: float = Field(default=2, gt=0, le=30, strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def provider_requirements(self) -> Self:
        if self.provider == "gemini" and self.model is None:
            raise ValueError("Set AI_MODEL for the Gemini provider")
        return self

    @classmethod
    def from_env(cls) -> "AIConfig":
        values = {
            "provider": os.environ.get("AI_PROVIDER", "mock"),
            "model": os.environ.get("AI_MODEL"),
        }
        for field in (
            "timeout_seconds",
            "max_context_chars",
            "max_snapshot_age_seconds",
            "max_steps",
            "search_timeout_seconds",
            "max_output_tokens",
            "max_concurrent_calls",
            "capacity_wait_seconds",
        ):
            env_name = f"AI_{field.upper()}"
            if env_name in os.environ:
                try:
                    values[field] = (
                        float if field in {"timeout_seconds", "capacity_wait_seconds"} else int
                    )(os.environ[env_name])
                except ValueError:
                    raise ValueError(f"Invalid numeric setting: {env_name}") from None
        return cls.model_validate(values)
