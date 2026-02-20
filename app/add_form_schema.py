"""Add form_schema column to inspection_templates

Run this once on your server:
    python add_form_schema.py

Or via Flask-Migrate:
    flask db migrate -m "add form_schema to inspection_templates"
    flask db upgrade
"""

# If you prefer to run this as a standalone script:
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from sqlalchemy import text

app = create_app()

with app.app_context():
    with db.engine.connect() as conn:
        # Check if column already exists
        result = conn.execute(text("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'inspection_templates'
              AND column_name = 'form_schema'
        """))
        exists = result.scalar()

        if not exists:
            conn.execute(text("""
                ALTER TABLE inspection_templates
                ADD COLUMN form_schema JSON NULL COMMENT 'JSON schema for the dynamic form builder'
            """))
            conn.commit()
            print("✓ Column 'form_schema' added to inspection_templates.")
        else:
            print("✓ Column 'form_schema' already exists — no changes made.")