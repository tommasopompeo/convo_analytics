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
            "SELECT path, single_sided FROM knowledge_entries WHERE id = ?", (audio_file_id,)
        ).fetchone()
        if row is None:
            return
        audio_path = row["path"]
        single_sided = bool(row["single_sided"])

        conn.execute(
            "UPDATE knowledge_entries SET transcription_status='transcribing', transcription_error=NULL WHERE id=?",
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
            "UPDATE knowledge_entries SET transcription_status='done', duration_sec=? WHERE id=?",
            (result["duration_sec"], audio_file_id),
        )
        conn.commit()

        from .web import export_transcript_to_file
        export_transcript_to_file(conn, transcript_id)
    except Exception as exc:  # noqa: BLE001 — must catch all so status is recorded
        conn.rollback()
        # str(exc) does not contain the API key (it lives in a request header,
        # never in messages); keep it short and never log/print it.
        message = f"{type(exc).__name__}: {exc}"[:500]
        try:
            conn.execute(
                "UPDATE knowledge_entries SET transcription_status='failed', transcription_error=? WHERE id=?",
                (message, audio_file_id),
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


def run_analysis_background(file_id: int, transcript_id: int, user_id: int) -> None:
    """Run LLM analysis on a text/document knowledge entry off the request path."""
    import asyncio
    import json
    from . import gemini_client, metrics as metrics_mod, profile_merge, prompt_builder
    from .web import load_current_profile, load_utterances

    conn = get_conn()
    try:
        # 1. Update status to 'transcribing' (which means analyzing for text/docs)
        conn.execute(
            "UPDATE knowledge_entries SET transcription_status='transcribing', transcription_error=NULL WHERE id=?",
            (file_id,),
        )
        conn.commit()

        # 2. Load necessary data to build prompt
        meta = conn.execute(
            """
            SELECT t.id, t.audio_file_id, a.conversation_type, a.owner_role,
                   a.objective, a.context_note, a.single_sided, a.recorded_date
              FROM transcripts t JOIN knowledge_entries a ON a.id = t.audio_file_id
             WHERE t.id = ? AND a.user_id = ?
            """,
            (transcript_id, user_id),
        ).fetchone()

        utterances = load_utterances(conn, transcript_id)
        metrics = metrics_mod.compute_metrics(utterances, None)
        speaker_labels = sorted({u["speaker_label"] for u in utterances})

        # Fetch actual speaker mapping to pass to build_prompt
        speaker_rows = conn.execute(
            "SELECT speaker_label, is_owner, local_name FROM speakers WHERE transcript_id=?",
            (transcript_id,),
        ).fetchall()
        speaker_mapping = {
            r["speaker_label"]: {
                "is_owner": bool(r["is_owner"]),
                "local_name": r["local_name"],
            }
            for r in speaker_rows
        }

        owner_label = None
        for label, info in speaker_mapping.items():
            if info["is_owner"]:
                owner_label = label
                break
        if owner_label is None:
            owner_label = "speaker_1"

        prompt = prompt_builder.build_prompt(
            metadata=dict(meta),
            owner_label=owner_label,
            speaker_labels=speaker_labels,
            metrics=metrics,
            utterances=utterances,
            profile_json=load_current_profile(conn, user_id),
            single_sided=False,
            speaker_mapping=speaker_mapping,
        )

        # 3. Call Gemini
        analysis = asyncio.run(gemini_client.analyze_conversation_async(prompt))

        # 4. Save analysis
        cur = conn.execute(
            "INSERT INTO analyses (transcript_id, metrics_json, llm_output_json, created_at) VALUES (?, ?, ?, ?)",
            (
                transcript_id,
                json.dumps(metrics, ensure_ascii=False),
                analysis.model_dump_json(by_alias=True),
                utc_now_iso(),
            ),
        )
        analysis_id = cur.lastrowid

        # 5. Merge profile
        current = load_current_profile(conn, user_id)
        merged = profile_merge.merge(
            current, analysis.owner_profile_update.model_dump(), analysis_id
        )
        if merged is not current:
            conn.execute(
                "INSERT INTO owner_profile (user_id, profile_data, updated_at) VALUES (?, ?, ?)",
                (
                    user_id,
                    json.dumps(merged, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )

        # 6. Update transcription status
        conn.execute(
            "UPDATE knowledge_entries SET transcription_status='done' WHERE id=?",
            (file_id,),
        )
        conn.commit()

    except Exception as exc:
        conn.rollback()
        message = f"{type(exc).__name__}: {exc}"[:500]
        try:
            conn.execute(
                "UPDATE knowledge_entries SET transcription_status='failed', transcription_error=? WHERE id=?",
                (message, file_id),
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
