import decimal
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.user import User
from app.repositories.snapshot_repo import SnapshotRepository
from app.schemas.snapshot import (
    HistoryObservation,
    PriceHistoryResponse,
    PriceSummary,
    SnapshotCreate,
    SnapshotResponse,
)
from app.services.observation_service import ObservationService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SnapshotService:
    def __init__(
        self,
        snapshot_repo: Optional[SnapshotRepository] = None,
        observation_service: Optional[ObservationService] = None,
    ):
        self.snapshot_repo = snapshot_repo or SnapshotRepository()
        self.observation_service = observation_service or ObservationService(
            snapshot_repo=self.snapshot_repo
        )

    def normalize_product_identity(self, retailer: str, retailer_product_id: str) -> tuple[str, str]:
        """Normalises retailer and product id. Listings are keyed by retailer + retailer_product_id."""
        norm_retailer = retailer.strip().lower()
        norm_product_id = retailer_product_id.strip()
        if "amazon" in norm_retailer:
            norm_product_id = norm_product_id.upper()
        return norm_retailer, norm_product_id

    def process_snapshot(
        self,
        db: Session,
        user: User,
        data: SnapshotCreate,
    ) -> SnapshotResponse:
        norm_retailer, norm_product_id = self.normalize_product_identity(
            data.retailer,
            data.retailer_product_id,
        )

        # 1. Upsert listing
        listing = self.snapshot_repo.get_listing_by_identity(
            db,
            retailer=norm_retailer,
            retailer_product_id=norm_product_id,
        )
        if listing:
            self.snapshot_repo.update_listing(
                db,
                listing=listing,
                title=data.title,
                url=data.url,
            )
        else:
            listing = self.snapshot_repo.create_listing(
                db,
                retailer=norm_retailer,
                retailer_product_id=norm_product_id,
                title=data.title,
                url=data.url,
            )

        # 2. Store product snapshot (belongs to user and listing)
        snapshot = self.snapshot_repo.create_snapshot(
            db,
            user_id=user.id,
            listing_id=listing.id,
            captured_price=data.price,
            availability=data.availability,
            document_id=data.document_id,
            missing_fields=data.missing_fields,
        )

        # 3. Shared observation recording & alert evaluation
        in_stock = data.availability.strip().lower() in ("in_stock", "available", "instock", "true")
        recorded, status, _ = self.observation_service.record_observation_and_evaluate_alerts(
            db=db,
            listing_id=listing.id,
            price=data.price,
            in_stock=in_stock,
            captured_at=utc_now(),
            source="snapshot",
            user_id=user.id,
        )

        db.commit()
        db.refresh(snapshot)
        db.refresh(listing)

        # 4. Compute price summary from real accepted history.
        # Rule: Both count >= 3 AND span >= MIN_HISTORY_SPAN_DAYS (7 days) must hold.
        # Otherwise verdict is insufficient_data with reason (not_enough_observations or history_too_short) and tracking_since.
        settings = get_settings()
        accepted_observations = self.snapshot_repo.get_all_accepted_observations(db, listing_id=listing.id)
        count = len(accepted_observations)

        first_ts: Optional[datetime] = None
        latest_ts: Optional[datetime] = None
        span_days: float = 0.0

        if accepted_observations:
            timestamps = [o.recorded_at or o.captured_at for o in accepted_observations]
            first_ts = min(timestamps)
            latest_ts = max(timestamps)
            span_seconds = (latest_ts - first_ts).total_seconds()
            span_days = span_seconds / 86400.0

        if count < 3:
            price_summary = PriceSummary(
                verdict="insufficient_data",
                reason="not_enough_observations",
                tracking_since=first_ts,
                lowest_price=None,
                highest_price=None,
                average_price=None,
                observation_count=count,
            )
        elif span_days < float(settings.MIN_HISTORY_SPAN_DAYS):
            price_summary = PriceSummary(
                verdict="insufficient_data",
                reason="history_too_short",
                tracking_since=first_ts,
                lowest_price=None,
                highest_price=None,
                average_price=None,
                observation_count=count,
            )
        else:
            prices = [obs.price for obs in accepted_observations]
            lowest = min(prices)
            highest = max(prices)
            avg = round(sum(prices) / decimal.Decimal(len(prices)), 2)

            if data.price <= lowest:
                verdict = "good_deal"
            elif data.price >= highest:
                verdict = "expensive"
            else:
                verdict = "fair_price"

            price_summary = PriceSummary(
                verdict=verdict,
                reason=None,
                tracking_since=first_ts,
                lowest_price=lowest,
                highest_price=highest,
                average_price=avg,
                observation_count=count,
            )

        return SnapshotResponse(
            snapshot_id=snapshot.id,
            listing_id=listing.id,
            price_recorded=recorded,
            price_summary=price_summary,
        )

    def get_price_history(
        self,
        db: Session,
        listing_id: uuid.UUID,
        days: int = 30,
        resolution: str = "daily",
    ) -> Optional[PriceHistoryResponse]:
        settings = get_settings()
        listing = self.snapshot_repo.get_listing_by_id(db, listing_id=listing_id)
        if not listing:
            return None

        since = utc_now() - timedelta(days=days)
        history_obs = self.snapshot_repo.get_history_observations(db, listing_id=listing_id, since=since)
        first_obs = self.snapshot_repo.get_first_accepted_observation(db, listing_id=listing_id)

        raw_items = [
            HistoryObservation(
                price=o.price,
                in_stock=o.in_stock,
                captured_at=o.captured_at,
            )
            for o in history_obs
        ]

        count = len(raw_items)

        if resolution == "daily":
            # Group by day, select lowest accepted price that day
            by_day: dict = {}
            for o in raw_items:
                d = o.captured_at.date()
                if d not in by_day or o.price < by_day[d].price:
                    by_day[d] = o
            obs_items = sorted(by_day.values(), key=lambda x: x.captured_at)
            obs_items = obs_items[:settings.MAX_HISTORY_DAILY_POINTS]  # PLACEHOLDER: 365
        else:
            obs_items = raw_items

        first_ts: Optional[datetime] = None
        latest_ts: Optional[datetime] = None
        span_days: float = 0.0

        if history_obs:
            timestamps = [o.recorded_at or o.captured_at for o in history_obs]
            first_ts = min(timestamps)
            latest_ts = max(timestamps)
            span_seconds = (latest_ts - first_ts).total_seconds()
            span_days = span_seconds / 86400.0
        elif first_obs:
            first_ts = first_obs.recorded_at or first_obs.captured_at

        first_seen_val = first_obs.captured_at if first_obs else None
        tracking_since_val = first_ts or (first_obs.recorded_at if first_obs else None)

        if count < 3:
            return PriceHistoryResponse(
                listing_id=listing_id,
                days=days,
                observations=obs_items,
                count=count,
                min=None,
                max=None,
                average=None,
                first_seen=first_seen_val,
                verdict="insufficient_data",
                reason="not_enough_observations",
                tracking_since=tracking_since_val,
            )

        if span_days < float(settings.MIN_HISTORY_SPAN_DAYS):
            return PriceHistoryResponse(
                listing_id=listing_id,
                days=days,
                observations=obs_items,
                count=count,
                min=None,
                max=None,
                average=None,
                first_seen=first_seen_val,
                verdict="insufficient_data",
                reason="history_too_short",
                tracking_since=tracking_since_val,
            )

        prices = [o.price for o in raw_items]
        min_price = min(prices)
        max_price = max(prices)
        avg_price = round(sum(prices) / decimal.Decimal(count), 2)
        latest_price = raw_items[-1].price

        if latest_price <= min_price:
            verdict = "good_deal"
        elif latest_price >= max_price:
            verdict = "expensive"
        else:
            verdict = "fair_price"

        return PriceHistoryResponse(
            listing_id=listing_id,
            days=days,
            observations=obs_items,
            count=count,
            min=min_price,
            max=max_price,
            average=avg_price,
            first_seen=first_seen_val,
            verdict=verdict,
            reason=None,
            tracking_since=tracking_since_val,
        )
