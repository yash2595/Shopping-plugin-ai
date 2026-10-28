from app.config import get_settings
from app.db import get_db
from app.deps import bearer_scheme, get_auth_service, get_current_user
from app.models.user import User
from app.schemas.auth import RegisterResponse
from app.services.auth_service import AuthService
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register_anonymous_user(
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Create an anonymous user and return an opaque bearer token."""
    settings = get_settings()
    if settings.AUTH_MODE == "oidc" or (
        settings.ENV == "production" and not settings.ALLOW_ANONYMOUS_REGISTRATION
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ANONYMOUS_REGISTRATION_DISABLED",
                "message": "Anonymous registration is disabled in this environment.",
            },
        )
    raw_token, expires_at = auth_service.register_anonymous_user(db)
    return RegisterResponse(
        access_token=raw_token,
        token_type="bearer",
        expires_at=expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Revoke the authenticated bearer token, including access to owned chat routes."""
    auth_service.revoke_token(db, credentials.credentials)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
