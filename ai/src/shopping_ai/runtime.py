"""One transport-independent integration boundary; no HTTP server or persistence.

Caller authenticates principals, loads owned state and persists returned checkpoints
with CAS before dispatch/playback. This module never executes browser proposals.
"""

from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from shopping_ai.browser import BrowserResult, BrowserTarget
from shopping_ai.checkpoint import dump_checkpoint, load_checkpoint
from shopping_ai.config import AIConfig
from shopping_ai.conversation import (
    Conversation,
    ConversationError,
    ConversationReply,
    ConversationState,
    ConversationTurn,
)
from shopping_ai.engine import ShoppingAI
from shopping_ai.intents import Interpreter
from shopping_ai.market import SearchReceipt, Store
from shopping_ai.market_dialogue import DetailInterpreter
from shopping_ai.models import Contract, Identifier
from shopping_ai.navigation import NavigationReceipt
from shopping_ai.orchestrator import Orchestrator, Principal
from shopping_ai.providers.base import DecisionProvider
from shopping_ai.providers.chat import ChatProvider, ChatSettings
from shopping_ai.providers.speech import SpeechProvider, SpeechSettings
from shopping_ai.voice import (
    AudioClip,
    Synthesizer,
    Transcriber,
    VoiceConversation,
    VoiceReply,
    VoiceTurn,
)


class TextEvent(Contract):
    kind: Literal["text"] = "text"
    turn: ConversationTurn


class AudioEvent(Contract):
    kind: Literal["audio"] = "audio"
    turn: VoiceTurn


class CancelEvent(Contract):
    kind: Literal["cancel"] = "cancel"
    request_id: Identifier


class BrowserEvent(Contract):
    kind: Literal["browser_result"] = "browser_result"
    result: BrowserResult


class MarketEvent(Contract):
    kind: Literal["market_result"] = "market_result"
    receipt: SearchReceipt


class NavigationEvent(Contract):
    kind: Literal["navigation_result"] = "navigation_result"
    receipt: NavigationReceipt


class AdvanceEvent(Contract):
    kind: Literal["advance"] = "advance"


class ResumeEvent(Contract):
    kind: Literal["resume"] = "resume"
    target: BrowserTarget


class ExpireEvent(Contract):
    kind: Literal["expire"] = "expire"
    scope: Literal["browser", "market"]


AssistantEvent = Annotated[
    TextEvent
    | AudioEvent
    | CancelEvent
    | BrowserEvent
    | MarketEvent
    | NavigationEvent
    | AdvanceEvent
    | ResumeEvent
    | ExpireEvent,
    Field(discriminator="kind"),
]
EVENTS = TypeAdapter(AssistantEvent)


class TextOutput(Contract):
    kind: Literal["text"] = "text"
    reply: ConversationReply

    @property
    def conversation_reply(self) -> ConversationReply:
        return self.reply

    @property
    def state(self) -> ConversationState:
        return self.reply.state


class VoiceOutput(Contract):
    kind: Literal["voice"] = "voice"
    reply: VoiceReply

    @property
    def conversation_reply(self) -> ConversationReply | None:
        return self.reply.reply

    @property
    def state(self) -> ConversationState | None:
        return self.reply.reply.state if self.reply.reply else None


AssistantOutput = Annotated[TextOutput | VoiceOutput, Field(discriminator="kind")]


class SpeechOutput(Contract):
    """Playback-only result: no state update or dispatchable browser proposals."""

    conversation_id: Identifier
    version: Annotated[int, Field(strict=True, ge=0)]
    status: Literal["complete", "error"]
    audio: AudioClip | None = None

    @model_validator(mode="after")
    def coherent(self):
        if (self.status == "complete") != (self.audio is not None):
            raise ValueError("Complete speech output requires audio")
        return self


class AssistantRuntime:
    def __init__(self, conversation: Conversation, voice: VoiceConversation | None = None):
        if voice is not None and voice.conversation is not conversation:
            raise ValueError("Voice and text must share the same Conversation instance")
        self.conversation = conversation
        self.voice = voice

    @staticmethod
    def snapshot(state: ConversationState, principal: Principal) -> bytes:
        return dump_checkpoint(state, principal)

    @staticmethod
    def restore(
        payload: bytes,
        principal: Principal,
        *,
        conversation_id: str,
        expected_version: int,
    ) -> ConversationState:
        return load_checkpoint(
            payload,
            principal,
            conversation_id=conversation_id,
            expected_version=expected_version,
        )

    def _require_voice(self) -> VoiceConversation:
        if self.voice is None:
            raise ValueError("VOICE_NOT_CONFIGURED")
        return self.voice

    async def render_speech(
        self,
        state: ConversationState,
        principal: Principal,
        reply: ConversationReply,
        *,
        expected_version: int,
    ) -> SpeechOutput:
        """Render a persisted current reply; never rerun STT, intent or actions.

        State/reply must come from trusted caller storage. This method cannot
        establish a database commit or prevent a concurrent newer turn during TTS.
        Compare returned version to current storage again before playback.
        """
        state = ConversationState.model_validate(state.model_dump(warnings=False))
        if state.principal != principal:
            raise ConversationError("CONVERSATION_ACCESS_DENIED")
        if state.version != expected_version:
            raise ConversationError("STALE_CONVERSATION_VERSION")
        reply = ConversationReply.model_validate(reply.model_dump(warnings=False))
        if reply.state != state:
            raise ConversationError("SPEECH_REPLY_STATE_MISMATCH")
        rendered = await self._require_voice().speak(reply, principal)
        return SpeechOutput(
            conversation_id=state.conversation_id,
            version=state.version,
            status="complete" if rendered.audio is not None else "error",
            audio=rendered.audio,
        )

    async def _output(
        self,
        reply: ConversationReply,
        principal: Principal,
        speak: bool,
    ) -> AssistantOutput:
        if speak:
            return VoiceOutput(reply=await self._require_voice().speak(reply, principal))
        return TextOutput(reply=reply)

    async def start(
        self,
        principal: Principal,
        *,
        language: Literal["en", "hi-Latn"] = "hi-Latn",
        currency: str = "INR",
        speak: bool = False,
    ) -> AssistantOutput:
        if speak:
            self._require_voice()
        return await self._output(
            self.conversation.start(principal, language=language, currency=currency),
            principal,
            speak,
        )

    async def handle(
        self,
        state: ConversationState,
        principal: Principal,
        event: AssistantEvent | dict,
        *,
        expected_version: int,
        speak: bool = False,
        defer_speech: bool = False,
    ) -> AssistantOutput:
        # Check ownership before any provider work or dispatch-specific behavior.
        state = ConversationState.model_validate(state.model_dump(warnings=False))
        if state.principal != principal:
            raise ConversationError("CONVERSATION_ACCESS_DENIED")
        if state.version != expected_version:
            raise ConversationError("STALE_CONVERSATION_VERSION")
        event = EVENTS.validate_python(event)
        if defer_speech and (speak or not isinstance(event, AudioEvent)):
            raise ValueError("Deferred speech requires an audio event without speak=True")
        if isinstance(event, AudioEvent):
            return VoiceOutput(
                reply=await self._require_voice().respond(
                    state,
                    principal,
                    event.turn,
                    expected_version=expected_version,
                    defer_speech=defer_speech,
                )
            )
        if speak:
            self._require_voice()
        kwargs = {"expected_version": expected_version}
        conversation = self.conversation
        if isinstance(event, TextEvent):
            reply = await conversation.respond(state, principal, event.turn, **kwargs)
        elif isinstance(event, CancelEvent):
            reply = await conversation.respond(
                state,
                principal,
                ConversationTurn(request_id=event.request_id, message="stop"),
                **kwargs,
            )
        elif isinstance(event, BrowserEvent):
            reply = conversation.accept_result(state, principal, event.result, **kwargs)
        elif isinstance(event, MarketEvent):
            reply = conversation.accept_market_receipt(state, principal, event.receipt, **kwargs)
        elif isinstance(event, NavigationEvent):
            reply = conversation.accept_navigation_receipt(
                state, principal, event.receipt, **kwargs
            )
        elif isinstance(event, AdvanceEvent):
            if state.active_run is not None and state.active_run.pending is not None:
                raise ConversationError("BROWSER_RESULT_REQUIRED")
            reply = await conversation.advance(state, principal, **kwargs)
        elif isinstance(event, ResumeEvent):
            reply = conversation.resume(state, principal, event.target, **kwargs)
        elif event.scope == "market":
            reply = conversation.expire_market_search(state, principal, **kwargs)
        else:
            reply = conversation.expire_pending(state, principal, **kwargs)
        return await self._output(reply, principal, speak)


def create_runtime(
    decisions: DecisionProvider,
    *,
    interpreter: Interpreter,
    detail_interpreter: DetailInterpreter,
    transcriber: Transcriber | None = None,
    synthesizer: Synthesizer | None = None,
    config: AIConfig | None = None,
    stores: tuple[Store, ...] = (),
    clock: Callable[[], datetime] | None = None,
) -> AssistantRuntime:
    """Wire independent adapters; no vendor selection, env access or resource ownership.

    Injected adapters belong to the caller. Reuse and close them using their own
    lifecycle. Nothing requires LLM decisions/intent/details or STT/TTS to use the
    same implementation, model, service or credential.
    """
    roles = ((decisions, "decide"), (interpreter, "interpret"), (detail_interpreter, "extract"))
    if (transcriber is None) != (synthesizer is None):
        raise ValueError("Configure both transcriber and synthesizer, or neither")
    if transcriber is not None:
        roles += ((transcriber, "transcribe"), (synthesizer, "synthesize"))
    for adapter, method in roles:
        if not callable(getattr(adapter, method, None)):
            raise ValueError(f"Adapter must implement {method}")
    conversation = Conversation(
        Orchestrator(ShoppingAI(decisions, config, clock=clock)),
        interpreter=interpreter,
        detail_interpreter=detail_interpreter,
        stores=stores,
    )
    voice = (
        VoiceConversation(conversation, transcriber, synthesizer)
        if transcriber is not None
        else None
    )
    return AssistantRuntime(conversation, voice)


@asynccontextmanager
async def live_runtime(
    chat: ChatSettings,
    *,
    speech: SpeechSettings | None = None,
    config: AIConfig | None = None,
    stores: tuple[Store, ...] = (),
):
    """Explicit opt-in live wiring with shared providers and owned shutdown.

    No .env files are loaded. No network calls occur until handling/speaking.
    Keep one context per worker/event loop, not one per user turn.
    """
    config = config or AIConfig()
    async with AsyncExitStack() as stack:
        model = await stack.enter_async_context(ChatProvider(chat, config))
        audio = None
        if speech is not None:
            audio = await stack.enter_async_context(SpeechProvider(speech, config))
        yield create_runtime(
            model,
            interpreter=model,
            detail_interpreter=model,
            transcriber=audio,
            synthesizer=audio,
            config=config,
            stores=stores,
        )
