"""SQLite access + schema (spec §4).

One helper opens connections in WAL mode with a busy timeout so the polling
reads on the Transcribing screen never collide with the background job's write.
The schema is created idempotently on startup, and any transcription stranded
by a crash/restart is reconciled to `failed`.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config


def utc_now_iso() -> str:
    """UTC ISO-8601 timestamp used for all created_at/updated_at columns."""
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    """Open a new SQLite connection.

    `check_same_thread=False` because the background transcription job runs in
    a worker thread and opens its own connection. WAL + busy_timeout keep the
    single-writer/many-reader access pattern smooth.
    """
    conn = sqlite3.connect(
        config.DB_PATH,
        check_same_thread=False,
        timeout=30.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_files (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER REFERENCES users(id),
    path                 TEXT NOT NULL,
    filename             TEXT NOT NULL,
    uploaded_at          TEXT NOT NULL,
    duration_sec         REAL,
    conversation_type    TEXT,
    owner_role           TEXT,
    objective            TEXT,
    context_note         TEXT,
    transcription_status TEXT NOT NULL DEFAULT 'uploaded',
    transcription_error  TEXT,
    single_sided         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transcripts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_file_id     INTEGER NOT NULL REFERENCES audio_files(id),
    raw_deepgram_json TEXT NOT NULL,
    plain_text        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS utterances (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id),
    speaker_label TEXT NOT NULL,
    start_sec     REAL NOT NULL,
    end_sec       REAL NOT NULL,
    text          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS speakers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id),
    speaker_label TEXT NOT NULL,
    is_owner      INTEGER NOT NULL DEFAULT 0,
    local_name    TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id   INTEGER NOT NULL REFERENCES transcripts(id),
    metrics_json    TEXT NOT NULL,
    llm_output_json TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_profile (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    profile_json    TEXT NOT NULL,
    archetype       TEXT,
    archetype_notes TEXT,
    updated_at      TEXT NOT NULL
);

-- Aggregate cross-corpus synthesis (append-only, like owner_profile). Each row
-- is one holistic re-read of the WHOLE corpus — the /profile centerpiece —
-- replacing the last-write-wins archetype with a genuine synthesis. Stage 1 is
-- pasted back by hand; Stage 2 (API) writes the same shape. `synthesis_type` is
-- manual | incremental | full; `source_analysis_ids` is the JSON list the
-- synthesis covered so the portrait stays auditable.
CREATE TABLE IF NOT EXISTS aggregate_insight (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER REFERENCES users(id),
    insight_json        TEXT NOT NULL,
    archetype           TEXT,
    synthesis_type      TEXT NOT NULL DEFAULT 'manual',
    conversation_count  INTEGER NOT NULL DEFAULT 0,
    source_analysis_ids TEXT,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_utterances_transcript ON utterances(transcript_id);
CREATE INDEX IF NOT EXISTS idx_speakers_transcript   ON speakers(transcript_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_audio     ON transcripts(audio_file_id);
CREATE INDEX IF NOT EXISTS idx_analyses_transcript   ON analyses(transcript_id);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent column migrations for DBs created before a column existed.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so a column added
    to SCHEMA above won't appear on a pre-existing `audio_files`. Add it here,
    guarded by a check so re-running is a no-op.
    """
    # Migrate audio_files
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(audio_files)")}
    if "single_sided" not in cols:
        conn.execute(
            "ALTER TABLE audio_files ADD COLUMN single_sided INTEGER NOT NULL DEFAULT 0"
        )
    if "user_id" not in cols:
        conn.execute(
            "ALTER TABLE audio_files ADD COLUMN user_id INTEGER REFERENCES users(id)"
        )

    # Migrate owner_profile
    profile_cols = {r["name"] for r in conn.execute("PRAGMA table_info(owner_profile)")}
    if "user_id" not in profile_cols:
        conn.execute(
            "ALTER TABLE owner_profile ADD COLUMN user_id INTEGER REFERENCES users(id)"
        )

    # Migrate aggregate_insight
    insight_cols = {r["name"] for r in conn.execute("PRAGMA table_info(aggregate_insight)")}
    if "user_id" not in insight_cols:
        conn.execute(
            "ALTER TABLE aggregate_insight ADD COLUMN user_id INTEGER REFERENCES users(id)"
        )


def init_db() -> None:
    """Create tables (idempotent) and reconcile stranded transcriptions.

    A process killed while a job was running leaves rows in 'transcribing'
    forever, hanging the spinner. Flip only those to 'failed' on startup.
    Rows in 'uploaded' are awaiting categorization and are legitimately
    resumable, so they are left untouched.
    """
    config.ensure_dirs()
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute(
            """
            UPDATE audio_files
               SET transcription_status = 'failed',
                   transcription_error  = 'Interrupted before completion (server restart).'
             WHERE transcription_status = 'transcribing'
            """
        )
        conn.commit()
    finally:
        conn.close()

