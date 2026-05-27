"""user_profiles: replace user_roles with rich profile table

Revision ID: c3a9f1d2e456
Revises: b80abde96bbd
Create Date: 2026-05-26 12:00:00.000000

What this migration does:
  1. Creates user_profiles table with the full profile schema
  2. Migrates existing rows from user_roles → user_profiles
  3. Drops user_roles

Domain enforcement and auto-provisioning are handled in auth.py at runtime.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'c3a9f1d2e456'
down_revision = 'b80abde96bbd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Create user_profiles ──────────────────────────────────────────────
    op.create_table(
        'user_profiles',

        # Primary key is the Supabase auth.users UUID (string)
        sa.Column('id', sa.String(), nullable=False),

        # Core identity
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('full_name', sa.String(), nullable=True),

        # Role within the casting platform
        sa.Column('role', sa.String(), nullable=False, server_default='viewer'),

        # Org structure
        sa.Column('team', sa.String(), nullable=True),
        sa.Column('sub_team', sa.String(), nullable=True),
        sa.Column('designation', sa.String(), nullable=True),
        sa.Column('location', sa.String(), nullable=True),
        sa.Column('org_id', sa.String(), nullable=True),      # future FK to orgs

        # Account status
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),

        # HR fields
        sa.Column('employee_id', sa.String(), nullable=True),
        sa.Column('mobile', sa.String(), nullable=True),

        # Manager hierarchy (self-referential)
        sa.Column('manager_id', sa.String(), nullable=True),
        sa.Column('manager_email', sa.String(), nullable=True),
        sa.Column('manager_name', sa.String(), nullable=True),

        # Timestamps
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),

        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='user_profiles_email_key'),
        sa.ForeignKeyConstraint(
            ['manager_id'], ['user_profiles.id'],
            name='user_profiles_manager_id_fkey',
            ondelete='SET NULL',
        ),
        sa.CheckConstraint(
            "role = ANY(ARRAY['admin','casting_manager','approver','viewer'])",
            name='user_profiles_role_check',
        ),
    )
    op.create_index('ix_user_profiles_email', 'user_profiles', ['email'])
    op.create_index('ix_user_profiles_id', 'user_profiles', ['id'])

    # ── 2. Migrate existing user_roles rows ──────────────────────────────────
    # user_roles has: id (int PK), user_id (str), email, role, created_at, updated_at
    # We map user_id → id in user_profiles
    op.execute("""
        INSERT INTO user_profiles (id, email, role, created_at, updated_at)
        SELECT
            user_id,
            COALESCE(email, user_id || '@unknown.invalid'),
            CASE
                WHEN role IN ('admin','casting_manager','approver','viewer') THEN role
                ELSE 'viewer'
            END,
            COALESCE(created_at, now()),
            COALESCE(updated_at, now())
        FROM user_roles
        ON CONFLICT (id) DO NOTHING
    """)

    # ── 3. Drop user_roles ───────────────────────────────────────────────────
    op.drop_table('user_roles')


def downgrade() -> None:
    # Recreate user_roles
    op.create_table(
        'user_roles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('ix_user_roles_user_id', 'user_roles', ['user_id'])

    # Migrate back
    op.execute("""
        INSERT INTO user_roles (user_id, email, role, created_at, updated_at)
        SELECT id, email, role, created_at, updated_at
        FROM user_profiles
        ON CONFLICT DO NOTHING
    """)

    op.drop_table('user_profiles')
