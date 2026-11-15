"""Weekly coach: reviews, LLM calls, budget, feedback and guideline cards (coach Task 3).

Revision ID: 0005_coach
Revises: 0004_body_saved
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_coach"
down_revision: str | None = "0004_body_saved"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")
_RETRIEVED = date(2026, 10, 13)

# Seed cards are created unverified; the user verifies each one in the UI (spec §2.3).
_CARDS = [
    {
        "id": "who_free_sugars",
        "source_org": "WHO",
        "title": "Guideline: sugars intake for adults and children (2015)",
        "quote": (
            "A new WHO guideline recommends adults and children reduce their daily intake of "
            "free sugars to less than 10% of their total energy intake."
        ),
        "url": (
            "https://www.who.int/news/item/"
            "04-03-2015-who-calls-on-countries-to-reduce-sugars-intake-among-adults-and-children"
        ),
        "retrieved_on": _RETRIEVED,
        "threshold": Decimal(10),
        "unit": "% energy",
        "notes": (
            "Guideline: https://www.who.int/publications/i/item/9789241549028. The app applies "
            "it to total sugars as a labelled proxy; it never claims free sugars (A11)."
        ),
    },
    {
        "id": "who_salt",
        "source_org": "WHO",
        "title": "Sodium reduction fact sheet",
        "quote": (
            "For adults, WHO recommends less than 2000 mg/day of sodium (equivalent to less "
            "than 5 g/day salt), or just under a teaspoon."
        ),
        "url": "https://www.who.int/news-room/fact-sheets/detail/sodium-reduction",
        "retrieved_on": _RETRIEVED,
        "threshold": Decimal(5),
        "unit": "g/day",
        "notes": None,
    },
    {
        "id": "efsa_protein_pri",
        "source_org": "EFSA",
        "title": "Scientific Opinion on Dietary Reference Values for protein (2012)",
        "quote": (
            "the PRI for adults of all ages was estimated to be 0.83 g protein/kg body weight "
            "per day and is applicable both to high quality protein and to protein in mixed diets."
        ),
        "url": "https://efsa.onlinelibrary.wiley.com/doi/10.2903/j.efsa.2012.2557",
        "retrieved_on": _RETRIEVED,
        "threshold": Decimal("0.83"),
        "unit": "g/kg body weight/day",
        "notes": (
            "Reference only. The PRI is a population minimum; the app's protein target is the "
            "user's own 1.8 g/kg, which is higher on purpose."
        ),
    },
]


def upgrade() -> None:
    op.create_table(
        "coach_review",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("iso_week", sa.Text, nullable=False),
        sa.Column("fact_sheet_hash", sa.Text, nullable=False),
        sa.Column("generation_no", sa.SmallInteger, nullable=False),
        sa.Column("fact_sheet", JSONB, nullable=False),
        sa.Column("suggestions", JSONB, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("fallback_reason", sa.Text),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("data_generation", sa.Integer, nullable=False),
        sa.CheckConstraint("generation_no IN (1, 2)", name="ck_coach_review_generation_no"),
        sa.CheckConstraint("source IN ('llm', 'template')", name="ck_coach_review_source"),
        sa.CheckConstraint(
            "(source = 'llm' AND fallback_reason IS NULL) OR (source = 'template' AND "
            "fallback_reason IS NOT NULL AND fallback_reason IN ('budget', 'validation', 'llm_error', 'none'))",
            name="ck_coach_review_fallback_reason",
        ),
        sa.CheckConstraint("data_generation >= 1", name="ck_coach_review_data_generation"),
        sa.UniqueConstraint(
            "iso_week", "fact_sheet_hash", "generation_no", name="uq_coach_review_week_hash_gen"
        ),
    )

    op.create_table(
        "llm_call",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("review_id", sa.Integer, sa.ForeignKey("coach_review.id", ondelete="CASCADE")),
        sa.Column("purpose", sa.Text, nullable=False, server_default="coach_weekly"),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("deployment", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("cost_eur", sa.Numeric(asdecimal=True)),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("validation_ok", sa.Boolean),
        sa.Column("validation_errors", JSONB),
        sa.Column("attempt", sa.SmallInteger, nullable=False),
        # Only filled when COACH_LOG_TEXT=true (local debugging); NULL otherwise.
        sa.Column("prompt_text", sa.Text),
        sa.Column("response_text", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("data_generation", sa.Integer, nullable=False),
        sa.CheckConstraint("purpose = 'coach_weekly'", name="ck_llm_call_purpose"),
        sa.CheckConstraint("data_generation >= 1", name="ck_llm_call_data_generation"),
    )
    op.create_index("ix_llm_call_review_id", "llm_call", ["review_id"])

    op.create_table(
        "llm_budget",
        sa.Column("period_start", sa.Date, primary_key=True),
        sa.Column("cap_eur", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("reserved_eur", sa.Numeric(asdecimal=True), nullable=False, server_default="0"),
        sa.Column("spent_eur", sa.Numeric(asdecimal=True), nullable=False, server_default="0"),
        sa.CheckConstraint("EXTRACT(DAY FROM period_start) = 1", name="ck_llm_budget_month_start"),
    )

    op.create_table(
        "coach_feedback",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column(
            "review_id",
            sa.Integer,
            sa.ForeignKey("coach_review.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suggestion_index", sa.SmallInteger, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("data_generation", sa.Integer, nullable=False),
        sa.CheckConstraint("value IN ('up', 'down', 'will_try')", name="ck_coach_feedback_value"),
        sa.CheckConstraint("data_generation >= 1", name="ck_coach_feedback_data_generation"),
    )
    op.create_index("ix_coach_feedback_review_id", "coach_feedback", ["review_id"])

    card = op.create_table(
        "guideline_card",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("source_org", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("retrieved_on", sa.Date, nullable=False),
        sa.Column("threshold", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("verified_by_user_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(card, _CARDS)


def downgrade() -> None:
    op.drop_table("guideline_card")
    op.drop_index("ix_coach_feedback_review_id", table_name="coach_feedback")
    op.drop_table("coach_feedback")
    op.drop_table("llm_budget")
    op.drop_index("ix_llm_call_review_id", table_name="llm_call")
    op.drop_table("llm_call")
    op.drop_table("coach_review")
