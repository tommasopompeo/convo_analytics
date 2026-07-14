import os
import pytest
from app import config


def test_get_deepgram_api_key(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram-key")
    assert config.get_deepgram_api_key() == "test-deepgram-key"

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY is not set"):
        config.get_deepgram_api_key()


def test_get_gemini_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    assert config.get_gemini_api_key() == "test-gemini-key"

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        config.get_gemini_api_key()
