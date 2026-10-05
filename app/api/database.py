"""How this service finds its database.

Separated from the routers so that a cross-cutting dependency - the C13 rate
limiter - can reach the engine without importing a router that imports the
limiter back. Nothing here knows about HTTP.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


def _database_url() -> str | None:
    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        return explicit
    instance = os.getenv("DB_INSTANCE_CONNECTION_NAME", "").strip()
    database = os.getenv("DB_NAME", "").strip()
    user = os.getenv("DB_IAM_USER", "").strip().removesuffix(".gserviceaccount.com")
    if instance and database and user:
        # Unix socket from the Cloud SQL volume mounted by C02; IAM auth, no password.
        return f"postgresql+psycopg://{user}@/{database}?host=/cloudsql/{instance}"
    return None


@lru_cache(maxsize=1)
def _engine(url: str) -> Any:
    from sqlalchemy import create_engine

    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
