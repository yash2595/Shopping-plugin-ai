import datetime
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.ai.contracts import AIProvider
from app.config import get_settings
from app.db import get_db
from app.deps import (
    get_ai_provider,
    get_current_user,
    get_observation_service,
    get_review_service,
    get_snapshot_service,
    get_usage_repo,
)
from app.models.user import User
from app.repositories.usage_repo import UsageRepository
from app.schemas.review import (
    ReviewSummaryPostRequest,
    ReviewSummaryResponse,
)
from app.schemas.snapshot import (
    ObservationCreate,
    ObservationResponse,
    PriceHistoryResponse,
)
from app.services.observation_service import ObservationService
from app.services.review_service import ReviewService
from app.services.snapshot_service import SnapshotService

router = APIRouter(prefix="/v1/listings", tags=["listings"])
settings = get_settings()


@router.post("/{listing_id}/observations", response_model=ObservationResponse, status_code=status.HTTP_200_OK)
def record_observation(
    listing_id: uuid.UUID,
    data: ObservationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    observation_service: ObservationService = Depends(get_observation_service),
    snapshot_service: SnapshotService = Depends(get_snapshot_service),
    usage_repo: UsageRepository = Depends(get_usage_repo),
):
    """
    Record a price observation for an existing listing.
    Requires caller to have captured at least one snapshot for this listing (otherwise 409 LISTING_NOT_SEEN).
    Enforces rate limits on hourly and daily observation counts (otherwise 429 RATE_LIMITED).
    Reuses 6h deduplication and >50% jump logic.
    Atomically triggers active qualifying alerts with trusted-observation rules.
    """
    # 1. Verify listing exists
    listing = snapshot_service.snapshot_repo.get_listing_by_id(db, listing_id)
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    # 2. Check caller has captured a snapshot for this listing
    has_seen = snapshot_service.snapshot_repo.has_user_snapshot_for_listing(
        db, user_id=current_user.id, listing_id=listing_id
    )
    if not has_seen:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "LISTING_NOT_SEEN",
                "message": "User has not captured a snapshot for this listing",
            },
        )

    # 3. Usage counters & Rate limiting
    server_now = datetime.datetime.now(datetime.timezone.utc)
    if data.captured_at and data.captured_at > server_now + datetime.timedelta(minutes=settings.MAX_FUTURE_TIMESTAMP_MINUTES):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TIMESTAMP",
                "message": "captured_at timestamp cannot be more than 5 minutes in the future",
            },
        )

    day = server_now.date()
    hourly_key = f"obs:{listing_id}:{server_now.hour}"
    daily_key = "obs_daily"

    hourly_count = usage_repo.increment_and_get_count(
        db, user_id=current_user.id, day=day, key=hourly_key
    )
    daily_count = usage_repo.increment_and_get_count(
        db, user_id=current_user.id, day=day, key=daily_key
    )

    if (
        hourly_count > settings.MAX_OBSERVATIONS_PER_LISTING_PER_HOUR
        or daily_count > settings.MAX_OBSERVATIONS_PER_USER_PER_DAY
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": "Observation rate limit exceeded",
            },
        )

    # 4. Record observation and trigger alerts atomically
    recorded, obs_status, triggered = observation_service.record_observation_and_evaluate_alerts(
        db=db,
        listing_id=listing_id,
        price=data.price,
        in_stock=data.in_stock,
        captured_at=data.captured_at,
        source="observation",
        user_id=current_user.id,
    )
    db.commit()

    return ObservationResponse(
        recorded=recorded,
        status=obs_status,
        triggered_alerts=triggered,
    )


@router.get("/{id}/history", response_model=PriceHistoryResponse)
def get_price_history(
    id: uuid.UUID,
    days: int = Query(30, ge=1, le=365),
    resolution: str = Query("daily", pattern="^(raw|daily)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    snapshot_service: SnapshotService = Depends(get_snapshot_service),
):
    """
    Retrieve historical price observations and deal statistics for a listing.
    Accepted observations only. Returns insufficient_data when fewer than 3 points.
    Resolution can be 'daily' (lowest accepted price per day) or 'raw'.
    """
    history = snapshot_service.get_price_history(
        db, listing_id=id, days=days, resolution=resolution
    )
    if not history:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )
    return history


@router.post("/{id}/review-summary", response_model=ReviewSummaryResponse, status_code=status.HTTP_200_OK)
def create_review_summary(
    id: uuid.UUID,
    data: ReviewSummaryPostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    review_service: ReviewService = Depends(get_review_service),
    ai_provider: AIProvider = Depends(get_ai_provider),
):
    """
    Generate or retrieve cached review summary for a listing.
    Requires user to have captured at least one snapshot for this listing.
    """
    return review_service.generate_review_summary(
        db=db,
        listing_id=id,
        user=current_user,
        data=data,
        ai_provider=ai_provider,
    )


@router.get("/{id}/review-summary", response_model=ReviewSummaryResponse, status_code=status.HTTP_200_OK)
def get_review_summary(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    review_service: ReviewService = Depends(get_review_service),
):
    """
    Retrieve latest stored review summary for a listing.
    Returns 404 NO_SUMMARY if none exists.
    """
    return review_service.get_latest_review_summary(db=db, listing_id=id)
