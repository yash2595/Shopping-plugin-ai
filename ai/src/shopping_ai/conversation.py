"""Provider-neutral dialogue checkpoints; no session registry, API or voice client.

Persist returned state with owner checks and compare-and-swap before delivering
its action. The default interpreter is a deliberately limited offline fallback.
"""

import asyncio
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from shopping_ai.browser import BrowserAction, BrowserResult, BrowserTarget
from shopping_ai.comparison import ComparisonError, ComparisonResult, compare_products
from shopping_ai.context import product_facts
from shopping_ai.intents import (
    BasicInterpreter,
    Interpreter,
    Meaning,
    Requirements,
    SlotName,
    explicit_open_request,
)
from shopping_ai.market import (
    MarketResult,
    MarketSearch,
    ProductIdentity,
    SearchReceipt,
    SearchTask,
    Store,
    accept_search_receipt,
    expire_search,
    plan_search,
    rank_offers,
)
from shopping_ai.market_dialogue import (
    DetailInterpreter,
    MarketDraft,
    OfflineDetailInterpreter,
    validate_detail_proposal,
)
from shopping_ai.models import (
    AIRequest,
    Contract,
    Fact,
    Identifier,
    Message,
    MutationAuthorization,
    ProductSnapshot,
)
from shopping_ai.navigation import (
    NavigationReceipt,
    OfferNavigation,
    propose_navigation,
    verify_navigation,
)
from shopping_ai.orchestrator import Orchestrator, Principal, RunState
from shopping_ai.providers.base import ProviderError


class ConversationContext(Contract):
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
        return self


class ConversationTurn(ConversationContext):
    request_id: Identifier
    message: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def nonempty_message(self) -> Self:
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
    active_market: MarketSearch | None = None
    market_draft: MarketDraft | None = None
    pending_navigation: OfferNavigation | None = None
    market_display_ids: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=120)

    @model_validator(mode="after")
    def coherent_context(self) -> Self:
        ids = [p.product_id for p in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError("Products must have stable distinct references")
        if any(ref is not None and ref not in ids for ref in (self.selected_id, self.previous_id)):
            raise ValueError("Selected and previous products must exist in context")
        if self.active_run and self.active_run.principal != self.principal:
            raise ValueError("Active run belongs to another principal")
        if self.pending_navigation and (
            (self.pending_navigation.owner_id, self.pending_navigation.session_id)
            != (self.principal.owner_id, self.principal.session_id)
            or self.active_market is None
            or self.pending_navigation.search_id != self.active_market.search_id
            or not any(
                offer == self.pending_navigation.offer
                for receipt in self.active_market.receipts
                for offer in receipt.offers
            )
        ):
            raise ValueError("Navigation must belong to this principal and market search")
        if self.active_market and (
            (self.active_market.owner_id, self.active_market.session_id)
            != (self.principal.owner_id, self.principal.session_id)
            or self.active_market.currency != self.currency
        ):
            raise ValueError("Market search must belong to this principal and currency")
        return self


class ConversationReply(Contract):
    state: ConversationState
    status: Literal[
        "chat",
        "needs_input",
        "ready_for_search",
        "ready_to_navigate",
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
    navigation: OfferNavigation | None = None
    facts: tuple[Fact, ...] = Field(default_factory=tuple, max_length=10)
    comparison: ComparisonResult | None = None
    search_tasks: tuple[SearchTask, ...] = Field(default_factory=tuple, max_length=6)
    market_result: MarketResult | None = None

    @model_validator(mode="after")
    def action_binding(self) -> Self:
        if (self.status == "ready_to_navigate") != (self.navigation is not None):
            raise ValueError("Navigation reply requires its pending proposal")
        if self.navigation and (
            self.state.pending_navigation != self.navigation or self.action or self.search_tasks
        ):
            raise ValueError("Navigation cannot dispatch other browser work")
        market = self.state.active_market
        if self.search_tasks and (
            self.status != "ready_for_search"
            or market is None
            or self.search_tasks != market.tasks
            or market.receipts
        ):
            raise ValueError("Search tasks must be the new owned market plan")
        if self.market_result and (
            market is None or self.market_result.search_id != market.search_id
        ):
            raise ValueError("Market result must belong to the active search")
        if self.action and self.search_tasks:
            raise ValueError("Cannot dispatch cart and search work together")
        if self.comparison:
            ids = {p.product_id for p in self.state.products}
            snapshots = {p.snapshot_id for p in self.state.products}
            refs = (
                fact.snapshot_id
                for row in self.comparison.rows
                for fact in (row.left, row.right)
                if fact
            )
            if (
                self.status not in {"chat", "complete"}
                or not {self.comparison.left_product_id, self.comparison.right_product_id} <= ids
                or any(ref not in snapshots for ref in refs)
            ):
                raise ValueError("Comparison must refer to this conversation's evidence")
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
    def __init__(
        self,
        orchestrator: Orchestrator,
        interpreter: Interpreter | None = None,
        *,
        stores: tuple[Store, ...] = (),
        detail_interpreter: DetailInterpreter | None = None,
    ):
        self.orchestrator = orchestrator
        self.interpreter = interpreter or BasicInterpreter()
        self.detail_interpreter = detail_interpreter or OfflineDetailInterpreter()
        self._detail_capacity = asyncio.Semaphore(orchestrator.ai.config.max_concurrent_calls)
        self.stores = tuple(Store.model_validate(store.model_dump()) for store in stores)
        if self.stores and (
            not 2 <= len(self.stores) <= 6
            or len({store.store_id for store in self.stores}) != len(self.stores)
            or len({store.origin for store in self.stores}) != len(self.stores)
        ):
            raise ValueError("Configure 2-6 distinct supported stores")

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
        navigation: OfferNavigation | None = None,
        facts: tuple[Fact, ...] = (),
        comparison: ComparisonResult | None = None,
        search_tasks: tuple[SearchTask, ...] = (),
        market_result: MarketResult | None = None,
        **updates,
    ) -> ConversationReply:
        if market_result is not None:
            updates["market_display_ids"] = tuple(
                item.offer.offer_id for item in market_result.ranked
            )
        speech = (
            market_result.speech_text
            if market_result is not None
            else comparison.speech(state.language)
            if comparison is not None
            else text
        )
        history = (*state.history, Message(role="assistant", text=text[:2000]))[-12:]
        next_state = ConversationState.model_validate(
            state.model_dump() | updates | {"version": state.version + 1, "history": history}
        )
        return ConversationReply(
            state=next_state,
            status=status,
            message=text,
            speech_text=speech,
            action=action,
            navigation=navigation,
            facts=facts,
            comparison=comparison,
            search_tasks=search_tasks,
            market_result=market_result,
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

    def begin_market_search(
        self,
        state: ConversationState,
        principal: Principal,
        *,
        identity: ProductIdentity,
        delivery_context: str,
        stores: tuple[Store, ...],
        expected_version: int,
    ) -> ConversationReply:
        """Start only after the caller has clarified and confirmed exact identity.

        Store configurations come from the trusted adapter, not a user/model URL.
        Persist the returned checkpoint with CAS before dispatching search_tasks.
        """
        state = self._check(state, principal, expected_version)
        state = self._settle_expired_market(state)
        if state.pending_navigation is not None:
            raise ConversationError("ACTIVE_NAVIGATION")
        run = state.active_run
        if run and (run.pending or run.recovery_required or run.status in {"ready", "paused"}):
            raise ConversationError("ACTIVE_BROWSER_TASK")
        if state.active_market and len(state.active_market.receipts) < len(
            state.active_market.tasks
        ):
            raise ConversationError("ACTIVE_MARKET_SEARCH")
        market = plan_search(
            search_id=uuid4().hex,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            identity=identity,
            currency=state.currency,
            delivery_context=delivery_context,
            stores=stores,
            now=self.orchestrator.ai.clock(),
            timeout_seconds=self.orchestrator.ai.config.search_timeout_seconds,
        )
        return self._reply(
            state,
            "ready_for_search",
            self._text(
                state,
                "Search plan ready. The browser adapter must now check the configured stores.",
                "Search plan taiyar hai. Browser adapter ab configured stores check karega.",
            ),
            active_market=market,
            market_draft=None,
            market_display_ids=(),
            awaiting=None,
            search_tasks=market.tasks,
        )

    def accept_market_receipt(
        self,
        state: ConversationState,
        principal: Principal,
        receipt: SearchReceipt,
        *,
        expected_version: int,
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        if state.active_market is None:
            raise ConversationError("NO_ACTIVE_MARKET_SEARCH")
        market = accept_search_receipt(
            state.active_market,
            receipt,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            expected_version=state.active_market.version,
            now=self.orchestrator.ai.clock(),
        )
        result = rank_offers(
            market,
            now=self.orchestrator.ai.clock(),
            config=self.orchestrator.ai.config,
            language=state.language,
        )
        return self._reply(
            state,
            "working" if result.status == "searching" else "complete",
            result.message,
            active_market=market,
            market_result=result,
        )

    def expire_market_search(
        self,
        state: ConversationState,
        principal: Principal,
        *,
        expected_version: int,
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        if state.active_market is None:
            raise ConversationError("NO_ACTIVE_MARKET_SEARCH")
        market = expire_search(
            state.active_market,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            expected_version=state.active_market.version,
            now=self.orchestrator.ai.clock(),
        )
        result = rank_offers(
            market,
            now=self.orchestrator.ai.clock(),
            config=self.orchestrator.ai.config,
            language=state.language,
        )
        return self._reply(
            state,
            "complete",
            result.message,
            active_market=market,
            market_result=result,
        )

    def _settle_expired_market(self, state: ConversationState) -> ConversationState:
        if (
            state.pending_navigation is not None
            and self.orchestrator.ai.clock() >= state.pending_navigation.expires_at
        ):
            state = ConversationState.model_validate(
                state.model_dump() | {"pending_navigation": None}
            )
        market = state.active_market
        if (
            market is not None
            and len(market.receipts) < len(market.tasks)
            and self.orchestrator.ai.clock() >= market.deadline_at
        ):
            market = expire_search(
                market,
                owner_id=state.principal.owner_id,
                session_id=state.principal.session_id,
                expected_version=market.version,
                now=self.orchestrator.ai.clock(),
            )
            return ConversationState.model_validate(state.model_dump() | {"active_market": market})
        return state

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
                active_market=None,
                market_draft=None,
                pending_navigation=None,
                market_display_ids=(),
            )
        state = self._settle_expired_market(state)
        busy = state.active_run is not None and (
            state.active_run.pending is not None
            or state.active_run.recovery_required
            or state.active_run.status in {"ready", "paused"}
        )
        busy = busy or state.pending_navigation is not None
        busy = busy or (
            state.active_market is not None
            and len(state.active_market.receipts) < len(state.active_market.tasks)
        )
        if state.market_draft is not None and not busy:
            return await self._collect_market_details(state, turn.message)
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
        except ProviderError:
            return self._reply(
                state,
                "error",
                self._text(
                    state,
                    "The request could not be understood. Please retry.",
                    "Request samajh nahi aayi. Dobara try karo.",
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
        if busy and meaning.intent not in {"product_question", "compare"}:
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
        if meaning.intent == "open_offer":
            if not explicit_open_request(turn.message):
                return self._reply(
                    state,
                    "needs_input",
                    self._text(
                        state,
                        "Please explicitly request opening one offer without a purchase/cart task.",
                        "Ek offer kholne ka explicit request do; cart/purchase alag task hai.",
                    ),
                )
            return self._open_offer(state, meaning, turn.target)
        if meaning.intent == "market_search":
            draft = MarketDraft()
            state = ConversationState.model_validate(
                state.model_dump() | {"market_draft": draft, "awaiting": None}
            )
            return await self._collect_market_details(state, turn.message, initial=True)
        if meaning.intent == "compare":
            if (
                state.active_market is not None
                and meaning.reference == "selected"
                and meaning.comparison_indices is None
            ):
                result = rank_offers(
                    state.active_market,
                    now=self.orchestrator.ai.clock(),
                    config=self.orchestrator.ai.config,
                    language=state.language,
                )
                return self._reply(
                    state,
                    "working" if result.status == "searching" else "complete",
                    result.message,
                    market_result=result,
                )
            return self._compare(state, meaning, busy=busy)
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
        if meaning.intent == "reviews":
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "Review evidence is not supported yet.",
                    "Review evidence support abhi baaki hai.",
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

    def _open_offer(
        self,
        state: ConversationState,
        meaning: Meaning,
        target: BrowserTarget | None,
    ) -> ConversationReply:
        market = state.active_market
        if market is None or target is None or not target.visible:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "A verified offer search and current visible tab are needed.",
                    "Verified offers aur current visible tab ki information chahiye.",
                ),
            )
        result = rank_offers(
            market,
            now=self.orchestrator.ai.clock(),
            config=self.orchestrator.ai.config,
            language=state.language,
        )
        offer_id = None
        if meaning.reference == "ordinal" and meaning.ordinal <= len(state.market_display_ids):
            offer_id = state.market_display_ids[meaning.ordinal - 1]
        elif meaning.reference == "selected" and len(result.cheapest_offer_ids) == 1:
            offer_id = result.cheapest_offer_ids[0]
        if offer_id is None:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "Choose an offer from the displayed ranking; "
                    "a unique cheapest offer is not confirmed.",
                    "Displayed ranking se exact offer choose karo; "
                    "ek unique cheapest offer confirm nahi hai.",
                ),
            )
        try:
            action = propose_navigation(
                market,
                principal=state.principal,
                target=target,
                offer_id=offer_id,
                now=self.orchestrator.ai.clock(),
                config=self.orchestrator.ai.config,
            )
        except ValueError:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "That offer needs fresh verification before opening. "
                    "Please refresh the search.",
                    "Offer kholne se pehle fresh verification chahiye. Search refresh karo.",
                ),
            )
        return self._reply(
            state,
            "ready_to_navigate",
            self._text(
                state,
                "Offer navigation is proposed. Waiting for visible-browser page evidence.",
                "Offer kholne ka proposal taiyar hai. "
                "Visible browser ke page evidence ka wait hai.",
            ),
            pending_navigation=action,
            navigation=action,
        )

    def accept_navigation_receipt(
        self,
        state: ConversationState,
        principal: Principal,
        receipt: NavigationReceipt,
        *,
        expected_version: int,
    ) -> ConversationReply:
        state = self._check(state, principal, expected_version)
        action = state.pending_navigation
        if action is None:
            raise ConversationError("NO_PENDING_NAVIGATION")
        product = verify_navigation(
            action,
            receipt,
            principal=principal,
            now=self.orchestrator.ai.clock(),
            config=self.orchestrator.ai.config,
        )
        if product is None:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "The browser could not verify the requested offer page.",
                    "Browser requested offer ka page verify nahi kar saka.",
                ),
                pending_navigation=None,
            )
        products = tuple(p for p in state.products if p.product_id != product.product_id)[-9:] + (
            product,
        )
        previous = (
            state.selected_id if state.selected_id != product.product_id else state.previous_id
        )
        if previous not in {p.product_id for p in products}:
            previous = None
        market = state.active_market
        changed = (
            product.price != action.offer.price
            or product.currency != action.offer.currency
            or product.in_stock is not True
        )
        if changed:
            # Contradicted listing evidence must not keep winning in cached rankings.
            receipts = tuple(
                receipt.model_copy(update={"status": "failed", "offers": ()})
                if any(offer.offer_id == action.offer.offer_id for offer in receipt.offers)
                else receipt
                for receipt in market.receipts
            )
            market = MarketSearch.model_validate(
                market.model_dump()
                | {
                    "version": market.version + 1,
                    "receipts": receipts,
                }
            )
        message = self._text(
            state,
            "The requested offer page is verified. Fresh product evidence is selected.",
            "Requested offer ka page verify ho gaya. Fresh product evidence select ho gaya hai.",
        )
        if changed:
            message += self._text(
                state,
                " Price/availability changed or is unknown; refresh cross-store prices.",
                " Price/availability badli hai ya unknown hai; cross-store prices refresh karo.",
            )
        return self._reply(
            state,
            "complete",
            message,
            pending_navigation=None,
            active_market=market,
            products=products,
            selected_id=product.product_id,
            previous_id=previous,
            market_display_ids=() if changed else state.market_display_ids,
            facts=product_facts(product)[:10],
        )

    async def _collect_market_details(
        self,
        state: ConversationState,
        message: str,
        *,
        initial: bool = False,
    ) -> ConversationReply:
        draft = state.market_draft
        if message.strip().casefold() in {"confirm", "haan confirm", "yes confirm"}:
            if draft.missing:
                return self._reply(state, "needs_input", draft.prompt(state.language))
            if not self.stores:
                return self._reply(
                    state,
                    "needs_input",
                    self._text(
                        state,
                        "Details are complete, but supported store adapters are not configured. "
                        "No websites have been searched.",
                        "Details complete hain, lekin store adapters configure nahi hain. "
                        "Koi website search nahi hui hai.",
                    ),
                )
            return self.begin_market_search(
                state,
                state.principal,
                identity=draft.identity(),
                delivery_context=draft.postcode,
                stores=self.stores,
                expected_version=state.version,
            )
        config = self.orchestrator.ai.config
        try:
            async with asyncio.timeout(config.capacity_wait_seconds):
                await self._detail_capacity.acquire()
        except TimeoutError:
            return self._reply(
                state,
                "error",
                self._text(
                    state,
                    "Detail understanding is busy. Please retry.",
                    "Details samajhne ki capacity busy hai. Dobara try karo.",
                ),
            )
        try:
            # Drafts contain mutable maps despite frozen contracts. Never expose
            # the saved checkpoint or validation baseline to an async adapter.
            private = MarketDraft.model_validate(draft.model_dump())
            async with asyncio.timeout(config.timeout_seconds):
                raw = await self.detail_interpreter.extract(message, private)
            draft = validate_detail_proposal(draft, raw, message)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return self._reply(
                state,
                "error",
                self._text(
                    state,
                    "Detail understanding timed out. Please retry.",
                    "Details samajhne mein timeout hua. Dobara try karo.",
                ),
            )
        except Exception:
            return self._reply(
                state,
                "needs_input",
                (
                    ""
                    if initial
                    else self._text(
                        state,
                        "I could not safely read that detail. ",
                        "Ye detail clear nahi hui. ",
                    )
                )
                + draft.prompt(state.language),
            )
        finally:
            self._detail_capacity.release()
        return self._reply(
            state,
            "needs_input",
            draft.prompt(state.language),
            market_draft=draft,
        )

    def _compare(
        self, state: ConversationState, meaning: Meaning, *, busy: bool
    ) -> ConversationReply:
        products = {p.product_id: p for p in state.products}
        pair = None
        if meaning.comparison_indices:
            left_index, right_index = meaning.comparison_indices
            if max(left_index, right_index) <= len(state.products):
                pair = (state.products[left_index - 1], state.products[right_index - 1])
        elif state.selected_id:
            other = None
            if meaning.reference == "previous":
                other = state.previous_id
            elif meaning.reference == "ordinal" and meaning.ordinal <= len(state.products):
                other = state.products[meaning.ordinal - 1].product_id
            elif meaning.reference == "selected":
                other = state.previous_id or (
                    next((key for key in products if key != state.selected_id), None)
                    if len(products) == 2
                    else None
                )
            if other and other != state.selected_id:
                pair = (products[state.selected_id], products[other])
        elif len(products) == 2 and meaning.reference == "selected":
            pair = tuple(state.products)
        if pair is None:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    "Which two products should I compare? Select them from the visible list.",
                    "Kaunse do products compare karne hain? Visible list se choose karo.",
                ),
            )
        try:
            comparison = compare_products(
                *pair, now=self.orchestrator.ai.clock(), config=self.orchestrator.ai.config
            )
        except ComparisonError as exc:
            return self._reply(
                state,
                "needs_input",
                self._text(
                    state,
                    f"Fresh, distinct product evidence is needed ({exc}).",
                    f"Fresh aur distinct product evidence chahiye ({exc}).",
                ),
            )
        return self._reply(
            state,
            "chat" if busy else "complete",
            comparison.render(state.language),
            comparison=comparison,
        )

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
