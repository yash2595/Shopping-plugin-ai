import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.user import AuthToken, ExternalIdentity, User
from app.repositories.user_repo import UserRepository

settings = get_settings()


class AuthService:
    def erase_shopping_data(self, db: Session, user_id) -> None:
        """Erase owned shopping records atomically; preserve identity and abuse limits.

        This is data erasure, not identity-provider account closure or backup removal.
        Session deletion cascades to checkpoints, events and pending browser work.
        """
        from sqlalchemy import delete
        from app.models.alert import AlertNotification, PriceAlert
        from app.models.context import ShoppingContext
        from app.models.product import PriceObservation, ProductSnapshot
        from app.models.review import ReviewSummary
        from app.models.session import Session as ChatSession

        db.execute(select(User.id).where(User.id == user_id).with_for_update())
        db.execute(delete(ReviewSummary).where(ReviewSummary.summary["owner_id"].astext == str(user_id)))
        for model in (AlertNotification, PriceAlert, PriceObservation, ProductSnapshot, ShoppingContext, ChatSession):
            db.execute(delete(model).where(model.user_id == user_id))
        db.commit()

    def __init__(self, user_repo: Optional[UserRepository] = None):
        self.user_repo = user_repo or UserRepository()
        self.verifier = None
        if settings.AUTH_MODE == "oidc":
            from app.services.identity import IdentityVerifier

            self.verifier = IdentityVerifier(settings)

    def hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register_anonymous_user(self, db: Session) -> Tuple[str, datetime]:
        if self.verifier is not None:
            raise ValueError("Anonymous registration is disabled")
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
        if self.verifier is not None:
            claims = self.verifier.verify(raw_token)
            if claims is None:
                return None
            revoked = db.scalar(
                select(AuthToken.id).where(
                    AuthToken.token_hash == self.hash_token(raw_token),
                    AuthToken.revoked_at.is_not(None),
                )
            )
            if revoked is not None:
                return None
            identity = db.get(ExternalIdentity, (claims["iss"], claims["sub"]))
            if identity is None:
                try:
                    with db.begin_nested():
                        user = self.user_repo.create_user(db, is_anonymous=False)
                        identity = ExternalIdentity(
                            issuer=claims["iss"], subject=claims["sub"], user_id=user.id
                        )
                        db.add(identity)
                        db.flush()
                except IntegrityError:
                    identity = db.get(ExternalIdentity, (claims["iss"], claims["sub"]))
                    if identity is None:
                        raise
                db.commit()
            return db.get(User, identity.user_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token):
            return None
        token_hash = self.hash_token(raw_token)
        auth_token = self.user_repo.get_valid_auth_token(db, token_hash)
        if not auth_token:
            return None
        return auth_token.user

    def revoke_token(self, db: Session, raw_token: str) -> None:
        if self.verifier is not None:
            claims = self.verifier.verify(raw_token)
            if claims is None:
                return
            identity = db.get(ExternalIdentity, (claims["iss"], claims["sub"]))
            if identity is None:
                return
            db.execute(
                insert(AuthToken)
                .values(
                    user_id=identity.user_id,
                    token_hash=self.hash_token(raw_token),
                    expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
                    revoked_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(index_elements=[AuthToken.token_hash])
            )
            db.commit()
            return
        self.user_repo.revoke_token(db, self.hash_token(raw_token))
        db.commit()
