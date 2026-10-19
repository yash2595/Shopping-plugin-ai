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

COLOUR_ALIASES = {
    "black": ("black", "ब्लैक", "काला", "काले", "काली"),
    "blue": ("blue", "ब्लू", "नीला", "नीले", "नीली"),
    "white": ("white", "व्हाइट", "सफेद"),
    "red": ("red", "रेड", "लाल"),
    "green": ("green", "ग्रीन", "हरा", "हरे", "हरी"),
}


def _words(text: str, *words: str) -> bool:
    # Include Devanagari combining marks in token boundaries.
    return bool(
        re.search(
            r"(?<![\w\u0900-\u097f])(?:"
            + "|".join(re.escape(word) for word in words)
            + r")(?![\w\u0900-\u097f])",
            text,
        )
    )


def _cart_command(text: str) -> bool:
    return _words(text, "cart", "कार्ट") and _words(
        text, "add", "daal", "dal", "put", "rakho", "डाल", "डालो", "डालें", "जोड़", "जोड़ो", "जोड़ें", "ऐड"
    )


def _selection_command(text: str) -> bool:
    return _words(text, "select", "choose", "चुनो", "चुनें", "सेलेक्ट")


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
        "market_refresh",
        "open_offer",
        "market_coverage",
        "market_exclusions",
        "market_costs",
        "market_savings",
        "market_availability",
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


def explicit_budget(message: str, awaiting: SlotName | None) -> Decimal | None:
    text = " ".join(message.casefold().split())
    budget_answer = awaiting == "budget" and re.fullmatch(
        r"(?:inr|rs\.?|₹)?\s*\d+(?:,\d{3})*(?:\.\d+)?\s*k?\s*(?:rupees|inr|tak)?", text
    )
    if not budget_answer and not re.search(r"\b(budget|under|below|tak|rs|inr|rupees)\b|₹", text):
        return None
    numbers = re.findall(r"(?<![\w.\-])\d+(?:,\d{3})*(?:\.\d+)?\s*[kK]?(?![\w.])", text)
    if len(numbers) != 1 or re.search(r"\b(usd|dollars|eur)\b|[$€]", text):
        return None
    value = numbers[0].replace(",", "").replace(" ", "")
    amount = Decimal(value.rstrip("kK")) * (1000 if value.endswith(("k", "K")) else 1)
    if 0 < amount <= Decimal("999999999999.99") and amount.as_tuple().exponent >= -2:
        return amount
    return None


def requirements_supported(slots: Requirements, message: str, awaiting: SlotName | None) -> bool:
    """Conservative source check; model interpretation cannot invent user preferences."""
    patch = slots.model_dump(exclude_none=True)
    text = " ".join(message.casefold().split())
    if patch and _words(
        text, "mat", "nahi", "nahin", "don't", "dont", "not", "मत", "नहीं", "नही", "ना"
    ):
        return False
    aliases = {
        "phone": ("phone", "mobile"),
        "earbuds": ("earbuds", "earphones"),
        "shoes": ("shoes", "jute"),
    }
    for key, value in patch.items():
        if key == "budget":
            if value != explicit_budget(message, awaiting):
                return False
        else:
            choices = aliases.get(value.casefold(), (value,)) if key == "category" else (value,)
            if key == "colour":
                choices = COLOUR_ALIASES.get(value.casefold(), (value,))
            if not any(_words(text, " ".join(item.casefold().split())) for item in choices):
                return False
    return True


def explicit_open_request(message: str) -> bool:
    text = " ".join(message.casefold().split())
    return bool(re.search(r"\b(open|kholo|khol)\b", text)) and not re.search(
        r"\b(mat|nahi|nahin|don't|dont|not|cart|buy|purchase|checkout|pay)\b",
        text,
    )


def explicit_mutation_request(
    message: str, intent: Literal["select_variant", "add_to_cart"]
) -> bool:
    text = " ".join(message.casefold().split())
    if re.search(
        r"\b(mat|nahi|nahin|don't|dont|not|never|without|avoid|how|kaise|why|what|meaning|matlab|"
        r"explain|tutorial|steps)\b|\b(can|should|may|could)\s+(i|we)\b",
        text,
    ):
        return False
    if _words(text, "मत", "नहीं", "नही", "ना", "बिना", "कैसे", "क्यों", "क्या", "मतलब", "समझाओ"):
        return False
    if unsupported_compound_request(message):
        return False
    if intent == "add_to_cart":
        return _cart_command(text)
    return _selection_command(text)


def unsupported_compound_request(message: str) -> bool:
    """Recognize explicit combined goals without pretending to parse all language."""
    text = " ".join(message.casefold().split())
    if not (
        _words(
            text,
            "and",
            "aur",
            "then",
            "phir",
            "also",
            "saath",
            "or",
            "ya",
            "और",
            "फिर",
            "साथ",
            "या",
        )
        or "&" in text
    ):
        return False
    families = (
        _words(
            text,
            "search",
            "find",
            "compare",
            "comparison",
            "refresh",
            "dhundo",
            "dhoondo",
            "खोजो",
            "ढूंढो",
            "ढूँढो",
            "तुलना",
            "रिफ्रेश",
        ),
        _words(text, "open", "kholo", "khol", "खोलो", "खोलें"),
        _selection_command(text),
        _cart_command(text),
        _words(text, "buy", "purchase", "checkout", "pay", "खरीदो", "भुगतान"),
        _words(text, "tell", "show", "bata", "batao", "explain", "बताओ", "दिखाओ", "समझाओ")
        and _words(
            text,
            "price",
            "cost",
            "battery",
            "stock",
            "specs",
            "shipping",
            "seller",
            "fees",
            "savings",
            "कीमत",
            "दाम",
            "बैटरी",
            "स्टॉक",
            "फीस",
            "बचत",
            "विक्रेता",
        ),
    )
    return sum(families) > 1


def explicit_refresh_request(message: str) -> bool:
    """Require an unambiguous standalone refresh; never discard compound goals."""
    text = " ".join(message.casefold().strip(" .!?").split())
    return text in {
        "refresh prices",
        "refresh cross-store prices",
        "refresh search",
        "search again",
        "retry search",
        "dobara search karo",
        "phir se search karo",
        "prices refresh karo",
        "price refresh karo",
    }


class BasicInterpreter:
    async def interpret(self, message: str, awaiting: SlotName | None) -> Meaning:
        text = " ".join(message.casefold().split())
        if unsupported_compound_request(message):
            return Meaning()
        if text in {"hi", "hello", "hey", "namaste", "namaskar", "नमस्ते"}:
            return Meaning(intent="greeting")
        if text in {"help", "madad", "kya kar sakte ho", "what can you do"}:
            return Meaning(intent="help")
        if explicit_refresh_request(message):
            return Meaning(intent="market_refresh")
        if (
            re.search(r"\b(why|kyu|kyun)\b", text)
            and re.search(r"\b(cheapest|sasta|incomplete|partial)\b", text)
            and not re.search(r"\b(open|kholo|cart|buy|pay)\b", text)
        ):
            return Meaning(intent="market_coverage")
        # An explicit negation must never become a cart or variant command.
        if _words(text, "mat", "nahi", "nahin", "don't", "dont", "not", "मत", "नहीं", "नही", "ना"):
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
        colours = [colour for colour, aliases in COLOUR_ALIASES.items() if _words(text, *aliases)]
        if len(colours) == 1:
            slots["colour"] = colours[0]
        # A budget is parsed only with explicit budget/currency language, or when
        # it answers the pending budget question. Never infer it from product specs.
        budget = explicit_budget(message, awaiting)
        if budget is not None:
            slots["budget"] = budget
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
            if (
                not is_compare
                or len(indices) != 2
                or re.search(
                    r"\b(cart|buy|purchase|checkout|pay|open|kholo|khol|select|choose)\b", text
                )
            ):
                return Meaning()
            comparison_indices = tuple(indices)
        elif indices:
            reference, ordinal = "ordinal", indices[0]
        if re.search(r"\b(previous|pichla|pichle)\b", text):
            if indices:
                return Meaning()
            reference = "previous"
        market_question = (
            "market_availability"
            if re.search(r"\b(seller|sellers|vendor)\b", text)
            or (
                re.search(r"\b(stock|availability|available)\b", text)
                and re.search(r"\b(offer|offers|store|stores|website|websites)\b", text)
            )
            else "market_savings"
            if re.search(r"\b(saving|savings|bachat|bachega|bachenge)\b", text)
            else "market_exclusions"
            if re.search(r"\b(excluded|exclude|exclusions|hata)\b", text)
            else "market_costs"
            if re.search(r"\b(shipping|delivery|fees|tax|breakdown)\b", text)
            else "market_coverage"
            if re.search(r"\b(coverage|incomplete|partial)\b", text)
            else None
        )
        if market_question:
            if comparison_indices or re.search(r"\b(open|kholo|cart|buy|pay|select)\b", text):
                return Meaning()
            intent = market_question
        elif re.search(r"\b(open|kholo|khol)\b", text):
            if comparison_indices or not explicit_open_request(message):
                return Meaning()
            if not (is_compare or reference == "ordinal" or re.search(r"\boffer\b", text)):
                return Meaning()
            intent = "open_offer"
        elif is_compare:
            intent = "compare"
        elif re.search(r"\b(review|reviews|complaints)\b", text):
            intent = "reviews"
        elif _cart_command(text):
            intent = "add_to_cart"
        elif _selection_command(text):
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
        if intent in {"add_to_cart", "select_variant"} and not explicit_mutation_request(
            message, intent
        ):
            return Meaning()
        return Meaning(
            intent=intent,
            slots=Requirements.model_validate(slots),
            reference=reference,
            ordinal=ordinal,
            comparison_indices=comparison_indices,
        )
