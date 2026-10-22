"""
Smoke test script for real GeminiProvider integration.
NOT part of pytest suite.

Usage:
    python scripts/smoke_gemini.py
"""

import os
import sys

# Ensure backend root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.ai.contracts import AIRequest, Answer, Clarification, ToolProposal
from app.ai.gemini import GeminiProvider
from app.config import get_settings


def main():
    settings = get_settings()

    if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY.strip() in ("", "None", "your-gemini-api-key-here"):
        print("SKIPPED: GEMINI_API_KEY is not set or placeholder in backend/.env.")
        print("To run this smoke test:")
        print("  1. Add your real GEMINI_API_KEY in backend/.env")
        print("  2. Run: python scripts/smoke_gemini.py")
        sys.exit(0)

    print("Sending tiny realistic request to GeminiProvider...")
    provider = GeminiProvider(
        api_key=settings.GEMINI_API_KEY,
        model=settings.GEMINI_MODEL,
    )

    request = AIRequest(
        user_text="What do you think of this price?",
        snapshot_facts={
            "retailer": "bestbuy",
            "title": "Logitech MX Master 3S Wireless Mouse",
            "price": "99.99",
            "currency": "USD",
            "availability": "in_stock",
        },
        price_summary={
            "min_price": "89.99",
            "max_price": "109.99",
            "avg_price": "99.49",
            "observation_count": 8,
            "insufficient_data": False,
        },
    )

    decision = provider.decide(request)

    print(f"Parsed Result Type: {type(decision).__name__}")
    if isinstance(decision, Answer):
        print(f"Text: {decision.text}")
    elif isinstance(decision, Clarification):
        print(f"Question: {decision.question}")
    elif isinstance(decision, ToolProposal):
        print(f"Tool: {decision.tool}")
        print(f"Args: {decision.args}")

    print("\nSending realistic ReviewSummaryRequest to GeminiProvider...")
    from app.ai.contracts import ReviewItemContract, ReviewSummaryRequest

    review_req = ReviewSummaryRequest(
        listing_title="Logitech MX Master 3S Wireless Mouse",
        reviews=[
            ReviewItemContract(rating=5, title="Excellent mouse", text="Ergonomic design, super quiet clicks and great battery life."),
            ReviewItemContract(rating=4, title="Good but pricey", text="Very comfortable for all-day coding. Scroll wheel is great."),
            ReviewItemContract(rating=2, title="Too heavy", text="A bit heavy for my small hands and the software is bloated."),
        ],
    )
    summary = provider.summarize_reviews(review_req)
    print(f"Overall: {summary.overall}")
    print(f"Pros: {summary.pros}")
    print(f"Cons: {summary.cons}")
    print(f"Themes: {summary.themes}")
    print(f"Caveat: {summary.caveat}")


if __name__ == "__main__":
    main()
