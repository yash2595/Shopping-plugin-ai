import decimal
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.alert import AlertNotification, PriceAlert
from app.models.product import PriceObservation


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertRepository:
    def create_alert(
        self,
        db: Session,
        user_id: uuid.UUID,
        listing_id: uuid.UUID,
        target_price: decimal.Decimal,
    ) -> PriceAlert:
        alert = PriceAlert(
            user_id=user_id,
            listing_id=listing_id,
            target_price=target_price,
            status="active",
            arm_count=1,
            version=1,
        )
        db.add(alert)
        db.flush()
        return alert

    def get_active_alert_by_user_listing(
        self,
        db: Session,
        user_id: uuid.UUID,
        listing_id: uuid.UUID,
    ) -> Optional[PriceAlert]:
        stmt = select(PriceAlert).where(
            PriceAlert.user_id == user_id,
            PriceAlert.listing_id == listing_id,
            PriceAlert.status == "active",
        )
        return db.scalars(stmt).first()

    def get_alert_by_id(
        self,
        db: Session,
        alert_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[PriceAlert]:
        stmt = select(PriceAlert).where(
            PriceAlert.id == alert_id,
            PriceAlert.user_id == user_id,
        )
        return db.scalars(stmt).first()

    def list_alerts(
        self,
        db: Session,
        user_id: uuid.UUID,
        status: Optional[str] = None,
        listing_id: Optional[uuid.UUID] = None,
    ) -> List[PriceAlert]:
        stmt = select(PriceAlert).where(PriceAlert.user_id == user_id)
        if status:
            stmt = stmt.where(PriceAlert.status == status)
        if listing_id:
            stmt = stmt.where(PriceAlert.listing_id == listing_id)
        stmt = stmt.order_by(PriceAlert.created_at.desc())
        return list(db.scalars(stmt).all())

    def update_alert_optimistic(
        self,
        db: Session,
        alert_id: uuid.UUID,
        user_id: uuid.UUID,
        expected_version: int,
        target_price: Optional[decimal.Decimal] = None,
        status: Optional[str] = None,
    ) -> Optional[PriceAlert]:
        values: Dict[str, Any] = {
            "version": expected_version + 1,
            "updated_at": utc_now(),
        }
        if target_price is not None:
            values["target_price"] = target_price
        if status is not None:
            values["status"] = status

        stmt = (
            update(PriceAlert)
            .where(
                PriceAlert.id == alert_id,
                PriceAlert.user_id == user_id,
                PriceAlert.version == expected_version,
            )
            .values(**values)
            .returning(PriceAlert)
        )
        return db.scalars(stmt).first()

    def rearm_alert(
        self,
        db: Session,
        alert_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[PriceAlert]:
        now = utc_now()
        stmt = (
            update(PriceAlert)
            .where(
                PriceAlert.id == alert_id,
                PriceAlert.user_id == user_id,
                PriceAlert.status == "triggered",
            )
            .values(
                status="active",
                arm_count=PriceAlert.arm_count + 1,
                version=PriceAlert.version + 1,
                triggered_at=None,
                triggered_price=None,
                updated_at=now,
            )
            .returning(PriceAlert)
        )
        return db.scalars(stmt).first()

    def get_qualifying_active_alerts_for_listing(
        self,
        db: Session,
        listing_id: uuid.UUID,
        price: decimal.Decimal,
    ) -> List[PriceAlert]:
        stmt = select(PriceAlert).where(
            PriceAlert.listing_id == listing_id,
            PriceAlert.status == "active",
            PriceAlert.target_price >= price,
        )
        return list(db.scalars(stmt).all())

    def trigger_alert_atomic(
        self,
        db: Session,
        alert_id: uuid.UUID,
        price: decimal.Decimal,
        triggered_at: datetime,
        observation_user_id: Optional[uuid.UUID] = None,
    ) -> Optional[PriceAlert]:
        """
        Atomically trigger active alert:
        An alert triggers ONLY if the qualifying observation comes from the alert owner,
        OR at least one other accepted observation from a DIFFERENT user at or below the target
        exists in the last 24h. Implemented inside the same atomic UPDATE ... RETURNING (EXISTS subquery).
        """
        since_24h = triggered_at - timedelta(hours=24)
        confirming_drop_subquery = (
            select(PriceObservation.id)
            .where(
                PriceObservation.listing_id == PriceAlert.listing_id,
                PriceObservation.status == "accepted",
                PriceObservation.user_id.is_not(None),
                PriceObservation.user_id != PriceAlert.user_id,
                PriceObservation.user_id != observation_user_id,
                PriceObservation.price <= PriceAlert.target_price,
                PriceObservation.recorded_at >= since_24h,
            )
            .exists()
        )

        owner_condition = (
            PriceAlert.user_id == observation_user_id
            if observation_user_id is not None
            else False
        )

        stmt = (
            update(PriceAlert)
            .where(
                PriceAlert.id == alert_id,
                PriceAlert.status == "active",
                or_(
                    owner_condition,
                    confirming_drop_subquery,
                ),
            )
            .values(
                status="triggered",
                triggered_at=triggered_at,
                triggered_price=price,
                updated_at=triggered_at,
                version=PriceAlert.version + 1,
            )
            .returning(PriceAlert)
        )
        return db.scalars(stmt).first()

    def create_notification(
        self,
        db: Session,
        alert_id: uuid.UUID,
        user_id: uuid.UUID,
        arm_count: int,
        channel: str,
        payload: Dict[str, Any],
    ) -> Optional[AlertNotification]:
        """
        Inserts notification using ON CONFLICT DO NOTHING on (alert_id, arm_count, channel)
        to prevent duplicate deliveries during concurrent observation triggers.
        """
        insert_stmt = (
            insert(AlertNotification)
            .values(
                id=uuid.uuid4(),
                alert_id=alert_id,
                user_id=user_id,
                arm_count=arm_count,
                channel=channel,
                status="pending",
                payload=payload,
                created_at=utc_now(),
            )
            .on_conflict_do_nothing(
                constraint="uq_alert_notifications_alert_arm_channel"
            )
            .returning(AlertNotification)
        )
        return db.scalars(insert_stmt).first()
