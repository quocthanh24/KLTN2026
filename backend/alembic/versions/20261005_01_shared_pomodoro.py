"""UC27 durable room timer and interval history."""
from alembic import op
from app.models.pomodoro import PomodoroState, PomodoroSession, PomodoroCompletion

revision = "20261005_01"
down_revision = "20260926_02"
branch_labels = None
depends_on = None


def upgrade():
    for model in (PomodoroState, PomodoroSession, PomodoroCompletion):
        model.__table__.create(op.get_bind())


def downgrade():
    for model in (PomodoroCompletion, PomodoroSession, PomodoroState):
        model.__table__.drop(op.get_bind())
