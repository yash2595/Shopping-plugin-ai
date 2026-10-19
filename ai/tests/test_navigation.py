import asyncio
from decimal import Decimal

import pytest

from shopping_ai import Conversation, ConversationTurn, Orchestrator, Principal, ShoppingAI
from shopping_ai.browser import BrowserTarget
from shopping_ai.market import Offer, ProductIdentity, SearchReceipt, Store
from shopping_ai.models import ProductSnapshot
from shopping_ai.navigation import NavigationReceipt
from shopping_ai.providers import MockProvider


@pytest.fixture
def setup(now):
    dialogue = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    principal = Principal(owner_id="owner", session_id="session")
    identity = ProductIdentity(brand="Example", model="One", attributes={"colour": "black"})
    state = dialogue.start(principal, language="en").state
    reply = dialogue.begin_market_search(
        state,
        principal,
        identity=identity,
        delivery_context="201001",
        stores=tuple(
            Store(
                store_id=name,
                origin=f"https://{name}.example",
                search_path="/search",
                query_parameter="q",
            )
            for name in ("a", "b")
        ),
        expected_version=state.version,
    )
    for index, task in enumerate(reply.search_tasks):
        receipt = SearchReceipt(
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
                    url=task.store.origin + "/product/one",
                    seller="seller",
                    observed_at=now,
                    in_stock=True,
                    price=str(100 + index),
                    currency="INR",
                    shipping="0",
                    extra_tax_and_fees="0",
                ),
            ),
        )
        reply = dialogue.accept_market_receipt(
            reply.state, principal, receipt, expected_version=reply.state.version
        )
    target = BrowserTarget(tab_id=7, document_id="source", active=True, window_focused=True)
    return dialogue, principal, reply.state, target


def say(dialogue, principal, state, target, message="open cheapest"):
    return asyncio.run(
        dialogue.respond(
            state,
            principal,
            ConversationTurn(
                request_id=f"turn{state.version}",
                message=message,
                target=target,
            ),
            expected_version=state.version,
        )
    )


def receipt(action, now, **updates):
    product = ProductSnapshot(
        snapshot_id="opened",
        product_id="one",
        document_id="newdoc",
        title="Example One",
        observed_at=now,
        price="100",
        currency="INR",
        in_stock=True,
    )
    return NavigationReceipt.model_validate(
        dict(
            action_id=action.action_id,
            search_id=action.search_id,
            owner_id=action.owner_id,
            session_id=action.session_id,
            status="opened",
            observed_at=now,
            url=action.offer.url,
            target=BrowserTarget(tab_id=7, document_id="newdoc", active=True, window_focused=True),
            identity=action.offer.identity,
            product=product,
        )
        | updates
    )


def accept(dialogue, principal, pending, item):
    return dialogue.accept_navigation_receipt(
        pending.state, principal, item, expected_version=pending.state.version
    )


def test_cheapest_open_proposal_to_verified_selected_product(setup, now):
    dialogue, principal, state, target = setup
    pending = say(dialogue, principal, state, target)
    assert pending.status == "ready_to_navigate"
    assert pending.navigation.offer.offer_id == "offer0"
    assert not pending.search_tasks and pending.action is None
    assert pending.state.selected_id is None
    verified = accept(dialogue, principal, pending, receipt(pending.navigation, now))
    assert verified.status == "complete" and verified.state.selected_id == "one"
    assert verified.state.products[-1].price == Decimal("100")
    assert verified.state.pending_navigation is None and verified.navigation is None
    assert verified.facts
    with pytest.raises(ValueError, match="NO_PENDING_NAVIGATION"):
        dialogue.accept_navigation_receipt(
            verified.state,
            principal,
            receipt(pending.navigation, now),
            expected_version=verified.state.version,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"owner_id": "other"},
        {"action_id": "other"},
        {"search_id": "other"},
        {"url": "https://evil.example/product"},
        {"target": BrowserTarget(tab_id=8, document_id="newdoc", active=True, window_focused=True)},
        {
            "target": BrowserTarget(
                tab_id=7, document_id="newdoc", active=False, window_focused=True
            )
        },
        {"identity": ProductIdentity(brand="Example", model="One", attributes={"colour": "blue"})},
    ],
)
def test_wrong_navigation_receipts_never_select_product(setup, now, updates):
    dialogue, principal, state, target = setup
    pending = say(dialogue, principal, state, target)
    with pytest.raises(ValueError):
        accept(dialogue, principal, pending, receipt(pending.navigation, now, **updates))
    assert pending.state.selected_id is None


def test_changed_price_invalidates_cached_store_ranking(setup, now):
    dialogue, principal, state, target = setup
    pending = say(dialogue, principal, state, target)
    item = receipt(pending.navigation, now)
    product = ProductSnapshot.model_validate(item.product.model_dump() | {"price": "200"})
    verified = accept(
        dialogue,
        principal,
        pending,
        NavigationReceipt.model_validate(item.model_dump() | {"product": product}),
    )
    assert verified.state.products[-1].price == 200
    assert verified.offer_verification.base_price_difference == Decimal("100")
    assert "Previous base price INR 100.00; current INR 200.00" in verified.message
    assert "increased: INR 100.00" in verified.speech_text
    assert verified.state.active_market.receipts[0].status == "failed"
    assert not verified.state.market_display_ids
    followup = say(dialogue, principal, verified.state, target, "cheapest")
    assert followup.market_result.status == "partial"
    assert not followup.market_result.cheapest_offer_ids


@pytest.mark.parametrize(
    "changes,delta,expected",
    [
        ({"price": "80"}, Decimal("-20"), "decreased: INR 20.00"),
        ({"currency": "USD"}, None, "current USD 100.00"),
        ({"price": None, "currency": None}, None, "current unknown"),
        ({"in_stock": False}, Decimal("0"), "current out of stock"),
        ({"in_stock": None}, Decimal("0"), "current unknown"),
    ],
)
def test_page_change_explanations_preserve_unknowns_and_do_not_convert_currency(
    setup, now, changes, delta, expected
):
    dialogue, principal, state, target = setup
    pending = say(dialogue, principal, state, target)
    item = receipt(pending.navigation, now)
    product = ProductSnapshot.model_validate(item.product.model_dump() | changes)
    verified = accept(
        dialogue,
        principal,
        pending,
        NavigationReceipt.model_validate(item.model_dump() | {"product": product}),
    )
    report = verified.offer_verification
    assert report.changed and report.base_price_difference == delta
    assert report.proposal == pending.navigation and report.product == product
    assert expected in verified.speech_text
    assert "fresh delivered total is not verified" in verified.speech_text
    assert not verified.search_tasks and verified.action is None
    assert verified.state.active_market.receipts[0].status == "failed"
    if changes.get("currency") == "USD":
        assert "increased" not in verified.message and "decreased" not in verified.message


def test_unchanged_page_report_roundtrips_and_rejects_unowned_snapshot(setup, now):
    from shopping_ai.conversation import ConversationReply

    dialogue, principal, state, target = setup
    pending = say(dialogue, principal, state, target)
    verified = accept(dialogue, principal, pending, receipt(pending.navigation, now))
    report = verified.offer_verification
    assert not report.changed and report.base_price_difference == 0
    assert report.explanation("en") == ""
    assert ConversationReply.model_validate_json(verified.model_dump_json()) == verified
    fake = report.model_dump()
    fake["product"]["snapshot_id"] = "unobserved"
    with pytest.raises(ValueError, match="owned search and selected page"):
        ConversationReply.model_validate(verified.model_dump() | {"offer_verification": fake})


def test_expired_hidden_and_cancelled_navigation_do_not_complete(setup, now):
    dialogue, principal, state, target = setup
    hidden = target.model_copy(update={"active": False})
    assert say(dialogue, principal, state, hidden).navigation is None
    pending = say(dialogue, principal, state, target)
    stopped = say(dialogue, principal, pending.state, target, "stop")
    assert stopped.state.pending_navigation is None
    with pytest.raises(ValueError, match="NO_PENDING_NAVIGATION"):
        accept(dialogue, principal, stopped, receipt(pending.navigation, now))
    dialogue.orchestrator.ai.clock = lambda: pending.navigation.expires_at
    with pytest.raises(ValueError, match="NAVIGATION_EXPIRED"):
        accept(dialogue, principal, pending, receipt(pending.navigation, now))
    retry = say(dialogue, principal, pending.state, target)
    assert retry.navigation.action_id != pending.navigation.action_id


def test_explicit_ordinal_uses_displayed_offer_ids(setup):
    dialogue, principal, state, target = setup
    pending = say(dialogue, principal, state, target, "open second offer")
    assert pending.navigation.offer.offer_id == state.market_display_ids[1] == "offer1"
    ambiguous = say(dialogue, principal, state, target, "open first and second offers")
    assert ambiguous.navigation is None
    compound = say(dialogue, principal, state, target, "open cheapest and add to cart")
    assert compound.navigation is None and compound.action is None


def test_model_intent_cannot_invent_an_open_request(setup):
    from shopping_ai.intents import Meaning

    dialogue, principal, state, target = setup

    class IncorrectInterpreter:
        async def interpret(self, message, awaiting):
            return Meaning(intent="open_offer")

    dialogue.interpreter = IncorrectInterpreter()
    reply = say(dialogue, principal, state, target, "tell me the cheapest price")
    assert reply.status == "needs_input" and reply.navigation is None
    assert reply.state.pending_navigation is None
