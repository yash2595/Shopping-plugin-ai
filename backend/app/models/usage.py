import datetime
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class UsageCounter(Base):
    __tablename__ = "usage_counters"

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
    day: Mapped[datetime.date] = mapped_column(
        Date,
        nullable=False,
    )
    key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "day", "key", name="uq_usage_counters_user_day_key"),
    )

    # Relationship
    user: Mapped["User"] = relationship("User")
