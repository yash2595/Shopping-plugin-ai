"""Bind verified issuer/subject identities to owned users.

Revision ID: d8a6b94f2c13
Revises: c7f5a83e1b02
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d8a6b94f2c13"
down_revision = "c7f5a83e1b02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_identities",
        sa.Column("issuer", sa.String(512), primary_key=True),
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])


def downgrade():
    op.drop_table("external_identities")
