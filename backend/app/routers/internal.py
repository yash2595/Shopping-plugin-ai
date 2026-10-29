from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_maintenance_service
from app.schemas.jobs import JobResult
from app.services.maintenance_service import MaintenanceService
from datetime import datetime, timezone
from sqlalchemy import func, select
from fastapi import Response
from app.models.usage import UsageCounter

router = APIRouter(prefix="/internal/jobs", tags=["internal"])


@router.get("/usage")
def aggregate_ai_usage(
    response: Response,
    x_job_secret: Optional[str] = Header(None, alias="X-Job-Secret"),
    db: Session = Depends(get_db),
    maintenance_service: MaintenanceService = Depends(get_maintenance_service),
):
    if not maintenance_service.verify_secret(x_job_secret):
        raise HTTPException(status_code=401, detail="Invalid job secret")
    today = datetime.now(timezone.utc).date()
    totals = dict(db.execute(select(UsageCounter.key, func.sum(UsageCounter.count)).where(
        UsageCounter.day == today, UsageCounter.key.in_(["ai_daily", "speech_daily"])
    ).group_by(UsageCounter.key)).all())
    response.headers["Cache-Control"] = "no-store"
    return {"day": today, "ai_requests": totals.get("ai_daily", 0),
            "speech_requests": totals.get("speech_daily", 0),
            "measurement": "admitted_requests_not_tokens_or_billed_cost"}


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
