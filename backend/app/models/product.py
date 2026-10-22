import decimal
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.user import User


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    retailer: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    retailer_product_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("retailer", "retailer_product_id", name="uq_listings_retailer_product_id"),
    )

    # Relationships
    snapshots: Mapped[List["ProductSnapshot"]] = relationship(
        "ProductSnapshot",
        back_populates="listing",
        cascade="all, delete-orphan",
    )
    price_observations: Mapped[List["PriceObservation"]] = relationship(
        "PriceObservation",
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="PriceObservation.captured_at.desc()",
    )


class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    captured_price: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    availability: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    missing_fields: Mapped[List[Any]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="snapshots",
    )
    listing: Mapped["Listing"] = relationship(
        "Listing",
        back_populates="snapshots",
    )


class PriceObservation(Base):
    __tablename__ = "price_observations"

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
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    price: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    in_stock: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(50),
        default="snapshot",
        nullable=False,
    )  # 'snapshot' | 'observation'
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="accepted",
        nullable=False,
    )  # 'accepted' | 'pending' | 'rejected'

    # Relationships
    listing: Mapped["Listing"] = relationship(
        "Listing",
        back_populates="price_observations",
    )
