"""Real SDK ai_request construction with a fake transport surface; no billable calls."""

import asyncio
from types import SimpleNamespace

import pytest

from shopping_ai import AIConfig
from shopping_ai.context import build_context
from shopping_ai.providers.base import ProviderError
from shopping_ai.providers.gemini import GeminiProvider


def test_gemini_requires_explicit_model_and_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="AI_MODEL"):
        GeminiProvider(AIConfig(provider="gemini"))
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiProvider(AIConfig(provider="gemini", model="test-model"))


def fake_client(monkeypatch, response=None, error=None):
    genai = pytest.importorskip("google.genai")
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.aio = self
            self.models = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            seen["closed"] = True

        async def generate_content(self, **kwargs):
            seen["ai_request"] = kwargs
            if error:
                raise error
            return SimpleNamespace(text=response)

    monkeypatch.setattr(genai, "Client", FakeClient)
    return seen


def test_real_adapter_builds_sdk_request_and_validates(monkeypatch, ai_request, now):
    seen = fake_client(monkeypatch, response='{"kind":"answer","fact_ids":["snap_1.price"]}')
    config = AIConfig(provider="gemini", model="account-model")
    provider = GeminiProvider(config, api_key="test-not-real")
    decision = asyncio.run(provider.decide(build_context(ai_request, config, now)))
    assert decision.fact_ids == ["snap_1.price"]
    assert seen["ai_request"]["model"] == "account-model"
    assert seen["ai_request"]["config"].response_mime_type == "application/json"
    assert seen["ai_request"]["config"].response_json_schema["properties"]["kind"]
    assert seen["client"]["http_options"].retry_options.attempts == 1
    assert seen["closed"]


def test_gemini_quota_error_is_redacted(monkeypatch, ai_request, now):
    errors = pytest.importorskip("google.genai.errors")
    fake_client(
        monkeypatch, error=errors.ClientError(429, {"error": {"message": "private detail"}})
    )
    config = AIConfig(provider="gemini", model="account-model")
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            GeminiProvider(config, api_key="test-not-real").decide(
                build_context(ai_request, config, now)
            )
        )
    assert error.value.code == "MODEL_RATE_LIMIT"
    assert error.value.retryable
    assert "private detail" not in str(error.value)


def test_gemini_network_error_is_retryable(monkeypatch, ai_request, now):
    httpx = pytest.importorskip("httpx")
    fake_client(monkeypatch, error=httpx.ConnectError("private connection detail"))
    config = AIConfig(provider="gemini", model="account-model")
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            GeminiProvider(config, api_key="test-not-real").decide(
                build_context(ai_request, config, now)
            )
        )
    assert error.value.code == "MODEL_CONNECTION_ERROR"
    assert error.value.retryable
