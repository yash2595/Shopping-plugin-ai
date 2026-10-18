"""Explicit checkpoint transitions; caller persists with compare-and-swap before dispatch.

This module keeps no user/session registry and executes no browser or DB actions.
Trusted principal and saved state must come from the authenticated backend later.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import Field, StrictBool, model_validator

from shopping_ai.browser import BrowserAction, BrowserResult, BrowserTarget, CartSnapshot
from shopping_ai.engine import ShoppingAI
from shopping_ai.models import AIRequest, AIResponse, Contract, Identifier, ShortText, ToolProposal


class Principal(Contract):
    owner_id: Identifier
    session_id: Identifier


class CartExpectation(Contract):
    product_id: Identifier
    variant_id: Identifier
    quantity: Annotated[int, Field(strict=True, ge=0, le=1000)]


class RunState(Contract):
    run_id: Identifier
    principal: Principal
    version: Annotated[int, Field(strict=True, ge=0)] = 0
    status: Literal[
        "ready", "awaiting_browser", "needs_input", "paused", "succeeded", "failed", "cancelled"
    ] = "ready"
    request: AIRequest
    target: BrowserTarget
    pending: BrowserAction | None = None
    last_response: AIResponse | None = None
    cart: CartSnapshot | None = None
    cart_expectation: CartExpectation | None = None
    recovery_required: StrictBool = False
    pause_code: Identifier | None = None
    summary: ShortText | None = None

    @model_validator(mode="after")
    def checkpoint_requirements(self) -> Self:
        if self.pending is not None:
            if self.status not in {"awaiting_browser", "cancelled"}:
                raise ValueError("Only awaiting/cancelled runs may retain a pending action")
            if (self.pending.run_id, self.pending.session_id, self.pending.run_version) != (
                self.run_id,
                self.principal.session_id,
                self.version,
            ):
                # Cancel increments the version, but keeps the original action receipt binding.
                if self.status != "cancelled" or (
                    self.pending.run_id != self.run_id
                    or self.pending.session_id != self.principal.session_id
                    or self.pending.run_version >= self.version
                ):
                    raise ValueError("Pending action does not belong to this checkpoint")
        elif self.status == "awaiting_browser":
            raise ValueError("Awaiting browser requires a pending action")
        if self.recovery_required and self.cart_expectation is None:
            raise ValueError("Cart recovery requires the original expected quantity")
        return self


class OrchestrationError(ValueError):
    """Stable codes, without reflecting untrusted result contents."""


class Orchestrator:
    def __init__(self, ai: ShoppingAI):
        self.ai = ai

    @staticmethod
    def _check(state: RunState, principal: Principal, expected_version: int) -> RunState:
        state = RunState.model_validate(state.model_dump(warnings=False))
        if state.principal != principal:
            raise OrchestrationError("RUN_ACCESS_DENIED")
        if state.version != expected_version:
            raise OrchestrationError("STALE_RUN_VERSION")
        return state

    @staticmethod
    def _change(state: RunState, **updates) -> RunState:
        return RunState.model_validate(
            state.model_dump() | updates | {"version": state.version + 1}
        )

    def start(self, principal: Principal, request: AIRequest, target: BrowserTarget) -> RunState:
        # This is a checkpoint candidate, not a durable task or authorization check.
        return RunState.model_validate(
            dict(
                run_id=uuid4().hex,
                principal=principal.model_dump(),
                request=request.model_dump(warnings=False),
                target=target.model_dump(),
            )
        )

    def _fresh(
        self, observed_at: datetime, now: datetime, *, after: datetime | None = None
    ) -> bool:
        age = (now - observed_at).total_seconds()
        return 0 <= age <= self.ai.config.max_snapshot_age_seconds and (
            after is None or observed_at >= after
        )

    def _dispatch(self, state: RunState, tool: ToolProposal) -> RunState:
        now = self.ai.clock()
        action = BrowserAction(
            action_id=uuid4().hex,
            run_id=state.run_id,
            session_id=state.principal.session_id,
            run_version=state.version + 1,
            target=state.target,
            tool=tool,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.ai.config.timeout_seconds),
        )
        return self._change(state, status="awaiting_browser", pending=action, pause_code=None)

    async def advance(
        self, state: RunState, principal: Principal, *, expected_version: int
    ) -> RunState:
        state = self._check(state, principal, expected_version)
        if state.status != "ready":
            raise OrchestrationError("RUN_NOT_READY")
        if state.request.stop_requested:
            return self._change(state, status="cancelled", pause_code="USER_CANCELLED")
        if not state.target.visible:
            return self._change(state, status="paused", pause_code="TAB_NOT_FOREGROUND")
        if state.request.step_count >= self.ai.config.max_steps:
            return self._change(state, status="paused", pause_code="STEP_LIMIT")
        request = AIRequest.model_validate(
            state.request.model_dump()
            | {
                "step_count": state.request.step_count + 1,
            }
        )
        next_state = RunState.model_validate(state.model_dump() | {"request": request})
        if state.recovery_required:
            if "read_cart" not in state.request.available_tools:
                return self._change(next_state, status="paused", pause_code="CART_READ_UNAVAILABLE")
            return self._dispatch(next_state, ToolProposal(name="read_cart"))
        if state.request.product and state.request.product.document_id != state.target.document_id:
            return self._change(state, status="paused", pause_code="DOCUMENT_CHANGED")
        response = await self.ai.decide(state.request)
        if response.status != "tool_proposed":
            status = {
                "answered": "succeeded",
                "needs_input": "needs_input",
                "blocked": "paused",
                "error": "failed",
                "cancelled": "cancelled",
            }[response.status]
            return self._change(
                next_state, status=status, last_response=response, pause_code=response.code
            )
        # Refresh cart baseline before a cart mutation. Do not infer one from chat/model text.
        state = RunState.model_validate(
            state.model_dump() | {"request": request, "last_response": response}
        )
        tool = response.tool
        if tool == request.last_verified_tool and tool.name == "select_variant":
            return self._change(state, status="paused", pause_code="REPEATED_ACTION")
        if tool.name == "add_to_cart":
            if state.cart is None or not self._fresh(state.cart.observed_at, self.ai.clock()):
                if "read_cart" not in request.available_tools:
                    return self._change(state, status="paused", pause_code="CART_BASELINE_REQUIRED")
                return self._dispatch(state, ToolProposal(name="read_cart"))
            baseline = state.cart.quantity_of(tool.product_id, tool.variant_id)
            expected = baseline + tool.quantity if tool.mode == "add_quantity" else tool.quantity
            if expected > 1000:
                return self._change(state, status="paused", pause_code="CART_QUANTITY_LIMIT")
            expectation = CartExpectation(
                product_id=tool.product_id, variant_id=tool.variant_id, quantity=expected
            )
            state = RunState.model_validate(state.model_dump() | {"cart_expectation": expectation})
            if baseline == expected:
                return self._complete_cart(state)
        return self._dispatch(state, tool)

    def _complete_cart(self, state: RunState, *, target: BrowserTarget | None = None) -> RunState:
        expected = state.cart_expectation
        return self._change(
            state,
            status="succeeded",
            pending=None,
            recovery_required=False,
            last_response=None,
            pause_code=None,
            target=target or state.target,
            summary=f"Verified cart: {expected.product_id} / {expected.variant_id}; "
            f"quantity {expected.quantity}.",
        )

    def accept_result(
        self, state: RunState, principal: Principal, result: BrowserResult, *, expected_version: int
    ) -> RunState:
        state = self._check(state, principal, expected_version)
        result = BrowserResult.model_validate(result.model_dump(warnings=False))
        action = state.pending
        if action is None:
            raise OrchestrationError("NO_PENDING_ACTION")
        if (
            result.action_id,
            result.run_id,
            result.session_id,
            result.run_version,
            result.source_document_id,
            result.target.tab_id,
        ) != (
            action.action_id,
            state.run_id,
            principal.session_id,
            action.run_version,
            action.target.document_id,
            action.target.tab_id,
        ):
            raise OrchestrationError("RESULT_BINDING_MISMATCH")
        now = self.ai.clock()
        if not self._fresh(result.observed_at, now, after=action.issued_at):
            raise OrchestrationError("STALE_RESULT")
        for snapshot in (result.product, result.cart):
            if snapshot and (
                not self._fresh(snapshot.observed_at, now, after=action.issued_at)
                or snapshot.observed_at > result.observed_at
            ):
                raise OrchestrationError("STALE_RESULT_EVIDENCE")
        updates = {"pending": None, "target": result.target}
        if state.status == "cancelled":
            # Preserve observations but never revive the cancelled task.
            expected = state.cart_expectation
            reconciled = (
                result.outcome == "observed"
                and result.cart is not None
                and (
                    expected is not None
                    and result.cart.quantity_of(expected.product_id, expected.variant_id)
                    == expected.quantity
                )
            )
            return self._change(
                state,
                **updates,
                cart=result.cart or state.cart,
                recovery_required=state.cart_expectation is not None and not reconciled,
                pause_code="CANCELLED_RESULT_RECORDED",
            )
        if result.outcome != "observed":
            uncertain_cart = action.tool.name == "add_to_cart"
            return self._change(
                state,
                **updates,
                status="paused",
                recovery_required=uncertain_cart or state.recovery_required,
                pause_code="ACTION_UNKNOWN" if uncertain_cart else result.error_code,
            )
        if not result.target.visible:
            return self._change(
                state,
                **updates,
                status="paused",
                recovery_required=action.tool.name == "add_to_cart" or state.recovery_required,
                pause_code="TAB_NOT_FOREGROUND",
            )
        if action.tool.name in {"read_cart", "add_to_cart"}:
            if result.cart is None:
                raise OrchestrationError("CART_EVIDENCE_REQUIRED")
            state = RunState.model_validate(state.model_dump() | {"cart": result.cart})
            if state.cart_expectation is not None:
                expected = state.cart_expectation
                if (
                    result.cart.quantity_of(expected.product_id, expected.variant_id)
                    == expected.quantity
                ):
                    return self._complete_cart(state, target=result.target)
                return self._change(
                    state,
                    **updates,
                    status="paused",
                    recovery_required=True,
                    pause_code="CART_OUTCOME_UNCERTAIN",
                )
        else:
            product = result.product
            if product is None:
                raise OrchestrationError("PRODUCT_EVIDENCE_REQUIRED")
            old = state.request.product
            if old and product.product_id != old.product_id:
                raise OrchestrationError("OBSERVED_PRODUCT_MISMATCH")
            if (
                action.tool.name == "select_variant"
                and product.selected_variant_id != action.tool.variant_id
            ):
                return self._change(
                    state, **updates, status="paused", pause_code="VARIANT_NOT_VERIFIED"
                )
            request = AIRequest.model_validate(
                state.request.model_dump() | {"product": product, "last_verified_tool": action.tool}
            )
            state = RunState.model_validate(state.model_dump() | {"request": request, "cart": None})
        return self._change(state, **updates, status="ready", pause_code=None)

    def expire_pending(
        self, state: RunState, principal: Principal, *, expected_version: int
    ) -> RunState:
        state = self._check(state, principal, expected_version)
        if state.pending is None:
            raise OrchestrationError("NO_PENDING_ACTION")
        if self.ai.clock() < state.pending.expires_at:
            raise OrchestrationError("ACTION_NOT_EXPIRED")
        uncertain_cart = state.pending.tool.name == "add_to_cart"
        return self._change(
            state,
            pending=None,
            status="cancelled" if state.status == "cancelled" else "paused",
            recovery_required=uncertain_cart or state.recovery_required,
            pause_code="ACTION_EXPIRED",
        )

    def pause(self, state: RunState, principal: Principal, *, expected_version: int) -> RunState:
        state = self._check(state, principal, expected_version)
        if state.status != "ready":
            raise OrchestrationError("RUN_NOT_READY")
        return self._change(state, status="paused", pause_code="USER_PAUSED")

    def resume(
        self, state: RunState, principal: Principal, target: BrowserTarget, *, expected_version: int
    ) -> RunState:
        state = self._check(state, principal, expected_version)
        target = BrowserTarget.model_validate(target.model_dump(warnings=False))
        if state.status != "paused" or state.pending is not None:
            raise OrchestrationError("RUN_NOT_RESUMABLE")
        if target.tab_id != state.target.tab_id or target.document_id != state.target.document_id:
            raise OrchestrationError("RESUME_TARGET_MISMATCH")
        return self._change(state, target=target, status="ready", pause_code=None)

    def cancel(self, state: RunState, principal: Principal, *, expected_version: int) -> RunState:
        state = self._check(state, principal, expected_version)
        if state.status in {"succeeded", "failed", "cancelled"}:
            raise OrchestrationError("RUN_TERMINAL")
        return self._change(state, status="cancelled", pause_code="USER_CANCELLED")
