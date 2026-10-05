"""Curated per-article search terms.

The proven MVP corpus carries a `keywords` list per article, and its reranker
scores `title title keywords excerpt`. The C05 port initially dropped that
signal, which cost one labelled case: a query for "receipt" retrieved the
invoice article rather than the order-confirmation article whose curated terms
name a receipt explicitly.

Curated terms are a real capability, not test tuning: a knowledge manager knows
that customers say "receipt" for what an article calls an "order confirmation",
and no amount of embedding similarity recovers vocabulary the document never
uses. They are stored separately from the body so that citation offsets stay
true - appending them to the body would put them inside quoted passages.

Revision ID: 20260915_0005
Revises: 20260915_0004
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0005"
down_revision: str | Sequence[str] | None = "20260915_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_articles",
        sa.Column(
            "search_terms",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )

    # Bounded: curated terms are a hint, not a second document. An unbounded
    # list would let one article dominate lexical scoring for every query.
    op.create_check_constraint(
        "search_terms_are_bounded",
        "knowledge_articles",
        "cardinality(search_terms) <= 24",
    )

    op.create_index(
        "ix_knowledge_articles_search_terms",
        "knowledge_articles",
        ["search_terms"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_articles_search_terms", table_name="knowledge_articles")
    # The bare name, not the rendered one: env.py's naming convention
    # (ck_%(table_name)s_%(constraint_name)s) is applied on drop as well as on
    # create, so passing the full name produces a doubled, non-existent one.
    op.drop_constraint("search_terms_are_bounded", "knowledge_articles", type_="check")
    op.drop_column("knowledge_articles", "search_terms")
