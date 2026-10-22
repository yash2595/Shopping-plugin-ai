import decimal
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.product import Listing


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PriceAlert(Base):
    __tablename__ = "price_alerts"

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
    target_price: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )  # 'active' | 'triggered' | 'cancelled'
    arm_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    triggered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    triggered_price: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
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
        CheckConstraint("target_price > 0", name="ck_price_alerts_target_price_positive"),
        Index(
            "uq_price_alerts_user_listing_active",
            "user_id",
            "listing_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    # Relationships
    user: Mapped["User"] = relationship("User")
    listing: Mapped["Listing"] = relationship("Listing")
    notifications: Mapped[List["AlertNotification"]] = relationship(
        "AlertNotification",
        back_populates="alert",
        cascade="all, delete-orphan",
    )


class AlertNotification(Base):
    __tablename__ = "alert_notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_alerts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    arm_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(
        String(50),
        default="extension",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
    )  # 'pending' | 'delivered' | 'dismissed'
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("alert_id", "arm_count", "channel", name="uq_alert_notifications_alert_arm_channel"),
    )

    # Relationships
    alert: Mapped["PriceAlert"] = relationship("PriceAlert", back_populates="notifications")
    user: Mapped["User"] = relationship("User")
