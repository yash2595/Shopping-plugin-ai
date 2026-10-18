"""Transport-independent contracts. All inputs are data, never executable code."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
ToolName = Literal["read_product", "read_cart", "select_variant", "add_to_cart"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Variant(Contract):
    variant_id: Identifier
    label: ShortText


class ProductSnapshot(Contract):
    snapshot_id: Identifier
    product_id: Identifier
    document_id: Identifier
    title: ShortText
    observed_at: AwareDatetime
    selected_variant_id: Identifier | None = None
    variants: list[Variant] = Field(default_factory=list, max_length=50)
    price: Money | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    in_stock: bool | None = None
    specs: dict[Annotated[str, Field(min_length=1, max_length=80)], ShortText] = Field(
        default_factory=dict, max_length=30
    )

    @model_validator(mode="after")
    def coherent_product(self) -> Self:
        ids = [v.variant_id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate variant IDs")
        if self.selected_variant_id and self.selected_variant_id not in ids:
            raise ValueError("Selected variant must be in the observed variant list")
        if (self.price is None) != (self.currency is None):
            raise ValueError("Price and currency must be supplied together")
        return self


class Message(Contract):
    role: Literal["user", "assistant"]
    text: Annotated[str, Field(min_length=1, max_length=2000)]


class MutationAuthorization(Contract):
    """Trusted caller grant, never extracted from model output or source text.

    The future backend must create this only after resolving explicit user intent.
    It is not authentication and must not be accepted verbatim from an untrusted client.
    """

    request_id: Identifier
    allowed_tools: list[Literal["select_variant", "add_to_cart"]] = Field(
        min_length=1, max_length=2
    )
    product_id: Identifier
    document_id: Identifier
    variant_id: Identifier
    quantity: Annotated[int, Field(strict=True, ge=1, le=10)]
    mode: Literal["ensure_quantity", "add_quantity"]
    max_unit_price: Money
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    expires_at: AwareDatetime


class ToolProposal(Contract):
    name: ToolName
    product_id: Identifier | None = None
    variant_id: Identifier | None = None
    quantity: Annotated[int, Field(strict=True, ge=1, le=10)] | None = None
    mode: Literal["ensure_quantity", "add_quantity"] | None = None

    @model_validator(mode="after")
    def tool_arguments(self) -> Self:
        args = (self.product_id, self.variant_id, self.quantity, self.mode)
        if self.name in {"read_product", "read_cart"} and any(v is not None for v in args):
            raise ValueError("Read tools accept no model-selected target or mutation arguments")
        if self.name == "select_variant":
            if not self.product_id or not self.variant_id:
                raise ValueError("Variant selection requires exact product and variant IDs")
            if self.quantity is not None or self.mode is not None:
                raise ValueError("Variant selection does not accept cart arguments")
        if self.name == "add_to_cart" and any(v is None for v in args):
            raise ValueError("Cart proposal requires product, variant, quantity and mode")
        return self


class ModelDecision(Contract):
    """One structured next step. Answers select facts instead of inventing prose."""

    kind: Literal["answer", "clarification", "tool"]
    fact_ids: list[
        Annotated[str, Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9_.:-]+$")]
    ] = Field(default_factory=list, max_length=10)
    question: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    tool: ToolProposal | None = None

    @model_validator(mode="after")
    def exactly_one_decision(self) -> Self:
        if self.kind == "answer":
            if not self.fact_ids or self.question is not None or self.tool is not None:
                raise ValueError("Answer requires only nonempty fact_ids")
            if len(set(self.fact_ids)) != len(self.fact_ids):
                raise ValueError("Fact IDs must be unique")
        elif self.kind == "clarification":
            if not self.question or self.fact_ids or self.tool is not None:
                raise ValueError("Clarification requires only a question")
        elif self.tool is None or self.fact_ids or self.question is not None:
            raise ValueError("Tool decision requires only a tool proposal")
        return self


class AIRequest(Contract):
    request_id: Identifier
    message: Annotated[str, StringConstraints(min_length=1, max_length=2000, strip_whitespace=True)]
    language: Literal["en", "hi-Latn"] = "hi-Latn"
    product: ProductSnapshot | None = None
    history: list[Message] = Field(default_factory=list, max_length=20)
    available_tools: list[ToolName] = Field(default_factory=lambda: ["read_product"], max_length=4)
    authorization: MutationAuthorization | None = None
    cancelled: bool = False
    step_count: Annotated[int, Field(strict=True, ge=0)] = 0


class Fact(Contract):
    fact_id: Annotated[str, Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9_.:-]+$")]
    key: str
    value: str
    snapshot_id: Identifier


class AIResponse(Contract):
    request_id: Identifier
    status: Literal["answered", "needs_input", "tool_proposed", "blocked", "error", "cancelled"]
    message: str
    facts: list[Fact] = Field(default_factory=list)
    tool: ToolProposal | None = None
    expected_document_id: Identifier | None = None
    code: str | None = None
    retryable: bool = False
    provider: str
    prompt_version: str
