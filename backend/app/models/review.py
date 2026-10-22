import decimal
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from sqlalchemy import (
    CHAR,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReviewSummary(Base):
    __tablename__ = "review_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    reviews_hash: Mapped[str] = mapped_column(
        CHAR(64),
        nullable=False,
    )
    sample_size: Mapped[int] = mapped_column(
        nullable=False,
    )
    average_rating_in_sample: Mapped[decimal.Decimal] = mapped_column(
        Numeric(3, 2),
        nullable=False,
    )
    summary: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "listing_id",
            "reviews_hash",
            name="uq_review_summaries_listing_hash",
        ),
    )

    # Relationships
    listing: Mapped["Listing"] = relationship("Listing")
