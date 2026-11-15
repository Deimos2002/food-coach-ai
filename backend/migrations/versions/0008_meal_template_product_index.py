"""Index meal-template ingredients by product for catalogue lookups.

Revision ID: 0008_meal_template_product_index
Revises: 0007_food_data_pack
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_meal_template_product_index"
down_revision: str | None = "0007_food_data_pack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_meal_template_item_product_id", "meal_template_item", ["product_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_meal_template_item_product_id", table_name="meal_template_item")
