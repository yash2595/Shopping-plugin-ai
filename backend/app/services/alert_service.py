import decimal
import uuid
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.alert import PriceAlert
from app.models.user import User
from app.repositories.alert_repo import AlertRepository
from app.repositories.snapshot_repo import SnapshotRepository
from app.schemas.alert import AlertUpdate


class AlertService:
    def __init__(
        self,
        alert_repo: Optional[AlertRepository] = None,
        snapshot_repo: Optional[SnapshotRepository] = None,
    ):
        self.alert_repo = alert_repo or AlertRepository()
        self.snapshot_repo = snapshot_repo or SnapshotRepository()

    def create_alert(
        self,
        db: Session,
        user: User,
        listing_id: uuid.UUID,
        target_price: decimal.Decimal,
    ) -> Tuple[Optional[PriceAlert], Optional[str]]:
        # 1. Verify listing exists
        listing = self.snapshot_repo.get_listing_by_id(db, listing_id)
        if not listing:
            return None, "listing_not_found"

        # 2. Check if active alert already exists for this listing
        existing_active = self.alert_repo.get_active_alert_by_user_listing(
            db,
            user_id=user.id,
            listing_id=listing_id,
        )
        if existing_active:
            return None, "ALERT_ALREADY_EXISTS"

        alert = self.alert_repo.create_alert(
            db,
            user_id=user.id,
            listing_id=listing_id,
            target_price=target_price,
        )
        db.commit()
        db.refresh(alert)
        return alert, None

    def list_alerts(
        self,
        db: Session,
        user: User,
        status: Optional[str] = None,
        listing_id: Optional[uuid.UUID] = None,
    ) -> List[PriceAlert]:
        return self.alert_repo.list_alerts(db, user_id=user.id, status=status, listing_id=listing_id)

    def update_alert(
        self,
        db: Session,
        user: User,
        alert_id: uuid.UUID,
        data: AlertUpdate,
    ) -> Tuple[Optional[PriceAlert], Optional[str]]:
        alert = self.alert_repo.get_alert_by_id(db, alert_id=alert_id, user_id=user.id)
        if not alert:
            return None, "alert_not_found"

        if alert.version != data.version:
            return None, "version_conflict"

        updated = self.alert_repo.update_alert_optimistic(
            db,
            alert_id=alert_id,
            user_id=user.id,
            expected_version=data.version,
            target_price=data.target_price,
            status=data.status,
        )
        if not updated:
            return None, "version_conflict"

        db.commit()
        db.refresh(updated)
        return updated, None

    def rearm_alert(
        self,
        db: Session,
        user: User,
        alert_id: uuid.UUID,
    ) -> Tuple[Optional[PriceAlert], Optional[str]]:
        alert = self.alert_repo.get_alert_by_id(db, alert_id=alert_id, user_id=user.id)
        if not alert:
            return None, "alert_not_found"

        if alert.status != "triggered":
            return None, "not_triggered"

        # Ensure no other active alert currently exists for this listing
        existing_active = self.alert_repo.get_active_alert_by_user_listing(
            db,
            user_id=user.id,
            listing_id=alert.listing_id,
        )
        if existing_active and existing_active.id != alert.id:
            return None, "ALERT_ALREADY_EXISTS"

        rearmed = self.alert_repo.rearm_alert(db, alert_id=alert_id, user_id=user.id)
        if not rearmed:
            return None, "not_triggered"

        db.commit()
        db.refresh(rearmed)
        return rearmed, None
