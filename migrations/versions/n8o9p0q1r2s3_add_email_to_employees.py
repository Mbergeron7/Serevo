"""Add email column to employees

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'n8o9p0q1r2s3'
down_revision = 'm7n8o9p0q1r2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('employees', sa.Column('email', sa.String(255), nullable=True))
    op.create_index('ix_employees_email', 'employees', ['email'])


def downgrade():
    op.drop_index('ix_employees_email', table_name='employees')
    op.drop_column('employees', 'email')
