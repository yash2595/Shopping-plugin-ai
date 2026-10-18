"""Optional HTTPS adapters for compatible transcription and speech endpoints.

No defaults select a vendor/model. The caller owns recording, media decoding,
playback and persistence. Requests are never retried or redirected automatically.
"""

import asyncio
import json
import os
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator

from shopping_ai.config import AIConfig
from shopping_ai.models import Contract, ShortText
from shopping_ai.providers.base import ProviderError
from shopping_ai.voice import AudioClip, Transcript


class AudioService(Contract):
    endpoint: Annotated[str, Field(min_length=1, max_length=2000)]
    model: ShortText
    api_key: SecretStr | None = Field(default=None, exclude=True)

    @field_validator("endpoint")
    @classmethod
    def trusted_endpoint(cls, value):
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Configure a trusted HTTPS audio endpoint")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value is not None and (
            not value.get_secret_value().strip()
            or any(char in value.get_secret_value() for char in "\r\n")
        ):
            raise ValueError("Invalid audio API key")
        return value


class SpeechSettings(Contract):
    stt: AudioService
    tts: AudioService
    voice: ShortText
    output_format: Literal["mp3", "wav"] = "mp3"

    @model_validator(mode="after")
    def service_paths(self):
        if not urlsplit(self.stt.endpoint).path.endswith("/audio/transcriptions"):
            raise ValueError("STT endpoint must end in /audio/transcriptions")
        if not urlsplit(self.tts.endpoint).path.endswith("/audio/speech"):
            raise ValueError("TTS endpoint must end in /audio/speech")
        return self

    @classmethod
    def from_env(cls):
        return cls(
            stt=AudioService(
                endpoint=os.environ.get("AI_STT_ENDPOINT", ""),
                model=os.environ.get("AI_STT_MODEL", ""),
                api_key=os.environ.get("AI_STT_API_KEY") or None,
            ),
            tts=AudioService(
                endpoint=os.environ.get("AI_TTS_ENDPOINT", ""),
                model=os.environ.get("AI_TTS_MODEL", ""),
                api_key=os.environ.get("AI_TTS_API_KEY") or None,
            ),
            voice=os.environ.get("AI_TTS_VOICE", ""),
            output_format=os.environ.get("AI_TTS_FORMAT", "mp3"),
        )


def _audio_header(data: bytes, format: str) -> bool:
    # Only a small format sanity check; not a codec/duration/security validator.
    if format == "wav":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 255 and data[1] & 224 == 224)


class SpeechProvider:
    def __init__(self, settings: SpeechSettings, config: AIConfig | None = None, *, client=None):
        self.settings = settings
        self.config = config or AIConfig()
        self._client = client
        self._owns_client = client is None
        self._closed = False
        self._capacity = asyncio.Semaphore(self.config.max_concurrent_calls)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _request(self, service: AudioService, *, limit: int, expected: str, **body) -> bytes:
        try:
            import httpx
        except ImportError:
            raise ProviderError("SPEECH_NOT_INSTALLED") from None
        if self._closed:
            raise ProviderError("SPEECH_PROVIDER_CLOSED")
        try:
            async with asyncio.timeout(self.config.capacity_wait_seconds):
                await self._capacity.acquire()
        except TimeoutError:
            raise ProviderError("SPEECH_CAPACITY", retryable=True) from None
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    limits=httpx.Limits(max_connections=self.config.max_concurrent_calls),
                )
            headers = {}
            if service.api_key:
                headers["Authorization"] = "Bearer " + service.api_key.get_secret_value()
            async with asyncio.timeout(self.config.timeout_seconds):
                async with self._client.stream(
                    "POST",
                    service.endpoint,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                    follow_redirects=False,
                    **body,
                ) as response:
                    status = response.status_code
                    if status != 200:
                        code = (
                            "SPEECH_RATE_LIMIT"
                            if status == 429
                            else "SPEECH_AUTH_ERROR"
                            if status in {401, 403}
                            else "SPEECH_API_ERROR"
                        )
                        raise ProviderError(code, retryable=status == 429 or status >= 500)
                    mime = response.headers.get("content-type", "").split(";")[0].lower()
                    accepted = (
                        {"application/json"}
                        if expected == "json"
                        else {"audio/mpeg", "application/octet-stream"}
                        if expected == "mp3"
                        else {"audio/wav", "audio/x-wav", "application/octet-stream"}
                    )
                    if mime not in accepted:
                        raise ProviderError("SPEECH_UNEXPECTED_CONTENT")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise ProviderError("SPEECH_RESPONSE_TOO_LARGE")
                        chunks.append(chunk)
            return b"".join(chunks)
        except ProviderError:
            raise
        except (TimeoutError, OSError, httpx.HTTPError):
            raise ProviderError("SPEECH_CONNECTION_ERROR", retryable=True) from None
        finally:
            self._capacity.release()

    async def transcribe(self, clip: AudioClip, *, language: str) -> Transcript:
        clip = clip.copy_validated()
        language_code = {"en": "en", "hi-Latn": "hi"}.get(language)
        if language_code is None:
            raise ProviderError("SPEECH_UNSUPPORTED_LANGUAGE")
        mime = {"wav": "audio/wav", "webm": "audio/webm", "ogg": "audio/ogg", "mp3": "audio/mpeg"}
        raw = await self._request(
            self.settings.stt,
            limit=65536,
            expected="json",
            data={
                "model": self.settings.stt.model,
                "response_format": "json",
                "language": language_code,
            },
            files={"file": (f"recording.{clip.format}", clip.data, mime[clip.format])},
        )
        try:
            payload = json.loads(raw)
            # The plain transcription endpoint does not supply a calibrated
            # utterance confidence. Do not manufacture one from text/logprobs.
            return Transcript(text=payload["text"], confidence=None)
        except (ValueError, TypeError, KeyError):
            raise ProviderError("SPEECH_INVALID_TRANSCRIPT") from None

    async def synthesize(self, text: str, *, language: str) -> AudioClip:
        if language not in {"en", "hi-Latn"}:
            raise ProviderError("SPEECH_UNSUPPORTED_LANGUAGE")
        if not text.strip() or len(text) > 4096:
            raise ProviderError("SPEECH_TEXT_LIMIT")
        raw = await self._request(
            self.settings.tts,
            limit=5_000_000,
            expected=self.settings.output_format,
            json={
                "model": self.settings.tts.model,
                "input": text,
                "voice": self.settings.voice,
                "response_format": self.settings.output_format,
            },
        )
        if not _audio_header(raw, self.settings.output_format):
            raise ProviderError("SPEECH_INVALID_AUDIO")
        return AudioClip(format=self.settings.output_format, data=raw)
