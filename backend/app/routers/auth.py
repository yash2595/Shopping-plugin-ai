from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_auth_service
from app.schemas.auth import RegisterResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register_anonymous_user(
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Create an anonymous user and return an opaque bearer token."""
    raw_token, expires_at = auth_service.register_anonymous_user(db)
    return RegisterResponse(
        access_token=raw_token,
        token_type="bearer",
        expires_at=expires_at,
    )
