import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from app.main import app
from app.db import get_conn, SCHEMA, _migrate
from app.web import db_dep, load_current_profile
from app.auth import hash_password
from app.models import AggregateOutput, AnalysisOutput
from app.prompt_builder import build_aggregate_prompt


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def temp_db():
    """Fixture providing a clean in-memory SQLite database setup."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    _migrate(conn)
    yield conn
    conn.close()


def test_profile_update_endpoint(temp_db):
    """Test POST /profile/update correctly updates the owner_profile table with the new schema fields."""
    app.dependency_overrides[db_dep] = lambda: temp_db
    try:
        # Seed user
        temp_db.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (1, "alice", hash_password("password123"), "2026-07-14T00:00:00Z"),
        )
        temp_db.commit()

        client = TestClient(app)

        # Login
        login_resp = client.post(
            "/login",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        cookies = {"session_token": login_resp.cookies["session_token"]}

        # Submit profile update form
        form_data = {
            "who_i_am": "I am a software engineer.",
            "current_issues": "Fixing bugs\nRefactoring tests\n",
            "recurrent_topics": "FastAPI\nPython\n",
            "strong_opinions": "Tabs over spaces\nClean code is key\n",
            "tone_and_sentiment": "Empathetic and positive",
        }
        
        response = client.post(
            "/profile/update",
            data=form_data,
            cookies=cookies,
            follow_redirects=False,
        )
        
        # Should redirect back to /profile
        assert response.status_code == 303
        assert response.headers["location"] == "/profile"

        # Check database for latest profile
        profile = load_current_profile(temp_db, 1)
        assert profile["who_i_am"] == "I am a software engineer."
        assert profile["current_issues"] == ["Fixing bugs", "Refactoring tests"]
        assert profile["recurrent_topics"] == ["FastAPI", "Python"]
        assert profile["strong_opinions"] == ["Tabs over spaces", "Clean code is key"]
        assert profile["tone_and_sentiment"] == "Empathetic and positive"
        assert profile["version"] == 1

    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_analyze_with_user_comment(temp_db):
    """Test POST /transcripts/{id}/analyze accepts user_comment, saves it, and displays it on the result page."""
    app.dependency_overrides[db_dep] = lambda: temp_db
    try:
        # Seed user
        temp_db.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (1, "alice", hash_password("password123"), "2026-07-14T00:00:00Z"),
        )
        # Seed knowledge_entry
        temp_db.execute(
            "INSERT INTO knowledge_entries (id, user_id, path, filename, uploaded_at, duration_sec, transcription_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "test.mp3", "test.mp3", "2026-07-14T00:00:00Z", 10.0, "done"),
        )
        # Seed transcript
        temp_db.execute(
            "INSERT INTO transcripts (id, audio_file_id, raw_deepgram_json, plain_text, created_at) VALUES (?, ?, ?, ?, ?)",
            (1, 1, "{}", "hello world", "2026-07-14T00:00:00Z"),
        )
        # Seed utterances
        temp_db.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) VALUES (?, ?, ?, ?, ?)",
            (1, "speaker_0", 0.0, 5.0, "hello world"),
        )
        # Seed speakers
        temp_db.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) VALUES (?, ?, ?, ?)",
            (1, "speaker_0", 1, "Alice"),
        )
        temp_db.commit()

        client = TestClient(app)

        # Login
        login_resp = client.post(
            "/login",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        cookies = {"session_token": login_resp.cookies["session_token"]}

        mock_analysis = AnalysisOutput.model_validate({
            "summary": "Meeting summary.",
            "overall_sentiment": {"owner": "positive", "conversation": "neutral"},
            "follow_ups": {"questions": [], "actions": []},
            "owner_insights": {"communication_style": "", "notable_behaviors": []},
            "owner_profile_update": {
                "recurring_topics_add": ["Productivity"],
                "communication_style_notes": [],
                "goals_concerns_add": [],
                "archetype_signal": "Leader",
            }
        })

        async def mock_analyze(*args, **kwargs):
            return mock_analysis

        with patch("app.gemini_client.analyze_conversation_async", new=mock_analyze):
            response = client.post(
                "/transcripts/1/analyze",
                data={"user_comment": "Costantino was in a rush"},
                cookies=cookies,
                follow_redirects=False,
            )

            assert response.status_code == 303
            analysis_id = response.headers["location"].split("/")[-1]

            # Verify comment saved to DB
            analysis_row = temp_db.execute(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
            assert analysis_row is not None
            assert analysis_row["user_comment"] == "Costantino was in a rush"

            # Verify result page renders the comment
            result_resp = client.get(f"/analyses/{analysis_id}", cookies=cookies)
            assert result_resp.status_code == 200
            assert "Costantino was in a rush" in result_resp.text

    finally:
        app.dependency_overrides.clear()


def test_build_aggregate_prompt_with_comments_and_overrides():
    """Verify build_aggregate_prompt outputs user comments and manual overrides correctly."""
    conversations = [
        {
            "ref": "test-convo",
            "analysis_id": 1,
            "conversation_type": "Interview",
            "owner_role": "Interviewer",
            "date": "2026-07-15",
            "single_sided": False,
            "analysis": {"summary": "A simple discussion."},
            "user_comment": "Note: COSTANTINO WAS IN A RUSH",
            "metric_summary": {},
        }
    ]
    user_profile = {
        "who_i_am": "I am Bob.",
        "current_issues": ["Issue 1"],
        "recurrent_topics": ["Topic 1"],
        "strong_opinions": ["Opinion 1"],
        "tone_and_sentiment": "Calm",
    }
    corpus_stats = {
        "conversation_count": 1,
        "conversation_types": ["Interview"],
        "total_length": "5m",
        "total_speaking_time": "4m",
        "date_range": [],
        "source_analysis_ids": [1],
    }

    prompt = build_aggregate_prompt(
        conversations=conversations,
        current_aggregate=None,
        user_profile=user_profile,
        corpus_stats=corpus_stats,
        synthesis_type="full",
    )

    # Verify manual override profile block exists in prompt
    assert "## Current user-edited profile (MANUAL OVERRIDES — respect these edits/feedback)" in prompt
    assert "I am Bob." in prompt
    assert "Issue 1" in prompt

    # Verify comment override is included in prompt
    assert "- User comment/missing context override: Note: COSTANTINO WAS IN A RUSH" in prompt
    
    # Verify instruction override is present
    assert "Respect any manual overrides provided in the 'Current user-edited profile'" in prompt
