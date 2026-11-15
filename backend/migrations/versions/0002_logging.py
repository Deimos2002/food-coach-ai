"""Logging: meals, meal items (frozen nutrient snapshots), day status, idempotency keys.

Revision ID: 0002_logging
Revises: 0001_initial
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0002_logging"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
DAY_STATUSES = ("not_logged", "partial", "complete")
QTY_KINDS = ("recorded", "estimated_range")
QTY_SOURCES = ("weighed", "package", "label_portion", "user_estimate", "photo_estimate")
UNITS = ("g", "ml")
MAX_AMOUNT = 5000


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _generation(table: str) -> list[sa.SchemaItem]:
    return [
        sa.Column("data_generation", sa.Integer, nullable=False),
        sa.CheckConstraint("data_generation >= 1", name=f"ck_{table}_generation"),
    ]


def upgrade() -> None:
    op.create_table(
        "meal",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("meal_type", sa.Text, nullable=False),
        sa.Column("skipped", sa.Boolean, nullable=False, server_default=sa.false()),
        *_generation("meal"),
        sa.CheckConstraint(_in("meal_type", MEAL_TYPES), name="ck_meal_type"),
    )
    op.create_index("ix_meal_day", "meal", ["day"])

    op.create_table(
        "meal_item",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column(
            "meal_id", sa.Integer, sa.ForeignKey("meal.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("requested_product_id", sa.Integer, sa.ForeignKey("product.id"), nullable=False),
        sa.Column("resolved_product_id", sa.Integer, sa.ForeignKey("product.id"), nullable=False),
        sa.Column("release_id", sa.Integer, sa.ForeignKey("catalogue_release.id"), nullable=True),
        sa.Column("profile_revision_id", sa.Integer, nullable=False),
        sa.Column("override_ids", ARRAY(sa.Integer), nullable=False, server_default="{}"),
        sa.Column("qty_kind", sa.Text, nullable=False),
        sa.Column("qty_value", sa.Numeric, nullable=False),
        sa.Column("qty_lower", sa.Numeric, nullable=False),
        sa.Column("qty_upper", sa.Numeric, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("qty_source", sa.Text, nullable=False),
        sa.Column("nutrients", JSONB, nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        *_generation("meal_item"),
        # The pinned revision must belong to the resolved product.
        sa.ForeignKeyConstraint(
            ["profile_revision_id", "resolved_product_id"],
            ["profile_revision.id", "profile_revision.product_id"],
            name="fk_meal_item_profile_revision",
        ),
        sa.CheckConstraint(_in("qty_kind", QTY_KINDS), name="ck_meal_item_qty_kind"),
        sa.CheckConstraint(_in("qty_source", QTY_SOURCES), name="ck_meal_item_qty_source"),
        sa.CheckConstraint(_in("unit", UNITS), name="ck_meal_item_unit"),
        sa.CheckConstraint(
            f"qty_lower >= 0 AND qty_lower <= qty_value AND qty_value <= qty_upper "
            f"AND qty_value > 0 AND qty_upper <= {MAX_AMOUNT}",
            name="ck_meal_item_quantity",
        ),
        sa.CheckConstraint(
            "qty_kind <> 'recorded' OR (qty_lower = qty_value AND qty_value = qty_upper)",
            name="ck_meal_item_recorded_is_point",
        ),
    )
    op.create_index("ix_meal_item_meal", "meal_item", ["meal_id"])

    op.create_table(
        "day",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_generation("day"),
        sa.CheckConstraint(_in("status", DAY_STATUSES), name="ck_day_status"),
        sa.CheckConstraint(
            "(status = 'complete') = (completed_at IS NOT NULL)", name="ck_day_completed_at"
        ),
    )

    op.create_table(
        "idempotency_key",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("response", JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_idempotency_key_created_at", "idempotency_key", ["created_at"])


def downgrade() -> None:
    for table in ("idempotency_key", "day", "meal_item", "meal"):
        op.drop_table(table)
