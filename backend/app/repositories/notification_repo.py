import uuid
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.alert import AlertNotification


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationRepository:
    def list_notifications(
        self,
        db: Session,
        user_id: uuid.UUID,
        status: Optional[str] = None,
    ) -> List[AlertNotification]:
        stmt = select(AlertNotification).where(AlertNotification.user_id == user_id)
        if status:
            stmt = stmt.where(AlertNotification.status == status)
        stmt = stmt.order_by(AlertNotification.created_at.desc())
        return list(db.scalars(stmt).all())

    def get_notification_by_id(
        self,
        db: Session,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[AlertNotification]:
        stmt = select(AlertNotification).where(
            AlertNotification.id == notification_id,
            AlertNotification.user_id == user_id,
        )
        return db.scalars(stmt).first()

    def ack_notification(
        self,
        db: Session,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[AlertNotification]:
        now = utc_now()
        stmt = (
            update(AlertNotification)
            .where(
                AlertNotification.id == notification_id,
                AlertNotification.user_id == user_id,
            )
            .values(
                status="delivered",
                delivered_at=now,
            )
            .returning(AlertNotification)
        )
        return db.scalars(stmt).first()
