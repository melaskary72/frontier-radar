"""web pages and private signals

Revision ID: 9b1e4d2c7a31
Revises: 7a3c9e1d4b20
Create Date: 2026-11-03 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b1e4d2c7a31'
down_revision: Union[str, Sequence[str], None] = '7a3c9e1d4b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('web_pages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('domain', sa.String(length=256), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('page_kind', sa.String(length=16), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('snapshot_id', sa.Integer(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['snapshot_id'], ['raw_snapshots.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('url')
    )
    op.create_index('ix_web_pages_domain', 'web_pages', ['domain'], unique=False)
    op.create_table('private_signals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=True),
    sa.Column('company_name', sa.String(length=256), nullable=False),
    sa.Column('domain', sa.String(length=256), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('person', sa.String(length=256), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=256), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('entered_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('snapshot_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.ForeignKeyConstraint(['snapshot_id'], ['raw_snapshots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_private_signals_company_id', 'private_signals', ['company_id'], unique=False)
    op.create_index('ix_private_signals_occurred_on', 'private_signals', ['occurred_on'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_private_signals_occurred_on', table_name='private_signals')
    op.drop_index('ix_private_signals_company_id', table_name='private_signals')
    op.drop_table('private_signals')
    op.drop_index('ix_web_pages_domain', table_name='web_pages')
    op.drop_table('web_pages')
