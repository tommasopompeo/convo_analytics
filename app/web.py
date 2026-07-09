"""Shared web helpers: the Jinja2 templates instance, a DB dependency, and
small query helpers used across route modules.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from fastapi.templating import Jinja2Templates

from . import config
from .db import get_conn
from .profile_merge import empty_profile

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


def fmt_ts(seconds) -> str:
    """mm:ss formatter, usable in Python and (registered below) in templates."""
    if seconds is None:
        return "--:--"
    total = int(round(float(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


templates.env.globals["fmt_ts"] = fmt_ts

# Human-readable labels for the categorization buttons (order preserved).
CONVERSATION_TYPES = [
    "Interview", "1-on-1 (work)", "Brainstorming", "Friends",
    "Family", "Meeting (3+)", "Other",
]
OWNER_ROLES = ["Interviewer", "Interviewee", "Facilitator", "Participant", "N/A"]


def db_dep():
    """FastAPI dependency yielding a connection that is always closed."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def load_current_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT profile_json FROM owner_profile ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        return json.loads(row["profile_json"])
    return empty_profile()


def owner_label_for(conn: sqlite3.Connection, transcript_id: int) -> Optional[str]:
    row = conn.execute(
        "SELECT speaker_label FROM speakers WHERE transcript_id=? AND is_owner=1",
        (transcript_id,),
    ).fetchone()
    return row["speaker_label"] if row else None


def load_utterances(conn: sqlite3.Connection, transcript_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT speaker_label, start_sec, end_sec, text FROM utterances "
        "WHERE transcript_id=? ORDER BY start_sec, end_sec",
        (transcript_id,),
    ).fetchall()
    return [dict(r) for r in rows]
