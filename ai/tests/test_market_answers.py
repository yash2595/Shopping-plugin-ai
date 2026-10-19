import asyncio
from datetime import timedelta
from decimal import Decimal

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


def known_totals(state, *, shipping="20", currency="INR", stock=True):
    """Create trusted normalized observations with complete comparative costs."""
    market = state.active_market
    data = market.model_dump()
    data["receipts"][0]["offers"][0].update(shipping=shipping, currency=currency, in_stock=stock)
    return state.model_copy(update={"active_market": type(market).model_validate(data)})


@pytest.mark.parametrize("message", ["how much savings", "kitni bachat hogi", "kitna bachega"])
def test_savings_use_delivered_totals_and_exact_sources(setup, message):
    conversation, principal, state = setup
    state = known_totals(state)
    reply = say(conversation, principal, state, message)
    answer = reply.market_answer
    assert answer.question == "savings" and answer.savings_amount == Decimal("20")
    assert [offer.offer_id for offer in answer.source_offers] == ["offer1", "offer0"]
    assert "INR 100.00" in answer.message and "INR 120.00" in answer.message
    assert "INR 20.00" in reply.speech_text
    assert "not guaranteed" in reply.speech_text
    assert not reply.search_tasks and reply.navigation is None and reply.action is None
    assert reply.state.active_market == state.active_market
    assert reply.state.market_display_ids == ("offer1", "offer0")


@pytest.mark.parametrize("changes", [{"shipping": None}, {"currency": "USD"}, {"stock": False}])
def test_savings_require_two_eligible_complete_same_currency_totals(setup, changes):
    conversation, principal, state = setup
    reply = say(conversation, principal, known_totals(state, **changes), "how much savings")
    assert reply.status == "needs_input"
    assert reply.market_answer.savings_amount is None
    assert not reply.market_answer.source_offers


def test_savings_ties_are_zero_and_stale_evidence_never_reused(setup, now):
    conversation, principal, state = setup
    state = known_totals(state, shipping="0")
    reply = say(conversation, principal, state, "savings")
    assert reply.market_answer.savings_amount == Decimal("0")
    conversation.orchestrator.ai.clock = lambda: now + timedelta(days=1)
    stale = say(conversation, principal, reply.state, "savings")
    assert stale.market_answer.savings_amount is None and stale.status == "needs_input"


def test_savings_contract_rejects_invented_difference(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, known_totals(state), "savings")
    with pytest.raises(ValueError, match="exact observed total difference"):
        MarketAnswer.model_validate(reply.market_answer.model_dump() | {"savings_amount": "99"})


def test_savings_does_not_drop_cart_action_or_negation():
    for message in ("savings batao and add to cart", "don't calculate savings"):
        assert asyncio.run(BasicInterpreter().interpret(message, None)).intent == "unknown"


def test_savings_respect_budget_and_displayed_ordinal(setup):
    conversation, principal, state = setup
    state = known_totals(state)
    first = say(conversation, principal, state, "savings")
    ordinal = say(conversation, principal, first.state, "second savings")
    assert ordinal.market_answer.savings_amount == Decimal("20")
    limited = state.active_market.model_copy(update={"max_total": Decimal("110")})
    reply = say(
        conversation, principal, state.model_copy(update={"active_market": limited}), "savings"
    )
    assert reply.status == "needs_input" and reply.market_answer.savings_amount is None


def test_partial_savings_are_scoped_and_do_not_dispatch_pending_work(setup):
    conversation, principal, state = setup
    state = known_totals(state)
    market = state.active_market
    task = market.tasks[0].model_dump()
    task.update(
        task_id="third",
        url="https://c.example/search?q=one",
        store=Store(
            store_id="c", origin="https://c.example", search_path="/search", query_parameter="q"
        ),
    )
    data = market.model_dump()
    data["tasks"] = (*data["tasks"], task)
    partial = state.model_copy(update={"active_market": type(market).model_validate(data)})
    reply = say(conversation, principal, partial, "savings")
    assert reply.status == "chat" and reply.market_answer.savings_amount == Decimal("20")
    assert not reply.market_result.cheapest_offer_ids
    assert "cheapest offer is not confirmed" in reply.speech_text
    assert reply.market_result.unresolved_store_ids == ("c",)
    assert not reply.search_tasks and reply.action is None and reply.navigation is None
    assert reply.state.active_market == partial.active_market


def test_explicit_pair_comparison_uses_displayed_ids_not_global_winner(setup):
    conversation, principal, state = setup
    state = known_totals(state)
    shown = say(conversation, principal, state, "savings")
    data = shown.state.active_market.model_dump()
    extra = data["receipts"][1]["offers"][0] | {
        "offer_id": "extra",
        "snapshot_id": "extra-snapshot",
        "price": "50",
    }
    data["receipts"][1]["offers"] = (*data["receipts"][1]["offers"], extra)
    updated = shown.state.model_copy(
        update={
            "active_market": type(state.active_market).model_validate(data),
        }
    )
    reply = say(conversation, principal, updated, "compare first and second")
    assert reply.market_answer.question == "comparison"
    assert reply.market_answer.savings_amount == Decimal("20")
    assert [o.offer_id for o in reply.market_answer.source_offers] == ["offer1", "offer0"]
    assert reply.market_result.cheapest_offer_ids == ("extra",)
    assert "delivery 20.00" in reply.message
    assert not reply.search_tasks and reply.navigation is None and reply.action is None


@pytest.mark.parametrize("expired", [False, True])
def test_pair_comparison_never_substitutes_for_ineligible_requested_offer(setup, now, expired):
    conversation, principal, state = setup
    shown = say(conversation, principal, known_totals(state), "savings")
    if expired:
        conversation.orchestrator.ai.clock = lambda: now + timedelta(days=1)
    else:
        market = shown.state.active_market.model_copy(update={"max_total": Decimal("110")})
        shown = shown.model_copy(
            update={"state": shown.state.model_copy(update={"active_market": market})}
        )
    reply = say(conversation, principal, shown.state, "compare first and second")
    assert reply.status == "needs_input"
    assert reply.market_answer.savings_amount is None
    assert not reply.market_answer.source_offers and not reply.search_tasks


def test_pair_comparison_requires_displayed_indices(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "compare first and third")
    assert reply.status == "needs_input" and reply.market_answer is None
    assert reply.comparison is None and not reply.search_tasks


def test_pair_answer_rejects_duplicate_or_missing_ids(setup, now):
    conversation, _, state = setup
    for pair in (None, ("offer0", "offer0")):
        with pytest.raises(ValueError, match="two distinct explicit"):
            answer_market(
                state.active_market,
                "comparison",
                pair_ids=pair,
                now=now,
                config=conversation.orchestrator.ai.config,
                language="en",
            )


def test_pair_comparison_does_not_discard_a_compound_action():
    for message in (
        "compare first and second and add to cart",
        "compare first and second and open",
    ):
        assert asyncio.run(BasicInterpreter().interpret(message, None)).intent == "unknown"


@pytest.mark.parametrize(
    "message", ["seller kaun hai", "which offers are in stock", "store availability"]
)
def test_seller_stock_answers_bind_observations_without_actions(setup, message):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, message)
    assert reply.market_answer.question == "availability"
    assert len(reply.market_answer.source_offers) == 2
    assert 'seller "Seller"' in reply.message and "observed stock: in stock" in reply.message
    assert "checkout availability are not verified" in reply.speech_text
    assert reply.state.market_display_ids == ("offer0", "offer1")
    assert not reply.search_tasks and reply.action is None and reply.navigation is None
    assert reply.state.active_market == state.active_market


@pytest.mark.parametrize("stock,label", [(None, "unknown"), (False, "out of stock")])
def test_unknown_and_unavailable_stock_are_not_reported_available(setup, stock, label):
    conversation, principal, state = setup
    state = known_totals(state, stock=stock)
    reply = say(conversation, principal, state, "offer stock")
    assert f'offer0): seller "Seller", observed stock: {label}' in reply.message
    assert reply.market_answer.source_offers[0].in_stock is stock
    assert "offer0" in reply.market_result.excluded_offer_ids


def test_stock_reference_is_saved_ordinal_and_stale_evidence_requires_refresh(setup, now):
    conversation, principal, state = setup
    shown = say(conversation, principal, known_totals(state), "savings")
    reply = say(conversation, principal, shown.state, "second seller")
    assert [o.offer_id for o in reply.market_answer.source_offers] == ["offer0"]
    conversation.orchestrator.ai.clock = lambda: now + timedelta(days=1)
    expired = say(conversation, principal, reply.state, "seller kaun hai")
    assert expired.status == "needs_input" and not expired.market_answer.source_offers
    assert 'seller "Seller"' not in expired.message


def test_plain_product_stock_and_compound_actions_are_not_market_stock_questions():
    assert (
        asyncio.run(BasicInterpreter().interpret("stock kitna hai", None)).intent
        == "product_question"
    )
    for message in ("seller batao and add to cart", "open first offer stock"):
        assert asyncio.run(BasicInterpreter().interpret(message, None)).intent == "unknown"


def test_long_untrusted_seller_label_keeps_speech_bounded_and_source_intact(setup):
    conversation, principal, state = setup
    data = state.active_market.model_dump()
    seller = "\x01" * 500
    data["receipts"][0]["offers"][0]["seller"] = seller
    market = type(state.active_market).model_validate(data)
    reply = say(
        conversation, principal, state.model_copy(update={"active_market": market}), "seller"
    )
    assert reply.market_answer.source_offers[0].seller == seller
    assert len(reply.speech_text) <= 1500 and "…" in reply.message
    assert "\\u0001" in reply.message


@pytest.mark.parametrize(
    "message,patch",
    [
        ("blue", {"colour": "blue"}),
        ("shoes", {"category": "shoes"}),
        ("size UK 9", {"size": "UK 9"}),
    ],
)
def test_product_preference_change_clears_previous_market_and_product_references(
    setup, product, message, patch
):
    from shopping_ai.intents import Meaning, Requirements

    class NewPreference:
        async def interpret(self, message, awaiting):
            return Meaning(intent="requirements", slots=Requirements(**patch))

    conversation, principal, state = setup
    state = state.model_copy(
        update={
            "requirements": Requirements(category="phone", colour="black", budget="200"),
            "products": (product,),
            "selected_id": product.product_id,
        }
    )
    conversation.interpreter = NewPreference()
    reply = say(conversation, principal, state, message)
    assert reply.status == "needs_input"
    assert reply.state.active_market is None and not reply.state.market_display_ids
    assert not reply.state.products and reply.state.selected_id is None
    assert reply.state.requirements.budget == Decimal("200")
    assert all(getattr(reply.state.requirements, key) == value for key, value in patch.items())
    assert reply.market_result is None and not reply.search_tasks and reply.action is None
    conversation.interpreter = BasicInterpreter()
    refresh = say(conversation, principal, reply.state, "refresh prices")
    assert refresh.status == "needs_input" and not refresh.search_tasks


def test_unchanged_colour_from_exact_identity_keeps_saved_offers(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "black")
    assert reply.state.active_market == state.active_market
    assert reply.state.requirements.colour == "black"
    assert not reply.search_tasks


def test_readonly_question_cannot_change_preference_during_pending_search(setup):
    conversation, principal, state = setup
    data = state.active_market.model_dump()
    data["receipts"] = data["receipts"][:1]
    pending = state.model_copy(
        update={"active_market": type(state.active_market).model_validate(data)}
    )
    reply = say(conversation, principal, pending, "blue price batao")
    assert reply.status == "paused" and reply.action is None
    assert reply.state.requirements == pending.requirements
    assert reply.state.active_market == pending.active_market and not reply.search_tasks


def test_explicit_variant_selection_retires_ranking_but_keeps_verified_page_action(
    setup, product, grant
):
    from shopping_ai.browser import BrowserTarget

    conversation, principal, state = setup
    state = state.model_copy(update={"products": (product,), "selected_id": product.product_id})
    reply = asyncio.run(
        conversation.respond(
            state,
            principal,
            ConversationTurn(
                request_id="r1",
                message="select blue",
                products=(product,),
                target=BrowserTarget(
                    tab_id=1, document_id=product.document_id, active=True, window_focused=True
                ),
                available_tools=("read_product", "select_variant"),
                authorization=grant.model_copy(update={"variant_id": "blue"}),
            ),
            expected_version=state.version,
        )
    )
    assert reply.state.active_market is None and not reply.state.market_display_ids
    assert reply.state.products == (product,)
    assert reply.action is not None and reply.action.tool.name == "select_variant"
