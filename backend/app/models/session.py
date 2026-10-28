import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.context import ShoppingContext
    from app.models.user import User


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("user_id", "id", name="uq_sessions_owner_id"),)

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
    title: Mapped[Optional[str]] = mapped_column(
        String(255),
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

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="sessions",
    )
    messages: Mapped[List["Message"]] = relationship(
        "Message",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.created_at.asc()",
    )
    context: Mapped[Optional["ShoppingContext"]] = relationship(
        "ShoppingContext",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    kind: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
        default="answer",
    )
    payload: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )
    request_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    @property
    def alert(self) -> Optional[dict]:
        if self.payload and isinstance(self.payload, dict):
            return self.payload.get("alert")
        return None

    # Unique constraint for idempotency per user
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_messages_user_request_id"),
    )

    # Relationships
    session: Mapped["Session"] = relationship(
        "Session",
        back_populates="messages",
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="messages",
    )


class AIConversation(Base):
    """Private core checkpoint. Never use this model as a public response DTO."""

    __tablename__ = "ai_conversations"

    conversation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    checkpoint: Mapped[str] = mapped_column(Text, nullable=False)
    public_reply: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "session_id", name="uq_ai_conversations_session"),
        UniqueConstraint(
            "user_id", "session_id", "conversation_id", name="uq_ai_conversations_owner"
        ),
        Index("ix_ai_conversations_owner_session", "user_id", "session_id"),
        ForeignKeyConstraint(
            ["user_id", "session_id"],
            ["sessions.user_id", "sessions.id"],
            name="fk_ai_conversations_owned_session",
            ondelete="CASCADE",
        ),
        CheckConstraint("version >= 0", name="ck_ai_conversations_version"),
        CheckConstraint("schema_version = 1", name="ck_ai_conversations_schema"),
        CheckConstraint(
            "octet_length(checkpoint) BETWEEN 1 AND 4000000",
            name="ck_ai_conversations_size",
        ),
    )


class AIEvent(Base):
    __tablename__ = "ai_events"
    conversation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "session_id", "conversation_id"],
            [
                "ai_conversations.user_id",
                "ai_conversations.session_id",
                "ai_conversations.conversation_id",
            ],
            name="fk_ai_events_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('processing', 'complete', 'failed')", name="ck_ai_events_status"
        ),
    )


class AIDispatch(Base):
    __tablename__ = "ai_dispatches"
    dispatch_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    origin: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    claim_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    claim_token: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    claim_target: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    receipt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "session_id", "conversation_id"],
            [
                "ai_conversations.user_id",
                "ai_conversations.session_id",
                "ai_conversations.conversation_id",
            ],
            name="fk_ai_dispatches_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('pending','claimed','received','suppressed','expired')",
            name="ck_ai_dispatches_status",
        ),
        CheckConstraint("kind IN ('browser','market','navigation')", name="ck_ai_dispatches_kind"),
        Index("ix_ai_dispatches_due", "status", "deadline_at"),
        Index("ix_ai_dispatches_session", "user_id", "session_id", "status"),
    )
