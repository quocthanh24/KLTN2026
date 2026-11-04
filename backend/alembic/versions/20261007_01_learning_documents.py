"""UC29 upload metadata and durable processing boundary."""
from alembic import op
import sqlalchemy as sa

revision = "20261007_01"
down_revision = "20261005_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("learning_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploader_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False, unique=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.Enum("PROCESSING", "READY", "FAILED", native_enum=False, create_constraint=True, name="ck_learning_document_status"), nullable=False),
        sa.Column("processing_error", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_learning_documents_channel_id", "learning_documents", ["channel_id"])


def downgrade():
    op.drop_table("learning_documents")
