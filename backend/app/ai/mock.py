import decimal
import re
from app.ai.contracts import (
    AIDecision,
    AIProvider,
    AIProviderError,
    AIRequest,
    Answer,
    Clarification,
    ReviewSummary,
    ReviewSummaryRequest,
    ToolProposal,
)


class MockProvider(AIProvider):
    """
    Deterministic mock AI provider implementing Phase 3 & 4 behaviors:
    - 'budget' -> ToolProposal(set_budget, <amount>)
    - 'prefer' -> ToolProposal(set_preference, <key>, <value>)
    - 'not this' / 'reject' -> ToolProposal(reject_current_product, <reason>)
    - 'review' -> Answer from review_summary or 'No review summary is available...'
    - 'good deal' -> Answer from facts (compares against budget_max if present)
    - 'notify me below 3000' -> ToolProposal(create_price_alert, 3000)
    - 'notify me' without number -> Clarification
    - 'crash' -> raises AIProviderError
    """

    def decide(self, request: AIRequest) -> AIDecision:
        text = request.user_text.strip().lower()

        # 1. Simulated failure path
        if "crash" in text:
            raise AIProviderError("Simulated AI provider failure")

        # 2. Budget tool path: "my budget is 3000"
        if "budget" in text:
            match = re.search(r"(\d+(?:\.\d+)?)", text)
            if match and match.group(1):
                raw_number = match.group(1)
                return ToolProposal(
                    tool="set_budget",
                    args={"budget_max": raw_number},
                )
            return Clarification(
                question="What maximum budget would you like to set?"
            )

        # 3. Preference tool path: "I prefer black", "prefer brand sony"
        if "prefer" in text:
            if "black" in text:
                return ToolProposal(
                    tool="set_preference",
                    args={"key": "color", "value": "black"},
                )
            if "sony" in text:
                return ToolProposal(
                    tool="set_preference",
                    args={"key": "brand", "value": "sony"},
                )
            return ToolProposal(
                tool="set_preference",
                args={"key": "category", "value": "electronics"},
            )

        # 4. Product rejection path: "not this one, too expensive"
        if "not this" in text or "reject" in text or "too expensive" in text:
            reason = "too expensive" if "expensive" in text else "not suitable"
            return ToolProposal(
                tool="reject_current_product",
                args={"reason": reason},
            )

        # 5. Review questions path: "are the reviews good?"
        if "review" in text:
            if request.review_summary:
                overall = request.review_summary.get("overall", "Positive overall feedback.")
                return Answer(text=f"Review summary: {overall}")
            return Answer(text="No review summary is available for this product yet.")

        # 6. Alert / notification path
        if "notify" in text or "alert" in text:
            match = re.search(r"(?:below|under|at|to|\$|₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)", text)
            if match and match.group(1):
                raw_number = match.group(1)
                return ToolProposal(
                    tool="create_price_alert",
                    args={"target_price": raw_number},
                )
            return Clarification(
                question="What target price would you like me to notify you at?"
            )

        # 7. Deal evaluation from facts & context
        if "deal" in text or "worth" in text:
            facts = request.snapshot_facts or {}
            curr_price = facts.get("price", "unknown")
            summary = request.price_summary or {}
            verdict = summary.get("verdict", "insufficient_data")

            # Check if budget is exceeded
            if request.context and request.context.budget_max is not None:
                try:
                    price_dec = decimal.Decimal(str(curr_price))
                    if price_dec > request.context.budget_max:
                        return Answer(
                            text=f"At ₹{curr_price}, this item exceeds your budget of ₹{request.context.budget_max}."
                        )
                except (decimal.InvalidOperation, ValueError):
                    pass

            if verdict == "insufficient_data":
                reason = summary.get("reason")
                if reason == "history_too_short":
                    span = summary.get("span", summary.get("span_days", 0))
                    return Answer(
                        text=f"The current price is ₹{curr_price}. Tracking has been active for {span} days, which is not enough history to confirm if this is a good deal."
                    )
                return Answer(
                    text=f"The current price is ₹{curr_price}. There is no price history yet to confirm if this is a good deal."
                )

            lowest = summary.get("lowest_price", curr_price)
            avg = summary.get("average_price", curr_price)
            return Answer(
                text=f"The verdict for this item is a {verdict}. The current price is ₹{curr_price} with a tracked average of ₹{avg} and lowest of ₹{lowest}."
            )

        # 8. Default factual response
        facts = request.snapshot_facts or {}
        curr_price = facts.get("price", "unknown")
        return Answer(
            text=f"I checked this listing. Current price is ₹{curr_price}. Let me know if you would like me to set a price alert or track history."
        )

    def summarize_reviews(self, request: ReviewSummaryRequest) -> ReviewSummary:
        """
        Deterministic review summary implementation.
        """
        for r in request.reviews:
            if "crash" in r.text.lower():
                raise AIProviderError("Simulated review summary failure")

        sample_size = len(request.reviews)
        avg_rating = sum(r.rating for r in request.reviews) / sample_size if sample_size > 0 else 0.0

        all_text = " ".join(r.text.lower() for r in request.reviews)
        pros = []
        cons = []
        themes = []

        if any(w in all_text for w in ["good", "great", "excellent", "love", "durable", "clear", "sound"]):
            pros.append("Solid sound quality and build")
            themes.append("Sound Performance")
        if any(w in all_text for w in ["battery", "life", "long"]):
            pros.append("Long battery performance")
            themes.append("Battery Life")
        if any(w in all_text for w in ["cheap", "expensive", "cost", "price"]):
            themes.append("Value for Money")
        if any(w in all_text for w in ["bad", "poor", "broken", "terrible", "slow", "noisy", "fail", "heavy"]):
            cons.append("Some users reported durability or noise issues")
        if not pros:
            pros.append("Generally positive customer impressions")
        if not cons:
            cons.append("Limited reported drawbacks in sample")
        if not themes:
            themes.append("General Usage")

        verdict = "mostly positive" if avg_rating >= 3.5 else "mixed"
        overall = f"Based on {sample_size} reviews, customer feedback is {verdict} with an average rating of {avg_rating:.1f}/5."
        caveat = f"Based on {sample_size} reviews shown on the page, not all reviews."

        return ReviewSummary(
            overall=overall,
            pros=pros[:5],
            cons=cons[:5],
            themes=themes[:5],
            caveat=caveat,
        )
