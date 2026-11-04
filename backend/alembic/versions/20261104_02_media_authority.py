"""Add durable media authorization and transactional revocation outbox."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='20261104_02'
down_revision='20261104_01'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('media_grants',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('auth_session_id',sa.Uuid(),sa.ForeignKey('auth_sessions.id',ondelete='CASCADE'),nullable=False),
        sa.Column('workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='CASCADE'),nullable=False),
        sa.Column('channel_id',sa.Uuid(),sa.ForeignKey('channels.id',ondelete='CASCADE'),nullable=False),
        sa.Column('membership_id',sa.Uuid(),nullable=False),
        sa.Column('user_version',sa.BigInteger(),nullable=False),sa.Column('workspace_version',sa.BigInteger(),nullable=False),
        sa.Column('issued_at',sa.DateTime(timezone=True),server_default=sa.text('clock_timestamp()'),nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('revoked_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint('expires_at > issued_at',name='ck_media_grant_expiry'),
        sa.CheckConstraint('user_version > 0 AND workspace_version > 0',name='ck_media_grant_versions'))
    op.create_index('ix_media_grant_user_session','media_grants',['user_id','auth_session_id'])
    op.create_index('ix_media_grant_workspace_channel','media_grants',['workspace_id','channel_id'])
    op.create_index('ix_media_grant_session','media_grants',['auth_session_id'])
    op.create_index('ix_media_grant_channel','media_grants',['channel_id'])
    op.create_table('media_revocations',
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('scope',sa.String(20),nullable=False),sa.Column('scope_id',sa.Uuid(),nullable=False),
        sa.Column('actor_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL')),sa.Column('grant_ids',JSONB(),nullable=False),
        sa.Column('state',sa.String(12),nullable=False,server_default='PENDING'),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.text('clock_timestamp()')),
        sa.Column('origin_xid',sa.BigInteger(),nullable=False,server_default=sa.text('txid_current()')),
        sa.Column('dispatch_at',sa.DateTime(timezone=True)),sa.Column('next_attempt_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.text('clock_timestamp()')),
        sa.Column('attempts',sa.Integer(),nullable=False,server_default='0'),sa.Column('completed_at',sa.DateTime(timezone=True)),
        sa.Column('ack',JSONB()),sa.Column('error_code',sa.String(64)),
        sa.CheckConstraint("state IN ('PENDING','COMPLETE','ERROR')",name='ck_media_revocation_state'),
        sa.CheckConstraint("scope IN ('USER','WORKSPACE','SESSION','CHANNEL','MEMBERSHIP')",name='ck_media_revocation_scope'),
        sa.CheckConstraint("jsonb_typeof(grant_ids)='array'",name='ck_media_revocation_grants'),
        sa.CheckConstraint('attempts >= 0',name='ck_media_revocation_attempts'),
        sa.CheckConstraint("(state='COMPLETE' AND ack IS NOT NULL AND completed_at IS NOT NULL AND COALESCE(ack->>'operation'=id::text AND ack->>'barrier'='transport-closed-v1' AND ack->>'pending'='false' AND length(ack->>'boot_id')=32,false)) OR (state<>'COMPLETE' AND completed_at IS NULL AND ack IS NULL)",name='ck_media_revocation_ack'))
    op.create_index('ix_media_outbox_due','media_revocations',['state','next_attempt_at'])
    op.create_index('ix_media_revocation_actor','media_revocations',['actor_id'])

def downgrade():
    # Explicitly opt-in Alembic downgrade only; not an operational rollback plan.
    op.drop_table('media_revocations')
    op.drop_table('media_grants')
