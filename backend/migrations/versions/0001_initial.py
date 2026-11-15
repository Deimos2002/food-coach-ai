"""Stage-1 schema: users, products, source revisions, overrides, portions, releases.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-12

Literal enum values are frozen here on purpose (migrations are history); a unit test
checks that they match the domain enums in ``app.domain``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUTRIENTS = (
    "energy_kcal",
    "protein",
    "carbohydrate",
    "sugars",
    "fat",
    "saturated_fat",
    "fibre",
    "salt",
    "caffeine",
    "alcohol",
    "polyols",
    "erythritol",
)
QUALIFIERS = ("none", "trace", "less_than")
SOURCES = ("label", "ciqual", "off_contributor", "manual")
DERIVATIONS = ("direct", "converted", "computed")
VERIFICATIONS = ("unverified", "user_verified", "flagged")
PRODUCT_STATUSES = ("active", "merged", "retired")
# Lifecycle only: which revision is live is defined solely by product_state (spec A.6).
REVISION_STATUSES = ("adopted", "pending_review", "rejected")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id() -> sa.Column:
    return sa.Column("id", sa.Integer, sa.Identity(), primary_key=True)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _product_fk() -> sa.Column:
    return sa.Column(
        "product_id",
        sa.Integer,
        sa.ForeignKey("product.id", ondelete="CASCADE"),
        nullable=False,
    )


def _value_columns(table: str) -> list[sa.SchemaItem]:
    """Nutrient value columns + checks. Unknown never stores a range; a known value always
    has a lower end; upper NULL means unbounded (e.g. a trace)."""
    return [
        sa.Column("known", sa.Boolean, nullable=False),
        sa.Column("lower", sa.Numeric, nullable=True),
        sa.Column("upper", sa.Numeric, nullable=True),
        sa.Column("qualifier", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("derivation", sa.Text, nullable=False),
        sa.Column("verification", sa.Text, nullable=False),
        sa.CheckConstraint(_in("qualifier", QUALIFIERS), name=f"ck_{table}_qualifier"),
        sa.CheckConstraint(_in("source", SOURCES), name=f"ck_{table}_source"),
        sa.CheckConstraint(_in("derivation", DERIVATIONS), name=f"ck_{table}_derivation"),
        sa.CheckConstraint(_in("verification", VERIFICATIONS), name=f"ck_{table}_verification"),
        sa.CheckConstraint(
            "known OR (lower IS NULL AND upper IS NULL)", name=f"ck_{table}_unknown_has_no_range"
        ),
        sa.CheckConstraint("NOT known OR lower IS NOT NULL", name=f"ck_{table}_known_has_lower"),
        sa.CheckConstraint("lower IS NULL OR lower >= 0", name=f"ck_{table}_lower_non_negative"),
        sa.CheckConstraint("upper IS NULL OR upper >= lower", name=f"ck_{table}_upper_ge_lower"),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    op.create_table(
        "user_account",
        _id(),
        sa.Column("entra_oid", sa.Text, nullable=False),
        sa.Column("data_generation", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint("entra_oid", name="uq_user_account_entra_oid"),
        sa.CheckConstraint("data_generation >= 1", name="ck_user_account_generation"),
    )

    op.create_table(
        "product",
        _id(),
        sa.Column("owner", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("brand", sa.Text, nullable=True),
        sa.Column("basis_unit", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("density_g_per_ml", sa.Numeric, nullable=True),
        sa.Column("density_source", sa.Text, nullable=True),
        sa.Column("includes_cooking_additions", sa.Text, nullable=True),
        sa.Column("search_text", sa.Text, nullable=False),
        sa.Column("barcode", sa.Text, nullable=True),
        sa.Column("data_generation", sa.Integer, nullable=True),
        _now("created_at"),
        sa.CheckConstraint(_in("owner", ("catalogue", "user")), name="ck_product_owner"),
        sa.CheckConstraint(_in("kind", ("product", "dish")), name="ck_product_kind"),
        sa.CheckConstraint(_in("basis_unit", ("g", "ml")), name="ck_product_basis_unit"),
        sa.CheckConstraint(_in("state", ("as_sold", "as_prepared")), name="ck_product_state"),
        sa.CheckConstraint(
            _in("includes_cooking_additions", ("yes", "no", "unanswered")),
            name="ck_product_cooking_additions",
        ),
        # A.3: only user-owned dishes carry the declaration; catalogue dishes leave it NULL.
        sa.CheckConstraint(
            "(kind = 'dish' AND owner = 'user') = (includes_cooking_additions IS NOT NULL)",
            name="ck_product_cooking_additions_only_for_user_dish",
        ),
        sa.CheckConstraint(
            "(density_g_per_ml IS NULL) = (density_source IS NULL)",
            name="ck_product_density_has_source",
        ),
        sa.CheckConstraint(
            "density_g_per_ml IS NULL OR density_g_per_ml > 0", name="ck_product_density_positive"
        ),
        sa.CheckConstraint(
            "(owner = 'user') = (data_generation IS NOT NULL)",
            name="ck_product_user_rows_have_generation",
        ),
        sa.CheckConstraint(
            "data_generation IS NULL OR data_generation >= 1", name="ck_product_generation"
        ),
    )
    op.create_index("ix_product_barcode", "product", ["barcode"])

    op.create_table(
        "source_record",
        _id(),
        _product_fk(),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("raw", JSONB, nullable=False),
        _now("fetched_at"),
        sa.Column("nutrition_hash", sa.Text, nullable=False),
        sa.UniqueConstraint("id", "product_id", name="uq_source_record_id_product"),
        sa.CheckConstraint(_in("source", SOURCES), name="ck_source_record_source"),
    )
    op.create_index("ix_source_record_product", "source_record", ["product_id"])

    op.create_table(
        "profile_revision",
        _id(),
        _product_fk(),
        sa.Column("source_record_id", sa.Integer, nullable=False),
        sa.Column("nutrition_hash", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("reasons", ARRAY(sa.Text), nullable=False, server_default="{}"),
        _now("created_at"),
        # Exactly one source record per revision, belonging to the same product.
        sa.ForeignKeyConstraint(
            ["source_record_id", "product_id"],
            ["source_record.id", "source_record.product_id"],
            name="fk_profile_revision_source_record",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source_record_id", name="uq_profile_revision_source_record"),
        sa.UniqueConstraint("id", "product_id", name="uq_profile_revision_id_product"),
        # A profile with the same nutrition fields is stored once per product and reused.
        sa.UniqueConstraint(
            "product_id", "nutrition_hash", name="uq_profile_revision_product_hash"
        ),
        sa.CheckConstraint(_in("status", REVISION_STATUSES), name="ck_profile_revision_status"),
    )
    # Lookups by product use uq_profile_revision_product_hash (leading column product_id).

    op.create_table(
        "nutrient_value",
        sa.Column(
            "revision_id",
            sa.Integer,
            sa.ForeignKey("profile_revision.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nutrient", sa.Text, nullable=False),
        *_value_columns("nutrient_value"),
        sa.PrimaryKeyConstraint("revision_id", "nutrient", name="pk_nutrient_value"),
        sa.CheckConstraint(_in("nutrient", NUTRIENTS), name="ck_nutrient_value_nutrient"),
    )

    op.create_table(
        "nutrient_override",
        _id(),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("product_id", sa.Integer, nullable=False),
        sa.Column("nutrient", sa.Text, nullable=False),
        *_value_columns("nutrient_override"),
        sa.Column("pinned_revision_id", sa.Integer, nullable=False),
        sa.Column("retain_across_revisions", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("data_generation", sa.Integer, nullable=False),
        _now("created_at"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name="fk_nutrient_override_product", ondelete="CASCADE"
        ),
        # The pinned revision must be a revision of the same product.
        sa.ForeignKeyConstraint(
            ["pinned_revision_id", "product_id"],
            ["profile_revision.id", "profile_revision.product_id"],
            name="fk_nutrient_override_pinned_revision",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in("nutrient", NUTRIENTS), name="ck_nutrient_override_nutrient"),
        sa.UniqueConstraint(
            "product_id",
            "nutrient",
            "version",
            name="uq_nutrient_override_product_nutrient_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_nutrient_override_version"),
        sa.CheckConstraint("data_generation >= 1", name="ck_nutrient_override_generation"),
    )
    op.create_index("ix_nutrient_override_product", "nutrient_override", ["product_id"])

    op.create_table(
        "portion",
        _id(),
        _product_fk(),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_portion_amount_positive"),
        sa.CheckConstraint(_in("kind", ("serving", "package")), name="ck_portion_kind"),
    )
    op.create_index("ix_portion_product", "portion", ["product_id"])

    op.create_table(
        "catalogue_release",
        _id(),
        _now("created_at"),
        sa.Column("note", sa.Text, nullable=False),
    )

    op.create_table(
        "active_release",
        sa.Column("singleton", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("release_id", sa.Integer, sa.ForeignKey("catalogue_release.id"), nullable=False),
        _now("activated_at"),
        sa.PrimaryKeyConstraint("singleton", name="pk_active_release"),
        sa.CheckConstraint("singleton", name="ck_active_release_singleton"),
    )

    op.create_table(
        "product_state",
        _id(),
        sa.Column(
            "release_id",
            sa.Integer,
            sa.ForeignKey("catalogue_release.id", ondelete="CASCADE"),
            nullable=True,
        ),
        _product_fk(),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("merged_into", sa.Integer, sa.ForeignKey("product.id"), nullable=True),
        sa.Column("successors", ARRAY(sa.Integer), nullable=False, server_default="{}"),
        sa.Column("active_revision_id", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(
            ["active_revision_id", "product_id"],
            ["profile_revision.id", "profile_revision.product_id"],
            name="fk_product_state_active_revision",
        ),
        sa.UniqueConstraint(
            "release_id",
            "product_id",
            name="uq_product_state_release_product",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(_in("status", PRODUCT_STATUSES), name="ck_product_state_status"),
        # identity.resolve raises on MERGED without merged_into: forbid it here.
        sa.CheckConstraint(
            "status <> 'merged' OR merged_into IS NOT NULL",
            name="ck_product_state_merged_has_target",
        ),
        sa.CheckConstraint(
            "status = 'merged' OR merged_into IS NULL",
            name="ck_product_state_merged_into_only_when_merged",
        ),
        sa.CheckConstraint(
            "merged_into IS NULL OR merged_into <> product_id",
            name="ck_product_state_not_merged_into_self",
        ),
    )
    op.create_index("ix_product_state_product", "product_state", ["product_id"])


def downgrade() -> None:
    for table in (
        "product_state",
        "active_release",
        "catalogue_release",
        "portion",
        "nutrient_override",
        "nutrient_value",
        "profile_revision",
        "source_record",
        "product",
        "user_account",
    ):
        op.drop_table(table)
    # pg_trgm / unaccent are left installed: other objects in the database may use them.
