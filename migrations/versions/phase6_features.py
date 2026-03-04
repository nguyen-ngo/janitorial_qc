"""Phase 6: Scheduled reports, re-inspection workflow, issue verification

Revision ID: phase6_features
Revises: phase1_projects_roles
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision      = 'phase6_features'
down_revision = 'phase1_projects_roles'   # <-- set to your current DB head
branch_labels = None
depends_on    = None


def upgrade():
    bind      = op.get_bind()
    inspector = sa.inspect(bind)
    tables    = inspector.get_table_names()

    # ── 1. scheduled_reports ─────────────────────────────────────────────────
    if 'scheduled_reports' not in tables:
        op.create_table(
            'scheduled_reports',
            sa.Column('id',            sa.Integer(),     primary_key=True),
            sa.Column('name',          sa.String(255),   nullable=False),
            sa.Column('report_type',   sa.Enum('summary', 'facility', 'issues'),
                                       nullable=False, server_default='summary'),
            sa.Column('frequency',     sa.Enum('daily', 'weekly', 'monthly'),
                                       nullable=False),
            sa.Column('facility_id',   sa.Integer(),
                                       sa.ForeignKey('facilities.id', ondelete='SET NULL'),
                                       nullable=True),
            sa.Column('recipients',    sa.JSON(),        nullable=False),   # list of email strings
            sa.Column('include_pdf',   sa.Boolean(),     nullable=False, server_default='0'),
            sa.Column('include_csv',   sa.Boolean(),     nullable=False, server_default='0'),
            sa.Column('active',        sa.Boolean(),     nullable=False, server_default='1'),
            sa.Column('created_by',    sa.Integer(),
                                       sa.ForeignKey('users.id', ondelete='SET NULL'),
                                       nullable=True),
            sa.Column('created_at',    sa.DateTime(),    nullable=False),
            sa.Column('last_sent_at',  sa.DateTime(),    nullable=True),
            sa.Column('next_send_at',  sa.DateTime(),    nullable=True),
        )

    # ── 2. inspections: parent_inspection_id + follow_up columns ────────────
    insp_cols = {c['name'] for c in inspector.get_columns('inspections')}

    if 'parent_inspection_id' not in insp_cols:
        op.add_column('inspections',
            sa.Column('parent_inspection_id', sa.Integer(),
                      sa.ForeignKey('inspections.id', ondelete='SET NULL'),
                      nullable=True))

    if 'follow_up_required' not in insp_cols:
        op.add_column('inspections',
            sa.Column('follow_up_required', sa.Boolean(),
                      nullable=False, server_default='0'))

    if 'follow_up_note' not in insp_cols:
        op.add_column('inspections',
            sa.Column('follow_up_note', sa.Text(), nullable=True))

    # ── 3. issues: verification columns + extend status enum ────────────────
    issue_cols = {c['name'] for c in inspector.get_columns('issues')}

    if 'verified_by' not in issue_cols:
        op.add_column('issues',
            sa.Column('verified_by', sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='SET NULL'),
                      nullable=True))

    if 'verified_at' not in issue_cols:
        op.add_column('issues',
            sa.Column('verified_at', sa.DateTime(), nullable=True))

    if 'verification_note' not in issue_cols:
        op.add_column('issues',
            sa.Column('verification_note', sa.Text(), nullable=True))

    # Extend the status ENUM to include 'pending_verification'
    # MySQL requires modifying the column definition directly
    op.execute(
        "ALTER TABLE issues MODIFY COLUMN status "
        "ENUM('open','in_progress','resolved','pending_verification') "
        "NOT NULL DEFAULT 'open'"
    )


def downgrade():
    # Revert status enum
    op.execute(
        "ALTER TABLE issues MODIFY COLUMN status "
        "ENUM('open','in_progress','resolved') "
        "NOT NULL DEFAULT 'open'"
    )

    issue_cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('issues')}
    for col in ('verified_by', 'verified_at', 'verification_note'):
        if col in issue_cols:
            op.drop_column('issues', col)

    insp_cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('inspections')}
    for col in ('parent_inspection_id', 'follow_up_required', 'follow_up_note'):
        if col in insp_cols:
            op.drop_column('inspections', col)

    op.drop_table('scheduled_reports')
