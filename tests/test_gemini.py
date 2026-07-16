import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.gemini_client import analyze_conversation_async, synthesize_profile_async
from app.models import AnalysisOutput, AggregateOutput
from app.main import app
from app.db import SCHEMA, _migrate
from app.web import db_dep
from app.auth import hash_password
from app import config


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── Unit Tests ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_analyze_conversation_async_success():
    mock_json = """{
        "summary": "A positive summary of the meeting.",
        "key_topics": [{"topic": "Marketing", "salience": "high", "driven_by_speaker": "owner"}],
        "sentiment_arc": [],
        "overall_sentiment": {"owner": "positive", "conversation": "positive"},
        "pivot_points": [],
        "conversation_gaps": [],
        "follow_ups": {"questions": ["When is the next sync?"], "actions": []},
        "owner_insights": {"communication_style": "supportive", "notable_behaviors": []},
        "owner_profile_update": {
            "recurring_topics_add": [],
            "communication_style_notes": [],
            "goals_concerns_add": [],
            "archetype_signal": "The Marketer"
        }
    }"""

    mock_response = MagicMock()
    mock_response.text = mock_json

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=mock_response)

    with patch("google.generativeai.GenerativeModel", return_value=mock_model) as mock_class_init, \
         patch("google.generativeai.configure") as mock_configure, \
         patch("app.gemini_client.get_gemini_api_key", return_value="fake-api-key"):

        result = await analyze_conversation_async("some prompt")

        mock_configure.assert_called_once_with(api_key="fake-api-key")
        mock_class_init.assert_called_once_with(config.GEMINI_ANALYSIS_MODEL)
        mock_model.generate_content_async.assert_called_once()

        assert isinstance(result, AnalysisOutput)
        assert result.summary == "A positive summary of the meeting."
        assert result.owner_profile_update.archetype_signal == "The Marketer"


@pytest.mark.anyio
async def test_analyze_conversation_async_invalid_json():
    mock_response = MagicMock()
    mock_response.text = "invalid json string"

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=mock_response)

    with patch("google.generativeai.GenerativeModel", return_value=mock_model), \
         patch("google.generativeai.configure"), \
         patch("app.gemini_client.get_gemini_api_key", return_value="fake-api-key"):

        with pytest.raises(ValidationError):
            await analyze_conversation_async("some prompt")


# ── Integration Tests ─────────────────────────────────────────────────────────

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


def test_analyze_endpoint_integration(temp_db):
    """Test the end-to-end flow from calling the endpoint, mock Gemini API call,

    database insertions, and redirects.
    """
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
            (1, 1, "{}", "hello", "2026-07-14T00:00:00Z"),
        )
        # Seed utterances
        temp_db.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) VALUES (?, ?, ?, ?, ?)",
            (1, "speaker_0", 0.0, 5.0, "hello"),
        )
        # Seed speakers
        temp_db.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) VALUES (?, ?, ?, ?)",
            (1, "speaker_0", 1, "Alice"),
        )
        temp_db.commit()

        client = TestClient(app)

        # Login to obtain session cookies
        login_resp = client.post(
            "/login",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        assert login_resp.status_code == 303
        cookies = {"session_token": login_resp.cookies["session_token"]}

        # Mock response from Gemini client
        mock_analysis = AnalysisOutput.model_validate({
            "summary": "Meeting summary.",
            "overall_sentiment": {"owner": "positive", "conversation": "positive"},
            "follow_ups": {"questions": [], "actions": []},
            "owner_insights": {"communication_style": "direct", "notable_behaviors": []},
            "owner_profile_update": {
                "recurring_topics_add": ["Productivity"],
                "communication_style_notes": [],
                "goals_concerns_add": [],
                "archetype_signal": "The Builder"
            }
        })

        async def mock_analyze(*args, **kwargs):
            return mock_analysis

        with patch("app.gemini_client.analyze_conversation_async", new=mock_analyze):
            response = client.post(
                "/transcripts/1/analyze",
                cookies=cookies,
                follow_redirects=False,
            )

            assert response.status_code == 303
            assert response.headers["location"] == "/analyses/1"

            # Check database for inserted analysis record
            analysis_row = temp_db.execute(
                "SELECT * FROM analyses WHERE transcript_id = 1"
            ).fetchone()
            assert analysis_row is not None
            assert "Meeting summary." in analysis_row["llm_output_json"]

            # Check owner profile update
            profile_row = temp_db.execute(
                "SELECT * FROM owner_profile WHERE user_id = 1"
            ).fetchone()
            assert profile_row is not None
            assert "Productivity" in profile_row["profile_data"]

    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_synthesize_profile_async_success():
    mock_json = """{
        "who_i_am": "A synthesized portrait of Alice.",
        "current_issues": [],
        "recurrent_topics": [],
        "strong_opinions": [],
        "tone_and_sentiment": "calm",
        "corpus_meta": {"conversation_count": 1, "synthesis_type": "full", "generated_at": "", "source_analysis_ids": []}
    }"""

    mock_response = MagicMock()
    mock_response.text = mock_json

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=mock_response)

    with patch("google.generativeai.GenerativeModel", return_value=mock_model) as mock_class_init, \
         patch("google.generativeai.configure") as mock_configure, \
         patch("app.gemini_client.get_gemini_api_key", return_value="fake-api-key"):

        result = await synthesize_profile_async("some prompt")

        mock_configure.assert_called_once_with(api_key="fake-api-key")
        mock_class_init.assert_called_once_with(config.GEMINI_SYNTHESIS_MODEL)
        mock_model.generate_content_async.assert_called_once()

        assert isinstance(result, AggregateOutput)
        assert result.who_i_am == "A synthesized portrait of Alice."


def test_refresh_profile_endpoints_integration(temp_db):
    """Test the GET /profile/refresh loads the spinner page and POST /profile/refresh

    makes the mock Gemini API call and stores the aggregate insight.
    """
    app.dependency_overrides[db_dep] = lambda: temp_db
    try:
        # Seed user
        temp_db.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (1, "bob", hash_password("password123"), "2026-07-14T00:00:00Z"),
        )
        # Seed knowledge_entry
        temp_db.execute(
            "INSERT INTO knowledge_entries (id, user_id, path, filename, uploaded_at, duration_sec, transcription_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "test.mp3", "test.mp3", "2026-07-14T00:00:00Z", 10.0, "done"),
        )
        # Seed transcript
        temp_db.execute(
            "INSERT INTO transcripts (id, audio_file_id, raw_deepgram_json, plain_text, created_at) VALUES (?, ?, ?, ?, ?)",
            (1, 1, "{}", "hello", "2026-07-14T00:00:00Z"),
        )
        # Seed utterances
        temp_db.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) VALUES (?, ?, ?, ?, ?)",
            (1, "speaker_0", 0.0, 5.0, "hello"),
        )
        # Seed speakers
        temp_db.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) VALUES (?, ?, ?, ?)",
            (1, "speaker_0", 1, "Bob"),
        )
        # Seed analyses
        temp_db.execute(
            "INSERT INTO analyses (id, transcript_id, metrics_json, llm_output_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (1, 1, "{}", '{"summary": "test", "overall_sentiment": {}, "follow_ups": {}, "owner_insights": {}, "owner_profile_update": {}}', "2026-07-14T00:00:00Z")
        )
        temp_db.commit()

        client = TestClient(app)

        # Login
        login_resp = client.post(
            "/login",
            data={"username": "bob", "password": "password123"},
            follow_redirects=False,
        )
        cookies = {"session_token": login_resp.cookies["session_token"]}

        # GET /profile/refresh should succeed and render the template since analyses exist
        response = client.get("/profile/refresh", cookies=cookies)
        assert response.status_code == 200
        assert "Synthesizing your profile" in response.text
        assert "spinner" in response.text

        # Mock response from synthesize_profile_async
        mock_aggregate = AggregateOutput.model_validate({
            "who_i_am": "A synthesized portrait of Bob.",
            "current_issues": [],
            "recurrent_topics": [],
            "strong_opinions": [],
            "tone_and_sentiment": "calm",
            "corpus_meta": {"conversation_count": 1, "synthesis_type": "full", "generated_at": "", "source_analysis_ids": [1]}
        })

        async def mock_synthesize(*args, **kwargs):
            return mock_aggregate

        with patch("app.gemini_client.synthesize_profile_async", new=mock_synthesize):
            # POST /profile/refresh should run synthesis and return success
            response = client.post(
                "/profile/refresh",
                cookies=cookies,
            )
            assert response.status_code == 200
            assert response.json() == {"status": "success"}

            # Check database for inserted aggregate insight
            agg_row = temp_db.execute(
                "SELECT * FROM aggregate_insight WHERE user_id = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert agg_row is not None
            assert agg_row["synthesis_type"] == "full"
            assert agg_row["conversation_count"] == 1
            assert "A synthesized portrait of Bob." in agg_row["insight_json"]

    finally:
        app.dependency_overrides.clear()
