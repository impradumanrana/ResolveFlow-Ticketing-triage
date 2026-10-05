"""Migration chain mechanics.

This file owns the *chain*: one linear head, a stable base, and the ability to
render every revision as PostgreSQL without credentials or a database.

The *content* of the schema - tenancy, constraints, indexes, append-only
guarantees - is owned by `test_schema_invariants.py`. Keeping the two separate
means a new migration updates one expectation, not two.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]

BASE_REVISION = "20260911_0001"
HEAD_REVISION = "20260920_0012"


def migration_config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def test_migration_chain_has_one_linear_head() -> None:
    """Two heads mean an ambiguous upgrade and a merge nobody intended."""
    scripts = ScriptDirectory.from_config(migration_config())

    assert scripts.get_heads() == [HEAD_REVISION]
    assert scripts.get_base() == BASE_REVISION


def test_every_revision_is_reachable_from_base_to_head() -> None:
    scripts = ScriptDirectory.from_config(migration_config())

    walked = [script.revision for script in scripts.walk_revisions()]
    assert walked[0] == HEAD_REVISION
    assert walked[-1] == BASE_REVISION
    assert len(walked) == len(set(walked)), "a revision appears twice in the chain"


def test_every_revision_declares_a_downgrade() -> None:
    """A migration that cannot be reversed cannot be rolled back."""
    scripts = ScriptDirectory.from_config(migration_config())

    for script in scripts.walk_revisions():
        source = Path(script.path).read_text(encoding="utf-8")
        assert "def downgrade()" in source, f"{script.revision} has no downgrade"


def test_migrations_render_as_postgresql_without_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.upgrade(migration_config(), "head", sql=True)
    rendered = capsys.readouterr().out

    assert BASE_REVISION in rendered
    assert HEAD_REVISION in rendered

    # A '%' inside raw SQL is escaped to '%%' by the DBAPI paramstyle, which
    # would be wrong if an operator applied the rendered SQL directly.
    assert "%%" not in rendered


def test_rendering_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline rendering must not reach for a database URL."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    command.upgrade(migration_config(), "head", sql=True)
