import asyncio
from decimal import Decimal

import pytest
from pydantic import ValidationError

from shopping_ai import Conversation, ConversationTurn, Orchestrator, Principal, ShoppingAI
from shopping_ai.browser import BrowserResult, BrowserTarget, CartLine, CartSnapshot
from shopping_ai.config import AIConfig
from shopping_ai.conversation import ConversationError, ConversationState
from shopping_ai.intents import BasicInterpreter, Meaning, Requirements
from shopping_ai.providers import MockProvider
from shopping_ai.providers.base import ProviderError


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


def test_intent_capacity_is_bounded_and_cancellation_releases_the_permit(now):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class BlockingInterpreter:
            calls = 0

            async def interpret(self, message, awaiting):
                self.calls += 1
                entered.set()
                await release.wait()
                return Meaning(intent="greeting")

        adapter = BlockingInterpreter()
        conversation = Conversation(
            Orchestrator(
                ShoppingAI(
                    MockProvider(),
                    AIConfig(max_concurrent_calls=1, capacity_wait_seconds=0.02),
                    clock=lambda: now,
                )
            ),
            adapter,
        )
        owner = Principal(owner_id="one", session_id="one")
        other = Principal(owner_id="two", session_id="two")
        state = conversation.start(owner, language="en").state
        other_state = conversation.start(other, language="en").state
        pending = asyncio.create_task(
            conversation.respond(
                state,
                owner,
                ConversationTurn(request_id="first", message="hi"),
                expected_version=state.version,
            )
        )
        await entered.wait()
        try:
            busy = await conversation.respond(
                other_state,
                other,
                ConversationTurn(request_id="busy", message="hi"),
                expected_version=other_state.version,
            )
            assert busy.status == "error" and "busy" in busy.message
            assert adapter.calls == 1 and busy.state.requirements == other_state.requirements
            assert busy.action is None and not busy.search_tasks
            stopped = await conversation.respond(
                state,
                owner,
                ConversationTurn(request_id="stop", message="stop"),
                expected_version=state.version,
            )
            assert stopped.status == "cancelled" and adapter.calls == 1
        finally:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        release.set()
        next_reply = await conversation.respond(
            busy.state,
            other,
            ConversationTurn(request_id="retry", message="hi"),
            expected_version=busy.state.version,
        )
        assert next_reply.status == "chat" and adapter.calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["provider", "timeout", "schema"])
def test_intent_failure_does_not_leak_capacity(now, failure):
    async def scenario():
        class FailingOnce:
            calls = 0

            async def interpret(self, message, awaiting):
                self.calls += 1
                if self.calls == 1:
                    if failure == "provider":
                        raise ProviderError("private-adapter-error")
                    if failure == "timeout":
                        await asyncio.Event().wait()
                    return Meaning.model_construct(intent="invalid")
                return Meaning(intent="greeting")

        adapter = FailingOnce()
        conversation = Conversation(
            Orchestrator(
                ShoppingAI(
                    MockProvider(),
                    AIConfig(max_concurrent_calls=1, timeout_seconds=0.02),
                    clock=lambda: now,
                )
            ),
            adapter,
        )
        principal = Principal(owner_id="owner", session_id="session")
        state = conversation.start(principal).state
        request = ConversationTurn(request_id="first", message="hi")
        if failure == "schema":
            with pytest.raises(ValueError):
                await conversation.respond(
                    state, principal, request, expected_version=state.version
                )
        else:
            failed = await conversation.respond(
                state, principal, request, expected_version=state.version
            )
            assert failed.status == "error" and "private-adapter-error" not in failed.message
        recovered = await conversation.respond(
            state,
            principal,
            ConversationTurn(request_id="second", message="hi"),
            expected_version=state.version,
        )
        assert recovered.status == "chat" and adapter.calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "intent,message",
    [
        ("add_to_cart", "price kitna hai"),
        ("add_to_cart", "don't add to cart"),
        ("add_to_cart", "how do I add to cart"),
        ("add_to_cart", "what does add to cart mean"),
        ("add_to_cart", "can I add to cart"),
        ("select_variant", "why select blue"),
        ("select_variant", "never choose black"),
    ],
)
def test_wrong_mutation_intent_does_not_use_a_supplied_grant(
    now, principal, state, product, grant, intent, message
):
    class WrongIntent:
        async def interpret(self, message, awaiting):
            return Meaning(intent=intent)

    class MustNotDecide:
        name = "test"

        async def decide(self, context):
            raise AssertionError("Unsupported mutation reached decision provider")

    conversation = Conversation(
        Orchestrator(ShoppingAI(MustNotDecide(), clock=lambda: now)), WrongIntent()
    )
    target = BrowserTarget(
        tab_id=1, document_id=product.document_id, active=True, window_focused=True
    )
    reply = respond(
        conversation,
        principal,
        state,
        message,
        "r1",
        products=(product,),
        target=target,
        authorization=grant,
        available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
    )
    assert reply.status == "needs_input" and reply.action is None
    assert reply.state.active_run is None and not reply.search_tasks
    assert reply.state.requirements == state.requirements


@pytest.mark.parametrize(
    "message,intent",
    [
        ("can you add black to cart", "add_to_cart"),
        ("please select blue", "select_variant"),
        ("how do I add to cart", "unknown"),
        ("what does select blue mean", "unknown"),
    ],
)
def test_mutation_request_examples_distinguish_commands_from_instructions(message, intent):
    assert asyncio.run(BasicInterpreter().interpret(message, None)).intent == intent


@pytest.mark.parametrize(
    "message,variant,tool",
    [
        ("काला वाला कार्ट में डाल दो", "black", "read_cart"),
        ("कार्ट में जोड़ें", "black", "read_cart"),
        ("नीला चुनें", "blue", "select_variant"),
    ],
)
def test_explicit_hindi_commands_use_existing_grants_and_observed_variants(
    conversation, principal, state, product, grant, message, variant, tool
):
    target = BrowserTarget(
        tab_id=1, document_id=product.document_id, active=True, window_focused=True
    )
    reply = respond(
        conversation,
        principal,
        state,
        message,
        "r1",
        products=(product,),
        target=target,
        authorization=grant.model_copy(update={"variant_id": variant}),
        available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
    )
    assert reply.action is not None and reply.action.tool.name == tool
    assert not reply.search_tasks


@pytest.mark.parametrize(
    "message",
    [
        "कार्ट में मत डालो",
        "कार्ट में नहीं डालना",
        "कैसे कार्ट में डालें",
        "नीला मत चुनो",
        "कीमत बताओ और कार्ट में डालो",
        "नीला चुनो और कार्ट में डाल दो",
    ],
)
def test_hindi_negations_questions_and_compound_actions_stay_unclear(message):
    assert asyncio.run(BasicInterpreter().interpret(message, None)).intent == "unknown"


def test_hindi_colour_aliases_respect_combining_mark_word_boundaries():
    from shopping_ai.intents import requirements_supported

    assert requirements_supported(Requirements(colour="blue"), "नीला चुनें", None)
    assert not requirements_supported(Requirements(colour="blue"), "नीलापन", None)
    assert not requirements_supported(Requirements(colour="green"), "ग्रीनहाउस", None)


@pytest.mark.parametrize(
    "message",
    [
        "search websites and add to cart",
        "compare stores aur cheapest offer kholo",
        "select black then add to cart",
        "find phone and buy it",
        "price batao aur cart mein daal do",
        "refresh prices and open cheapest",
        "search websites or open first offer",
    ],
)
def test_compound_goals_are_clarified_before_interpreter_or_state_updates(
    conversation, principal, state, message
):
    class MustNotRun:
        async def interpret(self, message, awaiting):
            raise AssertionError("Unsupported compound reached interpreter")

    dialogue = Conversation(conversation.orchestrator, MustNotRun())
    reply = respond(dialogue, principal, state, message)
    assert reply.status == "needs_input"
    assert reply.state.requirements == state.requirements
    assert reply.state.market_draft == state.market_draft
    assert reply.state.active_run == state.active_run
    assert not reply.search_tasks and reply.action is None and reply.navigation is None
    assert asyncio.run(BasicInterpreter().interpret(message, None)).intent == "unknown"


@pytest.mark.parametrize(
    "message,intent",
    [
        ("compare first and second", "compare"),
        ("black and blue headphones budget 2000", "requirements"),
        ("open cheapest offer", "open_offer"),
        ("black wala cart mein daal do", "add_to_cart"),
    ],
)
def test_lists_and_single_goals_are_not_mistaken_for_combined_actions(message, intent):
    assert asyncio.run(BasicInterpreter().interpret(message, None)).intent == intent


def test_compound_request_during_detail_collection_preserves_draft(conversation, principal, state):
    started = respond(conversation, principal, state, "search across websites")

    class MustNotExtract:
        async def extract(self, message, draft):
            raise AssertionError("Combined action reached detail extractor")

    conversation.detail_interpreter = MustNotExtract()
    reply = respond(conversation, principal, started.state, "search phone and buy it", "compound")
    assert reply.status == "needs_input"
    assert reply.state.market_draft == started.state.market_draft
    assert not reply.search_tasks and reply.action is None


def test_compound_request_preserves_inflight_cart_goal(
    conversation, principal, state, product, grant
):
    target = BrowserTarget(
        tab_id=1, document_id=product.document_id, active=True, window_focused=True
    )
    pending = respond(
        conversation,
        principal,
        state,
        "Black wala cart mein daal do",
        "r1",
        products=(product,),
        target=target,
        authorization=grant,
        available_tools=("read_product", "read_cart", "select_variant", "add_to_cart"),
    )
    assert pending.action is not None
    reply = respond(
        conversation, principal, pending.state, "search websites and add to cart", "combined"
    )
    assert reply.status == "needs_input" and reply.action is None
    assert reply.state.active_run == pending.state.active_run
    assert not reply.search_tasks and reply.navigation is None


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
    reply = respond(dialogue, principal, state, "Mujhe large backpack chahiye")
    assert reply.state.requirements.category == "backpack"
    assert reply.state.requirements.size == "large"
    assert reply.state.awaiting == "budget"


@pytest.mark.parametrize(
    "message,slots",
    [
        ("phone chahiye", Requirements(category="phone", budget="2000")),
        ("budget 2000", Requirements(budget="5000")),
        ("blue phone", Requirements(category="phone", colour="red")),
        ("size medium", Requirements(size="large")),
        ("128GB phone", Requirements(budget="128")),
        ("budget $100", Requirements(budget="100")),
        ("don't change budget 5000", Requirements(budget="5000")),
        ("Mujhe bada bag chahiye", Requirements(category="backpack", size="large")),
    ],
)
def test_model_slots_require_explicit_source_and_never_partially_update(
    conversation, principal, state, message, slots
):
    class InventedSlots:
        async def interpret(self, message, awaiting):
            return Meaning(intent="requirements", slots=slots)

    saved = respond(conversation, principal, state, "earbuds budget 1500")
    dialogue = Conversation(conversation.orchestrator, InventedSlots())
    reply = respond(dialogue, principal, saved.state, message, "unsupported")
    assert reply.status == "needs_input"
    assert reply.state.requirements == saved.state.requirements
    assert reply.state.slot_sources == saved.state.slot_sources
    assert reply.action is None and not reply.search_tasks and reply.navigation is None


@pytest.mark.parametrize(
    "message,slots",
    [
        ("mobile under 2k", Requirements(category="phone", budget="2000")),
        ("earphones budget 2,000.50", Requirements(category="earbuds", budget="2000.50")),
        (
            "shoes for office, size UK 9",
            Requirements(category="shoes", use_case="office", size="UK 9"),
        ),
    ],
)
def test_explicit_model_slots_keep_supported_aliases_and_decimal_budget(
    conversation, principal, state, message, slots
):
    class SupportedSlots:
        async def interpret(self, message, awaiting):
            return Meaning(intent="requirements", slots=slots)

    reply = respond(
        Conversation(conversation.orchestrator, SupportedSlots()), principal, state, message
    )
    assert reply.state.requirements == slots
    assert reply.state.slot_sources == {
        key: "turn_1" for key in slots.model_dump(exclude_none=True)
    }


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


@pytest.mark.parametrize(
    "language,expected",
    [
        ("en", "complete cart"),
        ("hi-Latn", "Poora cart"),
    ],
)
def test_incomplete_cart_read_explains_pause_without_zero_or_success_claim(
    conversation, principal, product, grant, now, language, expected
):
    state = conversation.start(principal, language=language).state
    pending = pending_baseline(conversation, principal, state, product, grant)
    failed = result_for(pending.state, now, 0).model_copy(
        update={
            "outcome": "failed",
            "cart": None,
            "error_code": "CART_COVERAGE_INCOMPLETE",
        }
    )
    reply = conversation.accept_result(
        pending.state,
        principal,
        failed,
        expected_version=pending.state.version,
    )
    assert reply.status == "paused" and reply.action is None
    assert expected in reply.message and "resume" in reply.message.lower()
    assert reply.speech_text == reply.message
    assert "CART_COVERAGE_INCOMPLETE" not in reply.message
    assert "Verified cart" not in reply.message
    assert reply.state.active_run.cart is None
    assert not reply.state.active_run.recovery_required


@pytest.mark.parametrize(
    "language,expected",
    [
        ("en", "will not repeat the add"),
        ("hi-Latn", "Add action dobara nahi"),
    ],
)
def test_unknown_cart_update_explains_read_only_recovery(
    conversation, principal, product, grant, now, language, expected
):
    state = conversation.start(principal, language=language).state
    pending = pending_baseline(conversation, principal, state, product, grant)
    baseline = conversation.accept_result(
        pending.state,
        principal,
        result_for(pending.state, now, 0),
        expected_version=pending.state.version,
    )
    add = asyncio.run(
        conversation.advance(
            baseline.state,
            principal,
            expected_version=baseline.state.version,
        )
    )
    lost = result_for(add.state, now, 0).model_copy(
        update={
            "outcome": "unknown",
            "cart": None,
            "error_code": "PRIVATE_EXECUTOR_DETAIL",
        }
    )
    reply = conversation.accept_result(
        add.state,
        principal,
        lost,
        expected_version=add.state.version,
    )
    assert reply.status == "paused" and reply.action is None
    assert reply.state.active_run.recovery_required
    assert expected in reply.message and "resume" in reply.message
    assert "PRIVATE_EXECUTOR_DETAIL" not in reply.speech_text
    assert "ACTION_UNKNOWN" not in reply.message
    assert reply.message == reply.speech_text


@pytest.mark.parametrize("code", ["PRIVATE_STORE_INTERNAL", "LOST_RECEIPT"])
def test_unrecognized_read_failure_code_is_not_spoken_to_user(
    conversation, principal, state, product, grant, now, code
):
    pending = pending_baseline(conversation, principal, state, product, grant)
    failed = result_for(pending.state, now, 0).model_copy(
        update={
            "outcome": "failed",
            "cart": None,
            "error_code": code,
        }
    )
    reply = conversation.accept_result(
        pending.state,
        principal,
        failed,
        expected_version=pending.state.version,
    )
    assert reply.status == "paused" and reply.action is None
    assert code not in reply.message and "verify nahi hua" in reply.message
    assert reply.state.active_run.pause_code == code
