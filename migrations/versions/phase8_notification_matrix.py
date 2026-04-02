"""Phase 8: Notification matrix — admin-controlled per-event recipient settings

Revision ID: phase8_notification_matrix
Revises: phase7_mobile_api
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision      = 'phase8_notification_matrix'
down_revision = 'phase7_mobile_api'
branch_labels = None
depends_on    = None


def upgrade():
    bind      = op.get_bind()
    inspector = sa.inspect(bind)
    tables    = inspector.get_table_names()

    if 'notification_matrix' not in tables:
        op.create_table(
            'notification_matrix',
            sa.Column('id',           sa.Integer,     primary_key=True),
            sa.Column('event_type',   sa.String(50),  nullable=False),
            sa.Column('role_key',     sa.String(30),  nullable=False),
            # enabled: whether this role receives notifications for this event
            sa.Column('enabled',      sa.Boolean,     nullable=False, server_default='1'),
            # custom_emails: JSON list of extra email addresses (role_key='custom')
            sa.Column('custom_emails', sa.Text,       nullable=True),
            sa.UniqueConstraint('event_type', 'role_key',
                                name='uq_notif_matrix_event_role'),
        )


def downgrade():
    op.drop_table('notification_matrix')
