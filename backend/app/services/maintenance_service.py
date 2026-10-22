from datetime import datetime, timedelta, timezone
import hmac
from typing import Optional
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.maintenance_repo import MaintenanceRepository
from app.schemas.jobs import JobResult

settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MaintenanceService:
    def __init__(self, maintenance_repo: Optional[MaintenanceRepository] = None):
        self.maintenance_repo = maintenance_repo or MaintenanceRepository()

    def verify_secret(self, provided_secret: Optional[str]) -> bool:
        if not provided_secret:
            return False
        return hmac.compare_digest(provided_secret, settings.JOB_SECRET)

    def run_cleanup_job(self, db: Session) -> JobResult:
        now = utc_now()

        # Retention cutoffs with PLACEHOLDER markers
        snapshot_cutoff = now - timedelta(days=settings.SNAPSHOT_RETENTION_DAYS)  # PLACEHOLDER: 30 days
        message_cutoff = now - timedelta(days=settings.MESSAGE_RETENTION_DAYS)  # PLACEHOLDER: 30 days
        notification_cutoff = now - timedelta(days=settings.NOTIFICATION_RETENTION_DAYS)  # PLACEHOLDER: 90 days
        session_cutoff = now - timedelta(days=settings.SESSION_INACTIVE_RETENTION_DAYS)  # PLACEHOLDER: 90 days
        review_summary_cutoff = now - timedelta(days=settings.REVIEW_SUMMARY_RETENTION_DAYS)  # PLACEHOLDER: 30 days

        expired_tokens = self.maintenance_repo.expire_old_auth_tokens(db, now)
        deleted_snapshots = self.maintenance_repo.delete_snapshots_older_than(db, snapshot_cutoff)
        deleted_messages = self.maintenance_repo.delete_messages_older_than(db, message_cutoff)
        deleted_notifications = self.maintenance_repo.delete_notifications_older_than(db, notification_cutoff)
        deleted_sessions = self.maintenance_repo.delete_sessions_older_than(db, session_cutoff)
        deleted_review_summaries = self.maintenance_repo.delete_review_summaries_older_than(db, review_summary_cutoff)

        db.commit()

        return JobResult(
            expired_tokens=expired_tokens,
            deleted_snapshots=deleted_snapshots,
            deleted_messages=deleted_messages,
            deleted_notifications=deleted_notifications,
            deleted_sessions=deleted_sessions,
            deleted_review_summaries=deleted_review_summaries,
        )
