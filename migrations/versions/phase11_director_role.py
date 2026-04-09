"""Phase 11: Rename supervisor role to director

Revision ID: phase11_director_role
Revises: phase10_customer_password_setup
Create Date: 2026-04-09

Changes
-------
1. Adds 'director' to the users.role ENUM.
2. Migrates all existing role='supervisor' users to role='director'.
3. Removes 'supervisor' from the ENUM once no rows use it.
4. Migrates notification_matrix rows keyed role_key='supervisor' → 'director'.
"""
from alembic import op
import sqlalchemy as sa

revision      = 'phase11_director_role'
down_revision = 'phase10_customer_password_setup'
branch_labels = None
depends_on    = None


def upgrade():
    # Step 1 — Expand ENUM to include both values (required before UPDATE)
    op.execute(
        "ALTER TABLE users MODIFY COLUMN role "
        "ENUM('admin','supervisor','director','inspector','project_manager','customer') "
        "NOT NULL"
    )

    # Step 2 — Migrate all supervisor users to director
    op.execute("UPDATE users SET role = 'director' WHERE role = 'supervisor'")

    # Step 3 — Remove 'supervisor' from the ENUM now that no rows reference it
    op.execute(
        "ALTER TABLE users MODIFY COLUMN role "
        "ENUM('admin','director','inspector','project_manager','customer') "
        "NOT NULL"
    )

    # Step 4 — Migrate notification_matrix role_key rows
    op.execute(
        "UPDATE notification_matrix SET role_key = 'director' WHERE role_key = 'supervisor'"
    )


def downgrade():
    # Step 1 — Expand ENUM to allow supervisor again
    op.execute(
        "ALTER TABLE users MODIFY COLUMN role "
        "ENUM('admin','supervisor','director','inspector','project_manager','customer') "
        "NOT NULL"
    )

    # Step 2 — Revert director users back to supervisor
    op.execute("UPDATE users SET role = 'supervisor' WHERE role = 'director'")

    # Step 3 — Remove 'director' from the ENUM
    op.execute(
        "ALTER TABLE users MODIFY COLUMN role "
        "ENUM('admin','supervisor','inspector','project_manager','customer') "
        "NOT NULL"
    )

    # Step 4 — Revert notification_matrix role_key rows
    op.execute(
        "UPDATE notification_matrix SET role_key = 'supervisor' WHERE role_key = 'director'"
    )
