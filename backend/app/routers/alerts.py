import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_alert_service, get_current_user
from app.models.user import User
from app.schemas.alert import AlertCreate, AlertRead, AlertUpdate
from app.services.alert_service import AlertService

router = APIRouter(prefix="/v1/alerts", tags=["alerts"])


@router.post("", response_model=AlertRead, status_code=status.HTTP_201_CREATED)
def create_alert(
    data: AlertCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    alert_service: AlertService = Depends(get_alert_service),
):
    """
    Create a new price alert on a listing.
    Rejects with 409 ALERT_ALREADY_EXISTS if an active alert already exists for this listing.
    """
    alert, err = alert_service.create_alert(
        db=db,
        user=current_user,
        listing_id=data.listing_id,
        target_price=data.target_price,
    )
    if err == "listing_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )
    if err == "ALERT_ALREADY_EXISTS":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALERT_ALREADY_EXISTS",
                "message": "An active alert already exists for this listing",
            },
        )
    return alert


@router.get("", response_model=List[AlertRead])
def list_alerts(
    status: Optional[str] = Query(None, pattern="^(active|triggered|cancelled)$"),
    listing_id: Optional[uuid.UUID] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    alert_service: AlertService = Depends(get_alert_service),
):
    """List all alerts belonging to the current user, optionally filtered by status and listing_id."""
    return alert_service.list_alerts(db=db, user=current_user, status=status, listing_id=listing_id)


@router.patch("/{id}", response_model=AlertRead)
def update_alert(
    id: uuid.UUID,
    data: AlertUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    alert_service: AlertService = Depends(get_alert_service),
):
    """
    Update target_price or cancel an alert.
    Requires matching current version (optimistic locking); returns 409 on version mismatch.
    Another user's alert ID returns 404.
    """
    updated, err = alert_service.update_alert(
        db=db,
        user=current_user,
        alert_id=id,
        data=data,
    )
    if err == "alert_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )
    if err == "version_conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "VERSION_CONFLICT",
                "message": "Alert was modified by another operation. Version conflict.",
            },
        )
    return updated


@router.post("/{id}/rearm", response_model=AlertRead)
def rearm_alert(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    alert_service: AlertService = Depends(get_alert_service),
):
    """
    Rearm a triggered alert back to active, incrementing arm_count.
    Only triggered alerts can be rearmed; active alerts return 400.
    Another user's alert ID returns 404.
    """
    rearmed, err = alert_service.rearm_alert(
        db=db,
        user=current_user,
        alert_id=id,
    )
    if err == "alert_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )
    if err == "not_triggered":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only triggered alerts can be rearmed",
        )
    if err == "ALERT_ALREADY_EXISTS":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALERT_ALREADY_EXISTS",
                "message": "An active alert already exists for this listing",
            },
        )
    return rearmed
