"""UC29 leases and atomically published source-located vectors."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = "20261013_01"
down_revision = "20261007_01"
branch_labels = None
depends_on = None


def upgrade():
    # Extension is provisioned by the administrator, never by the application role.
    op.add_column("learning_documents", sa.Column("processing_token", sa.Uuid(), nullable=True))
    op.add_column("learning_documents", sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table("document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("learning_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_location", JSONB(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("model_revision", sa.String(64), nullable=False),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_document_chunk_ordinal"))
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])


def downgrade():
    op.drop_table("document_chunks")
    op.drop_column("learning_documents", "processing_lease_until")
    op.drop_column("learning_documents", "processing_token")
