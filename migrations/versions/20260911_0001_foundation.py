"""Establish the migration chain before C04 introduces durable models.

Revision ID: 20260911_0001
Revises:
Create Date: 2026-09-11
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260911_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Intentionally empty: production tables are designed and added in C04."""
    pass


def downgrade() -> None:
    """The empty foundation has no database objects to remove."""
    pass
