"""Optional real Gemini adapter using constrained structured decisions.

No native function execution is enabled. All proposals pass application validation.
"""

import os

from shopping_ai.config import AIConfig
from shopping_ai.context import SYSTEM_PROMPT, BuiltContext
from shopping_ai.models import ModelDecision
from shopping_ai.providers.base import ProviderError


class GeminiProvider:
    name = "gemini"

    def __init__(self, config: AIConfig, api_key: str | None = None):
        if not config.model:
            raise ValueError("Set AI_MODEL to a model available to your Gemini project")
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Set GEMINI_API_KEY in the server environment")
        self.config = config

    async def decide(self, context: BuiltContext) -> ModelDecision:
        try:
            import httpx
            from google import genai
            from google.genai import errors, types
        except ImportError:
            raise ProviderError("GEMINI_NOT_INSTALLED") from None
        try:
            async with genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    timeout=int(self.config.timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            ).aio as client:
                response = await client.models.generate_content(
                    model=self.config.model,
                    contents=context.payload,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_json_schema=ModelDecision.model_json_schema(),
                        max_output_tokens=self.config.max_output_tokens,
                    ),
                )
                if not response.text:
                    raise ProviderError("EMPTY_MODEL_RESPONSE")
                return ModelDecision.model_validate_json(response.text)
        except errors.APIError as exc:
            code = int(exc.code or 0)
            if code == 429:
                raise ProviderError("MODEL_RATE_LIMIT", retryable=True) from None
            if code in {401, 403}:
                raise ProviderError("MODEL_AUTH_ERROR") from None
            raise ProviderError("MODEL_API_ERROR", retryable=code >= 500) from None
        except (OSError, TimeoutError, httpx.HTTPError):
            raise ProviderError("MODEL_CONNECTION_ERROR", retryable=True) from None
