"""Provider-neutral voice bridge. No microphone, playback, network or audio storage.

Persist reply.state with CAS before dispatching actions or playing returned audio.
STT/TTS failure never proves a browser task failed and must not replay a turn.
"""

import asyncio
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from shopping_ai.conversation import (
    Conversation,
    ConversationContext,
    ConversationError,
    ConversationReply,
    ConversationState,
    ConversationTurn,
)
from shopping_ai.models import Contract, Identifier
from shopping_ai.orchestrator import Principal

AudioFormat = Literal["wav", "webm", "ogg", "mp3"]


class AudioClip(Contract):
    format: AudioFormat
    # Never include audio bytes in repr/model_dump/logging by default.
    data: bytes = Field(min_length=1, max_length=5_000_000, repr=False, exclude=True)

    def copy_validated(self):
        return AudioClip(format=self.format, data=bytes(self.data))


class Transcript(Contract):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def meaningful(self):
        if not self.text.strip():
            raise ValueError("Empty speech transcript")
        return self


class Transcriber(Protocol):
    async def transcribe(self, clip: AudioClip, *, language: str) -> Transcript: ...


class Synthesizer(Protocol):
    async def synthesize(self, text: str, *, language: str) -> AudioClip: ...


class VoiceTurn(ConversationContext):
    request_id: Identifier
    audio: AudioClip


class VoiceReply(Contract):
    status: Literal["ready", "complete", "needs_input", "error"]
    message: str
    reply: ConversationReply | None = None
    audio: AudioClip | None = None
    failed_stage: Literal["capacity", "transcription", "conversation", "synthesis"] | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.status == "ready":
            if self.reply is None or self.audio is not None or self.failed_stage is not None:
                raise ValueError("Ready voice turn requires a reply awaiting synthesis")
        elif self.status == "complete":
            if self.reply is None or self.audio is None or self.failed_stage is not None:
                raise ValueError("Complete voice turn requires reply and audio")
        elif self.audio is not None or self.failed_stage is None:
            raise ValueError("Unfinished voice turn cannot contain speech audio")
        if self.failed_stage == "synthesis" and self.reply is None:
            raise ValueError("Synthesis failure must preserve the conversation checkpoint")
        if self.failed_stage in {"capacity", "transcription", "conversation"} and self.reply:
            raise ValueError("No conversation checkpoint exists at this failure stage")
        return self


class VoiceConversation:
    def __init__(
        self,
        conversation: Conversation,
        transcriber: Transcriber,
        synthesizer: Synthesizer,
        *,
        min_confidence: float = 0.75,
    ):
        if not 0 <= min_confidence <= 1:
            raise ValueError("Invalid confidence threshold")
        self.conversation = conversation
        self.transcriber = transcriber
        self.synthesizer = synthesizer
        self.min_confidence = min_confidence
        config = conversation.orchestrator.ai.config
        self._capacity = asyncio.Semaphore(config.max_concurrent_calls)
        self._speech_capacity = asyncio.Semaphore(config.max_concurrent_calls)

    async def respond(
        self,
        state: ConversationState,
        principal: Principal,
        turn: VoiceTurn,
        *,
        expected_version: int,
        defer_speech: bool = False,
    ) -> VoiceReply:
        state = ConversationState.model_validate(state.model_dump(warnings=False))
        # Authorize and validate before sending audio to any external adapter.
        if state.principal != principal:
            raise ConversationError("CONVERSATION_ACCESS_DENIED")
        if state.version != expected_version:
            raise ConversationError("STALE_CONVERSATION_VERSION")
        if turn.request_id in state.recent_request_ids:
            raise ConversationError("DUPLICATE_TURN")
        clip = turn.audio.copy_validated()
        context = ConversationContext.model_validate(
            turn.model_dump(exclude={"audio", "request_id"})
        )
        turn = VoiceTurn.model_validate(
            context.model_dump()
            | {
                "request_id": turn.request_id,
                "audio": clip,
            }
        )
        config = self.conversation.orchestrator.ai.config
        try:
            async with asyncio.timeout(config.capacity_wait_seconds):
                await self._capacity.acquire()
        except TimeoutError:
            return VoiceReply(
                status="error",
                message="Voice capacity is busy. Please retry.",
                failed_stage="capacity",
            )
        try:
            try:
                async with asyncio.timeout(config.timeout_seconds):
                    raw = await self.transcriber.transcribe(clip, language=state.language)
                transcript = Transcript.model_validate(raw.model_dump(warnings=False))
            except Exception:
                return VoiceReply(
                    status="error",
                    message="Speech could not be transcribed. Please retry.",
                    failed_stage="transcription",
                )
            if transcript.confidence is not None and transcript.confidence < self.min_confidence:
                return VoiceReply(
                    status="needs_input",
                    message="Speech was unclear. Please repeat or type it.",
                    failed_stage="transcription",
                )
            # Normalize only whole Hindi control utterances, never product data.
            controls = {
                "रुको": "stop",
                "बंद करो": "stop",
                "रद्द करो": "cancel",
                "कन्फर्म": "confirm",
                "हाँ कन्फर्म": "haan confirm",
            }
            text = controls.get(" ".join(transcript.text.strip().split()), transcript.text)
            text_turn = ConversationTurn.model_validate(
                context.model_dump()
                | {
                    "request_id": turn.request_id,
                    "message": text,
                }
            )
            try:
                reply = await self.conversation.respond(
                    state,
                    principal,
                    text_turn,
                    expected_version=expected_version,
                )
            except ConversationError:
                raise
            except Exception:
                return VoiceReply(
                    status="error",
                    message="The voice request could not be processed.",
                    failed_stage="conversation",
                )
            if defer_speech:
                return VoiceReply(status="ready", message=reply.message, reply=reply)
            return await self.speak(reply, principal)
        finally:
            self._capacity.release()

    async def speak(self, reply: ConversationReply, principal: Principal) -> VoiceReply:
        """Speak an owned reply, including browser progress/final results.

        Retry only this method for TTS failure. Never resubmit the original user
        turn to replay actions. Caller must discard audio from obsolete versions.
        """
        reply = ConversationReply.model_validate(reply.model_dump(warnings=False))
        if reply.state.principal != principal:
            raise ConversationError("CONVERSATION_ACCESS_DENIED")
        config = self.conversation.orchestrator.ai.config
        try:
            async with asyncio.timeout(config.capacity_wait_seconds):
                await self._speech_capacity.acquire()
        except TimeoutError:
            return VoiceReply(
                status="error",
                message=reply.message,
                reply=reply,
                failed_stage="synthesis",
            )
        try:
            try:
                async with asyncio.timeout(config.timeout_seconds):
                    clip = await self.synthesizer.synthesize(
                        reply.speech_text,
                        language=reply.state.language,
                    )
                clip = clip.copy_validated()
            except Exception:
                return VoiceReply(
                    status="error",
                    message=reply.message,
                    reply=reply,
                    failed_stage="synthesis",
                )
            return VoiceReply(status="complete", message=reply.message, reply=reply, audio=clip)
        finally:
            self._speech_capacity.release()
