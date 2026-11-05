"""Private channel AI conversation history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = '20261014_02'
down_revision = '20261014_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_conversations',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('channel_id',sa.Uuid(),sa.ForeignKey('channels.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('title',sa.String(120),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_ai_conversations_channel_id','ai_conversations',['channel_id'])
    op.create_index('ix_ai_conversations_user_id','ai_conversations',['user_id'])
    op.create_table('ai_turns',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('conversation_id',sa.Uuid(),sa.ForeignKey('ai_conversations.id',ondelete='CASCADE'),nullable=False),
        sa.Column('request_id',sa.Uuid(),nullable=False),sa.Column('question',sa.Text(),nullable=False),
        sa.Column('answer',sa.Text()),sa.Column('status',sa.String(30),nullable=False),
        sa.Column('processing_token',sa.Uuid()),
        sa.Column('sources',JSONB(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('conversation_id','request_id',name='uq_ai_turn_request'))
    op.create_index('ix_ai_turns_conversation_id','ai_turns',['conversation_id'])


def downgrade():
    op.drop_table('ai_turns')
    op.drop_table('ai_conversations')
