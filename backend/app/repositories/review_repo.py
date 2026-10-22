import decimal
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.review import ReviewSummary


class ReviewRepository:
    def get_latest_summary_for_listing(
        self,
        db: Session,
        listing_id: uuid.UUID,
    ) -> Optional[ReviewSummary]:
        stmt = (
            select(ReviewSummary)
            .where(ReviewSummary.listing_id == listing_id)
            .order_by(ReviewSummary.created_at.desc())
        )
        return db.scalars(stmt).first()

    def get_fresh_summary_by_hash(
        self,
        db: Session,
        listing_id: uuid.UUID,
        reviews_hash: str,
        since: datetime,
    ) -> Optional[ReviewSummary]:
        stmt = select(ReviewSummary).where(
            ReviewSummary.listing_id == listing_id,
            ReviewSummary.reviews_hash == reviews_hash,
            ReviewSummary.created_at >= since,
        )
        return db.scalars(stmt).first()

    def create_summary(
        self,
        db: Session,
        listing_id: uuid.UUID,
        reviews_hash: str,
        sample_size: int,
        average_rating_in_sample: decimal.Decimal,
        summary: Dict[str, Any],
    ) -> ReviewSummary:
        rev = ReviewSummary(
            listing_id=listing_id,
            reviews_hash=reviews_hash,
            sample_size=sample_size,
            average_rating_in_sample=average_rating_in_sample,
            summary=summary,
        )
        db.add(rev)
        db.flush()
        return rev

    def delete_summaries_older_than(
        self,
        db: Session,
        cutoff: datetime,
    ) -> int:
        stmt = delete(ReviewSummary).where(ReviewSummary.created_at < cutoff)
        res = db.execute(stmt)
        return res.rowcount or 0
