import asyncio
from datetime import timedelta

import pytest

from shopping_ai import Conversation, ConversationTurn, Orchestrator, Principal, ShoppingAI
from shopping_ai.conversation import ConversationReply
from shopping_ai.intents import BasicInterpreter
from shopping_ai.market import Offer, ProductIdentity, SearchReceipt, Store
from shopping_ai.market_answers import MarketAnswer, answer_market
from shopping_ai.providers import MockProvider


@pytest.fixture
def setup(now):
    conversation = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    principal = Principal(owner_id="owner", session_id="session")
    state = conversation.start(principal, language="en").state
    identity = ProductIdentity(brand="Example", model="One", attributes={"colour": "black"})
    reply = conversation.begin_market_search(
        state,
        principal,
        identity=identity,
        delivery_context="201001",
        expected_version=state.version,
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
    for index, task in enumerate(reply.search_tasks):
        item = SearchReceipt(
            search_id=reply.state.active_market.search_id,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            task_id=task.task_id,
            delivery_context="201001",
            status="complete",
            offers=(
                Offer(
                    offer_id=f"offer{index}",
                    snapshot_id=f"snap{index}",
                    identity=identity,
                    url=task.store.origin + "/one",
                    seller="Seller",
                    observed_at=now,
                    in_stock=True,
                    price="100",
                    currency="INR",
                    shipping=None if index == 0 else "0",
                    extra_tax_and_fees="0",
                ),
            ),
        )
        reply = conversation.accept_market_receipt(
            reply.state, principal, item, expected_version=reply.state.version
        )
    return conversation, principal, reply.state


def say(conversation, principal, state, message):
    return asyncio.run(
        conversation.respond(
            state,
            principal,
            ConversationTurn(
                request_id=f"turn{state.version}",
                message=message,
            ),
            expected_version=state.version,
        )
    )


@pytest.mark.parametrize(
    "message,question",
    [
        ("delivery charge kitna hai", "costs"),
        ("shipping breakdown", "costs"),
        ("why cheapest not confirmed", "coverage"),
        ("sasta confirm kyu nahi hua", "coverage"),
        ("why offer excluded", "exclusions"),
    ],
)
def test_followups_are_grounded_without_new_browser_dispatch(setup, message, question):
    conversation, principal, state = setup
    result = say(conversation, principal, state, message)
    assert result.market_answer.question == question
    assert result.market_answer.search_id == state.active_market.search_id
    assert result.speech_text == result.market_answer.speech_text
    assert result.action is None and result.navigation is None and not result.search_tasks
    assert result.state.active_market == state.active_market
    assert not result.market_result.cheapest_offer_ids


def test_unknown_shipping_is_not_zero_and_sources_stay_exact(setup):
    conversation, principal, state = setup
    result = say(conversation, principal, state, "shipping breakdown")
    assert "delivery unknown" in result.message
    assert "total unknown" in result.message
    assert "delivery 0.00" in result.message  # other store explicitly observed free
    assert result.market_answer.source_offers == tuple(
        offer for receipt in state.active_market.receipts for offer in receipt.offers
    )
    assert "unknown" in result.speech_text


def test_exclusion_explanation_names_the_actual_missing_evidence(setup):
    conversation, principal, state = setup
    result = say(conversation, principal, state, "why offer excluded")
    assert "offer0" in result.message and "delivery/tax/fees are unobserved" in result.message
    assert result.market_answer.source_offers[0].snapshot_id == "snap0"
    assert "unknown_total_cost" in result.market_result.exclusions[0].reasons


def test_stale_costs_require_fresh_evidence(setup, now):
    conversation, principal, state = setup
    conversation.orchestrator.ai.clock = lambda: now + timedelta(days=1)
    result = say(conversation, principal, state, "shipping breakdown")
    assert result.status == "needs_input"
    assert not result.market_answer.source_offers
    assert "100.00" not in result.message and not result.market_result.ranked


def test_invalid_or_previous_reference_does_not_guess_offer(setup):
    conversation, principal, state = setup
    for message in ("third delivery charge", "previous shipping breakdown"):
        result = say(conversation, principal, state, message)
        assert result.status == "needs_input" and result.market_answer is None


def test_reply_contract_rejects_invented_source_costs(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "shipping breakdown")
    original = reply.market_answer.source_offers[0]
    invented = Offer.model_validate(original.model_dump() | {"shipping": "0"})
    answer = MarketAnswer.model_validate(
        reply.market_answer.model_dump() | {"source_offers": (invented,)}
    )
    with pytest.raises(ValueError, match="exact observations"):
        ConversationReply.model_validate(reply.model_dump() | {"market_answer": answer})


def test_hinglish_coverage_explains_unknown_costs(setup, now):
    conversation, _, state = setup
    result, answer = answer_market(
        state.active_market,
        "coverage",
        now=now,
        config=conversation.orchestrator.ai.config,
        language="hi-Latn",
    )
    assert "cheapest confirm nahi hai" in answer.message
    assert "delivery/tax/fees observe nahi hue" in answer.speech_text
    assert not result.cheapest_offer_ids


def test_compound_cost_and_cart_goal_does_not_drop_action_part():
    meaning = asyncio.run(BasicInterpreter().interpret("shipping batao and add to cart", None))
    assert meaning.intent == "unknown"
