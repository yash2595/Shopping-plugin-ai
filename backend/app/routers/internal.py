from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_maintenance_service
from app.schemas.jobs import JobResult
from app.services.maintenance_service import MaintenanceService

router = APIRouter(prefix="/internal/jobs", tags=["internal"])


@router.post("/run", response_model=JobResult, status_code=status.HTTP_200_OK)
def run_maintenance_job(
    x_job_secret: Optional[str] = Header(None, alias="X-Job-Secret"),
    db: Session = Depends(get_db),
    maintenance_service: MaintenanceService = Depends(get_maintenance_service),
):
    """
    Internal maintenance job endpoint:
    - Verifies X-Job-Secret header using constant-time comparison
    - Expires outdated auth tokens
    - Purges snapshots older than 30 days
    - Purges messages older than 30 days
    - Purges notifications older than 90 days
    - Idempotent: running repeatedly returns 200 without error
    """
    if not maintenance_service.verify_secret(x_job_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid job secret",
        )

    return maintenance_service.run_cleanup_job(db)
