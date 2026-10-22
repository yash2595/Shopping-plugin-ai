import uuid
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.alert import AlertNotification
from app.models.user import User
from app.repositories.notification_repo import NotificationRepository


class NotificationService:
    def __init__(self, notification_repo: Optional[NotificationRepository] = None):
        self.notification_repo = notification_repo or NotificationRepository()

    def list_notifications(
        self,
        db: Session,
        user: User,
        status: Optional[str] = "pending",
    ) -> List[AlertNotification]:
        return self.notification_repo.list_notifications(db, user_id=user.id, status=status)

    def acknowledge_notification(
        self,
        db: Session,
        user: User,
        notification_id: uuid.UUID,
    ) -> Optional[AlertNotification]:
        acked = self.notification_repo.ack_notification(db, notification_id=notification_id, user_id=user.id)
        if not acked:
            return None
        db.commit()
        db.refresh(acked)
        return acked
