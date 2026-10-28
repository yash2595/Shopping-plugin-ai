import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.user import AuthToken, User
from sqlalchemy import select, update
from sqlalchemy.orm import Session


class UserRepository:
    def revoke_token(self, db: Session, token_hash: str) -> None:
        db.execute(update(AuthToken).where(
            AuthToken.token_hash == token_hash, AuthToken.revoked_at.is_(None),
        ).values(revoked_at=datetime.now(timezone.utc)))

    def create_user(self, db: Session, is_anonymous: bool = True) -> User:
        user = User(is_anonymous=is_anonymous)
        db.add(user)
        db.flush()
        return user

    def create_auth_token(
        self,
        db: Session,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> AuthToken:
        auth_token = AuthToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(auth_token)
        db.flush()
        return auth_token

    def get_valid_auth_token(self, db: Session, token_hash: str) -> Optional[AuthToken]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(AuthToken)
            .where(
                AuthToken.token_hash == token_hash,
                AuthToken.revoked_at.is_(None),
                AuthToken.expires_at > now,
            )
        )
        return db.scalars(stmt).first()

    def get_user_by_id(self, db: Session, user_id: uuid.UUID) -> Optional[User]:
        stmt = select(User).where(User.id == user_id)
        return db.scalars(stmt).first()
