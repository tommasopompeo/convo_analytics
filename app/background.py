"""Background transcription job.

Runs off the request path (via FastAPI BackgroundTasks, executed in Starlette's
threadpool since `transcribe` is a blocking sync call). Opens its OWN SQLite
connection because it runs in a worker thread. The entire job is wrapped so any
failure is recorded as status='failed' with a sanitized message — otherwise the
exception would be swallowed and the Transcribing spinner would hang forever.
"""
from __future__ import annotations

from . import deepgram_client
from .db import get_conn, utc_now_iso


def run_transcription(audio_file_id: int) -> None:
    """Transcribe one uploaded file and persist transcript + utterances + speakers."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT path, single_sided FROM audio_files WHERE id = ?", (audio_file_id,)
        ).fetchone()
        if row is None:
            return
        audio_path = row["path"]
        single_sided = bool(row["single_sided"])

        conn.execute(
            "UPDATE audio_files SET transcription_status='transcribing', transcription_error=NULL WHERE id=?",
            (audio_file_id,),
        )
        conn.commit()

        # Deepgram occasionally returns a silently-partial transcript on a long
        # upload (see deepgram_client._guard_partial). It's transient, so retry
        # a couple of times before giving up rather than persisting lossy data.
        for attempt in range(3):
            try:
                result = deepgram_client.transcribe(audio_path, single_sided=single_sided)
                break
            except deepgram_client.PartialTranscriptError:
                if attempt == 2:
                    raise

        # Persist everything in one transaction.
        cur = conn.execute(
            "INSERT INTO transcripts (audio_file_id, raw_deepgram_json, plain_text, created_at) VALUES (?, ?, ?, ?)",
            (audio_file_id, result["raw_json"], result["plain_text"], utc_now_iso()),
        )
        transcript_id = cur.lastrowid

        utterances = result["utterances"]
        if utterances:
            conn.executemany(
                "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) VALUES (?, ?, ?, ?, ?)",
                [
                    (transcript_id, u["speaker_label"], u["start_sec"], u["end_sec"], u["text"])
                    for u in utterances
                ],
            )
            for label in sorted({u["speaker_label"] for u in utterances}):
                conn.execute(
                    "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) VALUES (?, ?, 0, NULL)",
                    (transcript_id, label),
                )

        conn.execute(
            "UPDATE audio_files SET transcription_status='done', duration_sec=? WHERE id=?",
            (result["duration_sec"], audio_file_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — must catch all so status is recorded
        conn.rollback()
        # str(exc) does not contain the API key (it lives in a request header,
        # never in messages); keep it short and never log/print it.
        message = f"{type(exc).__name__}: {exc}"[:500]
        try:
            conn.execute(
                "UPDATE audio_files SET transcription_status='failed', transcription_error=? WHERE id=?",
                (message, audio_file_id),
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
