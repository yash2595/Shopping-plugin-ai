import json

import pytest

from shopping_ai import Conversation, Orchestrator, Principal, ShoppingAI
from shopping_ai.checkpoint import (
    MAX_CHECKPOINT_BYTES,
    CheckpointError,
    dump_checkpoint,
    load_checkpoint,
)
from shopping_ai.providers import MockProvider


@pytest.fixture
def setup(now):
    principal = Principal(owner_id="owner", session_id="session")
    dialogue = Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
    state = dialogue.start(principal).state
    return state, principal


def restore(payload, state, principal, **updates):
    return load_checkpoint(
        payload,
        principal,
        **(
            dict(
                conversation_id=state.conversation_id,
                expected_version=state.version,
            )
            | updates
        ),
    )


def test_owned_state_roundtrip_preserves_ids_history_and_version(setup):
    state, principal = setup
    payload = dump_checkpoint(state, principal)
    assert json.loads(payload)["schema_version"] == 1
    restored = restore(payload, state, principal)
    assert restored == state and restored is not state
    assert restored.history == state.history


def test_owner_session_conversation_and_version_are_bound(setup):
    state, principal = setup
    payload = dump_checkpoint(state, principal)
    for other in (
        Principal(owner_id="other", session_id="session"),
        Principal(owner_id="owner", session_id="other"),
    ):
        with pytest.raises(CheckpointError, match="ACCESS_DENIED"):
            restore(payload, state, other)
        with pytest.raises(CheckpointError, match="ACCESS_DENIED"):
            dump_checkpoint(state, other)
    with pytest.raises(CheckpointError, match="CONVERSATION_MISMATCH"):
        restore(payload, state, principal, conversation_id="other")
    for version in (state.version + 1, True, -1, "1"):
        with pytest.raises(CheckpointError, match="VERSION_MISMATCH"):
            restore(payload, state, principal, expected_version=version)


@pytest.mark.parametrize("version", [0, 2, True, "1", None])
def test_unknown_schema_is_rejected_without_guessing_migrations(setup, version):
    state, principal = setup
    data = json.loads(dump_checkpoint(state, principal))
    data["schema_version"] = version
    with pytest.raises(CheckpointError, match="SCHEMA_UNSUPPORTED"):
        restore(json.dumps(data).encode(), state, principal)


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b"{}",
        b"\xff",
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":1,"state":NaN}',
        b'{"schema_version":1,"state":Infinity}',
    ],
)
def test_malformed_duplicate_and_nonfinite_json_fail_closed(setup, payload):
    state, principal = setup
    with pytest.raises(CheckpointError, match="CHECKPOINT_INVALID"):
        restore(payload, state, principal)


def test_limits_and_validation_failures_do_not_echo_saved_private_data(setup):
    state, principal = setup
    with pytest.raises(CheckpointError, match="TOO_LARGE"):
        restore(b"x" * (MAX_CHECKPOINT_BYTES + 1), state, principal)
    data = json.loads(dump_checkpoint(state, principal))
    data["state"]["unexpected"] = "PRIVATE_USER_TEXT_DO_NOT_ECHO"
    with pytest.raises(CheckpointError) as caught:
        restore(json.dumps(data).encode(), state, principal)
    assert str(caught.value) == "CHECKPOINT_INVALID"
    assert "PRIVATE" not in str(caught.value)


def test_restoring_pending_navigation_does_not_extend_its_deadline(setup, now):
    from shopping_ai import AIConfig
    from shopping_ai.browser import BrowserTarget
    from shopping_ai.conversation import ConversationState
    from shopping_ai.market import (
        Offer,
        ProductIdentity,
        SearchReceipt,
        Store,
        accept_search_receipt,
        plan_search,
    )
    from shopping_ai.navigation import NavigationReceipt, propose_navigation, verify_navigation

    state, principal = setup
    identity = ProductIdentity(brand="Example", model="One", attributes={"colour": "black"})
    market = plan_search(
        search_id="search",
        owner_id=principal.owner_id,
        session_id=principal.session_id,
        identity=identity,
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
    for index, task in enumerate(market.tasks):
        market = accept_search_receipt(
            market,
            SearchReceipt(
                search_id=market.search_id,
                owner_id=principal.owner_id,
                session_id=principal.session_id,
                task_id=task.task_id,
                delivery_context=market.delivery_context,
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
                        shipping="0",
                        extra_tax_and_fees="0",
                    ),
                ),
            ),
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            expected_version=market.version,
            now=now,
        )
    action = propose_navigation(
        market,
        principal=principal,
        offer_id="offer0",
        now=now,
        config=AIConfig(),
        target=BrowserTarget(tab_id=1, document_id="source", active=True, window_focused=True),
    )
    state = ConversationState.model_validate(
        state.model_dump()
        | {
            "active_market": market,
            "pending_navigation": action,
        }
    )
    loaded = restore(dump_checkpoint(state, principal), state, principal)
    assert loaded.pending_navigation.action_id == action.action_id
    assert loaded.pending_navigation.expires_at == action.expires_at
    failed = NavigationReceipt(
        action_id=action.action_id,
        search_id=action.search_id,
        owner_id=principal.owner_id,
        session_id=principal.session_id,
        status="failed",
        observed_at=now,
    )
    with pytest.raises(ValueError, match="NAVIGATION_EXPIRED"):
        verify_navigation(
            loaded.pending_navigation,
            failed,
            principal=principal,
            now=action.expires_at,
            config=AIConfig(),
        )
