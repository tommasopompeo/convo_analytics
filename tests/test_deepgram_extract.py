"""Version-robust parsing of a Deepgram response dict (no SDK / no network).

The fixture mirrors the nova-3 wire shape verified against deepgram-sdk 7.4.0:
metadata.duration, results.channels[].alternatives[].transcript,
results.utterances[] with {speaker, start, end, transcript}.
"""
import pytest

from app.deepgram_client import (
    PartialTranscriptError,
    _guard_partial,
    extract,
)

MOCK_RESPONSE = {
    "metadata": {"duration": 12.34},
    "results": {
        "channels": [{"alternatives": [{"transcript": "Ciao, hello there."}]}],
        "utterances": [
            {"speaker": 0, "start": 0.0, "end": 1.5, "transcript": "Ciao."},
            {"speaker": 1, "start": 1.5, "end": 3.0, "transcript": "Hello there."},
            {"speaker": None, "start": 3.0, "end": 3.4, "transcript": "Ok."},
        ],
    },
}


def test_extract_plain_text_and_duration():
    out = extract(MOCK_RESPONSE)
    assert out["plain_text"] == "Ciao, hello there."
    assert out["duration_sec"] == 12.34


def test_extract_normalizes_speaker_labels():
    out = extract(MOCK_RESPONSE)
    labels = [u["speaker_label"] for u in out["utterances"]]
    assert labels == ["speaker_0", "speaker_1", "speaker_0"]  # None -> speaker_0


def test_extract_utterance_fields():
    out = extract(MOCK_RESPONSE)
    first = out["utterances"][0]
    assert first["start_sec"] == 0.0 and first["end_sec"] == 1.5
    assert first["text"] == "Ciao."


def test_extract_handles_empty_response():
    out = extract({"metadata": {}, "results": {}})
    assert out["utterances"] == []
    assert out["plain_text"] == ""
    assert out["duration_sec"] is None


def _parsed(utts, duration):
    covered = sum(b - a for a, b in utts)
    return {
        "utterances": [
            {"speaker_label": "speaker_0", "start_sec": a, "end_sec": b, "text": "x"}
            for a, b in utts
        ],
        "duration_sec": duration,
        "covered_speech_sec": covered,
    }


def test_guard_passes_normal_transcript():
    # Continuous conversation with only short gaps — must not raise.
    _guard_partial(_parsed([(0, 30), (31, 60), (62, 100)], 100))


def test_guard_blocks_large_internal_gap():
    # A 133s empty stretch mid-conversation = dropped speech (the run-3 bug).
    with pytest.raises(PartialTranscriptError, match="silent gap"):
        _guard_partial(_parsed([(0, 30), (163, 200)], 2414))


def test_guard_blocks_low_coverage():
    # Words end early / most of the audio produced nothing.
    with pytest.raises(PartialTranscriptError, match="coverage"):
        _guard_partial(_parsed([(0, 10), (11, 20)], 200))


def test_guard_ignores_short_or_unknown():
    _guard_partial({"utterances": [], "duration_sec": 100, "covered_speech_sec": 0})
    _guard_partial(_parsed([(0, 10)], None))


def test_guard_skips_when_single_sided():
    # One-sided capture: a huge gap AND low coverage are expected (the other
    # person is off-mic). Both signals must be waived when single_sided=True.
    _guard_partial(_parsed([(0, 30), (163, 200)], 2414), single_sided=True)
    _guard_partial(_parsed([(0, 10), (11, 20)], 200), single_sided=True)
