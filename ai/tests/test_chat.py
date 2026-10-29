import asyncio
import json

import pytest

from shopping_ai import (
    AIConfig,
    Conversation,
    ConversationTurn,
    Orchestrator,
    Principal,
    ShoppingAI,
)
from shopping_ai.market import Store
from shopping_ai.providers.base import ProviderError
from shopping_ai.providers.chat import ChatProvider, ChatSettings

httpx = pytest.importorskip("httpx")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.test:11434/v1/chat/completions",
        "http://127.0.0.1:8080/v1/chat/completions",
        "https://127.0.0.1:11434/v1/chat/completions",
        "http://127.0.0.1:11434/api/chat",
        "http://127.0.0.1:11434/v1/chat/completions?redirect=1",
        "http://user:pass@127.0.0.1:11434/v1/chat/completions",
        "http://127.0.0.1.attacker.test:11434/v1/chat/completions",
    ],
)
def test_local_transport_rejects_non_ollama_targets(endpoint):
    with pytest.raises(ValueError, match="loopback Ollama"):
        ChatSettings(local_only=True, endpoint=endpoint, model="local-model")


def test_local_ollama_transport_never_sends_cloud_key_and_uses_max_tokens():
    endpoint = "http://127.0.0.1:11434/v1/chat/completions"
    with pytest.raises(ValueError, match="cloud credentials"):
        ChatSettings(local_only=True, endpoint=endpoint, model="local", api_key="secret")
    with pytest.raises(ValueError, match="HTTPS"):
        ChatSettings(endpoint=endpoint, model="local")
    calls = []

    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        assert str(request.url) == endpoint
        body = json.loads(request.content)
        assert body["max_tokens"] == 1000
        assert "max_completion_tokens" not in body
        return httpx.Response(200, json=completion({"intent": "greeting"}))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            settings = ChatSettings(
                local_only=True, endpoint=endpoint, model="local", token_parameter="max_tokens"
            )
            async with ChatProvider(settings, client=client) as model:
                assert (await model.interpret("hello", None)).intent == "greeting"

    asyncio.run(scenario())
    assert len(calls) == 1


def settings(**updates):
    return ChatSettings.model_validate(
        dict(
            endpoint="https://model.example/v1/chat/completions",
            model="chosen-model",
            api_key="test-secret",
        )
        | updates
    )


def completion(value, **updates):
    return {
        "choices": [
            dict(
                finish_reason="stop",
                message={"content": json.dumps(value)},
            )
            | updates
        ]
    }


def test_real_transport_contract_wires_natural_dialogue_to_confirmed_search(now):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["authorization"] == "Bearer test-secret"
        assert body["model"] == "chosen-model"
        assert body["response_format"] == {"type": "json_object"}
        data = json.loads(body["messages"][1]["content"])
        assert "owner_id" not in data and "session_id" not in data
        if "previous" in data:
            value = dict(
                brand="Example",
                model="Phone One",
                attributes={"storage": "128GB"},
                condition="new",
                postcode="201001",
            )
        else:
            value = {"intent": "market_search"}
        return httpx.Response(200, json=completion(value))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with ChatProvider(settings(), client=client) as model:
                stores = tuple(
                    Store(
                        store_id=name,
                        origin=f"https://{name}.example",
                        search_path="/search",
                        query_parameter="q",
                    )
                    for name in ("a", "b")
                )
                conversation = Conversation(
                    Orchestrator(ShoppingAI(model, clock=lambda: now)),
                    interpreter=model,
                    detail_interpreter=model,
                    stores=stores,
                )
                principal = Principal(owner_id="user", session_id="session")
                state = conversation.start(principal).state
                reply = await conversation.respond(
                    state,
                    principal,
                    ConversationTurn(
                        request_id="first",
                        message="search websites new Example Phone One 128GB 201001",
                    ),
                    expected_version=state.version,
                )
                assert reply.state.market_draft.missing is None and not reply.search_tasks
                confirmed = await conversation.respond(
                    reply.state,
                    principal,
                    ConversationTurn(
                        request_id="confirm",
                        message="confirm",
                    ),
                    expected_version=reply.state.version,
                )
                assert len(confirmed.search_tasks) == 2 and len(calls) == 2
            assert not client.is_closed  # injected transport belongs to caller

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "MODEL_AUTH_ERROR", False),
        (403, "MODEL_AUTH_ERROR", False),
        (429, "MODEL_RATE_LIMIT", True),
        (500, "MODEL_API_ERROR", True),
        (302, "MODEL_API_ERROR", False),
        (400, "MODEL_API_ERROR", False),
    ],
)
def test_errors_are_redacted_without_retry_or_redirect(status, code, retryable):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="SECRET failure", headers={"Location": "https://evil.example"}
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = ChatProvider(settings(), client=client)
            with pytest.raises(ProviderError) as caught:
                await model.interpret("hi", None)
            assert caught.value.code == code and caught.value.retryable is retryable
            assert "SECRET" not in str(caught.value) and len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        completion({"intent": "made_up"}),
        completion({"intent": "greeting"}, finish_reason="length"),
        completion({}, message={"refusal": "no", "content": "{}"}),
        completion({}, message={"tool_calls": [{"name": "execute"}], "content": "{}"}),
        completion({}, message={"content": "not JSON"}),
        completion({}, message={"content": []}),
    ],
)
def test_malformed_truncated_and_unexpected_outputs_fail_closed(payload):
    async def scenario():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ProviderError):
                await ChatProvider(settings(), client=client).interpret("hello", None)

    asyncio.run(scenario())


def test_response_and_context_limits_are_bounded():
    async def scenario():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 65537))
        async with httpx.AsyncClient(transport=transport) as client:
            model = ChatProvider(settings(), client=client)
            with pytest.raises(ProviderError, match="MODEL_RESPONSE_TOO_LARGE"):
                await model.interpret("hi", None)
            with pytest.raises(ProviderError, match="CHAT_CONTEXT_LIMIT"):
                await model.interpret("x" * 100001, None)
            await model.aclose()
            with pytest.raises(ProviderError, match="CHAT_PROVIDER_CLOSED"):
                await model.interpret("hi", None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://model.example/v1/chat/completions",
        "https://key@model.example/v1/chat/completions",
        "https://model.example/v1/chat/completions?key=secret",
        "https://model.example/other",
    ],
)
def test_endpoint_requires_explicit_trusted_https_configuration(endpoint):
    with pytest.raises(ValueError):
        settings(endpoint=endpoint)
    assert "test-secret" not in repr(settings())
    assert "api_key" not in settings().model_dump()


def test_provider_failure_returns_safe_conversation_reply(now):
    async def scenario():
        transport = httpx.MockTransport(lambda _: httpx.Response(401, text="secret"))
        async with httpx.AsyncClient(transport=transport) as client:
            model = ChatProvider(settings(), client=client)
            conversation = Conversation(Orchestrator(ShoppingAI(model)), interpreter=model)
            principal = Principal(owner_id="owner", session_id="session")
            state = conversation.start(principal).state
            reply = await conversation.respond(
                state,
                principal,
                ConversationTurn(request_id="one", message="hi"),
                expected_version=state.version,
            )
            assert reply.status == "error" and "secret" not in reply.model_dump_json()
            assert reply.action is None and not reply.search_tasks

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fact_id,expected", [("snap_1.price", "answered"), ("invented.price", "blocked")]
)
def test_provider_decision_still_passes_engine_grounding(ai_request, now, fact_id, expected):
    async def scenario():
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json=completion(
                    {
                        "kind": "answer",
                        "fact_ids": [fact_id],
                    }
                ),
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            model = ChatProvider(settings(), AIConfig(), client=client)
            response = await ShoppingAI(model, clock=lambda: now).decide(ai_request)
            assert response.status == expected and response.tool is None

    asyncio.run(scenario())


def test_navigation_role_uses_its_own_structured_prompt_and_no_dom_context():
    from shopping_ai.navigation import BrowserPlanRequest

    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["model"] == "navigation-role-model"
        assert "read-only browser navigation" in body["messages"][0]["content"]
        assert "ModelDecision" not in body["messages"][0]["content"]
        assert set(json.loads(body["messages"][1]["content"])) == {
            "message",
            "current_site",
            "allowed_tab_ids",
        }
        return httpx.Response(
            200,
            json=completion(
                {
                    "status": "ready",
                    "steps": [{"kind": "search", "site": "amazon", "query": "headphones"}],
                }
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with ChatProvider(
                settings(), client=client, navigation_model="navigation-role-model"
            ) as model:
                plan = await model.plan_navigation(
                    BrowserPlanRequest(message="search headphones on amazon")
                )
                assert plan.status == "ready" and plan.steps[0].kind == "search"

    asyncio.run(scenario())
    assert len(calls) == 1
