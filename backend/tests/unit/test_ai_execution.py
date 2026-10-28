from threading import Event

import pytest

from app.ai.contracts import AIProviderError
from app.ai.execution import ProviderExecutor


@pytest.mark.parametrize("failure", ["connection", "timeout", "invalid"])
def test_local_first_degrades_to_deterministic_without_cloud(failure):
    import asyncio
    import json

    import httpx
    from shopping_ai.context import BuiltContext
    from shopping_ai.intents import BasicInterpreter
    from shopping_ai.market_dialogue import MarketDraft, OfflineDetailInterpreter
    from shopping_ai.providers.chat import ChatProvider, ChatSettings
    from shopping_ai.providers.mock import MockProvider

    from app.ai.runtime import LocalFirstProvider

    calls = []

    async def handler(request):
        calls.append(str(request.url))
        if failure == "connection":
            raise httpx.ConnectError("LOCAL_SERVER_UNAVAILABLE")
        if failure == "timeout":
            await asyncio.sleep(1)
        return httpx.Response(200, json={"choices": []})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            local = ChatProvider(
                ChatSettings(
                    local_only=True,
                    model="local",
                    endpoint="http://127.0.0.1:11434/v1/chat/completions",
                ),
                client=client,
            )
            route = LocalFirstProvider(
                local, MockProvider(), BasicInterpreter(), OfflineDetailInterpreter(), timeout=0.01
            )
            assert (await route.interpret("hello", None)).intent == "greeting"
            draft = await route.extract("brand: Example", MarketDraft())
            assert draft.brand == "Example"
            context = BuiltContext(
                payload=json.dumps(
                    {
                        "message": "battery",
                        "product": {"observation_fresh": True},
                        "facts": [
                            {
                                "fact_id": "snapshot.spec:0",
                                "key": "spec:0",
                                "value": "Battery: 5000mAh",
                            }
                        ],
                        "available_tools": [],
                        "authorization": None,
                    }
                ),
                facts=(),
                fresh=True,
            )
            decision = await route.decide(context)
            assert decision.kind == "answer" and decision.fact_ids == ["snapshot.spec:0"]

    asyncio.run(scenario())
    assert len(calls) == 3
    assert all(url.startswith("http://127.0.0.1:11434/") for url in calls)


def test_local_first_success_does_not_invoke_fallback():
    import asyncio

    from shopping_ai.intents import Meaning

    from app.ai.runtime import LocalFirstProvider

    class Local:
        async def interpret(self, message, awaiting):
            return Meaning(intent="greeting")

    class NoFallback:
        def __getattr__(self, name):
            raise AssertionError("Healthy local provider must not send data to fallback")

    route = LocalFirstProvider(Local(), NoFallback(), NoFallback(), NoFallback(), timeout=1)
    assert asyncio.run(route.interpret("hello", None)).intent == "greeting"


def test_configured_local_provider_completes_a_full_grounded_engine_response(monkeypatch):
    import asyncio
    from datetime import datetime, timezone

    from shopping_ai.models import AIRequest, ProductSnapshot
    from shopping_ai.providers.base import ProviderError
    from shopping_ai.providers.chat import ChatProvider

    from app.ai.runtime import configured_runtime
    from app.config import Settings

    async def unavailable(*args):
        raise ProviderError("MODEL_CONNECTION_ERROR")

    monkeypatch.setattr(ChatProvider, "_generate", unavailable)

    async def scenario():
        async with configured_runtime(
            Settings(_env_file=None, AI_RUNTIME_PROVIDER="local")
        ) as runtime:
            product = ProductSnapshot(
                snapshot_id="s",
                product_id="p",
                document_id="d",
                title="Phone",
                observed_at=datetime.now(timezone.utc),
                specs={"Battery": "5000 mAh"},
            )
            reply = await runtime.conversation.orchestrator.ai.decide(
                AIRequest(
                    request_id="local-response",
                    message="battery?",
                    product=product,
                    available_tools=[],
                )
            )
            assert reply.status == "answered" and reply.provider == "local_first"
            assert reply.facts[0].value == "Battery: 5000 mAh"

    asyncio.run(scenario())


def test_cancelling_local_inference_does_not_start_cloud_fallback():
    import asyncio

    from app.ai.runtime import LocalFirstProvider

    class Local:
        async def interpret(self, *args):
            await asyncio.sleep(10)

    class NoCloud:
        def __getattr__(self, name):
            raise AssertionError("Cancellation must not trigger a fallback request")

    async def scenario():
        route = LocalFirstProvider(Local(), NoCloud(), NoCloud(), NoCloud(), timeout=5)
        task = asyncio.create_task(route.interpret("private text", None))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_cloud_fallback_requires_explicit_valid_configuration():
    import asyncio

    from app.ai.runtime import configured_runtime
    from app.config import Settings

    async def scenario():
        config = Settings(_env_file=None, AI_RUNTIME_PROVIDER="local", AI_LOCAL_FALLBACK="cloud")
        async with configured_runtime(config):
            raise AssertionError("Incomplete cloud configuration should fail before any request")

    with pytest.raises(ValueError):
        asyncio.run(scenario())


@pytest.mark.parametrize("fallback", ["deterministic", "cloud"])
def test_configured_local_route_uses_cloud_only_after_explicit_opt_in(monkeypatch, fallback):
    import asyncio

    from shopping_ai.intents import Meaning
    from shopping_ai.providers.base import ProviderError
    from shopping_ai.providers.chat import ChatProvider

    from app.ai.runtime import configured_runtime
    from app.config import Settings

    calls = []

    async def generate(self, *args):
        calls.append("local" if self.settings.local_only else "cloud")
        if self.settings.local_only:
            raise ProviderError("MODEL_CONNECTION_ERROR")
        return Meaning(intent="greeting")

    monkeypatch.setattr(ChatProvider, "_generate", generate)

    async def scenario():
        config = Settings(
            _env_file=None,
            AI_RUNTIME_PROVIDER="local",
            AI_LOCAL_FALLBACK=fallback,
            AI_CHAT_ENDPOINT="https://model.example/v1/chat/completions",
            AI_CHAT_MODEL="chosen-cloud-model",
            AI_CHAT_API_KEY="test-only-key",
        )
        async with configured_runtime(config) as runtime:
            assert (
                await runtime.conversation.interpreter.interpret("hello", None)
            ).intent == "greeting"

    asyncio.run(scenario())
    assert calls == (["local", "cloud"] if fallback == "cloud" else ["local"])


def test_worker_heartbeat_only_advances_on_success(monkeypatch, tmp_path):
    import asyncio
    from contextlib import asynccontextmanager

    from app.ai import worker

    @asynccontextmanager
    async def runtime(settings):
        yield object()

    class Service:
        fail = False

        async def settle_due(self, db, limit):
            if self.fail:
                raise RuntimeError("DB unavailable")
            return 0

    service = Service()
    monkeypatch.setattr(worker, "configured_runtime", runtime)
    monkeypatch.setattr(worker, "RuntimeService", lambda *args: service)
    monkeypatch.setattr(worker.get_settings(), "AI_RUNTIME_ENABLED", True)
    path = tmp_path / "heartbeat"
    asyncio.run(worker.run(once=True, heartbeat_file=path))
    assert path.exists()
    timestamp = path.stat().st_mtime_ns
    service.fail = True
    with pytest.raises(RuntimeError, match="AI_DEADLINE_SETTLEMENT_FAILED"):
        asyncio.run(worker.run(once=True, heartbeat_file=path))
    assert path.stat().st_mtime_ns == timestamp


def test_timeout_returns_while_provider_is_blocked_and_keeps_capacity_reserved():
    started, release, finished = Event(), Event(), Event()
    executor = ProviderExecutor(1)

    def blocked():
        started.set()
        release.wait(3)
        finished.set()
        return "late result"

    try:
        with pytest.raises(TimeoutError):
            executor.call(blocked, timeout=0.05)
        assert started.is_set()
        assert not finished.is_set()
        with pytest.raises(AIProviderError, match="PROVIDER_BUSY"):
            executor.call(lambda: "must not start", timeout=1)
    finally:
        release.set()
        executor.close()
    assert finished.wait(2)


def test_provider_exception_does_not_leak_a_capacity_slot():
    executor = ProviderExecutor(1)
    try:

        def fail():
            raise AIProviderError("SAFE_CODE")

        with pytest.raises(AIProviderError, match="SAFE_CODE"):
            executor.call(fail, timeout=1)
        assert executor.call(lambda: "ok", timeout=1) == "ok"
    finally:
        executor.close()
