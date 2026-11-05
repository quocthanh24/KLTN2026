"""Durable UC37 room attendance and per-socket leases (additive)."""
from alembic import op
import sqlalchemy as sa

revision = '20261103_01'
down_revision = '20261030_02'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('study_sessions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.Uuid(), sa.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('grace_expires_at', sa.DateTime(timezone=True)),
        sa.Column('duration_us', sa.BigInteger(), nullable=False),
        sa.Column('end_reason', sa.String(40)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE','DISCONNECTED','COMPLETED','INVALID')", name='ck_study_sessions_status'),
        sa.CheckConstraint('last_activity_at >= started_at', name='ck_study_sessions_activity'),
        sa.CheckConstraint('duration_us >= 0', name='ck_study_sessions_duration'),
        sa.CheckConstraint("(status='DISCONNECTED') = (grace_expires_at IS NOT NULL)", name='ck_study_sessions_grace'),
        sa.CheckConstraint("(status IN ('ACTIVE','DISCONNECTED') AND ended_at IS NULL AND end_reason IS NULL) OR "
                           "(status='COMPLETED' AND ended_at IS NOT NULL AND end_reason IS NOT NULL AND ended_at=last_activity_at "
                           "AND ended_at>=started_at AND duration_us<=EXTRACT(EPOCH FROM (ended_at-started_at))*1000000) OR "
                           "(status='INVALID' AND ended_at IS NULL AND end_reason='INVALID_TIME' AND duration_us=0)", name='ck_study_sessions_completion'))
    op.create_index('uq_study_sessions_open_user_channel','study_sessions',['user_id','channel_id'],unique=True,
                    postgresql_where=sa.text("status IN ('ACTIVE','DISCONNECTED')"))
    op.create_index('ix_study_sessions_user_channel_started','study_sessions',['user_id','channel_id','started_at'])
    op.create_index('ix_study_sessions_recovery','study_sessions',['status','last_activity_at'])
    op.create_table('study_session_connections',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('session_id',sa.Uuid(),sa.ForeignKey('study_sessions.id',ondelete='CASCADE'),nullable=False),
        sa.Column('last_seen_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('lease_expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint('lease_expires_at > last_seen_at',name='ck_study_session_connections_lease'))
    op.create_index('ix_study_session_connections_session','study_session_connections',['session_id'])


def downgrade():
    op.drop_table('study_session_connections')
    op.drop_table('study_sessions')
