import asyncio

import pytest

from shopping_ai import Conversation, Orchestrator, Principal, ShoppingAI
from shopping_ai.conversation import ConversationError
from shopping_ai.providers import MockProvider
from shopping_ai.voice import AudioClip, Transcript, VoiceConversation, VoiceTurn


class STT:
    def __init__(self, text="hi", confidence=0.99):
        self.text, self.confidence, self.calls = text, confidence, 0

    async def transcribe(self, clip, *, language):
        self.calls += 1
        assert clip.data == b"input" and language == "hi-Latn"
        return Transcript(text=self.text, confidence=self.confidence)


class TTS:
    calls = 0

    async def synthesize(self, text, *, language):
        self.calls += 1
        return AudioClip(format="wav", data=b"synthesized-demo-not-real-audio")


@pytest.fixture
def setup(now):
    conversation = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    principal = Principal(owner_id="owner", session_id="session")
    state = conversation.start(principal).state
    stt, tts = STT(), TTS()
    return VoiceConversation(conversation, stt, tts), principal, state, stt, tts


def turn(request_id="voice1"):
    return VoiceTurn(request_id=request_id, audio=AudioClip(format="webm", data=b"input"))


def respond(voice, principal, state, item=None):
    return asyncio.run(
        voice.respond(state, principal, item or turn(), expected_version=state.version)
    )


def test_audio_to_transcript_conversation_and_speech(setup):
    voice, principal, state, stt, tts = setup
    result = respond(voice, principal, state)
    assert result.status == "complete" and result.audio.data
    assert result.reply.state.version == state.version + 1
    assert result.reply.state.recent_request_ids == ("voice1",)
    assert stt.calls == tts.calls == 1
    assert result.message == result.reply.message
    assert "data" not in result.audio.model_dump()
    assert "synthesized" not in repr(result)


def test_owner_version_and_replay_checked_before_stt(setup):
    voice, principal, state, stt, _ = setup
    other = Principal(owner_id="other", session_id="session")
    with pytest.raises(ConversationError, match="ACCESS_DENIED"):
        respond(voice, other, state)
    with pytest.raises(ConversationError, match="STALE_CONVERSATION_VERSION"):
        asyncio.run(voice.respond(state, principal, turn(), expected_version=state.version + 1))
    assert stt.calls == 0
    first = respond(voice, principal, state)
    with pytest.raises(ConversationError, match="DUPLICATE_TURN"):
        respond(voice, principal, first.reply.state)
    assert stt.calls == 1


def test_low_confidence_never_reaches_conversation_or_tts(setup):
    voice, principal, state, stt, tts = setup
    stt.text, stt.confidence = "confirm", 0.2
    result = respond(voice, principal, state)
    assert result.status == "needs_input" and result.reply is None and result.audio is None
    assert tts.calls == 0 and state.recent_request_ids == ()


def test_tts_failure_preserves_checkpoint_and_tts_only_retry(setup):
    voice, principal, state, stt, _ = setup

    class Broken:
        async def synthesize(self, *args, **kwargs):
            raise RuntimeError("SECRET_PROVIDER_KEY")

    voice.synthesizer = Broken()
    failed = respond(voice, principal, state)
    assert failed.status == "error" and failed.failed_stage == "synthesis"
    assert failed.reply.state.recent_request_ids == ("voice1",)
    assert failed.reply.state.version == state.version + 1
    assert "SECRET" not in failed.model_dump_json()
    voice.synthesizer = TTS()
    spoken = asyncio.run(voice.speak(failed.reply, principal))
    assert spoken.status == "complete" and spoken.reply.state == failed.reply.state
    assert stt.calls == 1
    with pytest.raises(ConversationError):
        asyncio.run(voice.speak(failed.reply, Principal(owner_id="other", session_id="other")))


def test_stt_failure_is_redacted_and_does_not_consume_turn(setup):
    voice, principal, state, _, tts = setup

    class Broken:
        async def transcribe(self, *args, **kwargs):
            raise RuntimeError("SECRET_PROVIDER_KEY")

    voice.transcriber = Broken()
    result = respond(voice, principal, state)
    assert result.failed_stage == "transcription" and result.reply is None
    assert "SECRET" not in result.model_dump_json() and tts.calls == 0
    assert not state.recent_request_ids


def test_voice_collects_search_details_through_existing_conversation(setup):
    voice, principal, state, stt, _ = setup
    for index, message in enumerate(
        (
            "search websites",
            "Example",
            "Phone One",
            "storage=128GB, colour=black",
            "new",
            "201001",
            "confirm",
        )
    ):
        stt.text = message
        result = respond(voice, principal, state, turn(f"voice{index}"))
        assert result.status == "complete"
        state = result.reply.state
    assert state.market_draft.missing is None
    assert state.active_market is None  # no trusted store adapters configured
    assert not result.reply.search_tasks


def test_audio_limits_and_forged_input_fail_before_adapter(setup):
    voice, principal, state, stt, _ = setup
    for data in (b"", b"x" * 5_000_001):
        with pytest.raises(ValueError):
            AudioClip(format="wav", data=data)
    forged = turn().model_copy(update={"request_id": "invalid id"})
    with pytest.raises(ValueError):
        respond(voice, principal, state, forged)
    assert stt.calls == 0


def test_cancellation_during_stt_propagates_without_consuming_turn(setup):
    voice, principal, state, _, _ = setup

    async def scenario():
        entered = asyncio.Event()

        class Blocking:
            async def transcribe(self, *args, **kwargs):
                entered.set()
                await asyncio.Event().wait()

        voice.transcriber = Blocking()
        task = asyncio.create_task(
            voice.respond(state, principal, turn(), expected_version=state.version)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        voice.transcriber = STT()
        retried = await voice.respond(state, principal, turn(), expected_version=state.version)
        assert retried.status == "complete"

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["रुको", "बंद करो", "रद्द करो"])
def test_hindi_control_utterances_reach_existing_cancel_flow(setup, text):
    voice, principal, state, stt, _ = setup
    stt.text = text
    result = respond(voice, principal, state)
    assert result.reply.status == "cancelled"
    assert result.reply.action is None
