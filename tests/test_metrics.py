"""Deterministic metrics: normal case, single speaker, empty, overlap, guards."""
from app.metrics import compute_metrics


def _u(spk, start, end, text):
    return {"speaker_label": spk, "start_sec": start, "end_sec": end, "text": text}


def test_two_speaker_basic():
    utts = [
        _u("speaker_0", 0.0, 2.0, "Hello there."),
        _u("speaker_1", 2.0, 5.0, "Hi how are you?"),
        _u("speaker_0", 5.0, 6.0, "Good."),
    ]
    m = compute_metrics(utts, duration_sec=6.0)

    assert m["conversation"]["turn_count"] == 3
    assert m["conversation"]["speaker_switch_count"] == 2
    assert m["conversation"]["total_duration_sec"] == 6.0

    by = {s["speaker_label"]: s for s in m["speakers"]}
    assert by["speaker_0"]["talk_time_sec"] == 3.0
    assert by["speaker_0"]["talk_time_pct"] == 50.0
    assert by["speaker_1"]["talk_time_pct"] == 50.0
    assert by["speaker_0"]["turns"] == 2
    assert by["speaker_1"]["turns"] == 1
    assert by["speaker_1"]["question_count"] == 1
    assert by["speaker_0"]["longest_monologue_sec"] == 2.0
    assert by["speaker_1"]["longest_monologue_sec"] == 3.0
    # WPM: speaker_1 has 4 words over 3s => 80 wpm
    assert by["speaker_1"]["wpm"] == 80.0


def test_single_speaker():
    m = compute_metrics([_u("speaker_0", 0.0, 4.0, "one two three")], duration_sec=4.0)
    s = m["speakers"][0]
    assert s["talk_time_pct"] == 100.0
    assert m["conversation"]["turn_count"] == 1
    assert m["conversation"]["speaker_switch_count"] == 0


def test_empty_returns_warning_not_crash():
    m = compute_metrics([], duration_sec=10.0)
    assert m["speakers"] == []
    assert m["warnings"]
    assert m["conversation"]["turn_count"] == 0


def test_zero_duration_guards_division():
    m = compute_metrics([_u("speaker_0", 2.0, 2.0, "hi")], duration_sec=0.0)
    s = m["speakers"][0]
    assert s["wpm"] is None           # talk time 0 -> no WPM, no crash
    assert s["talk_time_pct"] is None  # total speech 0 -> guarded


def test_overlap_recorded_and_talktime_overlap_safe():
    utts = [
        _u("speaker_0", 0.0, 5.0, "aaa bbb ccc"),
        _u("speaker_1", 3.0, 8.0, "ddd eee fff"),  # overlaps speaker_0
    ]
    m = compute_metrics(utts, duration_sec=8.0)
    # total_speech = 5 + 5 = 10, each 50% (can't exceed 100 under overlap)
    by = {s["speaker_label"]: s for s in m["speakers"]}
    assert by["speaker_0"]["talk_time_pct"] == 50.0
    overlaps = [g for g in m["conversation"]["inter_utterance_gaps"] if g["type"] == "overlap"]
    assert overlaps and overlaps[0]["gap_sec"] == -2.0


def test_long_pause_flagged():
    utts = [
        _u("speaker_0", 0.0, 1.0, "hi"),
        _u("speaker_0", 10.0, 11.0, "still there"),  # 9s gap
    ]
    m = compute_metrics(utts, duration_sec=11.0)
    assert m["conversation"]["long_pause_count"] == 1
