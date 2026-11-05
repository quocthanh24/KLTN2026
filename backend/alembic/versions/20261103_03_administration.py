"""UC41-45 additive moderation metadata; no initial administrator or destructive backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='20261103_03'
down_revision='20261103_02'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('users',sa.Column('admin_version',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('workspaces',sa.Column('admin_version',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('workspaces',sa.Column('disabled_at',sa.DateTime(timezone=True)))
    for table in ['chat_messages','learning_documents']:
        op.add_column(table,sa.Column('moderated_at',sa.DateTime(timezone=True)))
    op.create_table('violation_reports',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('reporter_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('target_type',sa.String(12),nullable=False),sa.Column('target_id',sa.Uuid(),nullable=False),
        sa.Column('channel_id',sa.Uuid(),nullable=False),sa.Column('workspace_id',sa.Uuid(),nullable=False),
        sa.Column('reason',sa.String(20),nullable=False),sa.Column('description',sa.Text()),
        sa.Column('status',sa.String(12),nullable=False,server_default='PENDING'),
        sa.Column('version',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('outcome',sa.String(20)),sa.Column('action',sa.String(24)),sa.Column('resolution',sa.Text()),
        sa.Column('resolved_by',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('resolved_at',sa.DateTime(timezone=True)),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("target_type IN ('MESSAGE','DOCUMENT')",name='ck_reports_target'),
        sa.CheckConstraint("reason IN ('SPAM','ABUSE','COPYRIGHT','OTHER')",name='ck_reports_reason'),
        sa.CheckConstraint("(status='PENDING' AND outcome IS NULL AND action IS NULL AND resolved_at IS NULL) OR "
            "(status='RESOLVED' AND outcome IN ('NO_VIOLATION','VIOLATION') AND action IN ('NONE','HIDE','ALREADY_UNAVAILABLE') "
            "AND resolved_at IS NOT NULL AND resolution IS NOT NULL)",name='ck_reports_resolution'))
    op.create_index('uq_reports_pending_reporter_target','violation_reports',['reporter_id','target_type','target_id'],unique=True,postgresql_where=sa.text("status='PENDING'"))
    op.create_index('ix_reports_status_created','violation_reports',['status','created_at','id'])
    op.create_table('administration_audit',
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('actor_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('target_type',sa.String(20),nullable=False),sa.Column('target_id',sa.Uuid(),nullable=False),
        sa.Column('action',sa.String(32),nullable=False),sa.Column('before',postgresql.JSONB(),nullable=False),
        sa.Column('after',postgresql.JSONB(),nullable=False),sa.Column('reason',sa.String(1000),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_index('ix_administration_audit_target','administration_audit',['target_type','target_id','created_at'])


def downgrade():
    op.drop_table('administration_audit');op.drop_table('violation_reports')
    for table in ['chat_messages','learning_documents']:op.drop_column(table,'moderated_at')
    op.drop_column('workspaces','disabled_at');op.drop_column('workspaces','admin_version');op.drop_column('users','admin_version')
