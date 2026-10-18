"""One transport-independent integration boundary; no HTTP server or persistence.

Caller authenticates principals, loads owned state and persists returned checkpoints
with CAS before dispatch/playback. This module never executes browser proposals.
"""

from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

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
from shopping_ai.models import Contract
from shopping_ai.navigation import NavigationReceipt
from shopping_ai.orchestrator import Orchestrator, Principal
from shopping_ai.providers.base import DecisionProvider
from shopping_ai.providers.chat import ChatProvider, ChatSettings
from shopping_ai.providers.speech import SpeechProvider, SpeechSettings
from shopping_ai.voice import Synthesizer, Transcriber, VoiceConversation, VoiceReply, VoiceTurn


class TextEvent(Contract):
    kind: Literal["text"] = "text"
    turn: ConversationTurn


class AudioEvent(Contract):
    kind: Literal["audio"] = "audio"
    turn: VoiceTurn


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
    ) -> AssistantOutput:
        # Check ownership before any provider work or dispatch-specific behavior.
        state = ConversationState.model_validate(state.model_dump(warnings=False))
        if state.principal != principal:
            raise ConversationError("CONVERSATION_ACCESS_DENIED")
        if state.version != expected_version:
            raise ConversationError("STALE_CONVERSATION_VERSION")
        event = EVENTS.validate_python(event)
        if isinstance(event, AudioEvent):
            return VoiceOutput(
                reply=await self._require_voice().respond(
                    state,
                    principal,
                    event.turn,
                    expected_version=expected_version,
                )
            )
        if speak:
            self._require_voice()
        kwargs = {"expected_version": expected_version}
        conversation = self.conversation
        if isinstance(event, TextEvent):
            reply = await conversation.respond(state, principal, event.turn, **kwargs)
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
