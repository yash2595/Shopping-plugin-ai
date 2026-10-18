"""Contracts for a future visible extension executor; no browser is launched here."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from shopping_ai.models import (
    Contract,
    Identifier,
    ProductSnapshot,
    ToolProposal,
)


class BrowserTarget(Contract):
    tab_id: Annotated[int, Field(strict=True, ge=0)]
    document_id: Identifier
    active: StrictBool
    window_focused: StrictBool

    @property
    def visible(self) -> bool:
        return self.active and self.window_focused


class VisibleExecution(Contract):
    """The executor must enforce these, not merely display a progress message.

    Its packaged site adapter performs scrolling/typing/clicks as appropriate for
    the semantic tool. Arbitrary scripts/selectors/URLs are never model arguments.
    """

    require_foreground: Literal[True] = True
    highlight_target: Literal[True] = True
    scroll_behavior: Literal["smooth"] = "smooth"
    typing_mode: Literal["visible"] = "visible"


class BrowserAction(Contract):
    action_id: Identifier
    run_id: Identifier
    session_id: Identifier
    run_version: Annotated[int, Field(strict=True, ge=1)]
    target: BrowserTarget
    tool: ToolProposal
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    presentation: VisibleExecution = Field(default_factory=VisibleExecution)

    @model_validator(mode="after")
    def dispatch_requirements(self) -> Self:
        if not self.target.visible:
            raise ValueError("Actions require an active tab in a focused browser window")
        if self.expires_at <= self.issued_at:
            raise ValueError("Action must expire after it was issued")
        return self


class CartLine(Contract):
    product_id: Identifier
    variant_id: Identifier
    quantity: Annotated[int, Field(strict=True, ge=1, le=1000)]


class CartSnapshot(Contract):
    observed_at: AwareDatetime
    document_id: Identifier
    lines: tuple[CartLine, ...] = Field(default_factory=tuple, max_length=200)

    @model_validator(mode="after")
    def distinct_lines(self) -> Self:
        keys = [(line.product_id, line.variant_id) for line in self.lines]
        if len(keys) != len(set(keys)):
            raise ValueError("Normalize duplicate cart lines before submitting observations")
        return self

    def quantity_of(self, product_id: str, variant_id: str) -> int:
        return next(
            (
                line.quantity
                for line in self.lines
                if (line.product_id, line.variant_id) == (product_id, variant_id)
            ),
            0,
        )


class BrowserResult(Contract):
    action_id: Identifier
    run_id: Identifier
    session_id: Identifier
    run_version: Annotated[int, Field(strict=True, ge=1)]
    source_document_id: Identifier
    target: BrowserTarget
    observed_at: AwareDatetime
    outcome: Literal["observed", "failed", "unknown"]
    product: ProductSnapshot | None = None
    cart: CartSnapshot | None = None
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def observation_requirements(self) -> Self:
        if self.outcome == "observed":
            if self.product is None and self.cart is None:
                raise ValueError("Observed outcome needs product or cart evidence")
            if self.error_code is not None:
                raise ValueError("Observed outcome cannot contain an error")
        elif self.error_code is None:
            raise ValueError("Failed/unknown results need a typed error code")
        for snapshot in (self.product, self.cart):
            if snapshot and snapshot.document_id != self.target.document_id:
                raise ValueError("Snapshot document must match the observed browser target")
        return self
