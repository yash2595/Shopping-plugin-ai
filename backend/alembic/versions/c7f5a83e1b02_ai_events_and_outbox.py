"""Durable runtime admission and committed browser work.

Revision ID: c7f5a83e1b02
Revises: b6e4d72f0a91
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "c7f5a83e1b02"
down_revision = "b6e4d72f0a91"
branch_labels = depends_on = None


def owner_fk(name):
    return sa.ForeignKeyConstraint(
        ["user_id", "session_id", "conversation_id"],
        [
            "ai_conversations.user_id",
            "ai_conversations.session_id",
            "ai_conversations.conversation_id",
        ],
        name=name,
        ondelete="CASCADE",
    )


def upgrade():
    op.add_column("ai_conversations", sa.Column("public_reply", JSONB(), nullable=True))
    op.create_unique_constraint(
        "uq_ai_conversations_session", "ai_conversations", ["user_id", "session_id"]
    )
    op.create_unique_constraint(
        "uq_ai_conversations_owner",
        "ai_conversations",
        ["user_id", "session_id", "conversation_id"],
    )
    op.create_table(
        "ai_events",
        sa.Column("conversation_id", sa.String(100), primary_key=True),
        sa.Column("event_id", sa.String(100), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("expected_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("lease_token", UUID(as_uuid=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", JSONB(), nullable=True),
        owner_fk("fk_ai_events_owner"),
        sa.CheckConstraint(
            "status IN ('processing','complete','failed')", name="ck_ai_events_status"
        ),
    )
    op.create_table(
        "ai_dispatches",
        sa.Column("dispatch_id", sa.String(100), primary_key=True),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("origin", sa.String(200), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("claim_id", sa.String(100)),
        sa.Column("claim_token", UUID(as_uuid=True)),
        sa.Column("claim_target", JSONB()),
        sa.Column("receipt_hash", sa.String(64)),
        owner_fk("fk_ai_dispatches_owner"),
        sa.CheckConstraint(
            "status IN ('pending','claimed','received','suppressed','expired')",
            name="ck_ai_dispatches_status",
        ),
        sa.CheckConstraint(
            "kind IN ('browser','market','navigation')", name="ck_ai_dispatches_kind"
        ),
    )
    op.create_index("ix_ai_dispatches_due", "ai_dispatches", ["status", "deadline_at"])
    op.create_index(
        "ix_ai_dispatches_session", "ai_dispatches", ["user_id", "session_id", "status"]
    )


def downgrade():
    op.drop_table("ai_dispatches")
    op.drop_table("ai_events")
    op.drop_constraint("uq_ai_conversations_owner", "ai_conversations", type_="unique")
    op.drop_constraint("uq_ai_conversations_session", "ai_conversations", type_="unique")
    op.drop_column("ai_conversations", "public_reply")
