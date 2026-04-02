"""Phase 9: Add full_name column to users table

Revision ID: phase9_user_full_name
Revises: phase8_notification_matrix
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision      = 'phase9_user_full_name'
down_revision = 'phase8_notification_matrix'
branch_labels = None
depends_on    = None


def upgrade():
    bind      = op.get_bind()
    inspector = sa.inspect(bind)
    columns   = [c['name'] for c in inspector.get_columns('users')]

    if 'full_name' not in columns:
        op.add_column(
            'users',
            sa.Column('full_name', sa.String(150), nullable=True, default=None),
        )


def downgrade():
    op.drop_column('users', 'full_name')
