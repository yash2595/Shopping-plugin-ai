from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.user import User
from app.repositories.user_repo import UserRepository

settings = get_settings()


class AuthService:
    def __init__(self, user_repo: Optional[UserRepository] = None):
        self.user_repo = user_repo or UserRepository()

    def hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register_anonymous_user(self, db: Session) -> Tuple[str, datetime]:
        raw_token = secrets.token_urlsafe(32)
        token_hash = self.hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.AUTH_TOKEN_EXPIRE_DAYS)

        user = self.user_repo.create_user(db, is_anonymous=True)
        self.user_repo.create_auth_token(
            db,
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.commit()
        return raw_token, expires_at

    def authenticate_token(self, db: Session, raw_token: str) -> Optional[User]:
        token_hash = self.hash_token(raw_token)
        auth_token = self.user_repo.get_valid_auth_token(db, token_hash)
        if not auth_token:
            return None
        return auth_token.user
