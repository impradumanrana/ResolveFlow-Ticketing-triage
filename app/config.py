from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def get_env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


LLM_PROVIDER = get_env("LLM_PROVIDER", "openai")
OPENAI_API_KEY = get_env("OPENAI_API_KEY")
OPENAI_MODEL = get_env("OPENAI_MODEL", "gpt-4.1-mini")
GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GROQ_API_KEY = get_env("GROQ_API_KEY")
OLLAMA_BASE_URL = get_env("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = get_env("OLLAMA_MODEL", "llama3.2:3b")
KB_THRESHOLD = float(get_env("KB_THRESHOLD", "0.55"))
HAS_OPENAI_KEY = bool(
    OPENAI_API_KEY
    and OPENAI_API_KEY.strip()
    and OPENAI_API_KEY not in {"your_api_key_here", "sk-your-key-here"}
)
USING_DEMO_PROVIDER = not (LLM_PROVIDER == "openai" and HAS_OPENAI_KEY)
