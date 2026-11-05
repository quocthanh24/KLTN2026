"""UC38-40 exact intervals; historical sessions remain explicitly untracked."""
from alembic import op
import sqlalchemy as sa

revision = '20261103_02'
down_revision = '20261103_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('study_sessions', sa.Column('analytics_tracked', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_table('study_activity_intervals',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('session_id', sa.Uuid(), sa.ForeignKey('study_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('ended_at > started_at', name='ck_study_activity_positive'),
        sa.UniqueConstraint('session_id', 'started_at', name='uq_study_activity_start'))
    op.create_table('pomodoro_focus_intervals',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('session_id', sa.Uuid(), sa.ForeignKey('pomodoro_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('interval', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('session_id', 'interval', name='uq_pomodoro_focus_cycle'),
        sa.CheckConstraint("status IN ('RUNNING','COMPLETED','ENDED')", name='ck_pomodoro_focus_status'),
        sa.CheckConstraint("(status='RUNNING' AND ended_at IS NULL) OR (status<>'RUNNING' AND ended_at>=started_at)", name='ck_pomodoro_focus_end'),
        sa.CheckConstraint('interval > 0', name='ck_pomodoro_focus_sequence'))
    op.create_table('pomodoro_focus_segments',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('focus_id', sa.Uuid(), sa.ForeignKey('pomodoro_focus_intervals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('focus_id', 'started_at', name='uq_pomodoro_focus_segment_start'),
        sa.CheckConstraint('ended_at IS NULL OR ended_at>=started_at', name='ck_pomodoro_focus_segment_time'))
    op.create_index('uq_pomodoro_focus_segment_open', 'pomodoro_focus_segments', ['focus_id'], unique=True,
                    postgresql_where=sa.text('ended_at IS NULL'))


def downgrade():
    op.drop_table('pomodoro_focus_segments')
    op.drop_table('pomodoro_focus_intervals')
    op.drop_table('study_activity_intervals')
    op.drop_column('study_sessions', 'analytics_tracked')
