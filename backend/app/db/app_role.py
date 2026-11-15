"""The non-admin application role ``foodcoach_app`` and its privileges (spec §3.6, R13).

Shared by migration 0006 and ``scripts/restore.py``: restores run ``pg_restore --no-acl``
and then re-apply these statements, so a dump never needs the source server's roles.
The role is created NOLOGIN; no statement here ever sets a password. Every statement is
idempotent. Default privileges bind to ``current_user``, so these must run as the same
admin login that runs migrations.
"""

from __future__ import annotations

APP_ROLE = "foodcoach_app"
_TABLE_PRIVS = "SELECT, INSERT, UPDATE, DELETE"
_SEQ_PRIVS = "USAGE, SELECT"


def app_role_statements() -> list[str]:
    return [
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        EXCEPTION WHEN duplicate_object OR unique_violation THEN
            NULL;  -- created concurrently by another database's migration
        END
        $$
        """,
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT {_TABLE_PRIVS} ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT {_SEQ_PRIVS} ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        # Only migrations write the schema version.
        f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {_TABLE_PRIVS} ON TABLES TO {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {_SEQ_PRIVS} ON SEQUENCES TO {APP_ROLE}",
    ]
