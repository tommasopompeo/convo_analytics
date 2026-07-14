"""Application configuration.

Loads the local `.env` (which holds DEEPGRAM_API_KEY and GEMINI_API_KEY) and exposes paths and
tunable constants. The API keys are read from the environment on demand and are
NEVER printed, logged, or written to disk.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of the `app/` package directory.
BASE_DIR = Path(__file__).resolve().parent.parent

# `load_dotenv` walks up from cwd to find `.env`; pass the explicit path so it
# works regardless of where uvicorn is launched from.
load_dotenv(BASE_DIR / ".env")

# ── Paths ────────────────────────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
DB_PATH = DATA_DIR / "app.db"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# ── Upload constraints ───────────────────────────────────────────────────
ALLOWED_EXT = {".mp3", ".m4a", ".wav"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

# ── Metrics tuning ─────────────────────────────────────────────────────────
LONG_PAUSE_SEC = 3.0  # inter-utterance gaps longer than this are flagged

# ── Deepgram language ────────────────────────────────────────────────────
# "multi" = Nova-3 multilingual (auto code-switch). "it" = monolingual Italian.
# For a conversation that is ~entirely Italian, "it" gives the model a single
# tight language model instead of hedging across 10 languages, which fixes the
# Spanish/garble substitutions ("siguiente", "padonesi") and improves names.
# Override at runtime with the DEEPGRAM_LANGUAGE env var without editing code.
LANGUAGE: str = os.environ.get("DEEPGRAM_LANGUAGE", "multi")

# ── Deepgram keyterms ──────────────────────────────────────────────────────
# Domain terms Nova-3 keeps mishearing in this corpus: people, projects, clients,
# acronyms. `keyterm` prompting is supported on nova-3 for both monolingual (it)
# and multilingual. The client passes `keyterm` only when this is non-empty.
# Keep to real proper nouns / jargon — do NOT add common words (hurts recall).
KEYTERMS: list[str] = [
    # People (spelled every which way in the raw transcript)
    "Seba", "Costantino", "Scaglione", "Pignatelli", "Barbi",
    "Leo Boscardi", "Michele", "Giuse", "Linda", "Stefania", "Tommy", "Lodo",
    # Projects / initiatives
    "Future of Travel", "workshop", "consuntivo",
    # Clients / entities
    "Stellantis", "Ferrari", "MSC", "Accenture", "McKinsey", "BCG",
    # Acronyms / domain jargon
    "FS", "CCNO", "GRT", "beautification", "deck", "staffing", "recoverability plan",
    "fiscal year", "ownership", "stakeholder",
]


def get_deepgram_api_key() -> str:
    """Return the Deepgram API key from the environment.

    Raises a clear error if missing. The key value is never included in the
    error message or any log line.
    """
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        raise RuntimeError(
            "DEEPGRAM_API_KEY is not set. Create a .env file at the project "
            "root containing DEEPGRAM_API_KEY=<value>."
        )
    return key


def get_gemini_api_key() -> str:
    """Return the Gemini API key from the environment.

    Raises a clear error if missing. The key value is never included in the
    error message or any log line.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Create a .env file at the project "
            "root containing GEMINI_API_KEY=<value>."
        )
    return key


# ── Authentication ─────────────────────────────────────────────────────────
JWT_SECRET: str = os.environ.get("JWT_SECRET", "local-development-secret-key-change-in-production")


def ensure_dirs() -> None:
    """Create local data directories if they do not exist."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

