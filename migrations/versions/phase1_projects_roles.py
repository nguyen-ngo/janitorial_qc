"""Phase 1: Add projects table, customer_assignments table, project_id on facilities, extend user role enum

Revision ID: phase1_projects_roles
Revises: (set this to your current DB head before running)
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# --- IMPORTANT: set down_revision to your live DB's current head ---
revision = 'phase1_projects_roles'
down_revision = '0003_add_user_active'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── 1. Create `projects` table ─────────────────────────────────────────
    if 'projects' not in existing_tables:
        op.create_table(
            'projects',
            sa.Column('id',                 sa.Integer(),      nullable=False),
            sa.Column('name',               sa.String(255),    nullable=False),
            sa.Column('description',        sa.Text(),         nullable=True),
            sa.Column('project_manager_id', sa.Integer(),      nullable=True),
            sa.Column('active',             sa.Boolean(),      nullable=False, server_default='1'),
            sa.Column('created_at',         sa.DateTime(),     nullable=False),
            sa.ForeignKeyConstraint(['project_manager_id'], ['users.id'], name='fk_project_manager'),
            sa.PrimaryKeyConstraint('id'),
        )

    # ── 2. Create `customer_assignments` table ─────────────────────────────
    if 'customer_assignments' not in existing_tables:
        op.create_table(
            'customer_assignments',
            sa.Column('id',          sa.Integer(),  nullable=False),
            sa.Column('user_id',     sa.Integer(),  nullable=False),
            sa.Column('project_id',  sa.Integer(),  nullable=False),
            sa.Column('facility_id', sa.Integer(),  nullable=True),
            sa.Column('created_at',  sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['facility_id'], ['facilities.id'],
                                    name='fk_ca_facility', ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['project_id'],  ['projects.id'],
                                    name='fk_ca_project',  ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'],     ['users.id'],
                                    name='fk_ca_user',     ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'project_id', 'facility_id',
                                name='uq_customer_assignment'),
        )
        op.create_index('ix_customer_assignments_user_id',
                        'customer_assignments', ['user_id'])
        op.create_index('ix_customer_assignments_project_id',
                        'customer_assignments', ['project_id'])
        op.create_index('ix_customer_assignments_facility_id',
                        'customer_assignments', ['facility_id'])

    # ── 3. Add `project_id` column to `facilities` ─────────────────────────
    existing_facility_cols = [c['name'] for c in inspector.get_columns('facilities')]
    if 'project_id' not in existing_facility_cols:
        op.add_column(
            'facilities',
            sa.Column('project_id', sa.Integer(), nullable=True)
        )
        op.create_foreign_key(
            'fk_facility_project',
            'facilities', 'projects',
            ['project_id'], ['id'],
            ondelete='SET NULL'
        )
        op.create_index('ix_facilities_project_id', 'facilities', ['project_id'])

    # ── 4. Extend `users.role` Enum with new values ────────────────────────
    # MySQL requires ALTER COLUMN to modify an ENUM.
    op.alter_column(
        'users', 'role',
        existing_type=mysql.ENUM('admin', 'supervisor', 'inspector'),
        type_=mysql.ENUM('admin', 'supervisor', 'inspector', 'project_manager', 'customer'),
        existing_nullable=False,
        nullable=False,
    )


def downgrade():
    # ── Reverse order of operations ────────────────────────────────────────

    # 4. Revert users.role Enum
    op.alter_column(
        'users', 'role',
        existing_type=mysql.ENUM('admin', 'supervisor', 'inspector', 'project_manager', 'customer'),
        type_=mysql.ENUM('admin', 'supervisor', 'inspector'),
        existing_nullable=False,
        nullable=False,
    )

    # 3. Remove project_id from facilities
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_facility_cols = [c['name'] for c in inspector.get_columns('facilities')]
    if 'project_id' in existing_facility_cols:
        op.drop_constraint('fk_facility_project', 'facilities', type_='foreignkey')
        op.drop_index('ix_facilities_project_id', table_name='facilities')
        op.drop_column('facilities', 'project_id')

    # 2. Drop customer_assignments
    existing_tables = inspector.get_table_names()
    if 'customer_assignments' in existing_tables:
        op.drop_table('customer_assignments')

    # 1. Drop projects
    if 'projects' in existing_tables:
        op.drop_table('projects')
