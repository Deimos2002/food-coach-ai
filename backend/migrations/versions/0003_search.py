"""Search: trigram index on product.search_text (Task 13).

Revision ID: 0003_search
Revises: 0002_logging
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_search"
down_revision: str | None = "0002_logging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_product_search_text_trgm ON product "
        "USING gin (search_text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_product_search_text_trgm")
