from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, get_snapshot_service
from app.models.user import User
from app.schemas.snapshot import SnapshotCreate, SnapshotResponse
from app.services.snapshot_service import SnapshotService

router = APIRouter(prefix="/v1/snapshots", tags=["snapshots"])


@router.post("", response_model=SnapshotResponse, status_code=status.HTTP_201_CREATED)
def create_snapshot(
    data: SnapshotCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    snapshot_service: SnapshotService = Depends(get_snapshot_service),
):
    """
    Ingest a product snapshot captured by the Chrome extension.
    Upserts listing, records snapshot, and tracks price observation.
    """
    return snapshot_service.process_snapshot(
        db=db,
        user=current_user,
        data=data,
    )
