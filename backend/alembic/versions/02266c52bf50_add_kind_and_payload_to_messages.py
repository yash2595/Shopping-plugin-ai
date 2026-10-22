"""add_kind_and_payload_to_messages

Revision ID: 02266c52bf50
Revises: 799faaf973f5
Create Date: 2026-10-20 00:15:18.515203

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02266c52bf50'
down_revision: Union[str, Sequence[str], None] = '799faaf973f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('kind', sa.String(length=32), nullable=True))
    op.add_column('messages', sa.Column('payload', sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'payload')
    op.drop_column('messages', 'kind')
