"""add_observation_trust_and_usage_counters

Revision ID: 799faaf973f5
Revises: 19d3cec20a91
Create Date: 2026-10-19 23:55:06.120573

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '799faaf973f5'
down_revision: Union[str, Sequence[str], None] = '19d3cec20a91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('usage_counters',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'day', 'key', name='uq_usage_counters_user_day_key')
    )
    op.create_index(op.f('ix_usage_counters_user_id'), 'usage_counters', ['user_id'], unique=False)
    op.add_column('price_observations', sa.Column('user_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_price_observations_user_id'), 'price_observations', ['user_id'], unique=False)
    op.create_foreign_key('fk_price_observations_user_id', 'price_observations', 'users', ['user_id'], ['id'], ondelete='SET NULL')

    # Backfill existing rows from product_snapshots where possible
    op.execute("""
        UPDATE price_observations po
        SET user_id = ps.user_id,
            source = 'snapshot'
        FROM (
            SELECT DISTINCT ON (listing_id) listing_id, user_id
            FROM product_snapshots
            ORDER BY listing_id, created_at ASC
        ) ps
        WHERE po.listing_id = ps.listing_id
          AND po.user_id IS NULL;
    """)
    op.execute("""
        UPDATE price_observations
        SET source = 'snapshot'
        WHERE source = 'extension_snapshot';
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_price_observations_user_id', 'price_observations', type_='foreignkey')
    op.drop_index(op.f('ix_price_observations_user_id'), table_name='price_observations')
    op.drop_column('price_observations', 'user_id')
    op.drop_index(op.f('ix_usage_counters_user_id'), table_name='usage_counters')
    op.drop_table('usage_counters')
