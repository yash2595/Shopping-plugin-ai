import decimal
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.alert_repo import AlertRepository
from app.repositories.snapshot_repo import SnapshotRepository
from app.schemas.snapshot import TriggeredAlertRead

settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ObservationService:
    def __init__(
        self,
        snapshot_repo: Optional[SnapshotRepository] = None,
        alert_repo: Optional[AlertRepository] = None,
    ):
        self.snapshot_repo = snapshot_repo or SnapshotRepository()
        self.alert_repo = alert_repo or AlertRepository()

    def record_observation_and_evaluate_alerts(
        self,
        db: Session,
        listing_id: uuid.UUID,
        price: decimal.Decimal,
        in_stock: bool,
        captured_at: Optional[datetime] = None,
        source: str = "snapshot",
        user_id: Optional[uuid.UUID] = None,
    ) -> Tuple[bool, str, List[TriggeredAlertRead]]:
        """
        Shared core function:
        1. Checks 6h deduplication for identical price on this listing.
        2. Detects >50% price jump against latest accepted price -> marks status 'pending'.
        3. If not skipped, records PriceObservation.
        4. If status is 'accepted', atomically evaluates and triggers all active alerts where price <= target_price.
        5. Inserts alert_notification for each triggered alert.
        Pending or rejected observations NEVER trigger alerts.
        """
        now = utc_now()
        # Reject captured_at more than 5 minutes in the future (422, code INVALID_TIMESTAMP)
        if captured_at and captured_at > now + timedelta(minutes=settings.MAX_FUTURE_TIMESTAMP_MINUTES):
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "INVALID_TIMESTAMP",
                    "message": "captured_at timestamp cannot be more than 5 minutes in the future",
                },
            )

        effective_captured_at = captured_at or now
        dedup_cutoff = now - timedelta(hours=settings.PRICE_OBSERVATION_DEDUP_HOURS)  # PLACEHOLDER: 6h server window

        # 1. 6h Deduplication using server recorded_at
        if self.snapshot_repo.has_identical_price_observation_since(
            db,
            listing_id=listing_id,
            price=price,
            since=dedup_cutoff,
        ):
            return False, "skipped", []

        # 2. Check price jump against latest accepted observation
        prev_obs = self.snapshot_repo.get_latest_accepted_observation(db, listing_id=listing_id)
        status_val = "accepted"
        if prev_obs and prev_obs.price > 0:
            jump = abs(price - prev_obs.price) / prev_obs.price
            threshold = decimal.Decimal(str(settings.PRICE_CHANGE_PENDING_THRESHOLD))  # PLACEHOLDER: 0.50
            if jump > threshold:
                status_val = "pending"

        # 3. Record PriceObservation with server recorded_at
        self.snapshot_repo.create_price_observation(
            db,
            listing_id=listing_id,
            price=price,
            in_stock=in_stock,
            status=status_val,
            source=source,
            user_id=user_id,
            captured_at=effective_captured_at,
            recorded_at=now,
        )

        triggered_reads: List[TriggeredAlertRead] = []

        # 4. Alert evaluation (ONLY for accepted observations)
        if status_val == "accepted":
            qualifying_alerts = self.alert_repo.get_qualifying_active_alerts_for_listing(
                db,
                listing_id=listing_id,
                price=price,
            )

            for alert in qualifying_alerts:
                # Trigger with ONE atomic statement:
                # UPDATE price_alerts SET status='triggered' ... WHERE id=:id AND status='active' RETURNING *
                triggered_alert = self.alert_repo.trigger_alert_atomic(
                    db,
                    alert_id=alert.id,
                    price=price,
                    triggered_at=now,
                    observation_user_id=user_id,
                )
                if triggered_alert:
                    payload = {
                        "listing_id": str(listing_id),
                        "target_price": str(triggered_alert.target_price),
                        "triggered_price": str(price),
                        "arm_count": triggered_alert.arm_count,
                    }
                    self.alert_repo.create_notification(
                        db,
                        alert_id=triggered_alert.id,
                        user_id=triggered_alert.user_id,
                        arm_count=triggered_alert.arm_count,
                        channel="extension",
                        payload=payload,
                    )
                    triggered_reads.append(
                        TriggeredAlertRead(
                            alert_id=triggered_alert.id,
                            user_id=triggered_alert.user_id,
                            target_price=triggered_alert.target_price,
                            triggered_price=price,
                        )
                    )

        return True, status_val, triggered_reads
