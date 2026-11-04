"""Add private V2 resumable checkpoints; legacy tables are unchanged."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '20261030_01'
down_revision = '20261014_02'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_chat_v2_jobs',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('turn_id',sa.Uuid(),sa.ForeignKey('ai_turns.id',ondelete='CASCADE'),nullable=False,unique=True),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('channel_id',sa.Uuid(),sa.ForeignKey('channels.id',ondelete='CASCADE'),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),sa.Column('stage',sa.String(30),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),sa.Column('lease_token',sa.Uuid()),
        sa.Column('lease_until',sa.DateTime(timezone=True)),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('checkpoint',JSONB(),nullable=False),sa.Column('error_code',sa.String(80)),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_ai_chat_v2_jobs_user_id','ai_chat_v2_jobs',['user_id'])
    op.create_index('ix_ai_chat_v2_jobs_channel_id','ai_chat_v2_jobs',['channel_id'])


def downgrade():
    # Operator-only migration rollback, never invoked by runtime/flag rollback.
    op.drop_table('ai_chat_v2_jobs')
