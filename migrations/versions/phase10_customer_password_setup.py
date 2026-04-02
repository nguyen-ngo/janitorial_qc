"""Phase 10: Customer password-setup workflow

Adds three columns to the users table to support the
invitation-based customer account creation flow:

  password_set                — False until the customer completes set-password
  set_password_token          — one-time URL token (64-char hex, nullable)
  set_password_token_expires  — UTC expiry datetime (nullable)

Revision ID: phase10_customer_password_setup
Revises: phase9_user_full_name
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision      = 'phase10_customer_password_setup'
down_revision = 'phase9_user_full_name'
branch_labels = None
depends_on    = None


def upgrade():
    bind      = op.get_bind()
    inspector = sa.inspect(bind)
    columns   = [c['name'] for c in inspector.get_columns('users')]

    if 'password_set' not in columns:
        op.add_column('users',
            sa.Column('password_set', sa.Boolean, nullable=False, server_default='1'))

    if 'set_password_token' not in columns:
        op.add_column('users',
            sa.Column('set_password_token', sa.String(64), nullable=True))

    if 'set_password_token_expires' not in columns:
        op.add_column('users',
            sa.Column('set_password_token_expires', sa.DateTime, nullable=True))


def downgrade():
    op.drop_column('users', 'set_password_token_expires')
    op.drop_column('users', 'set_password_token')
    op.drop_column('users', 'password_set')
