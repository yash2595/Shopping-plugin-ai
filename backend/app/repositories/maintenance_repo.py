from datetime import datetime, timezone
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.models.alert import AlertNotification
from app.models.product import ProductSnapshot
from app.models.review import ReviewSummary
from app.models.session import Message, Session as ChatSession
from app.models.user import AuthToken


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MaintenanceRepository:
    def expire_old_auth_tokens(self, db: Session, now: datetime) -> int:
        stmt = (
            update(AuthToken)
            .where(
                AuthToken.expires_at < now,
                AuthToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        result = db.execute(stmt)
        return result.rowcount

    def delete_snapshots_older_than(self, db: Session, cutoff: datetime) -> int:
        stmt = delete(ProductSnapshot).where(ProductSnapshot.created_at < cutoff)
        result = db.execute(stmt)
        return result.rowcount

    def delete_messages_older_than(self, db: Session, cutoff: datetime) -> int:
        stmt = delete(Message).where(Message.created_at < cutoff)
        result = db.execute(stmt)
        return result.rowcount

    def delete_notifications_older_than(self, db: Session, cutoff: datetime) -> int:
        stmt = delete(AlertNotification).where(AlertNotification.created_at < cutoff)
        result = db.execute(stmt)
        return result.rowcount

    def delete_sessions_older_than(self, db: Session, cutoff: datetime) -> int:
        stmt = delete(ChatSession).where(ChatSession.updated_at < cutoff)
        result = db.execute(stmt)
        return result.rowcount

    def delete_review_summaries_older_than(self, db: Session, cutoff: datetime) -> int:
        stmt = delete(ReviewSummary).where(ReviewSummary.created_at < cutoff)
        result = db.execute(stmt)
        return result.rowcount
