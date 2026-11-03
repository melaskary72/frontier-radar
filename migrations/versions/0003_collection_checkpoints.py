"""collection checkpoints for resumable batch collection

Revision ID: 7a3c9e1d4b20
Revises: cfce515e50bd
Create Date: 2026-11-02 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a3c9e1d4b20'
down_revision: Union[str, Sequence[str], None] = 'cfce515e50bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('collection_checkpoints',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=512), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('result_json', sa.JSON(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('collection_checkpoints')
