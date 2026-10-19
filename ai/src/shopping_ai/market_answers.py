"""Deterministic questions about saved market evidence; no browsing/model calls."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shopping_ai.config import AIConfig
from shopping_ai.market import MarketResult, MarketSearch, Offer, rank_offers
from shopping_ai.models import Contract, Identifier

MarketQuestion = Literal["coverage", "exclusions", "costs"]
REASONS = {
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


def answer_market(
    state: MarketSearch,
    question: MarketQuestion,
    *,
    now: datetime,
    config: AIConfig,
    language: Literal["en", "hi-Latn"],
    offer_id: str | None = None,
) -> tuple[MarketResult, MarketAnswer]:
    state = MarketSearch.model_validate(state.model_dump())
    result = rank_offers(state, now=now, config=config, language=language)
    hi = language == "hi-Latn"
    offers = {offer.offer_id: offer for receipt in state.receipts for offer in receipt.offers}
    sources = ()
    status = "answered"
    if question == "coverage":
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
        if offer_id is None and len(result.cheapest_offer_ids) == 1:
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
    )
