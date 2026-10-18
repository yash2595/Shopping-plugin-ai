"""State transitions with simulated extension observations, never real shopping effects."""

import asyncio
from datetime import timedelta

import pytest
from pydantic import ValidationError

from shopping_ai import AIConfig, Orchestrator, Principal, RunState, ShoppingAI
from shopping_ai.browser import (
    BrowserResult,
    BrowserTarget,
    CartLine,
    CartSnapshot,
    VisibleExecution,
)
from shopping_ai.models import AIRequest, ModelDecision, ToolProposal
from shopping_ai.orchestrator import OrchestrationError
from shopping_ai.providers import MockProvider


@pytest.fixture
def principal():
    return Principal(owner_id="u1", session_id="s1")


@pytest.fixture
def target():
    return BrowserTarget(tab_id=7, document_id="doc_1", active=True, window_focused=True)


@pytest.fixture
def orchestrator(now):
    return Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now))


@pytest.fixture
def state(orchestrator, principal, target, ai_request, grant):
    request = AIRequest.model_validate(
        ai_request.model_dump()
        | {
            "message": "Black wala cart mein daal do",
            "authorization": grant,
        }
    )
    return orchestrator.start(principal, request, target)


def advance(orchestrator, state, principal):
    return asyncio.run(orchestrator.advance(state, principal, expected_version=state.version))


def cart(now, quantity=0):
    return CartSnapshot(
        observed_at=now,
        document_id="doc_1",
        lines=()
        if quantity == 0
        else (CartLine(product_id="p42", variant_id="black", quantity=quantity),),
    )


def result_for(state, now, **updates):
    action = state.pending
    return BrowserResult.model_validate(
        dict(
            action_id=action.action_id,
            run_id=state.run_id,
            session_id=state.principal.session_id,
            run_version=action.run_version,
            source_document_id=action.target.document_id,
            target=action.target,
            observed_at=now,
            outcome="observed",
            cart=cart(now),
        )
        | updates
    )


def accept(orchestrator, state, principal, result):
    return orchestrator.accept_result(state, principal, result, expected_version=state.version)


def pending_cart(orchestrator, state, principal, now):
    baseline = advance(orchestrator, state, principal)
    assert baseline.pending.tool.name == "read_cart"
    observed = accept(orchestrator, baseline, principal, result_for(baseline, now))
    return advance(orchestrator, observed, principal)


def test_cart_flow_reads_baseline_then_verifies(orchestrator, state, principal, now):
    pending = pending_cart(orchestrator, state, principal, now)
    assert pending.pending.tool.name == "add_to_cart"
    assert pending.pending.presentation.require_foreground is True
    done = accept(orchestrator, pending, principal, result_for(pending, now, cart=cart(now, 1)))
    assert done.status == "succeeded"
    assert done.summary == "Verified cart: p42 / black; quantity 1."
    assert done.pending is None
    assert done.cart.quantity_of("p42", "black") == 1


def test_ensure_existing_quantity_avoids_click(orchestrator, state, principal, now):
    baseline = advance(orchestrator, state, principal)
    ready = accept(orchestrator, baseline, principal, result_for(baseline, now, cart=cart(now, 1)))
    done = advance(orchestrator, ready, principal)
    assert done.status == "succeeded"
    assert done.pending is None


def test_add_mode_uses_baseline(orchestrator, state, principal, now):
    grant = state.request.authorization.model_copy(update={"mode": "add_quantity"})
    request = state.request.model_copy(update={"authorization": grant})
    state = RunState.model_validate(state.model_dump() | {"request": request})
    baseline = advance(orchestrator, state, principal)
    ready = accept(orchestrator, baseline, principal, result_for(baseline, now, cart=cart(now, 2)))
    pending = advance(orchestrator, ready, principal)
    assert pending.cart_expectation.quantity == 3
    done = accept(orchestrator, pending, principal, result_for(pending, now, cart=cart(now, 3)))
    assert done.status == "succeeded"


def test_unknown_cart_only_reconciles_never_replays(orchestrator, state, principal, now):
    pending = pending_cart(orchestrator, state, principal, now)
    paused = accept(
        orchestrator,
        pending,
        principal,
        result_for(pending, now, outcome="unknown", error_code="LOST_RECEIPT", cart=None),
    )
    assert paused.status == "paused"
    assert paused.recovery_required
    ready = orchestrator.resume(paused, principal, paused.target, expected_version=paused.version)
    read = advance(orchestrator, ready, principal)
    assert read.pending.tool.name == "read_cart"
    still_unknown = accept(orchestrator, read, principal, result_for(read, now))
    assert still_unknown.status == "paused"
    assert still_unknown.recovery_required
    again = orchestrator.resume(
        still_unknown, principal, still_unknown.target, expected_version=still_unknown.version
    )
    assert advance(orchestrator, again, principal).pending.tool.name == "read_cart"


def test_unknown_cart_can_finish_after_read(orchestrator, state, principal, now):
    pending = pending_cart(orchestrator, state, principal, now)
    paused = accept(
        orchestrator,
        pending,
        principal,
        result_for(pending, now, outcome="unknown", error_code="LOST_RECEIPT", cart=None),
    )
    ready = orchestrator.resume(paused, principal, paused.target, expected_version=paused.version)
    read = advance(orchestrator, ready, principal)
    done = accept(orchestrator, read, principal, result_for(read, now, cart=cart(now, 1)))
    assert done.status == "succeeded"
    assert not done.recovery_required


def test_cancel_records_late_result_without_reviving(orchestrator, state, principal, now):
    pending = pending_cart(orchestrator, state, principal, now)
    cancelled = orchestrator.cancel(pending, principal, expected_version=pending.version)
    recorded = accept(
        orchestrator, cancelled, principal, result_for(pending, now, cart=cart(now, 1))
    )
    assert recorded.status == "cancelled"
    assert recorded.pending is None
    with pytest.raises(OrchestrationError, match="RUN_NOT_READY"):
        advance(orchestrator, recorded, principal)


@pytest.mark.parametrize("changes", [{"owner_id": "u2"}, {"session_id": "s2"}])
def test_other_identity_cannot_advance(orchestrator, state, principal, changes):
    other = Principal.model_validate(principal.model_dump() | changes)
    with pytest.raises(OrchestrationError, match="RUN_ACCESS_DENIED"):
        advance(orchestrator, state, other)


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_id", "other"),
        ("run_id", "other"),
        ("session_id", "other"),
        ("run_version", 99),
        ("source_document_id", "other"),
    ],
)
def test_result_binding_rejects_replays(orchestrator, state, principal, now, field, value):
    pending = advance(orchestrator, state, principal)
    with pytest.raises(OrchestrationError, match="RESULT_BINDING_MISMATCH"):
        accept(orchestrator, pending, principal, result_for(pending, now, **{field: value}))


def test_duplicate_receipt_not_applied_twice(orchestrator, state, principal, now):
    pending = advance(orchestrator, state, principal)
    result = result_for(pending, now)
    ready = accept(orchestrator, pending, principal, result)
    with pytest.raises(OrchestrationError, match="NO_PENDING_ACTION"):
        accept(orchestrator, ready, principal, result)


def test_old_checkpoint_version_rejected(orchestrator, state, principal):
    pending = advance(orchestrator, state, principal)
    with pytest.raises(OrchestrationError, match="STALE_RUN_VERSION"):
        asyncio.run(orchestrator.advance(pending, principal, expected_version=0))


def test_hidden_tab_pauses_before_model(orchestrator, state, principal):
    hidden = state.target.model_copy(update={"active": False})
    state = RunState.model_validate(state.model_dump() | {"target": hidden})
    paused = advance(orchestrator, state, principal)
    assert paused.pause_code == "TAB_NOT_FOREGROUND"
    assert paused.pending is None


def test_resume_cannot_switch_target(orchestrator, state, principal):
    paused = orchestrator.pause(state, principal, expected_version=state.version)
    other = state.target.model_copy(update={"tab_id": 8})
    with pytest.raises(OrchestrationError, match="RESUME_TARGET_MISMATCH"):
        orchestrator.resume(paused, principal, other, expected_version=paused.version)


def test_expired_mutation_is_uncertain_not_retryable(orchestrator, state, principal, now):
    pending = pending_cart(orchestrator, state, principal, now)
    orchestrator.ai.clock = lambda: now + timedelta(seconds=31)
    expired = orchestrator.expire_pending(pending, principal, expected_version=pending.version)
    assert expired.status == "paused"
    assert expired.recovery_required
    assert expired.pending is None


def test_stale_receipt_evidence_rejected(orchestrator, state, principal, now):
    pending = advance(orchestrator, state, principal)
    old_cart = cart(now - timedelta(seconds=1))
    with pytest.raises(OrchestrationError, match="STALE_RESULT_EVIDENCE"):
        accept(orchestrator, pending, principal, result_for(pending, now, cart=old_cart))


def test_budget_bounds_recovery_loop(orchestrator, state, principal):
    request = state.request.model_copy(update={"step_count": 8})
    state = RunState.model_validate(state.model_dump() | {"request": request})
    assert advance(orchestrator, state, principal).pause_code == "STEP_LIMIT"


def test_read_product_then_answer(orchestrator, principal, target, now, product):
    state = orchestrator.start(principal, AIRequest(request_id="r1", message="price?"), target)
    pending = advance(orchestrator, state, principal)
    ready = accept(
        orchestrator, pending, principal, result_for(pending, now, product=product, cart=None)
    )
    done = advance(orchestrator, ready, principal)
    assert done.status == "succeeded"
    assert done.last_response.message == "price: INR 1499.00"


def test_multiple_runs_do_not_mix_state(principal, target, product, now):
    class YieldingProvider:
        name = "test"

        async def decide(self, context):
            await asyncio.sleep(0)
            return ModelDecision(kind="tool", tool=ToolProposal(name="read_product"))

    orch = Orchestrator(
        ShoppingAI(YieldingProvider(), AIConfig(max_concurrent_calls=4), clock=lambda: now)
    )
    principals = [Principal(owner_id=f"user_{i}", session_id=f"session_{i}") for i in range(40)]
    states = [
        orch.start(p, AIRequest(request_id=f"request_{i}", message="read product"), target)
        for i, p in enumerate(principals)
    ]

    async def concurrent():
        return await asyncio.gather(
            *(
                orch.advance(s, p, expected_version=0)
                for s, p in zip(states, principals, strict=True)
            )
        )

    results = asyncio.run(concurrent())
    assert len({s.run_id for s in results}) == 40
    assert len({s.pending.action_id for s in results}) == 40
    assert [s.principal for s in results] == principals
    assert all(s.pending.session_id == s.principal.session_id for s in results)
    assert all(s.version == 1 and s.request.step_count == 1 for s in results)
    assert all(s.version == 0 and s.pending is None for s in states)


def test_browser_contract_rejects_invisible_execution():
    with pytest.raises(ValidationError):
        VisibleExecution(require_foreground=False)


def test_cart_contract_rejects_duplicate_lines(now):
    line = CartLine(product_id="p42", variant_id="black", quantity=1)
    with pytest.raises(ValidationError):
        CartSnapshot(observed_at=now, document_id="doc_1", lines=(line, line))


def test_variant_selection_continues_to_cart_without_repeating_click(
    orchestrator, state, principal, now
):
    grant = state.request.authorization.model_copy(update={"variant_id": "blue"})
    request = state.request.model_copy(
        update={"authorization": grant, "message": "Select blue and put it in cart"}
    )
    state = RunState.model_validate(state.model_dump() | {"request": request})
    pending = advance(orchestrator, state, principal)
    assert pending.pending.tool.name == "select_variant"
    product = state.request.product.model_copy(update={"selected_variant_id": "blue"})
    ready = accept(
        orchestrator, pending, principal, result_for(pending, now, product=product, cart=None)
    )
    assert ready.request.last_verified_tool.name == "select_variant"
    assert advance(orchestrator, ready, principal).pending.tool.name == "read_cart"


def test_repeated_verified_selection_pauses_instead_of_clicking(state, principal, now):
    tool = ToolProposal(name="select_variant", product_id="p42", variant_id="black")

    class RepeatingProvider:
        name = "test"

        async def decide(self, context):
            return ModelDecision(kind="tool", tool=tool)

    request = state.request.model_copy(update={"last_verified_tool": tool})
    state = RunState.model_validate(state.model_dump() | {"request": request})
    orch = Orchestrator(ShoppingAI(RepeatingProvider(), clock=lambda: now))
    paused = advance(orch, state, principal)
    assert paused.pause_code == "REPEATED_ACTION"
    assert paused.pending is None


def test_cart_postcondition_requires_exact_variant(orchestrator, state, principal, now):
    pending = pending_cart(orchestrator, state, principal, now)
    wrong = CartSnapshot(
        observed_at=now,
        document_id="doc_1",
        lines=(CartLine(product_id="p42", variant_id="blue", quantity=1),),
    )
    paused = accept(orchestrator, pending, principal, result_for(pending, now, cart=wrong))
    assert paused.status == "paused"
    assert paused.recovery_required


def test_result_from_different_tab_is_rejected(orchestrator, state, principal, now):
    pending = advance(orchestrator, state, principal)
    target = pending.target.model_copy(update={"tab_id": 999})
    with pytest.raises(OrchestrationError, match="RESULT_BINDING_MISMATCH"):
        accept(orchestrator, pending, principal, result_for(pending, now, target=target))


def test_stop_precedes_visibility_checks(orchestrator, state, principal):
    request = state.request.model_copy(update={"message": "stop"})
    target = state.target.model_copy(update={"active": False})
    state = RunState.model_validate(state.model_dump() | {"request": request, "target": target})
    stopped = advance(orchestrator, state, principal)
    assert stopped.status == "cancelled"
    assert stopped.pending is None


def test_new_document_does_not_inherit_mutation_grant(orchestrator, state, principal, now):
    pending = advance(orchestrator, state, principal)
    target = pending.target.model_copy(update={"document_id": "new_document"})
    snapshot = cart(now).model_copy(update={"document_id": "new_document"})
    ready = accept(
        orchestrator, pending, principal, result_for(pending, now, target=target, cart=snapshot)
    )
    paused = advance(orchestrator, ready, principal)
    assert paused.pause_code == "DOCUMENT_CHANGED"
    assert paused.pending is None


def test_result_evidence_without_matching_product_is_rejected(orchestrator, principal, state, now):
    class VariantProvider:
        name = "test"

        async def decide(self, context):
            return ModelDecision(
                kind="tool",
                tool=ToolProposal(name="select_variant", product_id="p42", variant_id="black"),
            )

    orch = Orchestrator(ShoppingAI(VariantProvider(), clock=lambda: now))
    pending = advance(orch, state, principal)
    product = state.request.product.model_copy(update={"product_id": "other_product"})
    with pytest.raises(OrchestrationError, match="OBSERVED_PRODUCT_MISMATCH"):
        accept(orch, pending, principal, result_for(pending, now, product=product, cart=None))


def test_resume_revalidates_copied_browser_target(orchestrator, state, principal):
    paused = orchestrator.pause(state, principal, expected_version=state.version)
    invalid = state.target.model_copy(update={"active": "true"})
    with pytest.raises(ValidationError):
        orchestrator.resume(paused, principal, invalid, expected_version=paused.version)
