import sqlite3
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.db import SCHEMA, _migrate
from app.main import app
from app.web import db_dep


def test_password_hashing():
    """Verify that passwords can be hashed and correctly verified."""
    pw = "secret-pass"
    hashed = auth.hash_password(pw)
    assert hashed != pw
    assert auth.verify_password(pw, hashed)
    assert not auth.verify_password("wrong-pass", hashed)


def test_jwt_tokens(monkeypatch):
    """Verify encoding and decoding of JWT tokens using config signing keys."""
    monkeypatch.setattr(auth.config, "JWT_SECRET", "test-secret-signing-key")
    payload = {"sub": "testuser", "custom": "data"}
    token = auth.create_access_token(payload)
    decoded = auth.decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "testuser"
    assert decoded["custom"] == "data"

    # Test invalid token
    assert auth.decode_access_token("invalid-token-string") is None


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


def test_auth_routes(temp_db):
    """Integration test verifying unauthenticated redirection, registration, login, session cookies, and logout."""
    # Override db dependency
    def override_db():
        yield temp_db

    app.dependency_overrides[db_dep] = override_db
    try:
        client = TestClient(app)

        # Visit home page, should redirect to /login (because UnauthenticatedException)
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

        # Register user
        response = client.post(
            "/register",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

        # Try to register same user again
        response = client.post(
            "/register",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "already taken" in response.text

        # Login
        response = client.post(
            "/login",
            data={"username": "alice", "password": "password123"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert "session_token" in response.cookies

        # Visit home page with session token
        response = client.get(
            "/", cookies={"session_token": response.cookies["session_token"]}
        )
        assert response.status_code == 200
        assert "Ciao, alice" in response.text

        # Logout
        response = client.post("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        # Cookie should be cleared
        assert (
            response.cookies.get("session_token") is None
            or response.cookies["session_token"] == ""
        )

    finally:
        app.dependency_overrides.clear()
