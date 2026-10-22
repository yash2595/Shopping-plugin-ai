from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.ai.contracts import AIProvider
from app.ai.mock import MockProvider
from app.db import get_db
from app.models.user import User
from app.repositories.usage_repo import UsageRepository
from app.services.alert_service import AlertService
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.maintenance_service import MaintenanceService
from app.services.notification_service import NotificationService
from app.services.observation_service import ObservationService
from app.repositories.context_repo import ContextRepository
from app.repositories.review_repo import ReviewRepository
from app.services.context_service import ContextService
from app.services.review_service import ReviewService
from app.services.session_service import SessionService
from app.services.snapshot_service import SnapshotService

bearer_scheme = HTTPBearer(auto_error=False)


def get_context_repo() -> ContextRepository:
    return ContextRepository()


def get_context_service() -> ContextService:
    return ContextService()


def get_review_repo() -> ReviewRepository:
    return ReviewRepository()


def get_review_service() -> ReviewService:
    return ReviewService()


def get_usage_repo() -> UsageRepository:
    return UsageRepository()


def get_auth_service() -> AuthService:
    return AuthService()


def get_session_service() -> SessionService:
    return SessionService()


def get_observation_service() -> ObservationService:
    return ObservationService()


def get_snapshot_service() -> SnapshotService:
    return SnapshotService()


def get_chat_service() -> ChatService:
    return ChatService()


def get_alert_service() -> AlertService:
    return AlertService()


def get_notification_service() -> NotificationService:
    return NotificationService()


def get_maintenance_service() -> MaintenanceService:
    return MaintenanceService()


def get_ai_provider() -> AIProvider:
    from app.config import get_settings
    settings = get_settings()
    if settings.AI_PROVIDER.lower() == "gemini":
        from app.ai.gemini import GeminiProvider
        return GeminiProvider()
    return MockProvider()


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> User:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_service.authenticate_token(db, credentials.credentials)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
