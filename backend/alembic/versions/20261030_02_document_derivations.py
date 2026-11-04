"""Add independent original-first per-unit derived progress. Opt-in runtime only.

Revision ID: 20261030_02
Revises: 20261030_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='20261030_02'
down_revision='20261030_01'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('document_derivations',
        sa.Column('document_id',sa.Uuid(),sa.ForeignKey('learning_documents.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('revision',sa.String(64),nullable=False),
        sa.Column('state',postgresql.JSONB(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))


def downgrade():
    # Operator must export progress before a separately approved destructive downgrade.
    op.drop_table('document_derivations')
