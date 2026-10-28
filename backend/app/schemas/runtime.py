"""Public runtime inputs; identity, grants and checkpoints never come from callers."""

import uuid
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator
from shopping_ai.browser import BrowserAction, BrowserTarget, CartSnapshot
from shopping_ai.comparison import ComparisonResult
from shopping_ai.market import MarketResult, Offer, ProductIdentity, SearchTask
from shopping_ai.market_answers import MarketAnswer
from shopping_ai.models import Contract, Fact, Identifier, Money, ProductSnapshot

Version = Annotated[int, Field(strict=True, ge=0)]


class RuntimeLimits(Contract):
    json_bytes: int
    audio_bytes: int
    events_per_user_per_day: int
    speech_per_user_per_day: int


class Capabilities(Contract):
    provider: Literal["mock", "chat", "local"]
    analysis_only: bool
    local_fallback: Literal["deterministic", "cloud"] | None
    authentication: Literal["anonymous", "oidc"]
    mutations_enabled: bool
    browser_origins: list[str]
    speech_to_text_configured: bool
    text_to_speech_configured: bool
    limits: RuntimeLimits


class StartConversation(Contract):
    language: Literal["en", "hi-Latn"] = "hi-Latn"
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "INR"


class RuntimeEvent(Contract):
    event_id: Identifier
    expected_version: Version
    kind: Literal["text", "cancel", "advance", "resume"]
    message: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    target: BrowserTarget | None = None
    origin: Annotated[str, Field(max_length=200)] | None = None


class Confirmation(Contract):
    event_id: Identifier
    expected_version: Version
    confirmed: Literal[True]
    operation: Literal["select_variant", "add_to_cart"]
    snapshot_id: Identifier
    product_id: Identifier
    variant_id: Identifier
    quantity: Annotated[int, Field(strict=True, ge=1, le=10)]
    mode: Literal["ensure_quantity", "add_quantity"]
    max_unit_price: Money
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    target: BrowserTarget
    origin: Annotated[str, Field(max_length=200)]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_boolean_confirmation(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class ClaimWork(Contract):
    claim_id: Identifier
    expected_version: Version
    target: BrowserTarget
    origin: Annotated[str, Field(max_length=200)]


class BrowserObservation(Contract):
    outcome: Literal["observed", "failed", "unknown"]
    target: BrowserTarget
    observed_at: AwareDatetime
    product: ProductSnapshot | None = None
    cart: CartSnapshot | None = None
    cart_coverage: Literal["complete", "partial", "unknown"] | None = None
    error_code: Identifier | None = None


class SearchObservation(Contract):
    status: Literal["complete", "failed", "blocked", "timed_out"]
    target: BrowserTarget
    observed_at: AwareDatetime
    offers: tuple[Offer, ...] = Field(default=(), max_length=20)


class NavigationObservation(Contract):
    status: Literal["opened", "failed"]
    observed_at: AwareDatetime
    url: Annotated[str, Field(max_length=3000)] | None = None
    target: BrowserTarget | None = None
    identity: ProductIdentity | None = None
    product: ProductSnapshot | None = None


class Receipt(Contract):
    event_id: Identifier
    expected_version: Version
    claim_token: uuid.UUID
    origin: Annotated[str, Field(max_length=200)]
    browser: BrowserObservation | None = None
    search: SearchObservation | None = None
    navigation: NavigationObservation | None = None


class SpeechRequest(Contract):
    expected_version: Version


class PublicReply(Contract):
    inference_fallback: Literal["deterministic", "cloud"] | None = None
    conversation_id: Identifier
    session_id: uuid.UUID
    version: Version
    event_id: Identifier | None = None
    status: Literal[
        "chat",
        "needs_input",
        "ready_for_search",
        "ready_to_navigate",
        "awaiting_browser",
        "working",
        "complete",
        "paused",
        "cancelled",
        "error",
    ]
    message: Annotated[str, Field(min_length=1, max_length=8000)]
    speech_text: Annotated[str, Field(min_length=1, max_length=8000)]
    facts: tuple[Fact, ...] = Field(default=(), max_length=10)
    products: tuple[ProductSnapshot, ...] = Field(default=(), max_length=10)
    comparison: ComparisonResult | None = None
    market_result: MarketResult | None = None
    market_answer: MarketAnswer | None = None
    work_ids: tuple[Identifier, ...] = Field(default=(), max_length=8)
    origin: str | None = None
    speech_available: bool = False


class PublicWork(Contract):
    dispatch_id: Identifier
    kind: Literal["browser", "market", "navigation"]
    status: Literal["pending", "claimed"]
    version: Version
    deadline_at: AwareDatetime


class PublicNavigation(Contract):
    action_id: Identifier
    search_id: Identifier
    offer: Offer
    source: BrowserTarget
    created_at: AwareDatetime
    expires_at: AwareDatetime
    reuse_visible_tab: Literal[True]


class WorkConstraints(Contract):
    max_unit_price: Money
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class ClaimedWork(Contract):
    dispatch_id: Identifier
    kind: Literal["browser", "market", "navigation"]
    version: Version
    deadline_at: AwareDatetime
    claim_token: uuid.UUID
    origin: Annotated[str, Field(max_length=200)]
    work: BrowserAction | SearchTask | PublicNavigation
    constraints: WorkConstraints | None = None
