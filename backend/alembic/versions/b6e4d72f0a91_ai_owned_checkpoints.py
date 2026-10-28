"""Private AI checkpoints bound to an existing owned session.

Revision ID: b6e4d72f0a91
Revises: 234eb99aacb2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b6e4d72f0a91"
down_revision = "234eb99aacb2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_sessions_owner_id", "sessions", ["user_id", "id"])
    op.create_table(
        "ai_conversations",
        sa.Column("conversation_id", sa.String(100), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("checkpoint", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id", "session_id"],
            ["sessions.user_id", "sessions.id"],
            name="fk_ai_conversations_owned_session",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version >= 0", name="ck_ai_conversations_version"),
        sa.CheckConstraint("schema_version = 1", name="ck_ai_conversations_schema"),
        sa.CheckConstraint(
            "octet_length(checkpoint) BETWEEN 1 AND 4000000",
            name="ck_ai_conversations_size",
        ),
    )
    op.create_index(
        "ix_ai_conversations_owner_session", "ai_conversations", ["user_id", "session_id"]
    )


def downgrade():
    # Checkpoints are private state. Export before an operator-approved downgrade;
    # dropping this table intentionally discards its contents, never user accounts.
    op.drop_index("ix_ai_conversations_owner_session", table_name="ai_conversations")
    op.drop_table("ai_conversations")
    op.drop_constraint("uq_sessions_owner_id", "sessions", type_="unique")
