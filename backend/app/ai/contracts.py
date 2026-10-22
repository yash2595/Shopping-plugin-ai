import decimal
from typing import Any, Dict, List, Literal, Optional, Protocol, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator

PREFERENCE_KEY_WHITELIST = {
    "color",
    "size",
    "brand",
    "category",
    "delivery_speed",
    "max_delivery_days",
}


class AIContractBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Answer(AIContractBase):
    text: str


class Clarification(AIContractBase):
    question: str


class CreatePriceAlertArgs(AIContractBase):
    target_price: decimal.Decimal = Field(..., gt=0)


class SetBudgetArgs(AIContractBase):
    budget_max: decimal.Decimal = Field(..., gt=0)


class SetPreferenceArgs(AIContractBase):
    key: str
    value: Union[str, int]

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        if v not in PREFERENCE_KEY_WHITELIST:
            raise ValueError(
                f"Invalid preference key '{v}'. Whitelist: {sorted(PREFERENCE_KEY_WHITELIST)}"
            )
        return v

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Union[str, int]) -> Union[str, int]:
        if isinstance(v, str) and len(v) > 50:  # PLACEHOLDER
            raise ValueError("Preference string value too long (max 50 chars)")
        if isinstance(v, int) and (v < 0 or v > 365):  # PLACEHOLDER
            raise ValueError("Preference int value out of range (0-365)")
        return v


class RejectCurrentProductArgs(AIContractBase):
    reason: Optional[str] = Field(None, max_length=200)


class ToolProposal(AIContractBase):
    tool: Literal["create_price_alert", "set_preference", "set_budget", "reject_current_product"]
    args: Dict[str, Any]


AIDecision = Union[Answer, Clarification, ToolProposal]


class ShoppingContextContract(AIContractBase):
    budget_max: Optional[decimal.Decimal] = None
    preferences: Dict[str, Any] = Field(default_factory=dict)
    rejected_titles: List[str] = Field(default_factory=list)


class AIRequest(AIContractBase):
    user_text: str
    snapshot_facts: Optional[Dict[str, Any]] = None
    price_summary: Optional[Dict[str, Any]] = None
    recent_messages: List[Dict[str, Any]] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    context: Optional[ShoppingContextContract] = None
    review_summary: Optional[Dict[str, Any]] = None


class ReviewItemContract(AIContractBase):
    rating: int = Field(..., ge=1, le=5)
    title: Optional[str] = None
    text: str = Field(..., max_length=1000)


class ReviewSummaryRequest(AIContractBase):
    listing_title: str
    reviews: List[ReviewItemContract]


class ReviewSummary(AIContractBase):
    overall: str = Field(..., max_length=500)
    pros: List[str] = Field(..., max_length=10)
    cons: List[str] = Field(..., max_length=10)
    themes: List[str] = Field(..., max_length=10)
    caveat: str = Field(..., max_length=300)


class AIProviderError(Exception):
    """Raised when an external or mock AI provider fails to produce a decision or summary."""
    pass


class AIProvider(Protocol):
    def decide(self, request: AIRequest) -> AIDecision:
        """Process an AIRequest and decide on an Answer, Clarification, or ToolProposal."""
        ...

    def summarize_reviews(self, request: ReviewSummaryRequest) -> ReviewSummary:
        """Summarize reviews for a listing."""
        ...

