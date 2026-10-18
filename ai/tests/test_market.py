import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest

from shopping_ai.config import AIConfig
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market import (
    Offer,
    ProductIdentity,
    SearchReceipt,
    Store,
    accept_search_receipt,
    plan_search,
    rank_offers,
)


def identity(**updates):
    return ProductIdentity.model_validate(
        dict(brand="Example", model="Phone 1", attributes={"storage": "128GB", "colour": "black"})
        | updates
    )


def plan(now):
    return plan_search(
        search_id="search1",
        owner_id="owner1",
        session_id="session1",
        identity=identity(),
        currency="INR",
        delivery_context="201001",
        now=now,
        stores=tuple(
            Store(
                store_id=name,
                origin=f"https://{name}.example",
                search_path="/search",
                query_parameter="q",
            )
            for name in ("a", "b")
        ),
    )


def receipt(state, index, now, **updates):
    data = dict(
        offer_id=f"offer{index}",
        snapshot_id=f"snap{index}",
        identity=identity(),
        url=f"https://{'a' if index == 0 else 'b'}.example/product/1",
        seller="Seller",
        observed_at=now,
        in_stock=True,
        price="100.00",
        currency="INR",
        shipping="0",
        extra_tax_and_fees="0",
    )
    return SearchReceipt(
        search_id=state.search_id,
        owner_id=state.owner_id,
        session_id=state.session_id,
        task_id=state.tasks[index].task_id,
        delivery_context=state.delivery_context,
        status="complete",
        offers=(Offer.model_validate(data | updates),),
    )


def accept(state, item, **updates):
    return accept_search_receipt(
        state,
        item,
        **(
            dict(
                owner_id=state.owner_id,
                session_id=state.session_id,
                expected_version=state.version,
                now=state.created_at,
            )
            | updates
        ),
    )


def test_rank_total_not_advertised_price_and_keep_ties(now):
    state = plan(now)
    state = accept(state, receipt(state, 0, now, price="80", shipping="30"))
    assert not rank_offers(state, now=now, config=AIConfig()).cheapest_offer_ids
    state = accept(state, receipt(state, 1, now, price="100"))
    result = rank_offers(state, now=now, config=AIConfig())
    assert result.status == "complete"
    assert result.cheapest_offer_ids == ("offer1",)
    assert result.ranked[0].total == Decimal("100")
    assert result.ranked[0].offer.url.endswith("/product/1")
    assert "+ delivery" not in result.speech_text.lower()
    assert "verified total" in result.speech_text


@pytest.mark.parametrize(
    "updates",
    [
        {"shipping": None},
        {"extra_tax_and_fees": None},
        {"in_stock": None},
        {"observed_at": "2020-01-01T00:00:00Z"},
        {"currency": "USD"},
    ],
)
def test_unknown_competitor_prevents_cheapest_claim(now, updates):
    state = plan(now)
    state = accept(state, receipt(state, 0, now, **updates))
    state = accept(state, receipt(state, 1, now))
    result = rank_offers(state, now=now, config=AIConfig())
    assert result.status == "partial"
    assert not result.cheapest_offer_ids


def test_wrong_variant_and_out_of_stock_do_not_win(now):
    for updates in (
        {"identity": identity(attributes={"storage": "256GB", "colour": "black"})},
        {"in_stock": False},
    ):
        state = plan(now)
        state = accept(state, receipt(state, 0, now, price="1", **updates))
        state = accept(state, receipt(state, 1, now))
        result = rank_offers(state, now=now, config=AIConfig())
        assert result.cheapest_offer_ids == ("offer1",)
        assert result.excluded_offer_ids == ("offer0",)


def test_equal_totals_keep_both_and_ignore_conditional_coupon(now):
    state = plan(now)
    state = accept(state, receipt(state, 0, now, conditional_discount_note="Bank card 20 off"))
    state = accept(state, receipt(state, 1, now))
    result = rank_offers(state, now=now, config=AIConfig())
    assert result.cheapest_offer_ids == ("offer0", "offer1")
    assert result.ranked[0].total == 100


def test_failed_store_is_explicit_partial(now):
    state = plan(now)
    state = accept(state, receipt(state, 0, now))
    state = accept(
        state,
        SearchReceipt(
            search_id=state.search_id,
            owner_id=state.owner_id,
            session_id=state.session_id,
            task_id=state.tasks[1].task_id,
            delivery_context=state.delivery_context,
            status="blocked",
        ),
    )
    result = rank_offers(state, now=now, config=AIConfig())
    assert result.status == "partial"
    assert result.unresolved_store_ids == ("b",)
    assert not result.cheapest_offer_ids


def test_receipt_binding_replay_and_version(now):
    state = plan(now)
    item = receipt(state, 0, now)
    for updates in ({"owner_id": "other"}, {"session_id": "other"}, {"expected_version": 1}):
        with pytest.raises(ValueError):
            accept(state, item, **updates)
    for updates in ({"search_id": "other"}, {"task_id": "other"}, {"delivery_context": "other"}):
        with pytest.raises(ValueError):
            accept(state, SearchReceipt.model_validate(item.model_dump() | updates))
    state = accept(state, item)
    with pytest.raises(ValueError, match="DUPLICATE_SEARCH_RECEIPT"):
        accept(state, item)


@pytest.mark.parametrize(
    "url",
    [
        "https://a.example.evil.test/item",
        "https://a.example@evil.test/item",
        "http://a.example/item",
        "javascript:alert(1)",
    ],
)
def test_offer_links_cannot_escape_store(now, url):
    state = plan(now)
    with pytest.raises(ValueError, match="OFFER_ORIGIN_MISMATCH"):
        accept(state, receipt(state, 0, now, url=url))


def test_future_prices_and_expired_prices_do_not_win(now):
    state = plan(now)
    state = accept(state, receipt(state, 0, now + timedelta(seconds=5)))
    state = accept(state, receipt(state, 1, now))
    assert not rank_offers(state, now=now, config=AIConfig()).cheapest_offer_ids
    assert not rank_offers(state, now=now + timedelta(days=1), config=AIConfig()).ranked


def test_search_encodes_query_and_does_not_mutate_caller_identity(now):
    state = plan(now)
    assert state.tasks[0].visible is True
    assert state.tasks[0].deadline_at == state.deadline_at
    assert state.tasks[0].url == "https://a.example/search?q=Example+Phone+1+128GB+black"
    assert identity(brand=" EXAMPLE ").matches(identity())
    assert not identity(attributes={"storage": "128GB"}).matches(identity())


@pytest.mark.parametrize(
    "message",
    [
        "find cheapest across websites",
        "amazon flipkart compare",
        "websites par sasta search kro",
    ],
)
def test_cross_store_requests_are_not_saved_product_comparison(message):
    result = asyncio.run(BasicInterpreter().interpret(message, None))
    assert result.intent == "market_search"


def dialogue(now):
    from shopping_ai import Conversation, Orchestrator, Principal, ShoppingAI
    from shopping_ai.providers import MockProvider

    conversation = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    principal = Principal(owner_id="owner1", session_id="session1")
    return conversation, principal, conversation.start(principal, language="en").state


def begin(conversation, principal, state, now):
    return conversation.begin_market_search(
        state,
        principal,
        identity=identity(),
        delivery_context="201001",
        stores=tuple(task.store for task in plan(now).tasks),
        expected_version=state.version,
    )


def test_dialogue_search_plan_to_final_offer_reply(now):
    conversation, principal, state = dialogue(now)
    reply = begin(conversation, principal, state, now)
    assert reply.status == "ready_for_search" and len(reply.search_tasks) == 2
    assert reply.action is None and reply.market_result is None
    first = receipt(reply.state.active_market, 0, now, shipping="50")
    progress = conversation.accept_market_receipt(
        reply.state,
        principal,
        first,
        expected_version=reply.state.version,
    )
    assert progress.status == "working" and not progress.search_tasks
    assert not progress.market_result.cheapest_offer_ids
    second = receipt(progress.state.active_market, 1, now)
    final = conversation.accept_market_receipt(
        progress.state,
        principal,
        second,
        expected_version=progress.state.version,
    )
    assert final.status == "complete" and not final.search_tasks
    assert final.market_result.cheapest_offer_ids == ("offer1",)
    assert final.speech_text == final.market_result.speech_text
    assert final.speech_text != final.message
    assert len(final.state.history) <= 12


def test_dialogue_market_cannot_be_overwritten_or_replayed(now):
    from shopping_ai import ConversationTurn, Principal
    from shopping_ai.conversation import ConversationError

    conversation, principal, state = dialogue(now)
    reply = begin(conversation, principal, state, now)
    with pytest.raises(ConversationError, match="ACTIVE_MARKET_SEARCH"):
        begin(conversation, principal, reply.state, now)
    with pytest.raises(ConversationError, match="STALE_CONVERSATION_VERSION"):
        conversation.accept_market_receipt(
            reply.state,
            principal,
            receipt(reply.state.active_market, 0, now),
            expected_version=state.version,
        )
    with pytest.raises(ConversationError, match="ACCESS_DENIED"):
        conversation.accept_market_receipt(
            reply.state,
            Principal(owner_id="other", session_id="session1"),
            receipt(reply.state.active_market, 0, now),
            expected_version=reply.state.version,
        )
    paused = asyncio.run(
        conversation.respond(
            reply.state,
            principal,
            ConversationTurn(request_id="turn1", message="add to cart"),
            expected_version=reply.state.version,
        )
    )
    assert paused.status == "paused" and paused.state.active_market == reply.state.active_market
    assert not paused.search_tasks and paused.action is None
    stopped = asyncio.run(
        conversation.respond(
            paused.state,
            principal,
            ConversationTurn(request_id="turn2", message="stop"),
            expected_version=paused.state.version,
        )
    )
    assert stopped.status == "cancelled" and stopped.state.active_market is None
    with pytest.raises(ConversationError, match="NO_ACTIVE_MARKET_SEARCH"):
        conversation.accept_market_receipt(
            stopped.state,
            principal,
            receipt(reply.state.active_market, 0, now),
            expected_version=stopped.state.version,
        )


def test_new_search_rejects_late_receipt_from_completed_search(now):
    conversation, principal, state = dialogue(now)
    reply = begin(conversation, principal, state, now)
    old_receipt = receipt(reply.state.active_market, 0, now)
    for index in range(2):
        reply = conversation.accept_market_receipt(
            reply.state,
            principal,
            receipt(reply.state.active_market, index, now),
            expected_version=reply.state.version,
        )
    restarted = begin(conversation, principal, reply.state, now)
    with pytest.raises(ValueError, match="RECEIPT_BINDING_MISMATCH"):
        conversation.accept_market_receipt(
            restarted.state,
            principal,
            old_receipt,
            expected_version=restarted.state.version,
        )


def test_cheapest_followup_refreshes_market_evidence_without_redispatch(now):
    from shopping_ai import ConversationTurn

    conversation, principal, state = dialogue(now)
    reply = begin(conversation, principal, state, now)
    for index in range(2):
        reply = conversation.accept_market_receipt(
            reply.state,
            principal,
            receipt(reply.state.active_market, index, now),
            expected_version=reply.state.version,
        )
    followup = asyncio.run(
        conversation.respond(
            reply.state,
            principal,
            ConversationTurn(request_id="followup", message="kaunsa sasta hai"),
            expected_version=reply.state.version,
        )
    )
    assert followup.market_result.cheapest_offer_ids == ("offer0", "offer1")
    assert followup.comparison is None and not followup.search_tasks and followup.action is None
    conversation.orchestrator.ai.clock = lambda: now + timedelta(days=1)
    expired = asyncio.run(
        conversation.respond(
            followup.state,
            principal,
            ConversationTurn(request_id="expired", message="cheapest"),
            expected_version=followup.state.version,
        )
    )
    assert not expired.market_result.cheapest_offer_ids
    assert not expired.market_result.ranked and not expired.search_tasks


def test_deadline_settlement_preserves_received_offers_and_rejects_late_results(now):
    from shopping_ai.market import expire_search

    state = plan(now)
    state = accept(state, receipt(state, 0, now))
    with pytest.raises(ValueError, match="SEARCH_NOT_EXPIRED"):
        expire_search(
            state,
            owner_id=state.owner_id,
            session_id=state.session_id,
            expected_version=state.version,
            now=state.deadline_at - timedelta(microseconds=1),
        )
    with pytest.raises(ValueError, match="SEARCH_EXPIRED"):
        accept(state, receipt(state, 1, now), now=state.deadline_at)
    # Ranking alone must never call an overdue search still 'searching'.
    overdue = rank_offers(state, now=state.deadline_at, config=AIConfig())
    assert overdue.status == "partial" and overdue.unresolved_store_ids == ("b",)
    settled = expire_search(
        state,
        owner_id=state.owner_id,
        session_id=state.session_id,
        expected_version=state.version,
        now=state.deadline_at,
    )
    assert settled.receipts[0] == state.receipts[0]
    assert settled.receipts[1].status == "timed_out"
    assert settled.version == state.version + 1
    result = rank_offers(settled, now=state.deadline_at, config=AIConfig())
    assert result.ranked[0].offer.offer_id == "offer0"
    assert not result.cheapest_offer_ids and "b" in result.message
    repeated = expire_search(
        settled,
        owner_id=state.owner_id,
        session_id=state.session_id,
        expected_version=settled.version,
        now=state.deadline_at,
    )
    assert repeated == settled


def test_expiration_checks_owner_version_and_clock(now):
    from shopping_ai.market import expire_search

    state = plan(now)
    kwargs = dict(
        owner_id=state.owner_id,
        session_id=state.session_id,
        expected_version=state.version,
        now=state.deadline_at,
    )
    for updates, code in (
        ({"owner_id": "other"}, "SEARCH_OWNER_MISMATCH"),
        ({"session_id": "other"}, "SEARCH_OWNER_MISMATCH"),
        ({"expected_version": 1}, "SEARCH_VERSION_CONFLICT"),
        ({"now": now.replace(tzinfo=None)}, "Invalid search clock"),
    ):
        with pytest.raises(ValueError, match=code):
            expire_search(state, **(kwargs | updates))


def test_conversation_settles_expired_search_and_can_start_again(now):
    from shopping_ai import ConversationTurn

    conversation, principal, state = dialogue(now)
    reply = begin(conversation, principal, state, now)
    conversation.orchestrator.ai.clock = lambda: reply.state.active_market.deadline_at
    expired = conversation.expire_market_search(
        reply.state, principal, expected_version=reply.state.version
    )
    assert expired.status == "complete" and expired.market_result.status == "partial"
    assert all(r.status == "timed_out" for r in expired.state.active_market.receipts)
    assert not expired.search_tasks and expired.action is None
    # A normal turn also settles the expired checkpoint without a timer callback.
    followup = asyncio.run(
        conversation.respond(
            reply.state,
            principal,
            ConversationTurn(
                request_id="overdue",
                message="cheapest",
            ),
            expected_version=reply.state.version,
        )
    )
    assert followup.market_result.status == "partial"
    assert len(followup.state.active_market.receipts) == 2
    old_id = followup.state.active_market.search_id
    restarted = begin(conversation, principal, followup.state, now)
    assert restarted.state.active_market.search_id != old_id
    assert len(restarted.search_tasks) == 2


def test_timeout_config_and_checkpoint_binding_are_validated(now):
    from shopping_ai.market import MarketSearch

    state = plan(now)
    for update in (
        {"deadline_at": now},
        {"deadline_at": now + timedelta(seconds=601)},
        {"tasks": (state.tasks[0], state.tasks[0])},
        {
            "receipts": (
                SearchReceipt.model_validate(
                    receipt(state, 0, now).model_dump()
                    | {
                        "owner_id": "other",
                    }
                ),
            )
        },
        {"receipts": (receipt(state, 0, now), receipt(state, 0, now))},
    ):
        with pytest.raises(ValueError):
            MarketSearch.model_validate(state.model_dump() | update)
    for value in (0, 601, True, 1.5):
        with pytest.raises(ValueError):
            AIConfig(search_timeout_seconds=value)


def test_timeout_status_cannot_be_injected_as_browser_receipt(now):
    state = plan(now)
    item = SearchReceipt.model_validate(
        receipt(state, 0, now).model_dump()
        | {
            "status": "timed_out",
            "offers": (),
        }
    )
    with pytest.raises(ValueError, match="only by expire_search"):
        accept(state, item)


def test_hinglish_speech_keeps_partial_coverage_and_discount_limits(now):
    state = plan(now)
    state = accept(state, receipt(state, 0, now))
    result = rank_offers(state, now=now, config=AIConfig(), language="hi-Latn")
    assert "cheapest confirm nahi hai" in result.speech_text
    assert "discounts deduct nahi kiye" in result.speech_text
    assert "complete results nahi mile" in result.speech_text
    assert not result.cheapest_offer_ids
    assert "+ delivery" in result.message and "+ delivery" not in result.speech_text


def test_many_ties_have_bounded_display_and_speech_without_losing_offers(now):
    state = plan_search(
        search_id="many",
        owner_id="owner1",
        session_id="session1",
        identity=identity(),
        currency="INR",
        delivery_context="201001",
        now=now,
        stores=tuple(
            Store(
                store_id=f"store{i}" + "x" * 94,
                origin=f"https://s{i}.example",
                search_path="/search",
                query_parameter="q",
            )
            for i in range(6)
        ),
    )
    base = receipt(plan(now), 0, now).offers[0]
    for site, task in enumerate(state.tasks):
        offers = tuple(
            Offer.model_validate(
                base.model_dump()
                | {
                    "offer_id": f"o{site}_{i}",
                    "snapshot_id": f"s{site}_{i}",
                    "url": task.store.origin + f"/product/{i}",
                }
            )
            for i in range(20)
        )
        state = accept(
            state,
            SearchReceipt(
                search_id=state.search_id,
                owner_id=state.owner_id,
                session_id=state.session_id,
                task_id=task.task_id,
                delivery_context=state.delivery_context,
                status="complete",
                offers=offers,
            ),
        )
    result = rank_offers(state, now=now, config=AIConfig())
    assert len(result.ranked) == len(result.cheapest_offer_ids) == 120
    assert len(result.speech_text) < 1500 and len(result.message) < 4096
    assert "120 offers" in result.speech_text
    for task in state.tasks:
        assert result.speech_text.count(task.store.store_id) == 1


def test_market_summary_is_the_text_sent_to_speech_adapter(now):
    from shopping_ai.voice import AudioClip, VoiceConversation

    conversation, principal, state = dialogue(now)
    reply = begin(conversation, principal, state, now)
    for index in range(2):
        reply = conversation.accept_market_receipt(
            reply.state,
            principal,
            receipt(reply.state.active_market, index, now),
            expected_version=reply.state.version,
        )
    spoken = []

    class Speech:
        async def transcribe(self, clip, *, language):
            pytest.fail("Speaking an existing reply must not run STT")

        async def synthesize(self, text, *, language):
            spoken.append(text)
            return AudioClip(format="wav", data=b"fake")

    speech = Speech()
    result = asyncio.run(VoiceConversation(conversation, speech, speech).speak(reply, principal))
    assert result.status == "complete"
    assert spoken == [reply.market_result.speech_text]
    assert spoken[0] != reply.message and "https://" not in spoken[0]
    assert reply.market_result.ranked[0].offer.url.startswith("https://")
