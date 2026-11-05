"""Administration integrity: reject null decision loopholes and invalid versions."""
from alembic import op
revision='20261104_01'
down_revision='20261103_03'
branch_labels=None
depends_on=None


CHECKS=[
 ('users','ck_users_admin_version','admin_version >= 1'),
 ('workspaces','ck_workspaces_admin_version','admin_version >= 1'),
 ('violation_reports','ck_reports_version','version >= 1'),
 ('violation_reports','ck_reports_outcome_action',"(status='PENDING' AND resolution IS NULL AND resolved_by IS NULL) OR (status='RESOLVED' AND outcome IS NOT NULL AND action IS NOT NULL AND ((outcome='NO_VIOLATION' AND action='NONE') OR (outcome='VIOLATION' AND action IN ('HIDE','ALREADY_UNAVAILABLE'))))"),
 ('violation_reports','ck_reports_resolved_time','resolved_at IS NULL OR resolved_at >= created_at')]


def upgrade():
    for table,name,condition in CHECKS:op.create_check_constraint(name,table,condition)


def downgrade():
    for table,name,_ in reversed(CHECKS):op.drop_constraint(name,table,type_='check')
