import asyncio
from decimal import Decimal

import pytest
from pydantic import ValidationError

from shopping_ai import Conversation, ConversationTurn, Orchestrator, Principal, ShoppingAI
from shopping_ai.browser import BrowserResult, BrowserTarget, CartLine, CartSnapshot
from shopping_ai.conversation import ConversationError, ConversationState
from shopping_ai.intents import BasicInterpreter, Meaning, Requirements
from shopping_ai.providers import MockProvider


@pytest.fixture
def principal():
    return Principal(owner_id="user_1", session_id="session_1")


@pytest.fixture
def conversation(now):
    return Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))


@pytest.fixture
def state(conversation, principal):
    return conversation.start(principal).state


def respond(conversation, principal, state, text, request_id="turn_1", **kwargs):
    turn = ConversationTurn(request_id=request_id, message=text, **kwargs)
    return asyncio.run(conversation.respond(state, principal, turn, expected_version=state.version))


def test_starts_with_greeting_without_provider(conversation, principal):
    reply = conversation.start(principal)
    assert reply.message == reply.speech_text == "Namaste! Aaj kya shopping karni hai?"
    assert reply.status == "chat"
    assert reply.action is None
    assert reply.state.version == 1


def test_gather_requirements_across_turns(conversation, principal, state):
    category = respond(conversation, principal, state, "Gym ke liye earbuds chahiye")
    assert category.state.awaiting == "budget"
    assert category.state.requirements.category == "earbuds"
    assert category.state.requirements.use_case == "gym"
    budget = respond(conversation, principal, category.state, "2,000 tak", "turn_2")
    assert budget.state.requirements.budget == Decimal("2000")
    assert budget.state.slot_sources == {
        "category": "turn_1",
        "use_case": "turn_1",
        "budget": "turn_2",
    }
    assert budget.status == "ready_for_search"
    assert budget.action is None
    assert "Search" in budget.message


def test_budget_first_then_category(conversation, principal, state):
    budget = respond(conversation, principal, state, "Budget 1500")
    assert budget.state.awaiting == "category"
    category = respond(conversation, principal, budget.state, "earbuds", "turn_2")
    assert category.status == "ready_for_search"
    assert category.state.requirements.budget == Decimal("1500")


def test_explicit_budget_correction_overrides_old_value(conversation, principal, state):
    first = respond(conversation, principal, state, "earbuds under 2000")
    correction = respond(conversation, principal, first.state, "budget 1500", "turn_2")
    assert correction.state.requirements.budget == Decimal("1500")
    assert correction.state.slot_sources["budget"] == "turn_2"
    assert first.state.requirements.budget == Decimal("2000")


def test_greeting_does_not_read_product(conversation, principal, state):
    reply = respond(conversation, principal, state, "hello")
    assert reply.status == "chat" and reply.action is None
    assert reply.state.active_run is None


def test_ordinal_reference_answers_from_correct_evidence(conversation, principal, state, product):
    second = product.model_copy(
        update={"product_id": "p2", "snapshot_id": "snap_2", "price": Decimal("999.00")}
    )
    reply = respond(
        conversation, principal, state, "Dusre wale ka price?", products=(product, second)
    )
    assert reply.state.selected_id == "p2"
    assert "INR 999.00" in reply.message
    assert reply.facts[0].snapshot_id == "snap_2"
    assert reply.status == "complete" and reply.action is None
    previous = respond(conversation, principal, reply.state, "Pehle wale ka price?", "turn_2")
    assert previous.state.previous_id == "p2"
    back = respond(conversation, principal, previous.state, "Pichle wale ka price?", "turn_3")
    assert back.state.selected_id == "p2"
    assert "999.00" in back.message


def test_ambiguous_reference_never_picks_first(conversation, principal, state, product):
    other = product.model_copy(update={"product_id": "p2"})
    reply = respond(conversation, principal, state, "Iska price?", products=(product, other))
    assert reply.status == "needs_input"
    assert reply.state.selected_id is None
    assert reply.action is None


def test_out_of_range_ordinal_asks(conversation, principal, state, product):
    reply = respond(conversation, principal, state, "Teesre ka price?", products=(product,))
    assert reply.status == "needs_input" and reply.action is None


def test_unsupported_review_claim_not_fabricated(conversation, principal, state, product):
    reply = respond(conversation, principal, state, "Heating reviews?", products=(product,))
    assert reply.status == "needs_input"
    assert "support" in reply.message.lower()
    assert reply.action is None


def test_reply_and_action_are_returned_together(conversation, principal, state, product, grant):
    grant = grant.model_copy(update={"request_id": "turn_1"})
    target = BrowserTarget(tab_id=7, document_id="doc_1", active=True, window_focused=True)
    reply = respond(
        conversation,
        principal,
        state,
        "Black wala cart mein daal do",
        products=(product,),
        target=target,
        authorization=grant,
        available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
    )
    assert reply.status == "awaiting_browser"
    assert reply.action.tool.name == "read_cart"
    assert reply.message == reply.speech_text == "Main cart check kar raha hoon."
    assert "Verified cart" not in reply.message
    assert reply.action == reply.state.active_run.pending


def result_for(state, now, quantity):
    run = state.active_run
    action = run.pending
    lines = (
        ()
        if quantity == 0
        else (CartLine(product_id="p42", variant_id="black", quantity=quantity),)
    )
    return BrowserResult(
        action_id=action.action_id,
        run_id=run.run_id,
        session_id=run.principal.session_id,
        run_version=action.run_version,
        source_document_id=action.target.document_id,
        target=action.target,
        observed_at=now,
        outcome="observed",
        cart=CartSnapshot(observed_at=now, document_id="doc_1", lines=lines),
    )


def pending_baseline(conversation, principal, state, product, grant):
    grant = grant.model_copy(update={"request_id": "turn_1"})
    target = BrowserTarget(tab_id=7, document_id="doc_1", active=True, window_focused=True)
    return respond(
        conversation,
        principal,
        state,
        "Black wala cart mein daal do",
        products=(product,),
        target=target,
        authorization=grant,
        available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
    )


def test_dialogue_wraps_verified_action_loop(conversation, principal, state, product, grant, now):
    first = pending_baseline(conversation, principal, state, product, grant)
    observed = conversation.accept_result(
        first.state,
        principal,
        result_for(first.state, now, 0),
        expected_version=first.state.version,
    )
    assert observed.status == "working" and observed.action is None
    pending = asyncio.run(
        conversation.advance(observed.state, principal, expected_version=observed.state.version)
    )
    assert pending.action.tool.name == "add_to_cart"
    assert "update kar raha" in pending.speech_text
    done = conversation.accept_result(
        pending.state,
        principal,
        result_for(pending.state, now, 1),
        expected_version=pending.state.version,
    )
    assert done.status == "complete"
    assert "Verified cart" in done.message
    assert done.action is None


def test_active_task_blocks_another_task(conversation, principal, state, product, grant):
    pending = pending_baseline(conversation, principal, state, product, grant)
    reply = respond(conversation, principal, pending.state, "add another to cart", "turn_2")
    assert reply.status == "paused" and reply.action is None
    assert reply.state.active_run.pending.action_id == pending.action.action_id


def test_stop_never_redispatches_pending_action(
    conversation, principal, state, product, grant, now
):
    pending = pending_baseline(conversation, principal, state, product, grant)
    stop = respond(conversation, principal, pending.state, "stop", "turn_2")
    assert stop.status == "cancelled" and stop.action is None
    assert stop.state.active_run.status == "cancelled"
    late = conversation.accept_result(
        stop.state,
        principal,
        result_for(pending.state, now, 0),
        expected_version=stop.state.version,
    )
    assert late.status == "cancelled" and late.action is None


def test_turn_replay_cannot_create_another_action(conversation, principal, state):
    reply = respond(conversation, principal, state, "hello")
    with pytest.raises(ConversationError, match="DUPLICATE_TURN"):
        respond(conversation, principal, reply.state, "hello")


def test_other_owner_cannot_read_or_advance_dialogue(conversation, principal, state):
    other = principal.model_copy(update={"owner_id": "other"})
    with pytest.raises(ConversationError, match="CONVERSATION_ACCESS_DENIED"):
        respond(conversation, other, state, "hello")


def test_stale_dialogue_version_rejected(conversation, principal, state):
    with pytest.raises(ConversationError, match="STALE_CONVERSATION_VERSION"):
        asyncio.run(
            conversation.respond(
                state,
                principal,
                ConversationTurn(request_id="turn_1", message="hello"),
                expected_version=0,
            )
        )


def test_readonly_question_does_not_receive_mutation_grant(
    conversation, principal, state, product, grant
):
    reply = respond(
        conversation, principal, state, "price?", products=(product,), authorization=grant
    )
    assert reply.status == "complete" and reply.action is None


def test_dialogue_budget_narrows_caller_grant(conversation, principal, state, product, grant):
    requirements = respond(conversation, principal, state, "earbuds under 1000", "setup")
    reply = pending_baseline(conversation, principal, requirements.state, product, grant)
    assert reply.status == "paused" and reply.action is None
    assert reply.state.active_run.pause_code == "PRICE_LIMIT"


def test_explicit_colour_conflict_asks_before_acting(
    conversation, principal, state, product, grant
):
    grant = grant.model_copy(update={"request_id": "turn_1"})
    target = BrowserTarget(tab_id=7, document_id="doc_1", active=True, window_focused=True)
    reply = respond(
        conversation,
        principal,
        state,
        "Blue wala cart mein daal do",
        products=(product,),
        target=target,
        authorization=grant,
        available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
    )
    assert reply.status == "needs_input" and reply.action is None
    assert reply.state.active_run is None


def test_missing_grant_does_not_authorize_from_conversation(
    conversation, principal, state, product
):
    target = BrowserTarget(tab_id=7, document_id="doc_1", active=True, window_focused=True)
    reply = respond(
        conversation,
        principal,
        state,
        "Black wala cart mein daal do",
        products=(product,),
        target=target,
        available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
    )
    assert reply.action is None
    assert reply.state.active_run.status == "needs_input"


def test_custom_interpreter_uses_generic_contract(conversation, principal, state):
    class CustomInterpreter:
        async def interpret(self, message, awaiting):
            return Meaning(
                intent="requirements", slots=Requirements(category="backpack", size="large")
            )

    dialogue = Conversation(conversation.orchestrator, CustomInterpreter())
    reply = respond(dialogue, principal, state, "Mujhe bada bag chahiye")
    assert reply.state.requirements.category == "backpack"
    assert reply.state.requirements.size == "large"
    assert reply.state.awaiting == "budget"


def test_concurrent_dialogues_do_not_mix_requirements(conversation):
    async def scenario():
        principals = [Principal(owner_id=f"user_{i}", session_id=f"session_{i}") for i in range(25)]
        states = [conversation.start(p).state for p in principals]
        return await asyncio.gather(
            *(
                conversation.respond(
                    s,
                    p,
                    ConversationTurn(request_id=f"turn_{i}", message=f"earbuds budget {1000 + i}"),
                    expected_version=s.version,
                )
                for i, (s, p) in enumerate(zip(states, principals, strict=True))
            )
        )

    replies = asyncio.run(scenario())
    assert len({r.state.conversation_id for r in replies}) == 25
    assert [r.state.requirements.budget for r in replies] == [Decimal(1000 + i) for i in range(25)]
    assert all(r.action is None for r in replies)


@pytest.mark.parametrize(
    "message", ["budget -500", "budget 1000 or 2000", "budget $100", "budget 0"]
)
def test_ambiguous_or_invalid_budget_not_saved(message):
    meaning = asyncio.run(BasicInterpreter().interpret(message, "budget"))
    assert meaning.slots.budget is None


def test_pending_budget_does_not_parse_cart_quantity():
    meaning = asyncio.run(BasicInterpreter().interpret("add 1 to cart", "budget"))
    assert meaning.slots.budget is None
    assert meaning.intent == "add_to_cart"


def test_explicit_negation_cannot_be_cart_intent():
    meaning = asyncio.run(BasicInterpreter().interpret("cart mein mat daal", None))
    assert meaning.intent == "unknown"


def test_requirements_support_k_notation():
    meaning = asyncio.run(BasicInterpreter().interpret("2k", "budget"))
    assert meaning.slots.budget == Decimal("2000")


def test_invalid_semantic_reference_is_rejected():
    with pytest.raises(ValidationError):
        Meaning(reference="ordinal", ordinal=None)


def test_other_session_run_cannot_be_embedded(state, principal, conversation, ai_request):
    other = Principal(owner_id="other", session_id="another_session")
    target = BrowserTarget(tab_id=7, document_id="doc_1", active=True, window_focused=True)
    run = conversation.orchestrator.start(other, ai_request, target)
    with pytest.raises(ValidationError):
        ConversationState.model_validate(state.model_dump() | {"active_run": run})


def test_can_answer_readonly_question_while_action_is_pending(
    conversation, principal, state, product, grant
):
    pending = pending_baseline(conversation, principal, state, product, grant)
    reply = respond(conversation, principal, pending.state, "Iska price?", "question_during_action")
    assert "1499.00" in reply.message
    assert reply.status == "chat"
    assert reply.action is None
    assert reply.state.active_run.pending.action_id == pending.action.action_id


def test_question_does_not_lose_pending_budget(conversation, principal, state, product):
    shopping = respond(conversation, principal, state, "earbuds chahiye", "shopping")
    question = respond(
        conversation, principal, shopping.state, "Iska price?", "question", products=(product,)
    )
    assert question.state.awaiting == "budget"
    answer = respond(conversation, principal, question.state, "2000", "budget_answer")
    assert answer.status == "ready_for_search"
    assert answer.state.requirements.budget == Decimal("2000")


def test_compound_goals_are_not_partially_executed():
    meaning = asyncio.run(BasicInterpreter().interpret("battery bata phir cart mein daal", None))
    assert meaning.intent == "unknown"


def test_dialogue_recovers_uncertain_cart_by_reading(
    conversation, principal, state, product, grant, now
):
    first = pending_baseline(conversation, principal, state, product, grant)
    baseline = conversation.accept_result(
        first.state,
        principal,
        result_for(first.state, now, 0),
        expected_version=first.state.version,
    )
    pending = asyncio.run(
        conversation.advance(baseline.state, principal, expected_version=baseline.state.version)
    )
    unknown = BrowserResult.model_validate(
        result_for(pending.state, now, 1).model_dump()
        | {
            "outcome": "unknown",
            "error_code": "LOST_RECEIPT",
            "cart": None,
        }
    )
    paused = conversation.accept_result(
        pending.state, principal, unknown, expected_version=pending.state.version
    )
    assert paused.status == "paused" and paused.action is None
    ready = conversation.resume(
        paused.state, principal, pending.action.target, expected_version=paused.state.version
    )
    read = asyncio.run(
        conversation.advance(ready.state, principal, expected_version=ready.state.version)
    )
    assert read.action.tool.name == "read_cart"
    done = conversation.accept_result(
        read.state, principal, result_for(read.state, now, 1), expected_version=read.state.version
    )
    assert done.status == "complete"


def test_cancelled_verified_cart_does_not_block_future_conversation(
    conversation, principal, state, product, grant, now
):
    first = pending_baseline(conversation, principal, state, product, grant)
    baseline = conversation.accept_result(
        first.state,
        principal,
        result_for(first.state, now, 0),
        expected_version=first.state.version,
    )
    pending = asyncio.run(
        conversation.advance(baseline.state, principal, expected_version=baseline.state.version)
    )
    stopped = respond(conversation, principal, pending.state, "stop", "stop_turn")
    recorded = conversation.accept_result(
        stopped.state,
        principal,
        result_for(pending.state, now, 1),
        expected_version=stopped.state.version,
    )
    assert recorded.status == "cancelled"
    assert not recorded.state.active_run.recovery_required
    reply = respond(conversation, principal, recorded.state, "shoes chahiye", "new_shopping")
    assert reply.state.requirements.category == "shoes"
    assert reply.state.awaiting == "budget"


def test_current_page_can_be_read_before_first_snapshot(
    conversation, principal, state, product, now
):
    target = BrowserTarget(tab_id=7, document_id="doc_1", active=True, window_focused=True)
    pending = respond(conversation, principal, state, "Iska price?", target=target)
    assert pending.action.tool.name == "read_product"
    action = pending.action
    result = BrowserResult(
        action_id=action.action_id,
        run_id=action.run_id,
        session_id=principal.session_id,
        run_version=action.run_version,
        source_document_id=target.document_id,
        target=target,
        observed_at=now,
        outcome="observed",
        product=product,
    )
    observed = conversation.accept_result(
        pending.state, principal, result, expected_version=pending.state.version
    )
    assert observed.state.selected_id == product.product_id
    assert observed.state.products[0] == product
    done = asyncio.run(
        conversation.advance(observed.state, principal, expected_version=observed.state.version)
    )
    assert done.status == "complete" and "1499.00" in done.message
    assert done.facts[0].snapshot_id == product.snapshot_id


def test_invalid_custom_interpreter_output_is_rejected(conversation, principal, state):
    class InvalidInterpreter:
        async def interpret(self, message, awaiting):
            return Meaning().model_copy(update={"intent": "arbitrary_checkout"})

    dialogue = Conversation(conversation.orchestrator, InvalidInterpreter())
    with pytest.raises(ValidationError):
        respond(dialogue, principal, state, "Do something")


def test_expired_dialogue_action_pauses_without_redispatch(
    conversation, principal, state, product, grant, now
):
    from datetime import timedelta

    pending = pending_baseline(conversation, principal, state, product, grant)
    conversation.orchestrator.ai.clock = lambda: now + timedelta(seconds=31)
    reply = conversation.expire_pending(
        pending.state, principal, expected_version=pending.state.version
    )
    assert reply.status == "paused" and reply.action is None
    assert reply.state.active_run.pause_code == "ACTION_EXPIRED"


def test_two_conversations_under_same_owner_keep_separate_slots(conversation, principal):
    first = conversation.start(principal).state
    second = conversation.start(principal).state
    first_reply = respond(conversation, principal, first, "earbuds budget 2000")
    second_reply = respond(conversation, principal, second, "shoes budget 5000")
    assert first_reply.state.requirements.category == "earbuds"
    assert second_reply.state.requirements.category == "shoes"
    assert first_reply.state.requirements.budget == Decimal("2000")
    assert second_reply.state.requirements.budget == Decimal("5000")
