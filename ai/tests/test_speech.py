import asyncio
import io
import json
import wave

import pytest

from shopping_ai import Conversation, Orchestrator, Principal, ShoppingAI
from shopping_ai.providers import MockProvider
from shopping_ai.providers.base import ProviderError
from shopping_ai.providers.speech import AudioService, SpeechProvider, SpeechSettings
from shopping_ai.voice import AudioClip, VoiceConversation, VoiceTurn

httpx = pytest.importorskip("httpx")


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 80)
    return buffer.getvalue()


def settings():
    return SpeechSettings(
        stt=AudioService(
            endpoint="https://stt.example/v1/audio/transcriptions",
            model="chosen-stt",
            api_key="stt-secret",
        ),
        tts=AudioService(
            endpoint="https://tts.example/v1/audio/speech", model="chosen-tts", api_key="tts-secret"
        ),
        voice="configured-voice",
        output_format="wav",
    )


def test_http_speech_adapters_integrate_with_voice_and_conversation(now):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "stt.example":
            assert request.headers["authorization"] == "Bearer stt-secret"
            body = request.content
            assert b'name="file"; filename="recording.wav"' in body
            assert b"chosen-stt" in body and wav_bytes() in body
            assert b'name="language"' in body and b"\r\nhi\r\n" in body
            return httpx.Response(200, json={"text": "hi"})
        assert request.headers["authorization"] == "Bearer tts-secret"
        body = json.loads(request.content)
        assert body["model"] == "chosen-tts" and body["voice"] == "configured-voice"
        assert body["response_format"] == "wav" and body["input"]
        return httpx.Response(200, content=wav_bytes(), headers={"content-type": "audio/wav"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with SpeechProvider(settings(), client=client) as speech:
                dialogue = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
                voice = VoiceConversation(dialogue, speech, speech)
                principal = Principal(owner_id="owner", session_id="session")
                state = dialogue.start(principal).state
                result = await voice.respond(
                    state,
                    principal,
                    VoiceTurn(
                        request_id="voice1",
                        audio=AudioClip(format="wav", data=wav_bytes()),
                    ),
                    expected_version=state.version,
                )
                assert result.status == "complete"
                assert result.audio.data == wav_bytes() and len(calls) == 2
                assert result.reply.state.recent_request_ids == ("voice1",)
            assert not client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "SPEECH_AUTH_ERROR"),
        (403, "SPEECH_AUTH_ERROR"),
        (429, "SPEECH_RATE_LIMIT"),
        (500, "SPEECH_API_ERROR"),
        (302, "SPEECH_API_ERROR"),
    ],
)
def test_errors_are_redacted_and_not_retried(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="secret", headers={"location": "https://evil.example"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError, match=code) as caught:
                await SpeechProvider(settings(), client=client).transcribe(
                    AudioClip(format="wav", data=wav_bytes()),
                    language="en",
                )
            assert "secret" not in str(caught.value) and len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": 42}, {"text": "x" * 2001}])
def test_invalid_transcripts_are_not_accepted(payload):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=payload),
            )
        ) as client:
            with pytest.raises(ProviderError, match="SPEECH_INVALID_TRANSCRIPT"):
                await SpeechProvider(settings(), client=client).transcribe(
                    AudioClip(format="wav", data=wav_bytes()),
                    language="hi-Latn",
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content,mime,code",
    [
        (b"{error}", "application/json", "SPEECH_UNEXPECTED_CONTENT"),
        (b"not audio", "audio/wav", "SPEECH_INVALID_AUDIO"),
        (b"x" * 5_000_001, "audio/wav", "SPEECH_RESPONSE_TOO_LARGE"),
    ],
)
def test_wrong_audio_and_oversized_outputs_fail_closed(content, mime, code):
    async def scenario():
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=content,
                headers={"content-type": mime},
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ProviderError, match=code):
                await SpeechProvider(settings(), client=client).synthesize("hello", language="en")

    asyncio.run(scenario())


def test_tts_limits_and_closed_provider_make_no_requests():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            speech = SpeechProvider(settings(), client=client)
            for text in ("", "x" * 4097):
                with pytest.raises(ProviderError, match="SPEECH_TEXT_LIMIT"):
                    await speech.synthesize(text, language="en")
            with pytest.raises(ProviderError, match="SPEECH_UNSUPPORTED_LANGUAGE"):
                await speech.synthesize("hello", language="unsupported")
            await speech.aclose()
            with pytest.raises(ProviderError, match="SPEECH_PROVIDER_CLOSED"):
                await speech.synthesize("hello", language="en")
            assert not calls

    asyncio.run(scenario())


def test_speech_configuration_hides_keys_and_rejects_unsafe_endpoint():
    config = settings()
    assert "stt-secret" not in repr(config) and "tts-secret" not in repr(config)
    assert "api_key" not in config.model_dump()["stt"]
    for endpoint in (
        "http://stt.example/audio/transcriptions",
        "https://key@stt.example/audio/transcriptions",
        "https://stt.example/audio/transcriptions?key=secret",
    ):
        with pytest.raises(ValueError):
            AudioService(endpoint=endpoint, model="model")
    with pytest.raises(ValueError):
        SpeechSettings(stt=config.tts, tts=config.stt, voice="voice")


def test_speech_deadline_capacity_and_permit_recovery():
    from shopping_ai import AIConfig

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"text": "hello"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            speech = SpeechProvider(
                settings(),
                AIConfig(
                    max_concurrent_calls=1,
                    capacity_wait_seconds=0.01,
                    timeout_seconds=0.1,
                ),
                client=client,
            )
            clip = AudioClip(format="wav", data=wav_bytes())
            first = asyncio.create_task(speech.transcribe(clip, language="en"))
            await entered.wait()
            with pytest.raises(ProviderError, match="SPEECH_CAPACITY"):
                await speech.transcribe(clip, language="en")
            with pytest.raises(ProviderError, match="SPEECH_CONNECTION_ERROR"):
                await first
            release.set()
            transcript = await speech.transcribe(clip, language="en")
            assert transcript.text == "hello" and transcript.confidence is None

    asyncio.run(scenario())
