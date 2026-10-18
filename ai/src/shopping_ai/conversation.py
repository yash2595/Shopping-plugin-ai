"""Provider-neutral dialogue checkpoints; no session registry, API or voice client.

Persist returned state with owner checks and compare-and-swap before delivering
its action. The default interpreter is a deliberately limited offline fallback.
"""

import asyncio
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from shopping_ai.browser import BrowserAction, BrowserResult, BrowserTarget
from shopping_ai.intents import BasicInterpreter, Interpreter, Meaning, Requirements, SlotName
from shopping_ai.models import (
    AIRequest,
    Contract,
    Fact,
    Identifier,
    Message,
    MutationAuthorization,
    ProductSnapshot,
)
from shopping_ai.orchestrator import Orchestrator, Principal, RunState


class ConversationTurn(Contract):
    request_id: Identifier
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    products: tuple[ProductSnapshot, ...] | None = Field(default=None, max_length=10)
    target: BrowserTarget | None = None
    available_tools: tuple[
        Literal["read_product", "read_cart", "select_variant", "add_to_cart"], ...
    ] = ("read_product",)
    authorization: MutationAuthorization | None = None

    @model_validator(mode="after")
    def distinct_products_and_tools(self) -> Self:
        if self.products and len({p.product_id for p in self.products}) != len(self.products):
            raise ValueError("Product references must be distinct")
        if len(self.available_tools) != len(set(self.available_tools)):
            raise ValueError("Tools must be distinct")
        if not self.message.strip():
            raise ValueError("Empty user turn")
        return self


class ConversationState(Contract):
    conversation_id: Identifier
    principal: Principal
    version: Annotated[int, Field(strict=True, ge=0)] = 0
    language: Literal["en", "hi-Latn"] = "hi-Latn"
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "INR"
    requirements: Requirements = Field(default_factory=Requirements)
    slot_sources: dict[str, Identifier] = Field(default_factory=dict, max_length=5)
    awaiting: SlotName | None = None
    products: tuple[ProductSnapshot, ...] = Field(default_factory=tuple, max_length=10)
    selected_id: Identifier | None = None
    previous_id: Identifier | None = None
    history: tuple[Message, ...] = Field(default_factory=tuple, max_length=12)
    recent_request_ids: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=50)
    active_run: RunState | None = None

    @model_validator(mode="after")
    def coherent_context(self) -> Self:
        ids = [p.product_id for p in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError("Products must have stable distinct references")
        if any(ref is not None and ref not in ids for ref in (self.selected_id, self.previous_id)):
            raise ValueError("Selected and previous products must exist in context")
        if self.active_run and self.active_run.principal != self.principal:
            raise ValueError("Active run belongs to another principal")
        return self


class ConversationReply(Contract):
    state: ConversationState
    status: Literal[
        "chat",
        "needs_input",
        "ready_for_search",
        "awaiting_browser",
        "working",
        "complete",
        "paused",
        "cancelled",
        "error",
    ]
    message: Annotated[str, Field(min_length=1, max_length=8000)]
    speech_text: Annotated[str, Field(min_length=1, max_length=8000)]
    action: BrowserAction | None = None
    facts: tuple[Fact, ...] = Field(default_factory=tuple, max_length=10)

    @model_validator(mode="after")
    def action_binding(self) -> Self:
        if self.facts:
            snapshots = {p.snapshot_id for p in self.state.products}
            if self.status not in {"chat", "complete"} or any(
                f.snapshot_id not in snapshots for f in self.facts
            ):
                raise ValueError("Answer facts must belong to conversation product evidence")
        if (self.status == "awaiting_browser") != (self.action is not None):
            raise ValueError("Awaiting-browser reply requires exactly one pending action")
        if self.action is not None:
            run = self.state.active_run
            if self.status != "awaiting_browser" or run is None or run.pending != self.action:
                raise ValueError("Reply action must be the active run's pending action")
        return self


class ConversationError(ValueError):
    pass


class Conversation:
    def __init__(self, orchestrator: Orchestrator, interpreter: Interpreter | None = None):
        self.orchestrator = orchestrator
        self.interpreter = interpreter or BasicInterpreter()

    @staticmethod
    def _check(state: ConversationState, principal: Principal, version: int) -> ConversationState:
        state = ConversationState.model_validate(state.model_dump(warnings=False))
        if state.principal != principal:
            raise ConversationError("CONVERSATION_ACCESS_DENIED")
        if state.version != version:
            raise ConversationError("STALE_CONVERSATION_VERSION")
        return state

    @staticmethod
    def _text(state: ConversationState, en: str, hi: str) -> str:
        return en if state.language == "en" else hi

    @staticmethod
    def _reply(
        state: ConversationState,
        status: str,
        text: str,
        *,
        action: BrowserAction | None = None,
        facts: tuple[Fact, ...] = (),
        **updates,
    ) -> ConversationReply:
        history = (*state.history, Message(role="assistant", text=text[:2000]))[-12:]
        next_state = ConversationState.model_validate(
            state.model_dump() | updates | {"version": state.version + 1, "history": history}
        )
        return ConversationReply(
            state=next_state,
            status=status,
            message=text,
            speech_text=text,
            action=action,
            facts=facts,
        )

    def start(
        self,
        principal: Principal,
        *,
        language: Literal["en", "hi-Latn"] = "hi-Latn",
        currency: str = "INR",
    ) -> ConversationReply:
        state = ConversationState(
            principal=principal, conversation_id=uuid4().hex, language=language, currency=currency
        )
        return self._reply(
            state,
            "chat",
            self._text(
                state,
                "Hello! What would you like to shop for today?",
                "Namaste! Aaj kya shopping karni hai?",
            ),
        )

    async def respond(
        self,
        state: ConversationState,
        principal: Principal,
        turn: ConversationTurn,
        *,
        expected_version: int,
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        turn = ConversationTurn.model_validate(turn.model_dump(warnings=False))
        if turn.request_id in state.recent_request_ids:
            raise ConversationError("DUPLICATE_TURN")
        state = ConversationState.model_validate(
            state.model_dump()
            | {
                "recent_request_ids": (*state.recent_request_ids, turn.request_id)[-50:],
                "history": (*state.history, Message(role="user", text=turn.message))[-12:],
            }
        )
        if turn.message.strip().casefold() in {"stop", "cancel", "ruko", "band karo"}:
            run = state.active_run
            if run and run.status not in {"succeeded", "failed", "cancelled"}:
                run = self.orchestrator.cancel(run, principal, expected_version=run.version)
            return self._reply(
                state,
                "cancelled",
                self._text(
                    state,
                    "Stopped. Any in-flight action still needs reconciliation.",
                    "Rok diya. Agar action pehle chal chuka hai toh uska result check karna hoga.",
                ),
                active_run=run,
            )
        busy = state.active_run is not None and (
            state.active_run.pending is not None
            or state.active_run.recovery_required
            or state.active_run.status in {"ready", "paused"}
        )
        try:
            async with asyncio.timeout(self.orchestrator.ai.config.timeout_seconds):
                raw = await self.interpreter.interpret(turn.message, state.awaiting)
            meaning = Meaning.model_validate(raw.model_dump(warnings=False))
        except TimeoutError:
            return self._reply(
                state,
                "error",
                self._text(
                    state,
                    "Understanding the request timed out. Please retry.",
                    "Request samajhne mein timeout hua. Dobara try karo.",
                ),
            )
        if meaning.intent in {"greeting", "help"}:
            return self._reply(
                state,
                "chat",
                self._text(
                    state,
                    "I can explain products and help with variant/cart actions. What do you need?",
                    "Product details ya variant/cart action mein help chahiye?",
                ),
            )
        if busy and meaning.intent != "product_question":
            return self._reply(
                state,
                "paused",
                self._text(
                    state,
                    "A task is active. Ask about products, continue or stop it.",
                    "Task active hai. Product ke sawaal poochho, continue karo ya roko.",
                ),
            )
        if meaning.intent == "unknown":
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "Please clarify what to find, explain or change. I have not taken an action.",
                    "Kya dhoondhna, samajhna ya change karna hai? Abhi koi action nahi kiya.",
                ),
            )
        patch = meaning.slots.model_dump(exclude_none=True)
        sources = state.slot_sources | {key: turn.request_id for key in patch}
        state = ConversationState.model_validate(
            state.model_dump()
            | {
                "requirements": state.requirements.model_dump() | patch,
                "slot_sources": sources,
            }
        )
        if meaning.intent == "requirements":
            for slot, en, hi in [
                ("category", "What product do you need?", "Kaunsa product chahiye?"),
                (
                    "budget",
                    f"What is your budget in {state.currency}?",
                    f"Budget kitna hai ({state.currency})?",
                ),
            ]:
                if getattr(state.requirements, slot) is None:
                    return self._reply(
                        state, "needs_input", self._text(state, en, hi), awaiting=slot
                    )
            return self._reply(
                state,
                "ready_for_search",
                self._text(
                    state,
                    "Requirements saved. Search is not connected yet; no search has run.",
                    "Requirements note hain. Search abhi connect nahi hai.",
                ),
                awaiting=None,
            )
        if turn.products is not None:
            ids = {p.product_id for p in turn.products}
            state = ConversationState.model_validate(
                state.model_dump()
                | {
                    "products": turn.products,
                    "selected_id": state.selected_id if state.selected_id in ids else None,
                    "previous_id": state.previous_id if state.previous_id in ids else None,
                }
            )
        selected = state.selected_id
        if meaning.reference == "ordinal":
            selected = (
                state.products[meaning.ordinal - 1].product_id
                if meaning.ordinal <= len(state.products)
                else None
            )
        elif meaning.reference == "previous":
            selected = state.previous_id
        elif selected is None and len(state.products) == 1:
            selected = state.products[0].product_id
        if (
            selected is None
            and not state.products
            and meaning.reference == "selected"
            and meaning.intent == "product_question"
            and turn.target is not None
            and not busy
        ):
            request = AIRequest(
                request_id=turn.request_id,
                message=turn.message,
                language=state.language,
                history=list(state.history[:-1][-6:]),
                available_tools=list(turn.available_tools),
            )
            run = self.orchestrator.start(principal, request, turn.target)
            run = await self.orchestrator.advance(run, principal, expected_version=run.version)
            return self._run_reply(state, run)
        if selected is None:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "Which product do you mean? Select one from the visible list.",
                    "Kaunsa product? Visible list se ek choose karo.",
                ),
            )
        previous = state.selected_id if state.selected_id != selected else state.previous_id
        state = ConversationState.model_validate(
            state.model_dump()
            | {
                "selected_id": selected,
                "previous_id": previous,
            }
        )
        if meaning.intent in {"compare", "reviews"}:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "Comparison/review evidence is not supported yet.",
                    "Comparison/review evidence support abhi baaki hai.",
                ),
            )
        product = next(p for p in state.products if p.product_id == selected)
        grant = turn.authorization if meaning.intent in {"select_variant", "add_to_cart"} else None
        if grant and meaning.slots.colour:
            matches = [
                v for v in product.variants if meaning.slots.colour in v.label.casefold().split()
            ]
            if len(matches) != 1 or matches[0].variant_id != grant.variant_id:
                return self._reply(
                    state,
                    "needs_input",
                    self._text(
                        state,
                        "The requested colour does not match the authorized variant. Confirm it.",
                        "Colour aur authorized variant match nahi hain. Variant confirm karo.",
                    ),
                )
        if grant and state.requirements.budget is not None:
            if grant.currency != state.currency:
                return self._reply(
                    state,
                    "needs_input",
                    self._text(
                        state,
                        "The action currency differs from your budget currency. Confirm it.",
                        "Action aur budget ki currency alag hai. Pehle currency confirm karo.",
                    ),
                )
            grant = MutationAuthorization.model_validate(
                grant.model_dump()
                | {
                    "max_unit_price": min(grant.max_unit_price, state.requirements.budget),
                }
            )
        request = AIRequest(
            request_id=turn.request_id,
            message=turn.message,
            language=state.language,
            history=list(state.history[:-1][-6:]),
            product=product,
            available_tools=list(turn.available_tools),
            authorization=grant,
        )
        # Read-only facts can refer to a saved product without navigating to it.
        if meaning.intent == "product_question":
            response = await self.orchestrator.ai.decide(request)
            if response.status != "tool_proposed":
                status = {
                    "answered": "complete",
                    "needs_input": "needs_input",
                    "blocked": "paused",
                    "error": "error",
                    "cancelled": "cancelled",
                }[response.status]
                return self._reply(
                    state,
                    "chat" if busy and status == "complete" else status,
                    response.message,
                    facts=tuple(response.facts),
                )
            if busy:
                return self._reply(
                    state,
                    "needs_input",
                    self._text(
                        state,
                        "Fresh evidence is needed. Resolve the active action before a new read.",
                        "Fresh evidence chahiye. Pehle active action ka result check karo.",
                    ),
                )
        target = turn.target
        if target is None:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "A current visible browser target is needed before acting.",
                    "Action ke liye current visible browser tab ki information chahiye.",
                ),
            )
        if product.document_id != target.document_id:
            return self._reply(
                state,
                "paused",
                self._text(
                    state,
                    "This product is not the current page. Open and refresh it first.",
                    "Ye product current page par nahi hai. Pehle uski fresh observation chahiye.",
                ),
            )
        run = self.orchestrator.start(principal, request, target)
        run = await self.orchestrator.advance(run, principal, expected_version=run.version)
        return self._run_reply(state, run)

    def _run_reply(self, state: ConversationState, run: RunState) -> ConversationReply:
        if run.pending:
            progress = {
                "read_product": (
                    "I am checking the product page.",
                    "Main product page check kar raha hoon.",
                ),
                "read_cart": ("I am checking the cart.", "Main cart check kar raha hoon."),
                "select_variant": (
                    "I am selecting the requested variant.",
                    "Main requested variant select kar raha hoon.",
                ),
                "add_to_cart": (
                    "I am updating the requested cart quantity.",
                    "Main requested cart quantity update kar raha hoon.",
                ),
            }[run.pending.tool.name]
            return self._reply(
                state,
                "awaiting_browser",
                self._text(state, *progress),
                action=run.pending,
                active_run=run,
                awaiting=None,
            )
        status = {
            "ready": "working",
            "needs_input": "needs_input",
            "paused": "paused",
            "succeeded": "complete",
            "failed": "error",
            "cancelled": "cancelled",
        }[run.status]
        message = run.summary or (run.last_response.message if run.last_response else None)
        if run.status == "ready":
            message = self._text(
                state,
                "Observation received. I can check the next step.",
                "Observation mil gayi. Ab agla step check kar sakta hoon.",
            )
        if run.status == "paused":
            message = self._text(
                state,
                f"Task paused ({run.pause_code}). No new action was dispatched.",
                f"Task pause hai ({run.pause_code}). Koi naya action nahi bheja.",
            )
        return self._reply(
            state,
            status,
            message or "Task stopped.",
            active_run=run,
            facts=tuple(run.last_response.facts) if run.last_response else (),
        )

    async def advance(
        self, state: ConversationState, principal: Principal, *, expected_version: int
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        if state.active_run is None:
            raise ConversationError("NO_ACTIVE_RUN")
        run = await self.orchestrator.advance(
            state.active_run, principal, expected_version=state.active_run.version
        )
        return self._run_reply(state, run)

    def accept_result(
        self,
        state: ConversationState,
        principal: Principal,
        result: BrowserResult,
        *,
        expected_version: int,
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        if state.active_run is None:
            raise ConversationError("NO_ACTIVE_RUN")
        run = self.orchestrator.accept_result(
            state.active_run, principal, result, expected_version=state.active_run.version
        )
        product = run.request.product
        products = tuple(
            product if product and p.product_id == product.product_id else p for p in state.products
        )
        selected = state.selected_id
        if product and product.product_id not in {p.product_id for p in products}:
            products = (*products, product)[-10:]
            selected = selected or product.product_id
        state = ConversationState.model_validate(
            state.model_dump()
            | {
                "products": products,
                "selected_id": selected,
            }
        )
        return self._run_reply(state, run)

    def resume(
        self,
        state: ConversationState,
        principal: Principal,
        target: BrowserTarget,
        *,
        expected_version: int,
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        if state.active_run is None:
            raise ConversationError("NO_ACTIVE_RUN")
        run = self.orchestrator.resume(
            state.active_run, principal, target, expected_version=state.active_run.version
        )
        return self._run_reply(state, run)

    def expire_pending(
        self, state: ConversationState, principal: Principal, *, expected_version: int
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        if state.active_run is None:
            raise ConversationError("NO_ACTIVE_RUN")
        run = self.orchestrator.expire_pending(
            state.active_run, principal, expected_version=state.active_run.version
        )
        return self._run_reply(state, run)
