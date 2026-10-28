from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.health_service import HealthService

router = APIRouter(tags=["health"])


def get_health_service() -> HealthService:
    return HealthService()


@router.get("/health")
def health_check():
    """Process up check."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(
    db: Session = Depends(get_db),
    health_service: HealthService = Depends(get_health_service),
):
    """Database/schema readiness only; does not spend model/speech quota."""
    try:
        ready = health_service.check_db(db) and health_service.health_repo.schema_ready(db)
    except Exception:
        ready = False
    if not ready:
        raise HTTPException(status_code=503, detail="Service is not ready")
    return {"status": "ready"}


@router.get("/health/db")
def db_health_check(
    db: Session = Depends(get_db),
    health_service: HealthService = Depends(get_health_service),
):
    """Database connectivity check (runs SELECT 1)."""
    try:
        is_alive = health_service.check_db(db)
        if not is_alive:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database ping failed",
            )
        return {"status": "ok", "db": "connected"}
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from None
