"""UC32 durable revocation and retryable original cleanup."""
from alembic import op
import sqlalchemy as sa

revision = "20261014_01"
down_revision = "20261013_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("learning_documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("learning_documents", sa.Column("object_deleted_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("learning_documents", "object_deleted_at")
    op.drop_column("learning_documents", "deleted_at")
