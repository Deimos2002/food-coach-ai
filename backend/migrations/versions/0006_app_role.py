"""Non-admin application role foodcoach_app (deployment Task 3, spec §3.6).

The role is created NOLOGIN without a password; the runbook enables login with a
password held in Key Vault. Roles are cluster-wide, so creation tolerates an existing
role and the downgrade only drops it when no other database still references it.
Nothing here needs superuser (Azure Flexible Server's admin is not one).

Revision ID: 0006_app_role
Revises: 0005_coach
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.app_role import APP_ROLE, app_role_statements

revision: str = "0006_app_role"
down_revision: str | None = "0005_coach"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = APP_ROLE
_TABLE_PRIVS = "SELECT, INSERT, UPDATE, DELETE"
_SEQ_PRIVS = "USAGE, SELECT"


def upgrade() -> None:
    # Tables and sequences created later by the migrating (admin) role get default
    # privileges bound to current_user, so every migration must run as the same admin login
    # (database-url-admin); another identity would create tables the app cannot use.
    for statement in app_role_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE {_SEQ_PRIVS} ON SEQUENCES FROM {ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE {_TABLE_PRIVS} ON TABLES FROM {ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {ROLE}")
    # CREATE on public stays revoked from PUBLIC (the PG15+ default).
    op.execute(
        f"""
        DO $$
        BEGIN
            DROP ROLE IF EXISTS {ROLE};
        EXCEPTION WHEN dependent_objects_still_exist THEN
            NULL;  -- still granted in another database on this server; keep it
        END
        $$
        """
    )
