"""Shared web helpers: the Jinja2 templates instance, a DB dependency, and
small query helpers used across route modules.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from fastapi import Cookie, Depends, Request
from fastapi.templating import Jinja2Templates

from . import config
from .auth import decode_access_token
from .db import get_conn
from .profile_merge import empty_profile

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


class UnauthenticatedException(Exception):
    """Exception raised when an endpoint requires an authenticated user but none is found."""
    pass


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


def get_current_user_optional(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    conn=Depends(db_dep),
) -> Optional[dict[str, Any]]:
    """FastAPI dependency that returns the currently logged-in user, or None if unauthenticated."""
    if not session_token:
        return None
    payload = decode_access_token(session_token)
    if not payload or "sub" not in payload:
        return None
    username = payload["sub"]
    row = conn.execute(
        "SELECT id, username FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not row:
        return None
    return dict(row)


def get_current_user(
    user: Optional[dict[str, Any]] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """FastAPI dependency that requires a logged-in user, raising UnauthenticatedException if missing."""
    if not user:
        raise UnauthenticatedException()
    return user


def load_current_profile(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT profile_data FROM owner_profile WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return json.loads(row["profile_data"])
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


def export_transcript_to_file(conn: sqlite3.Connection, transcript_id: int) -> None:
    """Export the transcript to a structured text file in data/exported_transcripts."""
    import os
    row = conn.execute(
        """
        SELECT t.id, a.filename
          FROM transcripts t JOIN knowledge_entries a ON a.id = t.audio_file_id
         WHERE t.id = ?
        """,
        (transcript_id,),
    ).fetchone()
    if not row:
        return

    filename = row["filename"]

    # Get owner label
    owner_row = conn.execute(
        "SELECT speaker_label FROM speakers WHERE transcript_id = ? AND is_owner = 1",
        (transcript_id,),
    ).fetchone()
    owner_label = owner_row["speaker_label"] if owner_row else None

    # Get all speaker names mapping
    local_names = {
        r["speaker_label"]: r["local_name"]
        for r in conn.execute(
            "SELECT speaker_label, local_name FROM speakers WHERE transcript_id = ?",
            (transcript_id,),
        ).fetchall()
    }

    # Get utterances
    utterances = conn.execute(
        "SELECT speaker_label, start_sec, text FROM utterances WHERE transcript_id = ? ORDER BY start_sec",
        (transcript_id,),
    ).fetchall()

    # Format the transcript lines
    lines = []
    lines.append(f"Transcript for: {filename}")
    lines.append("=" * 60)
    lines.append("")

    for u in utterances:
        ts = fmt_ts(u["start_sec"])
        label = u["speaker_label"]
        if label == owner_label:
            who = "You"
        elif local_names.get(label):
            who = local_names[label]
        else:
            who = label.replace("speaker_", "Voice ")

        lines.append(f"[{ts}] {who}: {u['text']}")

    base_name, _ = os.path.splitext(filename)
    export_dir = config.DATA_DIR / "exported_transcripts"
    os.makedirs(export_dir, exist_ok=True)
    
    base_path = os.path.join(export_dir, f"{base_name}_transcript")
    export_path = f"{base_path}.txt"
    counter = 1
    while os.path.exists(export_path):
        export_path = f"{base_path}_{counter}.txt"
        counter += 1

    with open(export_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


