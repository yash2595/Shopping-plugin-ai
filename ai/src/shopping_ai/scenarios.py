"""Fixed simulated integration journeys; no retailer, model or speech-service calls."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from shopping_ai.browser import BrowserTarget
from shopping_ai.config import AIConfig
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market import Offer, SearchReceipt, Store
from shopping_ai.market_dialogue import OfflineDetailInterpreter
from shopping_ai.models import ProductSnapshot
from shopping_ai.navigation import NavigationReceipt
from shopping_ai.orchestrator import Principal
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
            receipt = SearchReceipt(
                search_id=market.search_id,
                owner_id=self.principal.owner_id,
                session_id=self.principal.session_id,
                task_id=task.task_id,
                delivery_context=market.delivery_context,
                status="complete",
                offers=(
                    Offer(
                        offer_id=f"offer{index}",
                        snapshot_id=f"snapshot{index}",
                        identity=market.identity,
                        url=task.store.origin + "/product/one",
                        seller="Simulated seller",
                        observed_at=self.now,
                        in_stock=True,
                        price="999" if index == 0 else "1049",
                        currency="INR",
                        shipping="100" if index == 0 else "0",
                        extra_tax_and_fees="0",
                    ),
                ),
            )
            if index == 0:
                self.original_receipt = receipt
            output = await self.send({"kind": "market_result", "receipt": receipt})
        require(
            output.conversation_reply.market_result.cheapest_offer_ids == ("offer1",),
            "delivered_total_winner_incorrect",
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
        product = ProductSnapshot(
            snapshot_id="opened",
            product_id="phone",
            document_id="after",
            title="Simulated phone",
            observed_at=self.now,
            price="1200",
            currency="INR",
            in_stock=True,
        )
        opened = await self.send(
            {
                "kind": "navigation_result",
                "receipt": NavigationReceipt(
                    action_id=proposal.action_id,
                    search_id=proposal.search_id,
                    owner_id=self.principal.owner_id,
                    session_id=self.principal.session_id,
                    status="opened",
                    observed_at=self.now,
                    url=proposal.offer.url,
                    target=target.model_copy(update={"document_id": "after"}),
                    identity=proposal.offer.identity,
                    product=product,
                ),
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
