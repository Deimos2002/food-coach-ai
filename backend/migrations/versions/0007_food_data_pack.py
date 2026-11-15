"""English product names, meal templates and a per-item dish label (food data pack, Task 2).

New tables inherit foodcoach_app privileges from the default privileges set in 0006, so no
grants here. Templates are catalogue data (not user rows), so privacy deletion keeps them.

Revision ID: 0007_food_data_pack
Revises: 0006_app_role
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_food_data_pack"
down_revision: str | None = "0006_app_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product", sa.Column("name_en", sa.Text, nullable=True))
    op.add_column("meal_item", sa.Column("dish", sa.Text, nullable=True))
    op.create_table(
        "meal_template",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("name_en", sa.Text, nullable=True),
        sa.Column("cuisine", sa.Text, nullable=True),
        sa.Column("chain", sa.Text, nullable=True),
        sa.Column("estimate", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("source_url", sa.Text, nullable=True),
    )
    op.create_table(
        "meal_template_item",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Integer,
            sa.ForeignKey("meal_template.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("product.id"), nullable=False),
        sa.Column("grams", sa.Numeric, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.CheckConstraint("grams > 0", name="ck_meal_template_item_grams"),
    )
    op.create_index("ix_meal_template_item_template_id", "meal_template_item", ["template_id"])


def downgrade() -> None:
    op.drop_table("meal_template_item")
    op.drop_table("meal_template")
    op.drop_column("meal_item", "dish")
    op.drop_column("product", "name_en")
