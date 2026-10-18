"""Small offline dialogue fallback; replace through Interpreter for richer NLU.

Rules cover explicit shopping phrases only. Unclear text stays unclear; this is
not a general language model, and inferred slots never authorize browser actions.
"""

import re
from decimal import Decimal
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from shopping_ai.models import Contract, Money, ShortText

SlotName = Literal["category", "budget"]


class Requirements(Contract):
    category: ShortText | None = None
    budget: Money | None = None
    use_case: ShortText | None = None
    colour: ShortText | None = None
    size: ShortText | None = None

    @model_validator(mode="after")
    def positive_budget(self):
        if self.budget is not None and self.budget <= 0:
            raise ValueError("Budget must be positive")
        return self


class Meaning(Contract):
    intent: Literal[
        "greeting",
        "help",
        "requirements",
        "product_question",
        "select_variant",
        "add_to_cart",
        "compare",
        "market_search",
        "open_offer",
        "reviews",
        "unknown",
    ] = "unknown"
    slots: Requirements = Field(default_factory=Requirements)
    reference: Literal["selected", "previous", "ordinal"] = "selected"
    ordinal: int | None = Field(default=None, ge=1, le=10, strict=True)
    comparison_indices: (
        tuple[
            Annotated[int, Field(strict=True, ge=1, le=10)],
            Annotated[int, Field(strict=True, ge=1, le=10)],
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def reference_shape(self):
        if (self.reference == "ordinal") != (self.ordinal is not None):
            raise ValueError("Ordinal reference requires exactly one index")
        if self.comparison_indices is not None and (
            self.intent != "compare"
            or self.reference != "selected"
            or self.comparison_indices[0] == self.comparison_indices[1]
        ):
            raise ValueError("Comparison indices require two distinct explicit products")
        return self


class Interpreter(Protocol):
    async def interpret(self, message: str, awaiting: SlotName | None) -> Meaning: ...


def explicit_open_request(message: str) -> bool:
    text = " ".join(message.casefold().split())
    return bool(re.search(r"\b(open|kholo|khol)\b", text)) and not re.search(
        r"\b(mat|nahi|nahin|don't|dont|not|cart|buy|purchase|checkout|pay)\b",
        text,
    )


class BasicInterpreter:
    async def interpret(self, message: str, awaiting: SlotName | None) -> Meaning:
        text = " ".join(message.casefold().split())
        if text in {"hi", "hello", "hey", "namaste", "namaskar", "नमस्ते"}:
            return Meaning(intent="greeting")
        if text in {"help", "madad", "kya kar sakte ho", "what can you do"}:
            return Meaning(intent="help")
        # An explicit negation must never become a cart or variant command.
        if re.search(r"\b(mat|nahi|nahin|don't|dont|not)\b", text):
            return Meaning()
        # Cross-store requests must not fall back to comparing saved products.
        if (
            re.search(r"\b(across|websites|website|stores|sites|amazon|flipkart|ecommerce)\b", text)
            and re.search(r"\b(compare|comparison|search|find|cheapest|sasta|sasti)\b", text)
            and not re.search(
                r"\b(open|kholo|khol)\b",
                text,
            )
        ):
            return Meaning(intent="market_search")
        slots = {}
        for words, category in [
            (("earbuds", "earphones"), "earbuds"),
            (("headphones",), "headphones"),
            (("shoes", "jute"), "shoes"),
            (("laptop",), "laptop"),
            (("phone", "mobile"), "phone"),
        ]:
            if any(re.search(rf"\b{word}\b", text) for word in words):
                if "category" in slots:
                    return Meaning()  # Multiple categories need clarification.
                slots["category"] = category
        if "gym" in text.split():
            slots["use_case"] = "gym"
        colours = [
            colour
            for colour in ("black", "white", "blue", "red", "green")
            if re.search(rf"\b{colour}\b", text)
        ]
        if len(colours) == 1:
            slots["colour"] = colours[0]
        # A budget is parsed only with explicit budget/currency language, or when
        # it answers the pending budget question. Never infer it from product specs.
        budget_answer = awaiting == "budget" and re.fullmatch(
            r"(?:inr|rs\.?|₹)?\s*\d+(?:,\d{3})*(?:\.\d+)?\s*k?\s*(?:rupees|inr|tak)?", text
        )
        if budget_answer or re.search(r"\b(budget|under|below|tak|rs|inr|rupees)\b|₹", text):
            numbers = re.findall(r"(?<![\w.\-])\d+(?:,\d{3})*(?:\.\d+)?\s*[kK]?(?![\w.])", text)
            if len(numbers) == 1 and not re.search(r"\b(usd|dollars|eur)\b|[$€]", text):
                value = numbers[0].replace(",", "").replace(" ", "")
                amount = Decimal(value.rstrip("kK")) * (1000 if value.endswith(("k", "K")) else 1)
                if 0 < amount <= Decimal("999999999999.99") and amount.as_tuple().exponent >= -2:
                    slots["budget"] = amount
        is_compare = bool(
            re.search(r"\b(compare|comparison|better|cheaper|cheapest|sasta|sasti)\b", text)
        )
        ordinal_words = {
            "first": 1,
            "pehla": 1,
            "pehle": 1,
            "1st": 1,
            "second": 2,
            "dusra": 2,
            "dusre": 2,
            "2nd": 2,
            "third": 3,
            "teesra": 3,
            "teesre": 3,
            "3rd": 3,
        }
        indices = list(
            dict.fromkeys(
                ordinal_words[m.group()]
                for m in re.finditer(r"\b(?:" + "|".join(ordinal_words) + r")\b", text)
            )
        )
        reference, ordinal, comparison_indices = "selected", None, None
        if len(indices) > 1:
            if not is_compare or len(indices) != 2:
                return Meaning()
            comparison_indices = tuple(indices)
        elif indices:
            reference, ordinal = "ordinal", indices[0]
        if re.search(r"\b(previous|pichla|pichle)\b", text):
            if indices:
                return Meaning()
            reference = "previous"
        if re.search(r"\b(open|kholo|khol)\b", text):
            if comparison_indices or not explicit_open_request(message):
                return Meaning()
            if not (is_compare or reference == "ordinal" or re.search(r"\boffer\b", text)):
                return Meaning()
            intent = "open_offer"
        elif is_compare:
            intent = "compare"
        elif re.search(r"\b(review|reviews|complaints)\b", text):
            intent = "reviews"
        elif "cart" in text and re.search(r"\b(add|daal|dal|put|rakho)\b", text):
            intent = "add_to_cart"
        elif re.search(r"\b(select|choose)\b", text):
            intent = "select_variant"
        elif re.search(r"\b(price|cost|daam|battery|stock|specs|colour|color|naam|name)\b", text):
            intent = "product_question"
        elif slots:
            intent = "requirements"
        else:
            intent = "unknown"
        if intent in {"add_to_cart", "select_variant"} and (
            re.search(r"\b(price|battery|stock|specs)\b", text)
            and re.search(r"\b(bata|batao|tell|what|kitna|kitni)\b|\?", text)
        ):
            return Meaning()  # Do not silently discard part of a compound goal.
        return Meaning(
            intent=intent,
            slots=Requirements.model_validate(slots),
            reference=reference,
            ordinal=ordinal,
            comparison_indices=comparison_indices,
        )
