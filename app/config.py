from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def get_env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


OPENAI_API_KEY = get_env("OPENAI_API_KEY")
OPENAI_MODEL = get_env("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_EMBEDDING_MODEL = get_env("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_EMBEDDING_DIMENSIONS = int(get_env("OPENAI_EMBEDDING_DIMENSIONS", "1536"))
QDRANT_COLLECTION = get_env("QDRANT_COLLECTION", "resolveflow_knowledge")
KB_THRESHOLD = float(get_env("KB_THRESHOLD", "0.55"))
HAS_OPENAI_KEY = bool(
    OPENAI_API_KEY
    and OPENAI_API_KEY.strip()
    and OPENAI_API_KEY not in {"your_api_key_here", "sk-your-key-here"}
)
