import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.prompt_builder import build_prompt
from app.main import app
from app.db import SCHEMA, _migrate
from app.web import db_dep
from app.auth import hash_password
from app.models import AnalysisOutput

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
def temp_db():
    from app import config
    config.ensure_dirs()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    _migrate(conn)
    yield conn
    conn.close()

def test_build_prompt_recorded_date_and_speakers():
    metadata = {
        "conversation_type": "Interview",
        "owner_role": "Interviewer",
        "objective": "Understand candidates",
        "context_note": "A regular sync",
        "recorded_date": "2026-07-15",
        "single_sided": False
    }
    
    speaker_mapping = {
        "speaker_0": {"is_owner": True, "local_name": "Alice"},
        "speaker_1": {"is_owner": False, "local_name": "Bob"}
    }
    
    prompt = build_prompt(
        metadata=metadata,
        owner_label="speaker_0",
        speaker_labels=["speaker_0", "speaker_1"],
        metrics={},
        utterances=[
            {"speaker_label": "speaker_0", "start_sec": 0.0, "end_sec": 2.0, "text": "Hello Bob"},
            {"speaker_label": "speaker_1", "start_sec": 3.0, "end_sec": 5.0, "text": "Hi Alice"}
        ],
        profile_json={},
        single_sided=False,
        speaker_mapping=speaker_mapping
    )
    
    # Assert recorded_date is in metadata
    assert "Date of conversation: 2026-07-15" in prompt
    
    # Assert speakers are mapped
    assert "[00:00] you: Hello Bob" in prompt
    assert "[00:03] Bob: Hi Alice" in prompt
    
    # Check critical instructions for speakers
    assert "refer to the owner (the logged-in user) in the first person as 'you'" in prompt
    assert "refer to other speakers using their actual mapped names" in prompt


def test_plain_text_upload_flow(temp_db):
    app.dependency_overrides[db_dep] = lambda: temp_db
    try:
        # Seed user
        temp_db.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (1, "alice", hash_password("password123"), "2026-07-14T00:00:00Z"),
        )
        temp_db.commit()

        client = TestClient(app)

        # Login to obtain session cookies
        login_resp = client.post(
            "/login",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        cookies = {"session_token": login_resp.cookies["session_token"]}

        # POST to /upload with plain_text
        upload_resp = client.post(
            "/upload",
            data={
                "upload_type": "plain_text",
                "recorded_date": "2026-07-15",
                "plain_text": "This is a plain text conversation for testing."
            },
            cookies=cookies,
            follow_redirects=False
        )
        assert upload_resp.status_code == 303
        
        # Verify the database entry was created
        row = temp_db.execute("SELECT * FROM knowledge_entries WHERE user_id = 1").fetchone()
        assert row is not None
        assert row["entry_type"] == "text"
        assert row["recorded_date"] == "2026-07-15"
        assert row["raw_text"] == "This is a plain text conversation for testing."

        file_id = row["id"]
        
        # Now POST to /files/{file_id}/categorize
        # Mock background task run_analysis_background
        with patch("app.background.run_analysis_background") as mock_background_task:
            cat_resp = client.post(
                f"/files/{file_id}/categorize",
                data={
                    "conversation_type": "Interview",
                    "owner_role": "Interviewer",
                    "objective": "Testing upload flow",
                    "context_note": "A mock test document categorization",
                },
                cookies=cookies,
                follow_redirects=False
            )
            assert cat_resp.status_code == 303
            
            # Verify database records created during categorization for text entries
            transcript_row = temp_db.execute("SELECT * FROM transcripts WHERE audio_file_id = ?", (file_id,)).fetchone()
            assert transcript_row is not None
            assert transcript_row["plain_text"] == "This is a plain text conversation for testing."
            
            speakers_rows = temp_db.execute("SELECT * FROM speakers WHERE transcript_id = ?", (transcript_row["id"],)).fetchall()
            assert len(speakers_rows) == 1
            assert speakers_rows[0]["speaker_label"] == "speaker_1"
            assert speakers_rows[0]["is_owner"] == 1
            assert speakers_rows[0]["local_name"] == "alice"
            
            utterances_rows = temp_db.execute("SELECT * FROM utterances WHERE transcript_id = ?", (transcript_row["id"],)).fetchall()
            assert len(utterances_rows) == 1
            assert utterances_rows[0]["text"] == "This is a plain text conversation for testing."
            
            # Verify background analysis task was scheduled
            mock_background_task.assert_called_once()
            
    finally:
        app.dependency_overrides.clear()


def test_export_transcript_to_file(temp_db, tmp_path):
    from app.web import export_transcript_to_file
    from app import config
    import os

    original_data_dir = config.DATA_DIR
    config.DATA_DIR = tmp_path

    try:
        temp_db.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (1, 'alice', 'hash', '2026-07-14T00:00:00Z')"
        )
        temp_db.execute(
            "INSERT INTO knowledge_entries (id, user_id, path, filename, uploaded_at, duration_sec, transcription_status) VALUES (10, 1, 'foo.mp3', 'foo.mp3', '2026-07-14T00:00:00Z', 10.0, 'done')"
        )
        temp_db.execute(
            "INSERT INTO transcripts (id, audio_file_id, raw_deepgram_json, plain_text, created_at) VALUES (20, 10, '{}', 'hello bob', '2026-07-14T00:00:00Z')"
        )
        temp_db.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) VALUES (20, 'speaker_0', 1, 'Alice')"
        )
        temp_db.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) VALUES (20, 'speaker_1', 0, 'Bob')"
        )
        temp_db.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) VALUES (20, 'speaker_0', 1.5, 3.0, 'hello')"
        )
        temp_db.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) VALUES (20, 'speaker_1', 4.0, 6.0, 'hi')"
        )
        temp_db.commit()

        export_transcript_to_file(temp_db, 20)

        export_file = tmp_path / "exported_transcripts" / "foo_transcript.txt"
        assert export_file.exists()

        content = export_file.read_text(encoding="utf-8")
        assert "Transcript for: foo.mp3" in content
        assert "[00:02] You: hello" in content
        assert "[00:04] Bob: hi" in content

    finally:
        config.DATA_DIR = original_data_dir


def test_change_speaker_assignment(temp_db):
    app.dependency_overrides[db_dep] = lambda: temp_db
    try:
        # Seed user
        temp_db.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (1, 'alice', ?, ?)",
            (hash_password("password123"), "2026-07-14T00:00:00Z"),
        )
        # Seed knowledge entry (audio)
        temp_db.execute(
            "INSERT INTO knowledge_entries (id, user_id, path, filename, uploaded_at, duration_sec, transcription_status, entry_type) "
            "VALUES (10, 1, 'foo.mp3', 'foo.mp3', '2026-07-14T00:00:00Z', 10.0, 'done', 'audio')"
        )
        # Seed transcript
        temp_db.execute(
            "INSERT INTO transcripts (id, audio_file_id, raw_deepgram_json, plain_text, created_at) "
            "VALUES (20, 10, '{}', 'hello bob', '2026-07-14T00:00:00Z')"
        )
        # Seed speakers (speaker_1 is owner initially, speaker_2 is not)
        temp_db.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) "
            "VALUES (20, 'speaker_1', 1, NULL)"
        )
        temp_db.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) "
            "VALUES (20, 'speaker_2', 0, NULL)"
        )
        # Seed utterances
        temp_db.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) "
            "VALUES (20, 'speaker_1', 0.0, 2.0, 'hello')"
        )
        temp_db.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) "
            "VALUES (20, 'speaker_2', 3.0, 5.0, 'hi')"
        )
        temp_db.commit()

        client = TestClient(app)

        # Login to obtain session cookies
        login_resp = client.post(
            "/login",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        cookies = {"session_token": login_resp.cookies["session_token"]}

        # GET /transcripts/20/speakers (should load and have is_edit=True)
        resp = client.get("/transcripts/20/speakers", cookies=cookies)
        assert resp.status_code == 200
        assert "Change speaker assignment" in resp.text
        assert "Save changes" in resp.text

        # POST to /transcripts/20/speakers to change owner to speaker_2
        post_resp = client.post(
            "/transcripts/20/speakers",
            data={
                "owner": "speaker_2",
                "local_name__speaker_1": "Bob",
                "local_name__speaker_2": "Alice"
            },
            cookies=cookies,
            follow_redirects=False
        )
        assert post_resp.status_code == 303
        assert post_resp.headers["location"] == "/transcripts/20"

        # Verify database is updated
        s1 = temp_db.execute("SELECT * FROM speakers WHERE transcript_id=20 AND speaker_label='speaker_1'").fetchone()
        s2 = temp_db.execute("SELECT * FROM speakers WHERE transcript_id=20 AND speaker_label='speaker_2'").fetchone()
        assert s1["is_owner"] == 0
        assert s1["local_name"] == "Bob"
        assert s2["is_owner"] == 1
        assert s2["local_name"] == "Alice"

        # GET /transcripts/20 and check that the change is reflected (e.g. Change speaker assignment button and link are present)
        metrics_resp = client.get("/transcripts/20", cookies=cookies)
        assert metrics_resp.status_code == 200
        assert "Change speaker assignment" in metrics_resp.text

    finally:
        app.dependency_overrides.clear()


