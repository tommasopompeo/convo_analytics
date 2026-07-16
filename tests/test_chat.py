import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.main import app
from app.db import SCHEMA, _migrate
from app.web import db_dep
from app.auth import hash_password

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
def temp_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    _migrate(conn)
    yield conn
    conn.close()

def test_chat_routes(temp_db):
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

        # 1. Test GET /chat (no sessions)
        resp = client.get("/chat", cookies=cookies)
        assert resp.status_code == 200
        assert "Discover more about you" in resp.text
        assert "New Chat" in resp.text

        # Mock gemini client call
        mock_gemini = AsyncMock(return_value={
            "content": "Hello, I am your digital twin.",
            "sources": [{"title": "Google", "url": "https://google.com"}]
        })

        with patch("app.routes.chat.chat_with_persona_async", mock_gemini):
            # 2. Test POST /chat/message (creating a new session)
            post_resp = client.post(
                "/chat/message",
                data={"message": "Hello twin! What is my name?", "search_enabled": "true"},
                cookies=cookies
            )
            assert post_resp.status_code == 200
            data = post_resp.json()
            assert data["status"] == "success"
            assert data["session_id"] is not None
            assert data["ai_message"] == "Hello, I am your digital twin."
            assert len(data["sources"]) == 1
            assert data["sources"][0]["title"] == "Google"

            session_id = data["session_id"]

            # 3. Test GET /chat/{session_id}
            resp_session = client.get(f"/chat/{session_id}", cookies=cookies)
            assert resp_session.status_code == 200
            assert "Hello twin!" in resp_session.text
            assert "Hello, I am your digital twin." in resp_session.text

            # 4. Test POST /chat/message (sending message to existing session)
            post_resp2 = client.post(
                "/chat/message",
                data={"message": "Good to meet you.", "session_id": str(session_id), "search_enabled": "false"},
                cookies=cookies
            )
            assert post_resp2.status_code == 200
            data2 = post_resp2.json()
            assert data2["session_id"] == session_id

            # Verify both user message and twin message were stored
            messages_db = temp_db.execute(
                "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,)
            ).fetchall()
            # 4 messages: User message 1, AI message 1, User message 2, AI message 2
            assert len(messages_db) == 4
            assert messages_db[0]["role"] == "user"
            assert messages_db[1]["role"] == "model"
            assert messages_db[2]["role"] == "user"

            # 5. Access check: log in as another user, make sure we can't load Alice's session
            temp_db.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (2, "bob", hash_password("password456"), "2026-07-14T00:00:00Z"),
            )
            temp_db.commit()
            
            login_resp_bob = client.post(
                "/login",
                data={"username": "bob", "password": "password456"},
                follow_redirects=False,
            )
            cookies_bob = {"session_token": login_resp_bob.cookies["session_token"]}

            # bob tries to access alice's session, gets redirected to /chat
            resp_bob = client.get(f"/chat/{session_id}", cookies=cookies_bob, follow_redirects=False)
            assert resp_bob.status_code == 303
            assert resp_bob.headers["location"] == "/chat"

            # bob tries to post message to alice's session, gets 403 (unauthorized access)
            post_bob = client.post(
                "/chat/message",
                data={"message": "Hey", "session_id": str(session_id)},
                cookies=cookies_bob
            )
            assert post_bob.status_code == 403

    finally:
        app.dependency_overrides.clear()
