"""Deterministic side-by-side evidence; no model calls, browsing or suitability score."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from shopping_ai.config import AIConfig
from shopping_ai.context import product_facts
from shopping_ai.models import Contract, Fact, Identifier, Money, ProductSnapshot, ShortText


class ComparisonRow(Contract):
    attribute: ShortText
    left: Fact | None = None
    right: Fact | None = None

    @model_validator(mode="after")
    def has_evidence(self) -> Self:
        if self.left is None and self.right is None:
            raise ValueError("Comparison row requires evidence on at least one side")
        return self


class ComparisonResult(Contract):
    left_product_id: Identifier
    right_product_id: Identifier
    left_title: ShortText
    right_title: ShortText
    rows: tuple[ComparisonRow, ...] = Field(min_length=1, max_length=8)
    price_relation: Literal["left_lower", "right_lower", "equal", "missing", "different_currency"]
    difference: Money | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    limitations: tuple[ShortText, ...] = Field(default_factory=tuple, max_length=5)

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.left_product_id == self.right_product_id:
            raise ValueError("Comparison needs distinct products")
        comparable = self.price_relation in {"left_lower", "right_lower", "equal"}
        if comparable != (self.difference is not None and self.currency is not None):
            raise ValueError("Comparable prices require a difference and currency")
        if not comparable and (self.difference is not None or self.currency is not None):
            raise ValueError("Non-comparable prices cannot have a calculated difference")
        if self.price_relation == "equal" and self.difference != 0:
            raise ValueError("Equal prices must have zero difference")
        return self

    def speech(self, language: Literal["en", "hi-Latn"]) -> str:
        hi = language == "hi-Latn"
        intro = (
            f"Products {self.left_product_id} aur {self.right_product_id}. "
            if hi
            else (f"Products {self.left_product_id} and {self.right_product_id}. ")
        )
        if self.price_relation in {"left_lower", "right_lower"}:
            cheaper = (
                self.left_product_id
                if self.price_relation == "left_lower"
                else self.right_product_id
            )
            detail = (
                f"{cheaper} ka listed price {self.currency} {self.difference:.2f} kam hai. "
                if hi
                else f"{cheaper}: listed price is lower by {self.currency} {self.difference:.2f}. "
            )
        elif self.price_relation == "equal":
            detail = (
                "Observed listed prices same hain. " if hi else "Observed listed prices are equal. "
            )
        elif self.price_relation == "different_currency":
            detail = (
                "Currencies alag hain; price ranking nahi ki. "
                if hi
                else ("Currencies differ; no price ranking was made. ")
            )
        else:
            detail = (
                "Dono taraf complete price evidence nahi hai. "
                if hi
                else ("Price evidence is incomplete. ")
            )
        return (
            intro
            + detail
            + (
                "Delivery aur fees verify nahi hain. Ye overall quality recommendation nahi hai."
                if hi
                else "Delivery/fees are unverified. This is not an overall quality recommendation."
            )
        )

    def render(self, language: Literal["en", "hi-Latn"]) -> str:
        unknown = "unknown" if language == "en" else "data nahi hai"
        shortened = False

        def display(value: str) -> str:
            nonlocal shortened
            if len(value) > 200:
                shortened = True
                return value[:197] + "..."
            return value

        lines = [
            f"{display(self.left_title)} [{self.left_product_id}] vs "
            f"{display(self.right_title)} [{self.right_product_id}]"
        ]
        for row in self.rows:
            lines.append(
                f"{row.attribute}: {display(row.left.value) if row.left else unknown} | "
                f"{display(row.right.value) if row.right else unknown}"
            )
        if self.price_relation in {"left_lower", "right_lower"}:
            cheaper = (
                self.left_product_id
                if self.price_relation == "left_lower"
                else self.right_product_id
            )
            lines.append(
                f"Lower price: {cheaper}; difference {self.currency} {self.difference:.2f}."
            )
        elif self.price_relation == "equal":
            lines.append("Observed prices are equal.")
        lines.extend(self.limitations)
        if shortened:
            lines.append(
                "Display shortened; full source values are retained in comparison evidence."
            )
        return "\n".join(lines)


class ComparisonError(ValueError):
    pass


def compare_products(
    left: ProductSnapshot, right: ProductSnapshot, *, now: datetime, config: AIConfig
) -> ComparisonResult:
    # Copy/revalidate caller objects; no product can be mutated while comparing.
    left = ProductSnapshot.model_validate(left.model_dump(warnings=False))
    right = ProductSnapshot.model_validate(right.model_dump(warnings=False))
    if left.product_id == right.product_id:
        raise ComparisonError("SAME_PRODUCT")
    if left.snapshot_id == right.snapshot_id:
        raise ComparisonError("AMBIGUOUS_SNAPSHOT_IDS")
    for product in (left, right):
        age = (now - product.observed_at).total_seconds()
        if not 0 <= age <= config.max_snapshot_age_seconds:
            raise ComparisonError("STALE_COMPARISON_EVIDENCE")
    left_facts = {f.key: f for f in product_facts(left)}
    right_facts = {f.key: f for f in product_facts(right)}
    rows = []
    for key in ("price", "stock", "selected_variant"):
        if key in left_facts or key in right_facts:
            rows.append(
                ComparisonRow(attribute=key, left=left_facts.get(key), right=right_facts.get(key))
            )

    # Normalize labels, not values or units. Duplicate normalized labels are not
    # guessed or merged. References retain each snapshot's original fact index.
    def specs(product: ProductSnapshot, facts: dict[str, Fact]) -> dict[str, Fact]:
        result = {}
        duplicates = set()
        for i, name in enumerate(product.specs):
            key = " ".join(name.casefold().split())
            if not key:
                continue
            if key in result:
                duplicates.add(key)
            result[key] = facts[f"spec:{i}"]
        return {key: fact for key, fact in result.items() if key not in duplicates}

    left_specs, right_specs = specs(left, left_facts), specs(right, right_facts)
    attributes = sorted(left_specs.keys() | right_specs.keys())
    remaining = 8 - len(rows)
    for key in attributes[:remaining]:
        rows.append(
            ComparisonRow(attribute=key, left=left_specs.get(key), right=right_specs.get(key))
        )
    if not rows:
        rows.append(
            ComparisonRow(attribute="title", left=left_facts["title"], right=right_facts["title"])
        )
    relation, difference, currency = "missing", None, None
    limitations = [
        "Observed listed prices exclude unobserved shipping, taxes or fees.",
        "No overall quality ranking; specifications are source claims, not independent tests.",
    ]
    if left.price is not None and right.price is not None:
        if left.currency == right.currency:
            relation = (
                "left_lower"
                if left.price < right.price
                else "right_lower"
                if right.price < left.price
                else "equal"
            )
            difference, currency = abs(left.price - right.price), left.currency
        else:
            relation = "different_currency"
            limitations.append(
                "Currencies differ; no conversion or cheapest-product claim was made."
            )
    else:
        limitations.append("Price evidence is missing on at least one side.")
    if len(attributes) > remaining:
        limitations.append("Additional specification rows omitted to keep the comparison bounded.")
    if len(left_specs) != len(left.specs) or len(right_specs) != len(right.specs):
        limitations.append("Ambiguous duplicate specification labels were omitted.")
    return ComparisonResult(
        left_product_id=left.product_id,
        right_product_id=right.product_id,
        left_title=left.title,
        right_title=right.title,
        rows=tuple(rows),
        price_relation=relation,
        difference=difference,
        currency=currency,
        limitations=tuple(limitations),
    )
