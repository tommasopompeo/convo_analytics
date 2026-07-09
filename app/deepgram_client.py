"""Deepgram integration — the SOLE module that imports the deepgram SDK.

Confirmed against deepgram-sdk 7.4.0 (Fern-generated v7 line):
    client.listen.v1.media.transcribe_file(request=<bytes>, **options,
                                            request_options=RequestOptions(...))
returns a typed `ListenV1Response` pydantic model.

Parsing reads the response as a plain dict (`model_dump(mode="json")`) using the
documented wire keys (verified to have NO field aliases) so the extraction logic
is insulated from SDK attribute renames and is unit-testable with a fixture dict
via `extract()` — no network or SDK object required.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config

# Generous ceiling for long recordings (SDK default HTTP timeout is short).
_TIMEOUT_SEC = 1800  # 30 min — covers 1-hour files on slow connections

# Deepgram occasionally returns HTTP 200 with a SILENTLY partial transcript on a
# long upload (a transient server-side issue): full metadata.duration, last word
# near the end, but a multi-minute stretch in the middle transcribed as nothing.
# On a continuous conversation that empty stretch is physically implausible, so
# we treat an oversized internal gap (or an implausibly low coverage ratio) as a
# failure and let the caller retry, rather than persisting lossy data as 'done'.
# Baselines from real runs of this corpus: good=83% coverage / 16s max gap;
# the observed bad run = 73% / 133s gap. Thresholds sit well between them.
_MAX_INTERNAL_GAP_SEC = 60.0   # no 1-on-1 goes silent for a full minute mid-flow
_MIN_COVERAGE_RATIO = 0.5      # covered speech vs. audio duration


class PartialTranscriptError(RuntimeError):
    """Deepgram returned a 200 but dropped a large span of speech — retryable."""

# Fixed transcription options (spec §3). Native sentiment/topics/intents stay
# OFF (analysis happens in Claude). `keyterm` is added only when configured.
_FIXED_OPTIONS: dict[str, Any] = {
    "model": "nova-3",
    "diarize": True,
    "smart_format": True,
    "punctuate": True,
    "paragraphs": True,
    "utterances": True,
    "filler_words": True,
}


def transcribe(path: Path, *, single_sided: bool = False) -> dict[str, Any]:
    """Transcribe an audio file and return a parsed result.

    Returns a dict: {raw_json: str, plain_text: str, duration_sec: float|None,
    utterances: [{speaker_label, start_sec, end_sec, text}, ...]}.
    Raises on transport/API errors; the caller records the failure.

    `single_sided` means only the owner's microphone was captured (e.g. a call
    heard through headphones): long silences and low coverage are then EXPECTED —
    they're the other person speaking off-mic — so the partial-transcript guard is
    skipped for these files (see `_guard_partial`).
    """
    # Imported lazily so importing this module (e.g. in tests) never requires
    # the SDK or an API key to be present.
    from deepgram import DeepgramClient
    from deepgram.core.request_options import RequestOptions

    # Shrink the upload to a speech-optimized 16 kHz mono Opus stream. Deepgram
    # downsamples to 16 kHz internally anyway, so transcription quality is
    # unaffected, but a long recording's upload becomes ~5x smaller/faster —
    # avoiding Deepgram's server-side "Request upload timeout" on big files.
    audio_bytes = _prepare_upload(path)

    options = dict(_FIXED_OPTIONS)
    options["language"] = config.LANGUAGE
    if config.KEYTERMS:
        options["keyterm"] = list(config.KEYTERMS)

    client = DeepgramClient(api_key=config.get_deepgram_api_key())
    resp = client.listen.v1.media.transcribe_file(
        request=audio_bytes,
        request_options=RequestOptions(timeout_in_seconds=_TIMEOUT_SEC),
        **options,
    )

    raw = resp.model_dump(mode="json")
    parsed = extract(raw)
    _guard_partial(parsed, single_sided=single_sided)
    parsed["raw_json"] = json.dumps(raw, ensure_ascii=False)
    return parsed


def _guard_partial(parsed: dict[str, Any], *, single_sided: bool = False) -> None:
    """Raise PartialTranscriptError if the transcript looks silently truncated.

    Two independent signals, either of which fails the transcript:
      * an internal gap between consecutive utterances longer than
        `_MAX_INTERNAL_GAP_SEC` — a dropped chunk mid-conversation;
      * covered speech below `_MIN_COVERAGE_RATIO` of the audio duration.
    Short clips (no utterances, or duration unknown) are left alone.

    For a `single_sided` recording both signals fire by design — the other
    participant is off-mic, so long silences and low coverage are the norm and are
    indistinguishable from genuine truncation by this heuristic. The guard is
    therefore skipped; we still return early on an empty/durationless transcript.
    """
    utts = parsed.get("utterances") or []
    duration = parsed.get("duration_sec")
    if not utts or not duration:
        return
    if single_sided:
        return

    max_gap = max(
        (b["start_sec"] - a["end_sec"] for a, b in zip(utts, utts[1:])),
        default=0.0,
    )
    if max_gap > _MAX_INTERNAL_GAP_SEC:
        raise PartialTranscriptError(
            f"Dropped speech detected: {max_gap:.0f}s silent gap mid-transcript "
            f"(limit {_MAX_INTERNAL_GAP_SEC:.0f}s). Deepgram returned a partial "
            f"result; retry."
        )

    covered = parsed.get("covered_speech_sec") or 0.0
    if covered / duration < _MIN_COVERAGE_RATIO:
        raise PartialTranscriptError(
            f"Low transcript coverage: {covered:.0f}s of {duration:.0f}s "
            f"({100 * covered / duration:.0f}%). Deepgram returned a partial "
            f"result; retry."
        )


def _prepare_upload(path) -> bytes:
    """Return the bytes to upload — transcoded to 16 kHz mono Opus if ffmpeg is
    available, else the original file bytes (graceful fallback).

    16 kHz mono is the standard ASR working format; Opus at 24 kbps keeps speech
    crisp while cutting a 40-minute recording from tens of MB to a few MB.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    src = Path(path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return src.read_bytes()

    fd, tmp = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(src),
             "-ac", "1", "-ar", "16000",
             "-c:a", "libopus", "-b:a", "24k",
             tmp],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
        )
        data = Path(tmp).read_bytes()
        return data if data else src.read_bytes()
    except Exception:
        # ffmpeg missing codec / bad input / timeout — fall back to the original.
        return src.read_bytes()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def extract(raw: dict[str, Any]) -> dict[str, Any]:
    """Pure parser: raw Deepgram response dict -> normalized fields.

    Version-robust (reads documented wire keys) and side-effect free, so tests
    can exercise it with a fixture dict. Speaker integers are normalized to a
    stable string label ("speaker_0", ...) used everywhere else in the app.
    """
    results = raw.get("results") or {}
    metadata = raw.get("metadata") or {}

    plain_text = ""
    channels = results.get("channels") or []
    if channels:
        alternatives = channels[0].get("alternatives") or []
        if alternatives:
            plain_text = alternatives[0].get("transcript") or ""

    utterances: list[dict[str, Any]] = []
    for u in results.get("utterances") or []:
        speaker = u.get("speaker")
        speaker_idx = speaker if isinstance(speaker, int) else 0
        utterances.append({
            "speaker_label": f"speaker_{speaker_idx}",
            "start_sec": float(u.get("start") or 0.0),
            "end_sec": float(u.get("end") or 0.0),
            "text": (u.get("transcript") or "").strip(),
        })

    duration = metadata.get("duration")
    covered = sum(u["end_sec"] - u["start_sec"] for u in utterances)
    return {
        "plain_text": plain_text,
        "duration_sec": float(duration) if duration is not None else None,
        "covered_speech_sec": covered,
        "utterances": utterances,
    }
