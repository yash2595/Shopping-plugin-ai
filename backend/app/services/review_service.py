import concurrent.futures
import decimal
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.ai.contracts import (
    AIProvider,
    AIProviderError,
    ReviewItemContract,
    ReviewSummary,
    ReviewSummaryRequest,
)
from app.config import get_settings
from app.models.review import ReviewSummary as ReviewSummaryModel
from app.models.user import User
from app.repositories.review_repo import ReviewRepository
from app.repositories.snapshot_repo import SnapshotRepository
from app.repositories.usage_repo import UsageRepository
from app.schemas.review import (
    ReviewItemInput,
    ReviewSummaryPostRequest,
    ReviewSummaryResponse,
)

settings = get_settings()


def strip_html_and_control_chars(text: str) -> str:
    # Strip HTML tags
    cleaned = re.sub(r"<[^>]+>", "", text)
    # Strip non-printable/control characters
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    return cleaned.strip()


class ReviewService:
    def __init__(
        self,
        review_repo: Optional[ReviewRepository] = None,
        snapshot_repo: Optional[SnapshotRepository] = None,
        usage_repo: Optional[UsageRepository] = None,
    ):
        self.review_repo = review_repo or ReviewRepository()
        self.snapshot_repo = snapshot_repo or SnapshotRepository()
        self.usage_repo = usage_repo or UsageRepository()

    def generate_review_summary(
        self,
        db: Session,
        listing_id: uuid.UUID,
        user: User,
        data: ReviewSummaryPostRequest,
        ai_provider: AIProvider,
    ) -> ReviewSummaryResponse:
        now = datetime.now(timezone.utc)

        # 1. Verify listing exists
        listing = self.snapshot_repo.get_listing_by_id(db, listing_id)
        if not listing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "not_found", "message": "Listing not found"},
            )

        # 2. Check snapshot view trust (409 LISTING_NOT_SEEN)
        has_seen = self.snapshot_repo.has_user_snapshot_for_listing(
            db, user_id=user.id, listing_id=listing_id
        )
        if not has_seen:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "LISTING_NOT_SEEN",
                    "message": "User has not captured a snapshot for this listing",
                },
            )

        # 3. Check raw request size limits (422 REVIEWS_TOO_LARGE)
        if len(data.reviews) > settings.MAX_REVIEWS_PER_SUMMARY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "REVIEWS_TOO_LARGE",
                    "message": f"Maximum {settings.MAX_REVIEWS_PER_SUMMARY} reviews allowed per request",
                },
            )

        total_chars = sum(len(r.text) for r in data.reviews)
        if total_chars > settings.MAX_TOTAL_REVIEWS_CHARS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "REVIEWS_TOO_LARGE",
                    "message": f"Total review length exceeds {settings.MAX_TOTAL_REVIEWS_CHARS} characters",
                },
            )

        # 4. Clean, normalize, and filter reviews
        usable_reviews: List[ReviewItemInput] = []
        for r in data.reviews:
            if len(r.text) > settings.MAX_REVIEW_LENGTH_CHARS:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "REVIEWS_TOO_LARGE",
                        "message": f"Individual review exceeds {settings.MAX_REVIEW_LENGTH_CHARS} characters",
                    },
                )
            cleaned_text = strip_html_and_control_chars(r.text)
            cleaned_title = strip_html_and_control_chars(r.title) if r.title else None
            if cleaned_text:
                usable_reviews.append(
                    ReviewItemInput(
                        rating=r.rating,
                        title=cleaned_title,
                        text=cleaned_text,
                        reviewed_at=r.reviewed_at,
                    )
                )

        if len(usable_reviews) < settings.MIN_USABLE_REVIEWS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "NOT_ENOUGH_REVIEWS",
                    "message": f"At least {settings.MIN_USABLE_REVIEWS} usable reviews required to generate summary",
                },
            )

        # 5. Compute canonical hash
        # Sort canonically by rating, text, and title
        sorted_elements = sorted(
            [
                (r.rating, r.text, r.title or "")
                for r in usable_reviews
            ]
        )
        canonical_str = json.dumps(sorted_elements, sort_keys=True)
        reviews_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

        # Compute sample stats
        sample_size = len(usable_reviews)
        avg_rating = decimal.Decimal(str(round(sum(r.rating for r in usable_reviews) / sample_size, 2)))

        # 6. Check cache (< 7 days)
        cache_cutoff = now - timedelta(days=settings.REVIEW_SUMMARY_CACHE_DAYS)
        cached_summary = self.review_repo.get_fresh_summary_by_hash(
            db,
            listing_id=listing_id,
            reviews_hash=reviews_hash,
            since=cache_cutoff,
        )
        if cached_summary:
            sum_data = cached_summary.summary
            return ReviewSummaryResponse(
                listing_id=listing_id,
                overall=sum_data.get("overall", ""),
                pros=sum_data.get("pros", []),
                cons=sum_data.get("cons", []),
                themes=sum_data.get("themes", []),
                caveat=sum_data.get("caveat", f"Based on {sample_size} reviews shown on the page, not all reviews."),
                sample_size=cached_summary.sample_size,
                average_rating_in_sample=cached_summary.average_rating_in_sample,
                cached=True,
                kind=sum_data.get("kind", "summary"),
                created_at=cached_summary.created_at,
            )

        # 7. Check AI daily limit before calling AI
        ai_count = self.usage_repo.increment_and_get_count(
            db, user_id=user.id, day=now.date(), key="ai_daily"
        )
        if ai_count > settings.MAX_AI_CALLS_PER_USER_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "RATE_LIMITED",
                    "message": "Daily AI limit exceeded",
                },
            )

        # 8. Call AI Provider with timeout
        contract_req = ReviewSummaryRequest(
            listing_title=listing.title,
            reviews=[
                ReviewItemContract(rating=r.rating, title=r.title, text=r.text)
                for r in usable_reviews
            ],
        )

        ai_summary: Optional[ReviewSummary] = None
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(ai_provider.summarize_reviews, contract_req)
                ai_summary = future.result(timeout=settings.AI_TIMEOUT_SECONDS)
        except Exception:
            # Fallback handling on provider error or timeout
            fallback_caveat = f"Based on {sample_size} reviews shown on the page, not all reviews."
            return ReviewSummaryResponse(
                listing_id=listing_id,
                overall="Review summary is currently unavailable. Please try again in a moment.",
                pros=[],
                cons=[],
                themes=[],
                caveat=fallback_caveat,
                sample_size=sample_size,
                average_rating_in_sample=avg_rating,
                cached=False,
                kind="fallback",
                created_at=now,
            )

        # 9. Store summary in DB (DO NOT store raw review text)
        stored = self.review_repo.create_summary(
            db,
            listing_id=listing_id,
            reviews_hash=reviews_hash,
            sample_size=sample_size,
            average_rating_in_sample=avg_rating,
            summary={
                "overall": ai_summary.overall,
                "pros": ai_summary.pros,
                "cons": ai_summary.cons,
                "themes": ai_summary.themes,
                "caveat": ai_summary.caveat,
                "kind": "summary",
            },
        )
        db.commit()
        db.refresh(stored)

        return ReviewSummaryResponse(
            listing_id=listing_id,
            overall=ai_summary.overall,
            pros=ai_summary.pros,
            cons=ai_summary.cons,
            themes=ai_summary.themes,
            caveat=ai_summary.caveat,
            sample_size=sample_size,
            average_rating_in_sample=avg_rating,
            cached=False,
            kind="summary",
            created_at=stored.created_at,
        )

    def get_latest_review_summary(
        self,
        db: Session,
        listing_id: uuid.UUID,
    ) -> ReviewSummaryResponse:
        listing = self.snapshot_repo.get_listing_by_id(db, listing_id)
        if not listing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "not_found", "message": "Listing not found"},
            )

        latest = self.review_repo.get_latest_summary_for_listing(db, listing_id)
        if not latest:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NO_SUMMARY", "message": "No review summary available for this listing"},
            )

        sum_data = latest.summary
        return ReviewSummaryResponse(
            listing_id=listing_id,
            overall=sum_data.get("overall", ""),
            pros=sum_data.get("pros", []),
            cons=sum_data.get("cons", []),
            themes=sum_data.get("themes", []),
            caveat=sum_data.get("caveat", f"Based on {latest.sample_size} reviews shown on the page, not all reviews."),
            sample_size=latest.sample_size,
            average_rating_in_sample=latest.average_rating_in_sample,
            cached=True,
            kind=sum_data.get("kind", "summary"),
            created_at=latest.created_at,
        )
