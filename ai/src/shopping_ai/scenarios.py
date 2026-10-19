"""Fixed simulated integration journeys; no retailer, model or speech-service calls."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from shopping_ai.browser import BrowserResult, BrowserTarget, CartLine, CartSnapshot
from shopping_ai.config import AIConfig
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market import ProductIdentity, SearchReceipt, Store
from shopping_ai.market_dialogue import OfflineDetailInterpreter
from shopping_ai.models import MutationAuthorization
from shopping_ai.orchestrator import OrchestrationError, Principal
from shopping_ai.page_evidence import (
    CapturedCart,
    CapturedPage,
    ObservedOfferDetails,
    ObservedVariantControl,
    PageEvidenceError,
    build_cart_read_result,
    build_market_offer,
    build_navigation_result,
    decode_captured_cart,
    extract_product_snapshot,
)
from shopping_ai.providers import MockProvider
from shopping_ai.runtime import AssistantRuntime, create_runtime
from shopping_ai.voice import AudioClip, Transcript


class ScenarioFailure(Exception):
    pass


def require(condition: bool, code: str):
    if not condition:
        raise ScenarioFailure(code)


class SimulatedSpeech:
    def __init__(self):
        self.transcriptions = 0
        self.syntheses = 0

    async def transcribe(self, clip, *, language):
        self.transcriptions += 1
        return Transcript(text="savings", confidence=0.99)

    async def synthesize(self, text, *, language):
        self.syntheses += 1
        if self.syntheses == 1:
            raise RuntimeError("Simulated first-attempt TTS failure")
        return AudioClip(format="wav", data=b"simulated-not-playable-audio")


class Journey:
    def __init__(self, runtime: AssistantRuntime | None = None, principal: Principal | None = None):
        self.now = datetime(2026, 10, 18, 12, tzinfo=UTC)
        self.principal = principal or Principal(
            owner_id="evaluation", session_id="evaluation-session"
        )
        self.speech = SimulatedSpeech()
        self.runtime = runtime or create_runtime(
            MockProvider(),
            interpreter=BasicInterpreter(),
            detail_interpreter=OfflineDetailInterpreter(),
            transcriber=self.speech,
            synthesizer=self.speech,
            stores=tuple(
                Store(
                    store_id=name,
                    origin=f"https://{name}.example",
                    search_path="/search",
                    query_parameter="q",
                )
                for name in ("a", "b")
            ),
            clock=lambda: self.now,
        )
        self.state = None

    def persist(self, state):
        # A codec roundtrip only: no database durability/CAS implementation claim.
        self.state = self.runtime.restore(
            self.runtime.snapshot(state, self.principal),
            self.principal,
            conversation_id=state.conversation_id,
            expected_version=state.version,
        )

    async def send(self, event, **options):
        output = await self.runtime.handle(
            self.state,
            self.principal,
            event,
            expected_version=self.state.version,
            **options,
        )
        require(output.state is not None, "missing_checkpoint")
        self.persist(output.state)
        return output

    async def say(self, message, **context):
        return (
            await self.send(
                {
                    "kind": "text",
                    "turn": {
                        "request_id": f"turn{self.state.version}",
                        "message": message,
                        **context,
                    },
                }
            )
        ).conversation_reply

    async def prepare(self):
        self.persist((await self.runtime.start(self.principal, language="en")).state)
        for message in (
            "budget 1100",
            "search across websites",
            "Example",
            "Phone One",
            "storage=128GB,colour=black",
            "new",
            "201001",
            "confirm",
        ):
            reply = await self.say(message)
        require(len(reply.search_tasks) == 2, "search_plan_missing")
        self.original_receipt = None
        for index, task in enumerate(reply.search_tasks):
            market = self.state.active_market
            page, details = self.capture_offer(task, index)
            offer = build_market_offer(
                market,
                self.principal,
                task.task_id,
                page,
                details,
                expected_search_version=market.version,
                offer_id=f"offer{index}",
                snapshot_id=f"snapshot{index}",
                now=self.now,
                config=self.runtime.conversation.orchestrator.ai.config,
            )
            receipt = SearchReceipt(
                search_id=market.search_id,
                owner_id=self.principal.owner_id,
                session_id=self.principal.session_id,
                task_id=task.task_id,
                delivery_context=market.delivery_context,
                status="complete",
                offers=(offer,),
            )
            if index == 0:
                self.original_receipt = receipt
            output = await self.send({"kind": "market_result", "receipt": receipt})
        require(
            output.conversation_reply.market_result.cheapest_offer_ids == ("offer1",),
            "delivered_total_winner_incorrect",
        )

    def capture_offer(self, task, index, *, price=None):
        """Independent fixed source fixture, never a copy of requested identity."""
        url = task.store.origin + "/product/one"
        price = price if price is not None else "999" if index == 0 else "1049"
        metadata = {
            "@context": "https://schema.org",
            "@type": "Product",
            "url": url,
            "name": "Example Phone One",
            "brand": {"@type": "Brand", "name": "Example"},
            "model": "Phone One",
            "color": "black",
            "additionalProperty": [{"@type": "PropertyValue", "name": "storage", "value": "128GB"}],
            "offers": {
                "@type": "Offer",
                "url": url,
                "price": price,
                "priceCurrency": "INR",
                "availability": "https://schema.org/InStock",
                "itemCondition": "https://schema.org/NewCondition",
                "seller": {"@type": "Organization", "name": "Simulated seller"},
            },
        }
        page = CapturedPage(
            target=BrowserTarget(
                tab_id=7 + index,
                document_id=f"market-page{index}",
                active=True,
                window_focused=True,
            ),
            url=url,
            observed_at=self.now,
            html='<script type="application/ld+json">' + json.dumps(metadata) + "</script>",
            visible_title="Example Phone One",
            visible_price=price,
            visible_currency="INR",
            visible_stock=True,
        )
        details = ObservedOfferDetails(
            document_id=page.target.document_id,
            observed_at=self.now,
            identity=ProductIdentity(
                brand="Example",
                model="Phone One",
                attributes={"storage": "128GB", "colour": "black"},
                condition="new",
            ),
            seller="Simulated seller",
            delivery_context="201001",
            currency="INR",
            shipping="100" if index == 0 else "0",
            extra_tax_and_fees="0",
        )
        return page, details

    async def capture_rejection(self):
        await self.say("refresh prices")
        market = self.state.active_market
        task = market.tasks[0]
        page, details = self.capture_offer(task, 0)
        before = self.state
        try:
            build_market_offer(
                market,
                self.principal,
                task.task_id,
                page.model_copy(update={"visible_price": Decimal("1")}),
                details,
                expected_search_version=market.version,
                offer_id="corrected",
                snapshot_id="corrected",
                now=self.now,
            )
        except PageEvidenceError as exc:
            require(str(exc) == "VISIBLE_PRICE_MISMATCH", "unexpected_capture_rejection")
        else:
            raise ScenarioFailure("contradictory_capture_accepted")
        require(self.state == before and not market.receipts, "rejection_consumed_task")
        offer = build_market_offer(
            market,
            self.principal,
            task.task_id,
            page,
            details,
            expected_search_version=market.version,
            offer_id="corrected",
            snapshot_id="corrected",
            now=self.now,
        )
        reply = await self.send(
            {
                "kind": "market_result",
                "receipt": SearchReceipt(
                    search_id=market.search_id,
                    owner_id=self.principal.owner_id,
                    session_id=self.principal.session_id,
                    task_id=task.task_id,
                    delivery_context="201001",
                    status="complete",
                    offers=(offer,),
                ),
            }
        )
        require(
            len(self.state.active_market.receipts) == 1
            and not reply.conversation_reply.market_result.cheapest_offer_ids,
            "corrected_capture_not_scoped_to_pending_search",
        )

    async def budget_and_pair(self):
        search = self.state.active_market
        limited = await self.say("budget 1050")
        require(
            [r.offer.offer_id for r in limited.market_result.ranked] == ["offer1"],
            "budget_filter_failed",
        )
        await self.say("budget 1100")
        savings = await self.say("savings")
        pair = await self.say("compare first and second")
        require(
            savings.market_answer.savings_amount == Decimal("50")
            and pair.market_answer.savings_amount == Decimal("50"),
            "pair_difference_incorrect",
        )
        require(
            self.state.active_market.receipts == search.receipts
            and not pair.search_tasks
            and pair.action is None,
            "readonly_pair_dispatched_work",
        )

    async def refresh_isolation(self):
        old = self.state.active_market
        fresh = await self.say("refresh prices")
        require(
            self.state.active_market.search_id != old.search_id
            and not self.state.active_market.receipts
            and len(fresh.search_tasks) == 2,
            "refresh_reused_evidence",
        )
        try:
            await self.send({"kind": "market_result", "receipt": self.original_receipt})
        except ValueError:
            pass
        else:
            raise ScenarioFailure("old_receipt_accepted")
        again = await self.say("refresh prices")
        require(again.status == "paused" and not again.search_tasks, "pending_refresh_redispatched")

    async def stale_sources(self):
        self.now += timedelta(days=1)
        for message in ("savings", "seller", "shipping breakdown"):
            reply = await self.say(message)
            require(
                reply.status == "needs_input" and not reply.market_answer.source_offers,
                "expired_offer_reused",
            )

    async def navigation_change(self):
        target = BrowserTarget(tab_id=7, document_id="before", active=True, window_focused=True)
        proposal = (await self.say("open cheapest", target=target)).navigation
        require(proposal is not None, "navigation_not_proposed")
        page, details = self.capture_offer(self.state.active_market.tasks[1], 1, price="1200")
        page = page.model_copy(
            update={"target": target.model_copy(update={"document_id": "after"})}
        )
        receipt = build_navigation_result(
            proposal,
            self.principal,
            page,
            details.identity,
            snapshot_id="opened",
            product_id="phone",
            now=self.now,
            config=self.runtime.conversation.orchestrator.ai.config,
        )
        opened = await self.send(
            {
                "kind": "navigation_result",
                "receipt": receipt,
            }
        )
        require(
            opened.conversation_reply.offer_verification.base_price_difference == Decimal("151"),
            "page_change_difference_incorrect",
        )
        ranking = await self.say("cheapest")
        require(not ranking.market_result.cheapest_offer_ids, "changed_offer_remained_confirmed")

    async def deferred_voice_retry(self):
        output = await self.send(
            {
                "kind": "audio",
                "turn": {
                    "request_id": "audio",
                    "audio": AudioClip(format="webm", data=b"simulated-input"),
                },
            },
            defer_speech=True,
        )
        require(
            output.reply.status == "ready" and self.speech.syntheses == 0,
            "tts_ran_before_deferred_return",
        )
        reply = output.conversation_reply
        for expected in ("error", "complete"):
            spoken = await self.runtime.render_speech(
                self.state,
                self.principal,
                reply,
                expected_version=self.state.version,
            )
            require(spoken.status == expected, "speech_retry_status_incorrect")
        require(
            self.speech.transcriptions == 1 and self.speech.syntheses == 2,
            "speech_retry_replayed_transcription",
        )
        require(
            set(spoken.model_dump()) == {"conversation_id", "version", "status", "audio"},
            "playback_output_contains_dispatch_data",
        )

    async def lost_cart_receipt(self):
        await self.captured_variant_cart(recover=True)

    async def captured_variant_cart(self, *, recover=False):
        # New conversation: comparison setup must not supply cart permission or identity.
        page, _ = self.capture_offer(self.state.active_market.tasks[0], 0)
        self.persist((await self.runtime.start(self.principal, language="en")).state)
        control = ObservedVariantControl(
            control_id="variants",
            document_id=page.target.document_id,
            observed_at=self.now,
            variants=(
                {"variant_id": "black", "label": "Black"},
                {"variant_id": "blue", "label": "Blue"},
            ),
            selected_variant_id="black",
            price_variant_id="black",
        )
        page = page.model_copy(
            update={
                "html": page.html + '<select id="variants" name="id">'
                '<option value="black">Black</option>'
                '<option value="blue" selected>Blue</option></select>',
                "variant_control": control,
            }
        )
        snapshot = extract_product_snapshot(
            page,
            snapshot_id="cart-page",
            product_id="p42",
            now=self.now,
        )
        request_id = f"turn{self.state.version}"
        grant = MutationAuthorization(
            request_id=request_id,
            allowed_tools=["add_to_cart"],
            product_id="p42",
            document_id=page.target.document_id,
            variant_id="black",
            quantity=1,
            mode="ensure_quantity",
            max_unit_price="1100",
            currency="INR",
            expires_at=self.now + timedelta(seconds=60),
        )
        reply = await self.say(
            "Black wala cart mein daal do",
            products=(snapshot,),
            target=page.target,
            available_tools=("read_cart", "add_to_cart"),
            authorization=grant,
        )
        require(
            reply.action is not None and reply.action.tool.name == "read_cart",
            "cart_baseline_missing",
        )
        for quantity in (0, 1):
            action = self.state.active_run.pending
            require(action is not None, "missing_cart_action")
            expected = "read_cart" if quantity == 0 else "add_to_cart"
            require(action.tool.name == expected, "unexpected_cart_action")
            if quantity == 1 and recover:
                lost = BrowserResult(
                    action_id=action.action_id,
                    run_id=action.run_id,
                    session_id=self.principal.session_id,
                    run_version=action.run_version,
                    source_document_id=action.target.document_id,
                    target=action.target,
                    observed_at=self.now,
                    outcome="unknown",
                    error_code="LOST_RECEIPT",
                )
                await self.send({"kind": "browser_result", "result": lost})
                require(self.state.active_run.status == "paused", "unknown_add_not_paused")
                require(self.state.active_run.recovery_required, "recovery_marker_missing")
                # Neither an automatic advance nor repeated recovery may replay the add.
                try:
                    await self.send({"kind": "advance"})
                except OrchestrationError:
                    pass
                else:
                    raise ScenarioFailure("paused_run_advanced")
                require(self.state.active_run.pending is None, "paused_add_replayed")
                for observed_quantity in (0, 1):
                    await self.send({"kind": "resume", "target": page.target})
                    await self.send({"kind": "advance"})
                    action = self.state.active_run.pending
                    require(
                        action is not None and action.tool.name == "read_cart",
                        "recovery_replayed_mutation",
                    )
                    read = build_cart_read_result(
                        self.state.active_run,
                        self.principal,
                        CartSnapshot(
                            observed_at=self.now,
                            document_id=page.target.document_id,
                            lines=()
                            if observed_quantity == 0
                            else (CartLine(product_id="p42", variant_id="black", quantity=1),),
                        ),
                        page.target,
                        coverage="complete",
                        expected_run_version=self.state.active_run.version,
                        now=self.now,
                    )
                    output = await self.send({"kind": "browser_result", "result": read})
                    if observed_quantity == 0:
                        require(
                            self.state.active_run.status == "paused",
                            "unconfirmed_cart_reported_success",
                        )
                        require(
                            self.state.active_run.pending is None, "empty_recovery_replayed_add"
                        )
                break
            receipt = BrowserResult(
                action_id=action.action_id,
                run_id=action.run_id,
                session_id=self.principal.session_id,
                run_version=action.run_version,
                source_document_id=action.target.document_id,
                target=action.target,
                observed_at=self.now,
                outcome="observed",
                cart=CartSnapshot(
                    observed_at=self.now,
                    document_id=page.target.document_id,
                    lines=()
                    if quantity == 0
                    else (CartLine(product_id="p42", variant_id="black", quantity=1),),
                ),
            )
            if quantity == 0:
                capture = decode_captured_cart(
                    CapturedCart(
                        target=receipt.target,
                        cart=receipt.cart,
                        coverage="complete",
                    )
                    .model_dump_json()
                    .encode()
                )
                receipt = build_cart_read_result(
                    self.state.active_run,
                    self.principal,
                    capture.cart,
                    capture.target,
                    coverage=capture.coverage,
                    expected_run_version=self.state.active_run.version,
                    now=self.now,
                )
            output = await self.send({"kind": "browser_result", "result": receipt})
            if quantity == 0:
                require(self.state.active_run.status != "succeeded", "premature_cart_success")
                await self.send({"kind": "advance"})
        require(output.conversation_reply.status == "complete", "cart_not_complete")
        require("Verified cart" in output.conversation_reply.message, "cart_not_verified")

    async def shared_session_isolation(self):
        class YieldingInterpreter(BasicInterpreter):
            active = 0
            peak = 0

            async def interpret(self, message, awaiting):
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(0)
                    return await super().interpret(message, awaiting)
                finally:
                    self.active -= 1

        class ConcurrentDetails(OfflineDetailInterpreter):
            active = 0
            peak = 0

            async def extract(self, message, draft):
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(0)
                    return await super().extract(message, draft)
                finally:
                    self.active -= 1

        details = ConcurrentDetails()
        interpreter = YieldingInterpreter()
        shared = create_runtime(
            MockProvider(),
            interpreter=interpreter,
            detail_interpreter=details,
            stores=self.runtime.conversation.stores,
            config=AIConfig(max_concurrent_calls=2),
            clock=lambda: self.now,
        )

        async def user(index):
            principal = Principal(owner_id=f"user{index // 2}", session_id=f"session{index}")
            journey = Journey(shared, principal)
            await journey.prepare()
            await journey.say(f"budget {1100 + index}")
            return journey

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(user(index)) for index in range(8)]
        journeys = [task.result() for task in tasks]
        require(1 < details.peak <= 2 and details.active == 0, "shared_detail_capacity_failed")
        require(
            1 < interpreter.peak <= 2 and interpreter.active == 0, "shared_intent_capacity_failed"
        )
        require(
            len({j.state.active_market.search_id for j in journeys}) == 8,
            "shared_search_ids_collided",
        )
        for index, journey in enumerate(journeys):
            require(
                journey.state.requirements.budget == Decimal(1100 + index), "session_budget_mixed"
            )
            foreign = journeys[(index + 1) % len(journeys)]
            # Identical local request/offer IDs must not defeat owner/session scope.
            for principal, event in (
                (foreign.principal, {"kind": "cancel", "request_id": "foreign-stop"}),
                (journey.principal, {"kind": "market_result", "receipt": foreign.original_receipt}),
            ):
                try:
                    await shared.handle(
                        journey.state, principal, event, expected_version=journey.state.version
                    )
                except ValueError:
                    pass
                else:
                    raise ScenarioFailure("cross_session_event_accepted")


async def run_scenarios() -> tuple[tuple[str, bool, str], ...]:
    results = []
    for name in (
        "budget_and_pair",
        "refresh_isolation",
        "stale_sources",
        "navigation_change",
        "deferred_voice_retry",
        "shared_session_isolation",
        "capture_rejection",
        "captured_variant_cart",
        "lost_cart_receipt",
    ):
        journey = Journey()
        try:
            async with asyncio.timeout(15):
                await journey.prepare()
                await getattr(journey, name)()
        except ScenarioFailure as exc:
            results.append((name, False, str(exc)))
        except Exception:
            results.append((name, False, "scenario_or_schema_failure"))
        else:
            results.append((name, True, "matched"))
    return tuple(results)
