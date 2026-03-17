"""
migrations/versions/phase7_mobile_api.py
-----------------------------------------
Phase 7 — Mobile API: JWT refresh tokens and APNs device tokens.

Revision ID : phase7_mobile_api
Revises     : phase6_features
Create Date : 2026-03-17

New tables
----------
api_refresh_tokens
    Stores server-side refresh token hashes for mobile sessions.
    Enables instant revocation by deleting the row.

api_device_tokens
    Stores APNs device tokens for push notification delivery.
    One row per (user_id, device_id) — upserted on every app launch.

All columns include safe IF NOT EXISTS / IF EXISTS guards so the
migration is idempotent and safe to re-run.
"""

from alembic import op
import sqlalchemy as sa

revision      = 'phase7_mobile_api'
down_revision = 'phase6_features'
branch_labels = None
depends_on    = None


def upgrade():
    bind      = op.get_bind()
    inspector = sa.inspect(bind)
    tables    = inspector.get_table_names()

    # ── 1. api_refresh_tokens ─────────────────────────────────────────────
    if 'api_refresh_tokens' not in tables:
        op.create_table(
            'api_refresh_tokens',
            sa.Column('id',          sa.Integer(),     primary_key=True),
            sa.Column('user_id',     sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='CASCADE'),
                      nullable=False),
            sa.Column('token_hash',  sa.String(64),    nullable=False, unique=True),
            sa.Column('device_id',   sa.String(64),    nullable=True),
            sa.Column('device_name', sa.String(100),   nullable=True),
            sa.Column('created_at',  sa.DateTime(),    nullable=False),
            sa.Column('expires_at',  sa.DateTime(),    nullable=False),
            sa.Column('revoked',     sa.Boolean(),     nullable=False,
                      server_default='0'),
        )
        op.create_index('ix_api_refresh_tokens_user_id',
                        'api_refresh_tokens', ['user_id'])
        op.create_index('ix_api_refresh_tokens_token_hash',
                        'api_refresh_tokens', ['token_hash'], unique=True)

    # ── 2. api_device_tokens ──────────────────────────────────────────────
    if 'api_device_tokens' not in tables:
        op.create_table(
            'api_device_tokens',
            sa.Column('id',            sa.Integer(),   primary_key=True),
            sa.Column('user_id',       sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='CASCADE'),
                      nullable=False),
            sa.Column('device_id',     sa.String(64),  nullable=False),
            sa.Column('apns_token',    sa.String(200), nullable=False),
            sa.Column('device_name',   sa.String(100), nullable=True),
            sa.Column('app_version',   sa.String(20),  nullable=True),
            sa.Column('registered_at', sa.DateTime(),  nullable=False),
        )
        op.create_index('ix_api_device_tokens_user_id',
                        'api_device_tokens', ['user_id'])
        op.create_unique_constraint(
            'uq_device_token_user_device',
            'api_device_tokens',
            ['user_id', 'device_id'],
        )


def downgrade():
    bind      = op.get_bind()
    inspector = sa.inspect(bind)
    tables    = inspector.get_table_names()

    if 'api_device_tokens' in tables:
        op.drop_table('api_device_tokens')

    if 'api_refresh_tokens' in tables:
        op.drop_table('api_refresh_tokens')
