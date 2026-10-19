"""Deterministic questions about saved market evidence; no browsing/model calls."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from shopping_ai.config import AIConfig
from shopping_ai.market import MarketResult, MarketSearch, Offer, rank_offers
from shopping_ai.models import Contract, Identifier

MarketQuestion = Literal["coverage", "exclusions", "costs", "savings", "comparison", "availability"]
REASONS = {
    "over_budget": (
        "observed cost exceeds the total budget",
        "observed cost total budget se zyada hai",
    ),
    "identity_mismatch": ("model/variant does not match", "model/variant match nahi karta"),
    "currency_mismatch": (
        "currency differs; no conversion",
        "currency alag hai; conversion nahi ki",
    ),
    "out_of_stock": ("observed out of stock", "observation mein out of stock"),
    "stock_unknown": ("stock is unknown", "stock unknown hai"),
    "future_observation": ("invalid future observation", "future observation invalid hai"),
    "predates_search": ("observation predates the search", "observation search se pehle ka hai"),
    "stale_observation": ("observation has expired", "observation expire ho gaya hai"),
    "unknown_total_cost": (
        "delivery/tax/fees are unobserved",
        "delivery/tax/fees observe nahi hue",
    ),
}


class MarketAnswer(Contract):
    search_id: Identifier
    question: MarketQuestion
    status: Literal["answered", "needs_input"] = "answered"
    message: Annotated[str, Field(min_length=1, max_length=8000)]
    speech_text: Annotated[str, Field(min_length=1, max_length=1500)]
    source_offers: tuple[Offer, ...] = Field(default_factory=tuple, max_length=5)
    savings_amount: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def grounded_savings(self):
        if self.savings_amount is not None:
            if (
                self.question not in {"savings", "comparison"}
                or self.status != "answered"
                or len(self.source_offers) != 2
            ):
                raise ValueError("Savings require exactly two answered source offers")
            lower, higher = self.source_offers
            if (
                lower.offer_id == higher.offer_id
                or lower.currency != higher.currency
                or not lower.identity.matches(higher.identity)
                or lower.total is None
                or higher.total is None
                or higher.total - lower.total != self.savings_amount
            ):
                raise ValueError("Savings must equal the exact observed total difference")
        elif self.question in {"savings", "comparison"} and self.status == "answered":
            raise ValueError("Answered savings require an observed difference")
        return self


def answer_market(
    state: MarketSearch,
    question: MarketQuestion,
    *,
    now: datetime,
    config: AIConfig,
    language: Literal["en", "hi-Latn"],
    offer_id: str | None = None,
    pair_ids: tuple[str, str] | None = None,
) -> tuple[MarketResult, MarketAnswer]:
    if question == "comparison":
        if pair_ids is None or len(pair_ids) != 2 or pair_ids[0] == pair_ids[1] or offer_id:
            raise ValueError("Comparison requires two distinct explicit offer IDs")
    elif pair_ids is not None:
        raise ValueError("Offer pairs require a comparison question")
    state = MarketSearch.model_validate(state.model_dump())
    result = rank_offers(state, now=now, config=config, language=language)
    hi = language == "hi-Latn"
    offers = {offer.offer_id: offer for receipt in state.receipts for offer in receipt.offers}
    sources = ()
    status = "answered"
    savings = None
    if question in {"savings", "comparison"}:
        ranked = result.ranked
        lower = ranked[0] if ranked else None
        higher = next(
            (
                item
                for item in ranked
                if lower is not None
                and item.store_id != lower.store_id
                and (offer_id is None or item.offer.offer_id == offer_id)
            ),
            None,
        )
        # A reference to the lowest offer asks for its nearest other-store rival.
        if lower is not None and offer_id == lower.offer.offer_id:
            higher = next((item for item in ranked if item.store_id != lower.store_id), None)
        if pair_ids is not None:
            chosen = [item for item in ranked if item.offer.offer_id in pair_ids]
            lower, higher = chosen if len(chosen) == 2 else (None, None)
        if lower is None or higher is None:
            status = "needs_input"
            message = (
                "Saving ke liye do stores ke fresh eligible verified totals chahiye."
                if hi
                else "Savings require fresh eligible verified totals from two stores."
            )
            if pair_ids is not None:
                message = (
                    "Dono requested offers ke fresh eligible verified totals chahiye."
                    if hi
                    else "Both requested offers need fresh eligible verified totals."
                )
            speech = message
        else:
            savings = higher.total - lower.total
            sources = (lower.offer, higher.offer)
            message = (
                f"{lower.store_id} ({lower.offer.offer_id}, {state.currency} {lower.total:.2f}) "
                f"vs {higher.store_id} ({higher.offer.offer_id}, "
                f"{state.currency} {higher.total:.2f}): "
                + ("observed total ka difference " if hi else "observed total difference ")
                + f"{state.currency} {savings:.2f}."
            )
            message += (
                " Sirf in observations ka comparison hai; checkout saving guarantee nahi hai."
                if hi
                else " This compares these observations only; checkout savings are not guaranteed."
            )
            speech = message
        message += "\n" + result.speech_text
        speech += " " + result.speech_text
        if pair_ids is not None and sources:
            message += "\n" + "\n".join(
                f"{offer.offer_id}: {offer.currency} price {offer.price:.2f}, "
                f"delivery {offer.shipping:.2f}, extra tax/fees "
                f"{offer.extra_tax_and_fees:.2f}; observed in stock."
                for offer in sources
            )
    elif question == "coverage":
        returned = {receipt.task_id: receipt.status for receipt in state.receipts}
        rows = [
            f"{task.store.store_id}: {returned.get(task.task_id, 'pending')}"
            for task in state.tasks
        ]
        reasons = sorted({reason for excluded in result.exclusions for reason in excluded.reasons})
        labels = "; ".join(REASONS[reason][1 if hi else 0] for reason in reasons)
        message = result.speech_text + "\n" + "\n".join(rows)
        speech = result.speech_text
        if labels:
            explanation = (
                (" Offer evidence ki kami: " if hi else " Offer evidence limits: ") + labels + "."
            )
            message += explanation
            speech += explanation
    elif question == "exclusions":
        chosen = [
            item for item in result.exclusions if offer_id is None or item.offer_id == offer_id
        ][:5]
        sources = tuple(offers[item.offer_id] for item in chosen)
        if chosen:
            rows = [
                f"{item.offer_id}: " + "; ".join(REASONS[r][1 if hi else 0] for r in item.reasons)
                for item in chosen
            ]
            message = "\n".join(rows)
            speech = rows[0]
            if len(rows) > 1:
                speech += (
                    " Baaki reasons screen par hain." if hi else " Other reasons are on screen."
                )
            if len(result.exclusions) > len(chosen):
                message += "\nAdditional exclusions remain in structured results."
        else:
            message = (
                "Is reference par offer exclusion nahi mila."
                if hi
                else "No offer-level exclusion was found for this reference."
            )
            speech = message
        message += "\n" + result.speech_text
    else:
        if question == "costs" and offer_id is None and len(result.cheapest_offer_ids) == 1:
            offer_id = result.cheapest_offer_ids[0]
        candidates = [
            offer
            for offer in offers.values()
            if (offer_id is None or offer.offer_id == offer_id)
            and state.identity.matches(offer.identity)
            and 0 <= (now - offer.observed_at).total_seconds() <= config.max_snapshot_age_seconds
            and offer.observed_at >= state.created_at
        ][:5]
        sources = tuple(candidates)
        if not sources:
            status = "needs_input"
            message = (
                "Fresh offer evidence chahiye; search refresh karo."
                if hi
                else "Fresh offer evidence is required; refresh the search."
            )
            speech = message
        elif question == "availability":
            labels = {True: "in stock", False: "out of stock", None: "unknown"}
            stores = {
                offer.offer_id: task.store.store_id
                for task in state.tasks
                for receipt in state.receipts
                if receipt.task_id == task.task_id
                for offer in receipt.offers
            }

            def seller_label(offer):
                label = offer.seller[:120] + ("…" if len(offer.seller) > 120 else "")
                return json.dumps(label, ensure_ascii=False)

            rows = [
                f"{stores[offer.offer_id]} ({offer.offer_id}): "
                f"seller {seller_label(offer)}, "
                f"observed stock: {labels[offer.in_stock]}."
                for offer in sources
            ]
            limit = (
                " Sirf saved fresh observations hain; seller authenticity ya checkout "
                "availability verify nahi ki. Stock se budget/total-cost eligibility "
                "prove nahi hoti."
                if hi
                else " These are saved fresh observations; seller authenticity and checkout "
                "availability are not verified. Stock does not prove budget/total-cost eligibility."
            )
            message = "\n".join(rows) + limit
            speech = rows[0] + limit
            if len(rows) > 1:
                speech += (
                    " Baaki seller/stock details screen par hain."
                    if hi
                    else " Other seller/stock details are on screen."
                )
            if len(candidates) == 5:
                message += "\nShowing at most five matching observations."
            if result.unresolved_store_ids:
                notice = (
                    " Kuch requested stores ke complete results nahi mile."
                    if hi
                    else " Some requested stores lack complete results."
                )
                message += notice
                speech += notice
        else:

            def amount(value):
                return "unknown" if value is None else f"{value:.2f}"

            rows = [
                f"{offer.offer_id}: {offer.currency} price {amount(offer.price)}, "
                f"delivery {amount(offer.shipping)}, "
                f"extra tax/fees {amount(offer.extra_tax_and_fees)}, "
                f"total {amount(offer.total)}."
                for offer in sources
            ]
            limit = (
                " Conditional discounts deduct nahi kiye; stock/eligibility results mein dekho."
                if hi
                else " Conditional discounts are not deducted; check stock/eligibility in results."
            )
            message = "\n".join(rows) + limit
            speech = " ".join(rows[:2]) + limit
            if len(rows) > 2:
                speech += (
                    " Baaki costs screen par hain." if hi else " Remaining costs are on screen."
                )
    return result, MarketAnswer(
        search_id=state.search_id,
        question=question,
        status=status,
        message=message,
        speech_text=speech,
        source_offers=sources,
        savings_amount=savings,
    )
