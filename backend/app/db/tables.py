"""SQLAlchemy Core table definitions for the stage-1 schema (migration ``0001_initial``).

The migration is the source of truth for DDL; these definitions mirror it for queries.
Every quantity/nutrient column is NUMERIC (Decimal), never float. Unknown values have
NULL ``lower``/``upper``; a known zero is ``lower = upper = 0``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

metadata = sa.MetaData()

_ID = sa.Integer


def _value_columns() -> list[sa.Column]:
    """Nutrient value columns shared by ``nutrient_value`` and ``nutrient_override``."""
    return [
        sa.Column("known", sa.Boolean, nullable=False),
        sa.Column("lower", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("upper", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("qualifier", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("derivation", sa.Text, nullable=False),
        sa.Column("verification", sa.Text, nullable=False),
    ]


user_account = sa.Table(
    "user_account",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column("entra_oid", sa.Text, nullable=False, unique=True),
    sa.Column("data_generation", sa.Integer, nullable=False, server_default="1"),
)

product = sa.Table(
    "product",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("brand", sa.Text, nullable=True),
    sa.Column("basis_unit", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("density_g_per_ml", sa.Numeric(asdecimal=True), nullable=True),
    sa.Column("density_source", sa.Text, nullable=True),
    sa.Column("includes_cooking_additions", sa.Text, nullable=True),
    sa.Column("search_text", sa.Text, nullable=False),
    sa.Column("barcode", sa.Text, nullable=True),
    sa.Column("data_generation", sa.Integer, nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

# Migration 0007 objects live on their own MetaData: ``metadata`` above mirrors 0001 exactly
# (tests/db/test_schema_unit.py pins it). ``product.name_en`` is added by 0007 and queried
# by name (``sa.column("name_en")``), not through ``product``.
food_data_metadata = sa.MetaData()

meal_template = sa.Table(
    "meal_template",
    food_data_metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column("name", sa.Text, nullable=False, unique=True),
    sa.Column("name_en", sa.Text, nullable=True),
    sa.Column("cuisine", sa.Text, nullable=True),
    sa.Column("chain", sa.Text, nullable=True),
    sa.Column("estimate", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("source_url", sa.Text, nullable=True),
)

meal_template_item = sa.Table(
    "meal_template_item",
    food_data_metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column(
        "template_id",
        _ID,
        sa.ForeignKey("meal_template.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("product_id", _ID, nullable=False),  # FK to product.id lives in the migration
    sa.Column("grams", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.CheckConstraint("grams > 0", name="ck_meal_template_item_grams"),
)

source_record = sa.Table(
    "source_record",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column("product_id", _ID, sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("external_id", sa.Text, nullable=False),
    sa.Column("raw", JSONB, nullable=False),
    sa.Column(
        "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("nutrition_hash", sa.Text, nullable=False),
    sa.UniqueConstraint("id", "product_id", name="uq_source_record_id_product"),
)

profile_revision = sa.Table(
    "profile_revision",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column("product_id", _ID, sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
    sa.Column("source_record_id", _ID, nullable=False, unique=True),
    sa.Column("nutrition_hash", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("reasons", ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["source_record_id", "product_id"],
        ["source_record.id", "source_record.product_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("id", "product_id", name="uq_profile_revision_id_product"),
    sa.UniqueConstraint("product_id", "nutrition_hash", name="uq_profile_revision_product_hash"),
)

nutrient_value = sa.Table(
    "nutrient_value",
    metadata,
    sa.Column(
        "revision_id",
        _ID,
        sa.ForeignKey("profile_revision.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("nutrient", sa.Text, primary_key=True),
    *_value_columns(),
)

nutrient_override = sa.Table(
    "nutrient_override",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("product_id", _ID, sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
    sa.Column("nutrient", sa.Text, nullable=False),
    *_value_columns(),
    sa.Column("pinned_revision_id", _ID, nullable=False),
    sa.Column("retain_across_revisions", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("data_generation", sa.Integer, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["pinned_revision_id", "product_id"],
        ["profile_revision.id", "profile_revision.product_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint(
        "product_id",
        "nutrient",
        "version",
        name="uq_nutrient_override_product_nutrient_version",
    ),
)

portion = sa.Table(
    "portion",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column("product_id", _ID, sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("amount", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
)

catalogue_release = sa.Table(
    "catalogue_release",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("note", sa.Text, nullable=False),
)

active_release = sa.Table(
    "active_release",
    metadata,
    sa.Column("singleton", sa.Boolean, primary_key=True, server_default=sa.true()),
    sa.Column("release_id", _ID, sa.ForeignKey("catalogue_release.id"), nullable=False),
    sa.Column(
        "activated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)

product_state = sa.Table(
    "product_state",
    metadata,
    sa.Column("id", _ID, sa.Identity(), primary_key=True),
    sa.Column(
        "release_id",
        _ID,
        sa.ForeignKey("catalogue_release.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column("product_id", _ID, sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("merged_into", _ID, sa.ForeignKey("product.id"), nullable=True),
    sa.Column("successors", ARRAY(sa.Integer), nullable=False, server_default="{}"),
    sa.Column("active_revision_id", _ID, nullable=False),
    sa.ForeignKeyConstraint(
        ["active_revision_id", "product_id"],
        ["profile_revision.id", "profile_revision.product_id"],
    ),
    sa.UniqueConstraint(
        "release_id",
        "product_id",
        name="uq_product_state_release_product",
        postgresql_nulls_not_distinct=True,
    ),
)
