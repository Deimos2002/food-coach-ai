"""Weights, profile, saved meals and explicit aliases (Task 14).

Revision ID: 0004_body_saved
Revises: 0003_search
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_body_saved"
down_revision: str | None = "0003_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weight_entry",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("kg", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("data_generation", sa.Integer, nullable=False),
    )

    op.create_table(
        "profile",
        sa.Column("singleton", sa.Boolean, primary_key=True, server_default=sa.true()),
        sa.Column("sex", sa.Text, nullable=False),
        sa.Column("birth_year", sa.Integer, nullable=False),
        sa.Column("height_cm", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("activity", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("pace_pct_per_week", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("data_generation", sa.Integer, nullable=False),
        sa.CheckConstraint("singleton", name="ck_profile_singleton"),
    )

    op.create_table(
        "saved_meal",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("data_generation", sa.Integer, nullable=False),
    )

    op.create_table(
        "saved_meal_item",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column(
            "saved_meal_id",
            sa.Integer,
            sa.ForeignKey("saved_meal.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.Integer, nullable=False),
        sa.Column("amount", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_saved_meal_item_saved_meal_id", "saved_meal_item", ["saved_meal_id"]
    )

    op.create_table(
        "alias",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("product_id", sa.Integer, nullable=False),
        sa.Column("data_generation", sa.Integer, nullable=False),
    )
    op.create_index("ix_alias_product_id", "alias", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_alias_product_id", table_name="alias")
    op.drop_table("alias")
    op.drop_index("ix_saved_meal_item_saved_meal_id", table_name="saved_meal_item")
    op.drop_table("saved_meal_item")
    op.drop_table("saved_meal")
    op.drop_table("profile")
    op.drop_table("weight_entry")
