import asyncio

import pytest

from shopping_ai import Conversation, ConversationTurn, Orchestrator, Principal, ShoppingAI
from shopping_ai.browser import BrowserResult, BrowserTarget, CartLine, CartSnapshot
from shopping_ai.market import Offer, SearchReceipt, Store
from shopping_ai.navigation import NavigationReceipt
from shopping_ai.providers import MockProvider
from shopping_ai.providers.chat import ChatSettings
from shopping_ai.providers.speech import AudioService, SpeechSettings
from shopping_ai.runtime import AssistantRuntime, TextEvent, VoiceOutput, live_runtime
from shopping_ai.voice import AudioClip, Transcript, VoiceConversation


class Speech:
    async def transcribe(self, clip, *, language):
        return Transcript(text="hi", confidence=0.99)

    async def synthesize(self, text, *, language):
        return AudioClip(format="wav", data=b"fake-speech")


def stores():
    return tuple(
        Store(
            store_id=name,
            origin=f"https://{name}.example",
            search_path="/search",
            query_parameter="q",
        )
        for name in ("a", "b")
    )


def test_single_runtime_routes_discovery_navigation_and_verified_cart(now, product, grant):
    async def scenario():
        conversation = Conversation(
            Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)), stores=stores()
        )
        speech = Speech()
        runtime = AssistantRuntime(conversation, VoiceConversation(conversation, speech, speech))
        principal = Principal(owner_id="owner", session_id="session")
        output = await runtime.start(principal, language="en")
        state = output.reply.state

        async def send(event, speak=False):
            nonlocal state
            output = await runtime.handle(
                state, principal, event, expected_version=state.version, speak=speak
            )
            reply = output.reply.reply if isinstance(output, VoiceOutput) else output.reply
            payload = runtime.snapshot(reply.state, principal)
            state = runtime.restore(
                payload,
                principal,
                conversation_id=reply.state.conversation_id,
                expected_version=reply.state.version,
            )
            assert state == reply.state
            return reply

        for index, message in enumerate(
            (
                "search websites",
                "Example",
                "Earbuds",
                "colour=black",
                "new",
                "201001",
                "confirm",
            )
        ):
            reply = await send(
                {"kind": "text", "turn": {"request_id": f"t{index}", "message": message}}
            )
        assert reply.status == "ready_for_search"
        tasks = reply.search_tasks
        for index, task in enumerate(tasks):
            await send(
                {
                    "kind": "market_result",
                    "receipt": SearchReceipt(
                        search_id=state.active_market.search_id,
                        owner_id=principal.owner_id,
                        session_id=principal.session_id,
                        task_id=task.task_id,
                        delivery_context="201001",
                        status="complete",
                        offers=(
                            Offer(
                                offer_id=f"offer{index}",
                                snapshot_id=f"offer-snap{index}",
                                identity=state.active_market.identity,
                                url=task.store.origin + "/product/earbuds",
                                seller="Seller",
                                observed_at=now,
                                in_stock=True,
                                price=str(1499 + index),
                                currency="INR",
                                shipping="0",
                                extra_tax_and_fees="0",
                            ),
                        ),
                    ),
                },
                speak=True,
            )
        source = BrowserTarget(tab_id=7, document_id="source", active=True, window_focused=True)
        pending = await send(
            {
                "kind": "text",
                "turn": ConversationTurn(
                    request_id="open",
                    message="open cheapest",
                    target=source,
                ),
            }
        )
        assert pending.navigation.offer.offer_id == "offer0"
        target = source.model_copy(update={"document_id": "doc_1"})
        await send(
            {
                "kind": "navigation_result",
                "receipt": NavigationReceipt(
                    action_id=pending.navigation.action_id,
                    search_id=state.active_market.search_id,
                    owner_id=principal.owner_id,
                    session_id=principal.session_id,
                    observed_at=now,
                    status="opened",
                    url=pending.navigation.offer.url,
                    target=target,
                    identity=state.active_market.identity,
                    product=product,
                ),
            }
        )
        assert state.selected_id == product.product_id
        baseline = await send(
            {
                "kind": "text",
                "turn": ConversationTurn(
                    request_id=grant.request_id,
                    message="Black wala cart mein daal do",
                    target=target,
                    authorization=grant,
                    available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
                ),
            }
        )
        assert baseline.action.tool.name == "read_cart"
        with pytest.raises(ValueError, match="BROWSER_RESULT_REQUIRED"):
            await send({"kind": "advance"})

        def browser_result(action, quantity):
            return BrowserResult(
                action_id=action.action_id,
                run_id=action.run_id,
                session_id=principal.session_id,
                run_version=action.run_version,
                source_document_id=action.target.document_id,
                target=target,
                observed_at=now,
                outcome="observed",
                cart=CartSnapshot(
                    observed_at=now,
                    document_id=target.document_id,
                    lines=()
                    if quantity == 0
                    else (
                        CartLine(
                            product_id=product.product_id,
                            variant_id="black",
                            quantity=quantity,
                        ),
                    ),
                ),
            )

        observed = await send(
            {"kind": "browser_result", "result": browser_result(baseline.action, 0)}
        )
        assert observed.action is None
        addition = await send({"kind": "advance"})
        assert addition.action.tool.name == "add_to_cart"
        final = await send(
            {"kind": "browser_result", "result": browser_result(addition.action, 1)}, speak=True
        )
        assert final.status == "complete" and final.action is None
        assert "Verified cart" in final.message
        assert state.active_run.status == "succeeded"

    asyncio.run(scenario())


def test_runtime_validates_event_owner_and_version_before_any_work(now):
    async def scenario():
        conversation = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
        runtime = AssistantRuntime(conversation)
        principal = Principal(owner_id="owner", session_id="session")
        state = (await runtime.start(principal)).reply.state
        event = TextEvent(turn=ConversationTurn(request_id="one", message="hi"))
        for who, version, code in (
            (Principal(owner_id="other", session_id="session"), state.version, "ACCESS_DENIED"),
            (principal, state.version + 1, "STALE_CONVERSATION_VERSION"),
        ):
            with pytest.raises(ValueError, match=code):
                await runtime.handle(state, who, event, expected_version=version)
        with pytest.raises(ValueError):
            await runtime.handle(
                state, principal, {"kind": "execute_arbitrary_code"}, expected_version=state.version
            )
        with pytest.raises(ValueError, match="VOICE_NOT_CONFIGURED"):
            await runtime.handle(
                state, principal, event, expected_version=state.version, speak=True
            )
        reply = await runtime.handle(state, principal, event, expected_version=state.version)
        with pytest.raises(ValueError, match="DUPLICATE_TURN"):
            await runtime.handle(
                reply.reply.state, principal, event, expected_version=reply.reply.state.version
            )

    asyncio.run(scenario())


def test_runtime_audio_routes_through_the_same_conversation(now):
    async def scenario():
        conversation = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
        speech = Speech()
        voice = VoiceConversation(conversation, speech, speech)
        runtime = AssistantRuntime(conversation, voice)
        principal = Principal(owner_id="owner", session_id="session")
        started = await runtime.start(principal, speak=True)
        state = started.reply.reply.state
        output = await runtime.handle(
            state,
            principal,
            {
                "kind": "audio",
                "turn": {"request_id": "voice", "audio": AudioClip(format="webm", data=b"input")},
            },
            expected_version=state.version,
        )
        assert output.kind == "voice" and output.reply.audio.data
        assert output.state == output.conversation_reply.state
        assert output.reply.reply.state.recent_request_ids == ("voice",)
        other = Conversation(conversation.orchestrator)
        with pytest.raises(ValueError, match="same Conversation"):
            AssistantRuntime(other, voice)

    asyncio.run(scenario())


def test_live_factory_shares_providers_and_closes_on_failure_without_network():
    async def scenario():
        chat = ChatSettings(endpoint="https://model.example/v1/chat/completions", model="chosen")
        speech = SpeechSettings(
            stt=AudioService(
                endpoint="https://stt.example/v1/audio/transcriptions", model="chosen-stt"
            ),
            tts=AudioService(endpoint="https://tts.example/v1/audio/speech", model="chosen-tts"),
            voice="chosen-voice",
        )
        with pytest.raises(RuntimeError, match="shutdown"):
            async with live_runtime(chat, speech=speech, stores=stores()) as runtime:
                model = runtime.conversation.orchestrator.ai.provider
                audio = runtime.voice.transcriber
                assert model is runtime.conversation.interpreter
                assert model is runtime.conversation.detail_interpreter
                assert audio is runtime.voice.synthesizer
                assert model._client is None and audio._client is None
                raise RuntimeError("shutdown")
        assert model._closed and audio._closed

    asyncio.run(scenario())


def test_generic_factory_wires_independent_adapters_without_provider_configuration(
    now, monkeypatch
):
    from shopping_ai.intents import BasicInterpreter
    from shopping_ai.market_dialogue import OfflineDetailInterpreter
    from shopping_ai.runtime import create_runtime

    for name in ("AI_CHAT_ENDPOINT", "AI_CHAT_MODEL", "AI_STT_ENDPOINT", "AI_TTS_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)

    class STTOnly:
        async def transcribe(self, clip, *, language):
            return Transcript(text="hi", confidence=0.99)

    class TTSOnly:
        async def synthesize(self, text, *, language):
            return AudioClip(format="wav", data=b"fake")

    async def scenario():
        decisions, intent, detail = MockProvider(), BasicInterpreter(), OfflineDetailInterpreter()
        stt, tts = STTOnly(), TTSOnly()
        assistant = create_runtime(
            decisions,
            interpreter=intent,
            detail_interpreter=detail,
            transcriber=stt,
            synthesizer=tts,
            clock=lambda: now,
        )
        assert assistant.conversation.orchestrator.ai.provider is decisions
        assert assistant.conversation.interpreter is intent
        assert assistant.conversation.detail_interpreter is detail
        assert assistant.voice.transcriber is stt and assistant.voice.synthesizer is tts
        principal = Principal(owner_id="owner", session_id="session")
        started = await assistant.start(principal, speak=True)
        output = await assistant.handle(
            started.state,
            principal,
            {
                "kind": "audio",
                "turn": {"request_id": "voice", "audio": AudioClip(format="webm", data=b"input")},
            },
            expected_version=started.state.version,
        )
        assert output.reply.status == "complete" and output.state.principal == principal

    asyncio.run(scenario())


def test_generic_factory_rejects_partial_speech_and_invalid_roles():
    from shopping_ai.intents import BasicInterpreter
    from shopping_ai.market_dialogue import OfflineDetailInterpreter
    from shopping_ai.runtime import create_runtime

    kwargs = dict(interpreter=BasicInterpreter(), detail_interpreter=OfflineDetailInterpreter())
    with pytest.raises(ValueError, match="both transcriber and synthesizer"):
        create_runtime(MockProvider(), **kwargs, transcriber=Speech())
    with pytest.raises(ValueError, match="implement decide"):
        create_runtime(object(), **kwargs)
