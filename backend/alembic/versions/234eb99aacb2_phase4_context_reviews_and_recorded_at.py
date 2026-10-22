"""phase4_context_reviews_and_recorded_at

Revision ID: 234eb99aacb2
Revises: 02266c52bf50
Create Date: 2026-10-22 01:47:12.009029

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '234eb99aacb2'
down_revision: Union[str, Sequence[str], None] = '02266c52bf50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add recorded_at to price_observations
    op.add_column(
        'price_observations',
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True)
    )
    op.execute("UPDATE price_observations SET recorded_at = captured_at WHERE recorded_at IS NULL")
    op.alter_column('price_observations', 'recorded_at', nullable=False)
    op.create_index(op.f('ix_price_observations_recorded_at'), 'price_observations', ['recorded_at'], unique=False)

    # 2. Create shopping_contexts
    op.create_table(
        'shopping_contexts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('budget_max', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('preferences', sa.dialects.postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('budget_max IS NULL OR budget_max > 0', name='ck_shopping_contexts_budget_max_positive'),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', name='uq_shopping_contexts_session_id')
    )
    op.create_index(op.f('ix_shopping_contexts_user_id'), 'shopping_contexts', ['user_id'], unique=False)

    # Backfill shopping_contexts for existing sessions
    op.execute("""
        INSERT INTO shopping_contexts (id, user_id, session_id, budget_max, currency, preferences, version, created_at, updated_at)
        SELECT gen_random_uuid(), user_id, id, NULL, 'INR', '{}'::jsonb, 1, created_at, updated_at
        FROM sessions
        WHERE id NOT IN (SELECT session_id FROM shopping_contexts);
    """)

    # 3. Create rejected_products
    op.create_table(
        'rejected_products',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('context_id', sa.UUID(), nullable=False),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['context_id'], ['shopping_contexts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('context_id', 'listing_id', name='uq_rejected_products_context_listing')
    )
    op.create_index(op.f('ix_rejected_products_context_id'), 'rejected_products', ['context_id'], unique=False)

    # 4. Create review_summaries
    op.create_table(
        'review_summaries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('reviews_hash', sa.CHAR(length=64), nullable=False),
        sa.Column('sample_size', sa.Integer(), nullable=False),
        sa.Column('average_rating_in_sample', sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column('summary', sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('listing_id', 'reviews_hash', name='uq_review_summaries_listing_hash')
    )
    op.create_index(op.f('ix_review_summaries_listing_id'), 'review_summaries', ['listing_id'], unique=False)
    op.create_index(op.f('ix_review_summaries_created_at'), 'review_summaries', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_review_summaries_created_at'), table_name='review_summaries')
    op.drop_index(op.f('ix_review_summaries_listing_id'), table_name='review_summaries')
    op.drop_table('review_summaries')

    op.drop_index(op.f('ix_rejected_products_context_id'), table_name='rejected_products')
    op.drop_table('rejected_products')

    op.drop_index(op.f('ix_shopping_contexts_user_id'), table_name='shopping_contexts')
    op.drop_table('shopping_contexts')

    op.drop_index(op.f('ix_price_observations_recorded_at'), table_name='price_observations')
    op.drop_column('price_observations', 'recorded_at')

