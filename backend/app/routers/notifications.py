import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, get_notification_service
from app.models.user import User
from app.schemas.notification import NotificationRead
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@router.get("", response_model=List[NotificationRead])
def list_notifications(
    status: Optional[str] = Query("pending", pattern="^(pending|delivered|dismissed)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    notification_service: NotificationService = Depends(get_notification_service),
):
    """List notifications for current user, default status=pending."""
    return notification_service.list_notifications(db=db, user=current_user, status=status)


@router.post("/{id}/ack", response_model=NotificationRead)
def acknowledge_notification(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    notification_service: NotificationService = Depends(get_notification_service),
):
    """Acknowledge a notification as delivered."""
    acked = notification_service.acknowledge_notification(db=db, user=current_user, notification_id=id)
    if not acked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    return acked
