import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest

from shopping_ai import (
    AIConfig,
    Conversation,
    ConversationTurn,
    Orchestrator,
    Principal,
    ShoppingAI,
)
from shopping_ai.comparison import ComparisonError, compare_products
from shopping_ai.intents import BasicInterpreter
from shopping_ai.models import ProductSnapshot
from shopping_ai.providers import MockProvider


def second(product, **updates):
    return ProductSnapshot.model_validate(
        product.model_dump()
        | {
            "product_id": "p2",
            "snapshot_id": "snap_2",
            "title": "Second earbuds",
            "price": "999.00",
        }
        | updates
    )


def compare(left, right, now):
    return compare_products(left, right, now=now, config=AIConfig())


def test_exact_price_difference_with_evidence(product, now):
    result = compare(product, second(product), now)
    assert result.price_relation == "right_lower"
    assert result.difference == Decimal("500.00")
    assert result.currency == "INR"
    price = next(row for row in result.rows if row.attribute == "price")
    assert price.left.fact_id == "snap_1.price"
    assert price.right.fact_id == "snap_2.price"
    assert price.left.value == "INR 1499.00"
    assert price.right.value == "INR 999.00"
    assert "No overall quality ranking" in result.render("en")


def test_equal_prices_are_not_ranked(product, now):
    result = compare(product, second(product, price=product.price), now)
    assert result.price_relation == "equal" and result.difference == 0
    assert "Lower price:" not in result.render("en")


def test_float_arithmetic_is_not_used(product, now):
    left = ProductSnapshot.model_validate(product.model_dump() | {"price": "0.30"})
    result = compare(left, second(product, price="0.10"), now)
    assert result.difference == Decimal("0.20")


def test_currency_mismatch_never_produces_cheapest_claim(product, now):
    result = compare(product, second(product, price="10.00", currency="USD"), now)
    assert result.price_relation == "different_currency"
    assert result.difference is None and result.currency is None
    assert "Lower price:" not in result.render("hi-Latn")


def test_missing_price_stays_unknown(product, now):
    result = compare(product, second(product, price=None, currency=None), now)
    assert result.price_relation == "missing"
    row = next(row for row in result.rows if row.attribute == "price")
    assert row.right is None
    assert "data nahi hai" in result.render("hi-Latn")


@pytest.mark.parametrize("offset", [timedelta(minutes=-3), timedelta(seconds=1)])
def test_stale_or_future_observation_is_rejected(product, now, offset):
    right = second(product, observed_at=now + offset)
    with pytest.raises(ComparisonError, match="STALE_COMPARISON_EVIDENCE"):
        compare(product, right, now)


def test_same_product_cannot_compare_with_itself(product, now):
    with pytest.raises(ComparisonError, match="SAME_PRODUCT"):
        compare(product, product, now)


def test_ambiguous_snapshot_identity_rejected(product, now):
    with pytest.raises(ComparisonError, match="AMBIGUOUS_SNAPSHOT_IDS"):
        compare(product, second(product, snapshot_id=product.snapshot_id), now)


def test_spec_labels_normalize_but_units_are_not_guessed(product, now):
    right = second(product, specs={" battery ": "1800 minutes"})
    result = compare(product, right, now)
    row = next(row for row in result.rows if row.attribute == "battery")
    assert "30 hours" in row.left.value
    assert row.right.value == " battery : 1800 minutes"
    assert "better battery" not in result.render("en").lower()


def test_missing_attribute_keeps_one_sided_evidence(product, now):
    result = compare(product, second(product, specs={"Weight": "20 g"}), now)
    battery = next(row for row in result.rows if row.attribute == "battery")
    weight = next(row for row in result.rows if row.attribute == "weight")
    assert battery.left is not None and battery.right is None
    assert weight.left is None and weight.right is not None


def test_duplicate_spec_labels_are_not_arbitrarily_picked(product, now):
    right = second(product, specs={"Battery": "10 hours", "battery": "20 hours"})
    result = compare(product, right, now)
    row = next(row for row in result.rows if row.attribute == "battery")
    assert row.right is None
    assert any("duplicate" in note for note in result.limitations)


def test_rows_and_display_are_bounded_without_discarding_source_values(product, now):
    left = ProductSnapshot.model_validate(
        product.model_dump()
        | {
            "title": "A" * 500,
            "specs": {f"Spec{i}": "x" * 500 for i in range(30)},
        }
    )
    right = second(product, specs={f"Spec{i}": "y" * 500 for i in range(30)})
    result = compare(left, right, now)
    assert len(result.rows) <= 8
    assert len(result.render("en")) < 8000
    assert any("omitted" in note for note in result.limitations)
    assert "Display shortened" in result.render("en")
    spec = next(row for row in result.rows if row.attribute.startswith("spec"))
    assert len(spec.left.value) > 500


def dialogue(product, now):
    principal = Principal(owner_id="u1", session_id="s1")
    conversation = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    state = conversation.start(principal).state
    return conversation, principal, state


def turn(conversation, principal, state, message, request_id="r1", **kwargs):
    return asyncio.run(
        conversation.respond(
            state,
            principal,
            ConversationTurn(request_id=request_id, message=message, **kwargs),
            expected_version=state.version,
        )
    )


def test_two_product_conversation_comparison_needs_no_model(product, now):
    conversation, principal, state = dialogue(product, now)

    class UnusedProvider:
        name = "unused"

        async def decide(self, context):
            pytest.fail("Comparison should not need a model call")

    conversation.orchestrator.ai.provider = UnusedProvider()
    result = turn(
        conversation, principal, state, "kaunsa sasta hai?", products=(product, second(product))
    )
    assert result.status == "complete" and result.action is None
    assert result.comparison.right_product_id == "p2"
    assert "500.00" in result.message
    assert result.speech_text == result.message


def test_explicit_reference_pair_preserves_user_order(product, now):
    conversation, principal, state = dialogue(product, now)
    result = turn(
        conversation,
        principal,
        state,
        "Compare second and first",
        products=(product, second(product)),
    )
    assert result.comparison.left_product_id == "p2"
    assert result.comparison.right_product_id == product.product_id
    assert result.comparison.price_relation == "left_lower"


def test_previous_product_comparison_keeps_selection(product, now):
    conversation, principal, state = dialogue(product, now)
    first = turn(
        conversation, principal, state, "Pehle wale ka price?", products=(product, second(product))
    )
    selected = turn(conversation, principal, first.state, "Dusre wale ka price?", "r2")
    result = turn(conversation, principal, selected.state, "Pichle wale se compare karo", "r3")
    assert result.comparison.left_product_id == "p2"
    assert result.comparison.right_product_id == product.product_id
    assert result.state.selected_id == "p2"
    assert result.state.previous_id == product.product_id


def test_more_than_two_unselected_candidates_need_clarification(product, now):
    conversation, principal, state = dialogue(product, now)
    third = second(product, product_id="p3", snapshot_id="snap_3")
    result = turn(
        conversation, principal, state, "compare karo", products=(product, second(product), third)
    )
    assert result.status == "needs_input" and result.comparison is None
    assert result.action is None


def test_stale_comparison_does_not_invent_a_result(product, now):
    conversation, principal, state = dialogue(product, now)
    result = turn(
        conversation,
        principal,
        state,
        "compare karo",
        products=(product, second(product, observed_at=now - timedelta(minutes=3))),
    )
    assert result.status == "needs_input" and result.comparison is None
    assert "STALE_COMPARISON_EVIDENCE" in result.message


def test_comparison_does_not_select_unknown_products(product, now):
    conversation, principal, state = dialogue(product, now)
    result = turn(
        conversation,
        principal,
        state,
        "Compare first and third",
        products=(product, second(product)),
    )
    assert result.status == "needs_input" and result.comparison is None


def test_reference_parser_does_not_turn_two_product_cart_request_into_action():
    meaning = asyncio.run(BasicInterpreter().interpret("add first and second to cart", None))
    assert meaning.intent == "unknown"


def test_blank_spec_label_is_omitted(product, now):
    result = compare(product, second(product, specs={"   ": "unlabelled value"}), now)
    assert all(row.attribute.strip() for row in result.rows)
    assert any("omitted" in note for note in result.limitations)


def test_comparison_is_available_while_an_action_is_pending(product, now):
    from shopping_ai.browser import BrowserTarget

    conversation, principal, state = dialogue(product, now)
    target = BrowserTarget(tab_id=1, document_id="doc_1", active=True, window_focused=True)
    pending = turn(conversation, principal, state, "Iska price?", target=target)
    action_id = pending.action.action_id
    result = turn(
        conversation,
        principal,
        pending.state,
        "Compare first and second",
        "comparison_turn",
        products=(product, second(product)),
    )
    assert result.status == "chat" and result.action is None
    assert result.comparison is not None
    assert result.state.active_run.pending.action_id == action_id
