"""Deterministic conversation metrics (spec §7) — pure Python, never an LLM.

Input is a list of utterance dicts {speaker_label, start_sec, end_sec, text}.
Output is a JSON-serializable dict displayed in the UI and injected into the
Claude prompt as ground truth. Every division is guarded; single-speaker,
empty, zero-duration, and overlapping-diarization inputs all produce a
well-formed result rather than raising.
"""
from __future__ import annotations

from typing import Any, Optional

from . import config
from .fillers import count_fillers


def _dur(u: dict[str, Any]) -> float:
    """Clamped utterance duration (negatives/zeros from skew -> 0)."""
    return max(0.0, float(u["end_sec"]) - float(u["start_sec"]))


def _word_count(text: str) -> int:
    """Whitespace-split word count — correct for IT + EN (both space-delimited)."""
    return len(text.split())


def _pct(part: float, whole: float) -> Optional[float]:
    """Percentage guarded against a zero denominator."""
    if whole <= 0:
        return None
    return round(part / whole * 100, 1)


def _turns(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive same-speaker utterances into turns (in start order).

    A turn = a maximal run of consecutive utterances by one speaker. Each turn
    carries its speaker, span (first start -> last end), and word count.
    """
    turns: list[dict[str, Any]] = []
    for u in utterances:
        if turns and turns[-1]["speaker_label"] == u["speaker_label"]:
            t = turns[-1]
            t["end_sec"] = float(u["end_sec"])
            t["word_count"] += _word_count(u["text"])
            t["utterance_count"] += 1
        else:
            turns.append({
                "speaker_label": u["speaker_label"],
                "start_sec": float(u["start_sec"]),
                "end_sec": float(u["end_sec"]),
                "word_count": _word_count(u["text"]),
                "utterance_count": 1,
            })
    return turns


def compute_metrics(
    utterances: list[dict[str, Any]],
    duration_sec: Optional[float] = None,
) -> dict[str, Any]:
    """Compute per-speaker and conversation-level metrics.

    `duration_sec` is the wall-clock media duration (from Deepgram metadata),
    surfaced separately so silence-vs-speech is visible. Talk-time percentages
    use the sum of speaker talk-time as the denominator (overlap-safe; matches
    the spec's "sum of (end-start) / total" and sums to ~100%).
    """
    utterances = sorted(utterances, key=lambda u: (float(u["start_sec"]), float(u["end_sec"])))
    warnings: list[str] = []

    if not utterances:
        warnings.append("No speech detected in this recording.")
        return {
            "warnings": warnings,
            "conversation": {
                "total_duration_sec": round(duration_sec, 2) if duration_sec else 0.0,
                "total_speech_sec": 0.0,
                "turn_count": 0,
                "speaker_switch_count": 0,
                "long_pause_count": 0,
                "inter_utterance_gaps": [],
            },
            "speakers": [],
        }

    turns = _turns(utterances)

    # ── Per-speaker aggregates ─────────────────────────────────────────────
    labels = sorted({u["speaker_label"] for u in utterances})
    total_speech = sum(_dur(u) for u in utterances)
    total_words = sum(_word_count(u["text"]) for u in utterances)

    speakers: list[dict[str, Any]] = []
    for label in labels:
        u_of = [u for u in utterances if u["speaker_label"] == label]
        t_of = [t for t in turns if t["speaker_label"] == label]

        talk_time = sum(_dur(u) for u in u_of)
        words = sum(_word_count(u["text"]) for u in u_of)
        n_turns = len(t_of)
        fillers = sum(count_fillers(u["text"]) for u in u_of)

        talk_min = talk_time / 60.0
        wpm = round(words / talk_min, 1) if talk_min > 0 else None
        avg_turn_sec = round(talk_time / n_turns, 1) if n_turns else None
        avg_turn_words = round(words / n_turns, 1) if n_turns else None
        longest_monologue = round(max((t["end_sec"] - t["start_sec"] for t in t_of), default=0.0), 1)
        question_count = sum(1 for u in u_of if u["text"].strip().endswith("?"))
        filler_rate = round(fillers / words, 3) if words else None

        speakers.append({
            "speaker_label": label,
            "talk_time_sec": round(talk_time, 1),
            "talk_time_pct": _pct(talk_time, total_speech),
            "word_count": words,
            "word_count_pct": _pct(words, total_words),
            "wpm": wpm,
            "turns": n_turns,
            "avg_turn_length_sec": avg_turn_sec,
            "avg_turn_length_words": avg_turn_words,
            "longest_monologue_sec": longest_monologue,
            "question_count": question_count,  # approximate (?-terminated)
            "filler_count": fillers,
            "filler_rate": filler_rate,  # approximate
        })

    # ── Conversation-level ─────────────────────────────────────────────────
    gaps: list[dict[str, Any]] = []
    long_pause_count = 0
    for prev, nxt in zip(utterances, utterances[1:]):
        gap = round(float(nxt["start_sec"]) - float(prev["end_sec"]), 2)
        is_overlap = gap < 0
        is_long = gap > config.LONG_PAUSE_SEC
        if is_long:
            long_pause_count += 1
        if is_long or is_overlap:
            gaps.append({
                "after_end_sec": round(float(prev["end_sec"]), 2),
                "gap_sec": gap,
                "type": "overlap" if is_overlap else "long_pause",
            })

    total_dur = duration_sec if duration_sec is not None else (
        max(float(u["end_sec"]) for u in utterances) - min(float(u["start_sec"]) for u in utterances)
    )

    conversation = {
        "total_duration_sec": round(total_dur, 2),
        "total_speech_sec": round(total_speech, 1),
        "turn_count": len(turns),
        "speaker_switch_count": max(0, len(turns) - 1),
        "long_pause_count": long_pause_count,
        "inter_utterance_gaps": gaps,
    }

    return {"warnings": warnings, "conversation": conversation, "speakers": speakers}
