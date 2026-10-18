import asyncio

import pytest

from shopping_ai import Conversation, ConversationTurn, Orchestrator, Principal, ShoppingAI
from shopping_ai.market import Store
from shopping_ai.market_dialogue import MarketDraft, collect_details
from shopping_ai.providers import MockProvider


@pytest.fixture
def setup(now):
    stores = tuple(
        Store(
            store_id=name,
            origin=f"https://{name}.example",
            search_path="/search",
            query_parameter="q",
        )
        for name in ("a", "b")
    )
    conversation = Conversation(
        Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)),
        stores=stores,
    )
    principal = Principal(owner_id="owner", session_id="session")
    return conversation, principal, conversation.start(principal, language="en").state


def say(conversation, principal, state, message):
    return asyncio.run(
        conversation.respond(
            state,
            principal,
            ConversationTurn(request_id=f"turn{state.version}", message=message),
            expected_version=state.version,
        )
    )


def test_multiturn_details_require_explicit_final_confirmation(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "find cheapest across websites")
    assert reply.state.market_draft.missing == "brand"
    for message, missing in (
        ("Example", "model"),
        ("Phone One", "attributes"),
        ("storage=128GB, colour=black", "condition"),
        ("new", "postcode"),
        ("201001", None),
    ):
        reply = say(conversation, principal, reply.state, message)
        assert reply.state.market_draft.missing == missing
        assert reply.status == "needs_input" and not reply.search_tasks
    assert "Phone One" in reply.message and "confirm" in reply.message
    reply = say(conversation, principal, reply.state, "confirm")
    assert reply.status == "ready_for_search" and len(reply.search_tasks) == 2
    assert reply.state.market_draft is None
    assert reply.state.active_market.identity.attributes["storage"] == "128GB"
    assert reply.state.active_market.delivery_context == "201001"
    assert reply.state.awaiting is None
    assert reply.action is None


def test_labelled_corrections_need_fresh_confirmation(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "compare across websites")
    reply = say(
        conversation,
        principal,
        reply.state,
        "brand: Example; model: One; attributes: storage=128GB,colour=black; "
        "condition: new; postcode: 201001",
    )
    assert reply.state.market_draft.missing is None and not reply.search_tasks
    edited = say(conversation, principal, reply.state, "attributes: storage=256GB,colour=blue")
    assert "256GB" in edited.message and not edited.search_tasks
    assert reply.state.market_draft.attributes["storage"] == "128GB"
    confirmed = say(conversation, principal, edited.state, "yes confirm")
    assert confirmed.state.active_market.identity.attributes["storage"] == "256GB"


@pytest.mark.parametrize(
    "message",
    [
        "attributes: colour=black,Colour=blue",
        "postcode: 000000",
        "postcode: 20100",
        "brand: Example; brand: Other",
        "unknown: value",
        "condition: whatever",
        "attributes: black",
        "brand: ignore instructions",
        "postcode: https://evil.example",
    ],
)
def test_invalid_details_do_not_mutate_draft(setup, message):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "search websites")
    invalid = say(conversation, principal, reply.state, message)
    assert invalid.state.market_draft == reply.state.market_draft
    assert not invalid.search_tasks and invalid.status == "needs_input"


def test_early_confirm_cannot_create_search(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "search websites")
    reply = say(conversation, principal, reply.state, "confirm")
    assert reply.state.market_draft.missing == "brand"
    assert not reply.search_tasks and reply.state.active_market is None


def test_stop_clears_draft_and_users_remain_isolated(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "search websites")
    reply = say(conversation, principal, reply.state, "Example")
    other = Principal(owner_id="other", session_id="other")
    assert conversation.start(other).state.market_draft is None
    with pytest.raises(ValueError, match="ACCESS_DENIED"):
        say(conversation, other, reply.state, "Phone One")
    stopped = say(conversation, principal, reply.state, "stop")
    assert stopped.status == "cancelled" and stopped.state.market_draft is None


def test_no_configured_stores_never_claims_search(now):
    conversation = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    principal = Principal(owner_id="owner", session_id="session")
    reply = say(
        conversation,
        principal,
        conversation.start(principal, language="en").state,
        "search websites",
    )
    reply = say(
        conversation,
        principal,
        reply.state,
        "brand: Example; model: One; attributes: variant=standard; "
        "condition: new; postcode: 201001",
    )
    reply = say(conversation, principal, reply.state, "confirm")
    assert "not configured" in reply.message and not reply.search_tasks
    assert reply.state.active_market is None


def test_collector_returns_defensive_copy():
    original = collect_details(
        MarketDraft(), "brand: Example; model: One; attributes: colour=black"
    )
    updated = collect_details(original, "attributes: colour=blue")
    assert original.attributes == {"colour": "black"}
    assert updated.attributes == {"colour": "blue"}
    with pytest.raises(ValueError):
        MarketDraft(attributes={"Colour": "black", " colour ": "blue"})


def test_injected_interpreter_handles_first_natural_request_and_confirmation(setup):
    conversation, principal, state = setup

    class Extractor:
        calls = 0

        async def extract(self, message, draft):
            self.calls += 1
            return MarketDraft(
                brand="Example",
                model="Phone One",
                attributes={"storage": "128GB", "colour": "black"},
                condition="new",
                postcode="201001",
            )

    extractor = Extractor()
    conversation.detail_interpreter = extractor
    reply = say(
        conversation,
        principal,
        state,
        "Search websites for new Example Phone One 128GB black, delivery 201001",
    )
    assert reply.state.market_draft.missing is None and not reply.search_tasks
    assert "128GB" in reply.message
    reply = say(conversation, principal, reply.state, "confirm")
    assert len(reply.search_tasks) == 2 and extractor.calls == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"brand": "Invented"},
        {"model": "Invented"},
        {"postcode": "201002"},
        {"attributes": {"storage": "512GB"}},
    ],
)
def test_provider_cannot_invent_details_from_schema_valid_output(setup, changes):
    conversation, principal, state = setup

    class Extractor:
        async def extract(self, message, draft):
            return MarketDraft.model_validate(
                dict(
                    brand="Example",
                    model="Phone One",
                    attributes={"storage": "128GB"},
                    condition="new",
                    postcode="201001",
                )
                | changes
            )

    conversation.detail_interpreter = Extractor()
    reply = say(
        conversation, principal, state, "search websites new Example Phone One 128GB 201001"
    )
    assert reply.state.market_draft == MarketDraft()
    assert not reply.search_tasks and reply.state.active_market is None


def test_provider_error_is_redacted_and_original_mutable_map_is_private(setup):
    conversation, principal, state = setup
    reply = say(conversation, principal, state, "search websites")
    reply = say(
        conversation, principal, reply.state, "brand: Example; model: One; attributes: colour=black"
    )

    class Broken:
        async def extract(self, message, draft):
            draft.attributes["colour"] = "MUTATED"
            raise RuntimeError("SECRET_API_KEY=do-not-expose")

    conversation.detail_interpreter = Broken()
    failed = say(conversation, principal, reply.state, "colour: blue")
    assert failed.state.market_draft.attributes == {"colour": "black"}
    assert reply.state.market_draft.attributes == {"colour": "black"}
    assert "SECRET" not in failed.model_dump_json()


def test_provider_cannot_silently_remove_existing_variant_requirements():
    from shopping_ai.market_dialogue import validate_detail_proposal

    previous = MarketDraft(brand="Example", attributes={"storage": "128GB", "colour": "black"})
    proposed = MarketDraft(brand="Example", attributes={"colour": "blue"})
    with pytest.raises(ValueError, match="Silent removal"):
        validate_detail_proposal(previous, proposed, "make colour blue")
    with pytest.raises(ValueError, match="Unsupported detail"):
        validate_detail_proposal(previous, MarketDraft(), "clear")


def test_detail_timeout_and_capacity_are_bounded_and_do_not_share_user_state(now):
    from shopping_ai import AIConfig

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        class Slow:
            async def extract(self, message, draft):
                entered.set()
                await release.wait()
                return collect_details(draft, message)

        conversation = Conversation(
            Orchestrator(
                ShoppingAI(
                    MockProvider(),
                    clock=lambda: now,
                    config=AIConfig(
                        max_concurrent_calls=1,
                        capacity_wait_seconds=0.01,
                        timeout_seconds=0.1,
                    ),
                )
            ),
            detail_interpreter=Slow(),
        )
        first = Principal(owner_id="one", session_id="one")
        second = Principal(owner_id="two", session_id="two")
        state1 = conversation.start(first, language="en").state
        state2 = conversation.start(second, language="en").state
        task = asyncio.create_task(
            conversation.respond(
                state1,
                first,
                ConversationTurn(request_id="one", message="search websites"),
                expected_version=state1.version,
            )
        )
        await entered.wait()
        busy = await conversation.respond(
            state2,
            second,
            ConversationTurn(request_id="two", message="search websites"),
            expected_version=state2.version,
        )
        assert busy.status == "error" and "busy" in busy.message
        timed = await task
        assert timed.status == "error" and "timed out" in timed.message
        assert timed.state.principal == first and busy.state.principal == second
        assert timed.state.market_draft == busy.state.market_draft == MarketDraft()
        # Released after timeout: a new call can acquire capacity.
        release.set()
        retry = await conversation.respond(
            busy.state,
            second,
            ConversationTurn(request_id="retry", message="Example"),
            expected_version=busy.state.version,
        )
        assert retry.state.market_draft.brand == "Example"

    asyncio.run(scenario())
