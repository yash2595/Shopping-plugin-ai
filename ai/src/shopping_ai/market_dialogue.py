"""Conservative offline collection of user-confirmed cross-store search details.

This fallback accepts direct answers to one requested field or labelled updates.
It does not guess model IDs/variants from a product title or call a provider.
"""

import re
from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator

from shopping_ai.market import ProductIdentity
from shopping_ai.models import Contract, ShortText

DetailField = Literal["brand", "model", "attributes", "condition", "postcode"]


class MarketDraft(Contract):
    brand: ShortText | None = None
    model: ShortText | None = None
    attributes: dict[
        Annotated[str, Field(min_length=1, max_length=80)],
        Annotated[str, Field(min_length=1, max_length=120)],
    ] = Field(default_factory=dict, max_length=20)
    condition: Literal["new", "used", "refurbished"] | None = None
    postcode: Annotated[str, Field(pattern=r"^[1-9][0-9]{5}$")] | None = None

    @field_validator("attributes")
    @classmethod
    def distinct_attributes(cls, value):
        labels = [" ".join(key.casefold().split()) for key in value]
        if any(not label for label in labels) or any(not item.strip() for item in value.values()):
            raise ValueError("Variant labels and values cannot be blank")
        if len(labels) != len(set(labels)):
            raise ValueError("Duplicate variant labels")
        return value

    @property
    def missing(self) -> DetailField | None:
        for field in ("brand", "model", "attributes", "condition", "postcode"):
            if not getattr(self, field):
                return field
        return None

    def identity(self) -> ProductIdentity:
        if self.missing:
            raise ValueError("Incomplete market identity")
        return ProductIdentity(
            brand=self.brand,
            model=self.model,
            attributes=self.attributes,
            condition=self.condition,
        )

    def prompt(self, language: Literal["en", "hi-Latn"]) -> str:
        prompts = {
            "brand": ("Which brand?", "Kaunsa brand chahiye?"),
            "model": ("What is the exact model name/number?", "Exact model name/number batao."),
            "attributes": (
                "Specify every required variant detail, e.g. storage=128GB, colour=black. "
                "If there is only one variant, explicitly enter variant=standard.",
                "Saari required variant details do, jaise storage=128GB, colour=black. "
                "Sirf ek variant ho toh explicitly variant=standard likho.",
            ),
            "condition": (
                "Which condition: new, used or refurbished?",
                "Condition batao: new, used ya refurbished?",
            ),
            "postcode": (
                "What is the six-digit Indian delivery postcode?",
                "Delivery pincode batao (6 digits).",
            ),
        }
        if self.missing:
            return prompts[self.missing][0 if language == "en" else 1]
        attributes = ", ".join(f"{key}={value}" for key, value in self.attributes.items())
        summary = f"{self.brand} | {self.model} | {attributes} | {self.condition} | {self.postcode}"
        return summary + (
            ". Confirm these exact search details with 'confirm', or edit using "
            "brand: ...; model: ...; attributes: ...; condition: ...; postcode: ... ."
            if language == "en"
            else ". Details sahi hain toh 'confirm' bolo. Badalna ho toh "
            "brand: ...; model: ...; attributes: ...; condition: ...; postcode: ... likho."
        )


def collect_details(draft: MarketDraft, message: str) -> MarketDraft:
    """Invalid/ambiguous input raises without changing the existing checkpoint."""
    draft = MarketDraft.model_validate(draft.model_dump())
    text = message.strip()
    values = {}
    if ":" in text:
        for part in text.split(";"):
            key, separator, value = part.strip().partition(":")
            key = key.strip().casefold()
            if not separator or key not in MarketDraft.model_fields or key in values:
                raise ValueError("Use distinct supported detail labels")
            values[key] = value.strip()
    elif draft.missing:
        values[draft.missing] = text
    else:
        raise ValueError("Confirm or provide labelled corrections")
    if "attributes" in values:
        attributes = {}
        for item in values["attributes"].split(","):
            key, separator, value = item.strip().partition("=")
            key = " ".join(key.casefold().split())
            if not separator or not key or not value.strip() or key in attributes:
                raise ValueError("Variant details require unique key=value pairs")
            attributes[key] = value.strip()
        values["attributes"] = attributes
    if "condition" in values:
        values["condition"] = values["condition"].casefold()
    # Prevent an obvious conversation command being consumed as a brand/model.
    for field in ("brand", "model"):
        if field in values and re.search(
            r"\b(stop|cancel|confirm|search|cart|ignore|instructions|find|cheapest|cheaper|"
            r"compare|comparison|sasta|sasti|websites|website|stores|sites|across|ecommerce)\b",
            values[field],
            re.I,
        ):
            raise ValueError("Please provide only the requested product detail")
    return MarketDraft.model_validate(draft.model_dump() | values)


class DetailInterpreter(Protocol):
    """A model adapter proposes a complete draft, never an action or confirmation.

    Retain existing fields unless the current user explicitly corrects them.
    Unknown values stay absent. No principal, browser state or credentials are
    passed to the interpreter. The adapter must not retain cross-user context.
    """

    async def extract(self, message: str, draft: MarketDraft) -> MarketDraft: ...


class OfflineDetailInterpreter:
    async def extract(self, message: str, draft: MarketDraft) -> MarketDraft:
        return collect_details(draft, message)


def validate_detail_proposal(
    previous: MarketDraft,
    proposed: MarketDraft,
    message: str,
) -> MarketDraft:
    """Reject invented values even when provider output passes its JSON schema.

    Literal support is necessary, not proof of semantic correctness. A separate
    user confirmation is still required. No unit/currency/model-name expansion.
    """
    proposed = MarketDraft.model_validate(proposed.model_dump(warnings=False))
    source = " ".join(message.casefold().split())

    def supported(value: str) -> bool:
        phrase = " ".join(value.casefold().split())
        return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", source))

    for name in ("brand", "model", "condition", "postcode"):
        old, new = getattr(previous, name), getattr(proposed, name)
        if old != new and (new is None or not supported(new)):
            raise ValueError("Unsupported detail proposal")
    previous_attrs = {" ".join(k.casefold().split()): v for k, v in previous.attributes.items()}
    proposed_attrs = {" ".join(k.casefold().split()): v for k, v in proposed.attributes.items()}
    removed = previous_attrs.keys() - proposed_attrs.keys()
    if removed and not re.search(r"\battributes\s*:|\b(replace|reset)\b", source):
        raise ValueError("Silent removal of variant requirements")
    for key, value in proposed_attrs.items():
        if previous_attrs.get(key) != value and not supported(value):
            raise ValueError("Unsupported variant proposal")
    return proposed
