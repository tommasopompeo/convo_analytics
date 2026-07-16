"""Home / library, upload, categorization, and transcription-status routes."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import config
from ..background import run_transcription
from ..db import utc_now_iso
from ..web import CONVERSATION_TYPES, OWNER_ROLES, db_dep, get_current_user, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn=Depends(db_dep), user=Depends(get_current_user)):
    rows = conn.execute(
        """
        SELECT a.id, a.filename, a.uploaded_at, a.conversation_type,
               a.transcription_status,
               (SELECT t.id FROM transcripts t WHERE t.audio_file_id = a.id
                ORDER BY t.id DESC LIMIT 1) AS transcript_id,
               (SELECT an.id FROM analyses an
                  JOIN transcripts t2 ON t2.id = an.transcript_id
                 WHERE t2.audio_file_id = a.id
                 ORDER BY an.id DESC LIMIT 1) AS analysis_id
          FROM knowledge_entries a
         WHERE a.user_id = ?
         ORDER BY a.id DESC
        """,
        (user["id"],),
    ).fetchall()
    recordings = [dict(r) for r in rows]
    return templates.TemplateResponse(
        request,
        "home.html",
        {"recordings": recordings, "user": user, "nav": "recordings"},
    )


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, user=Depends(get_current_user)):
    from datetime import date
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"user": user, "today": date.today().isoformat()},
    )


@router.post("/upload")
async def upload_file(
    request: Request,
    upload_type: str = Form("audio"),
    recorded_date: str = Form(""),
    audio: Optional[UploadFile] = None,
    doc: Optional[UploadFile] = None,
    plain_text: Optional[str] = Form(None),
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    from datetime import date
    today_str = date.today().isoformat()
    if not recorded_date:
        recorded_date = today_str

    if upload_type == "audio":
        if not audio or not audio.filename:
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"error": "Please select an audio file.", "user": user, "today": today_str},
                status_code=400,
            )
        ext = Path(audio.filename).suffix.lower()
        if ext not in config.ALLOWED_EXT:
            allowed = ", ".join(sorted(config.ALLOWED_EXT))
            return templates.TemplateResponse(
                request,
                "upload.html",
                {
                    "error": f"Unsupported file type '{ext or 'unknown'}'. Allowed: {allowed}.",
                    "user": user,
                    "today": today_str,
                },
                status_code=400,
            )

        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = config.AUDIO_DIR / stored_name
        with dest.open("wb") as out:
            shutil.copyfileobj(audio.file, out)

        if dest.stat().st_size > config.MAX_UPLOAD_BYTES:
            dest.unlink(missing_ok=True)
            limit_mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"error": f"File is too large (limit {limit_mb} MB).", "user": user, "today": today_str},
                status_code=400,
            )

        cur = conn.execute(
            "INSERT INTO knowledge_entries (user_id, path, filename, uploaded_at, transcription_status, entry_type, recorded_date) "
            "VALUES (?, ?, ?, ?, 'uploaded', 'audio', ?)",
            (user["id"], str(dest), audio.filename, utc_now_iso(), recorded_date),
        )
        conn.commit()
        return RedirectResponse(f"/files/{cur.lastrowid}/categorize", status_code=303)

    elif upload_type == "file_text":
        if not doc or not doc.filename:
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"error": "Please select a text or PDF document.", "user": user, "today": today_str},
                status_code=400,
            )
        ext = Path(doc.filename).suffix.lower()
        if ext not in {".txt", ".pdf"}:
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"error": f"Unsupported file type '{ext or 'unknown'}'. Allowed: .txt, .pdf.", "user": user, "today": today_str},
                status_code=400,
            )

        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = config.DOCS_DIR / stored_name
        
        doc_bytes = await doc.read()
        
        # Max size check: 50 MB for documents
        max_doc_bytes = 50 * 1024 * 1024
        if len(doc_bytes) > max_doc_bytes:
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"error": "Document is too large (limit 50 MB).", "user": user, "today": today_str},
                status_code=400,
            )

        # Extract text
        if ext == ".txt":
            try:
                extracted_text = doc_bytes.decode("utf-8", errors="replace")
            except Exception as e:
                return templates.TemplateResponse(
                    request,
                    "upload.html",
                    {"error": f"Failed to read text file: {e}", "user": user, "today": today_str},
                    status_code=400,
                )
        else:  # .pdf
            try:
                import fitz
                pdf_doc = fitz.open(stream=doc_bytes, filetype="pdf")
                text_parts = []
                for page in pdf_doc:
                    text_parts.append(page.get_text())
                extracted_text = "\n".join(text_parts).strip()
            except Exception as e:
                return templates.TemplateResponse(
                    request,
                    "upload.html",
                    {"error": f"Failed to extract text from PDF: {e}", "user": user, "today": today_str},
                    status_code=400,
                )

        if not extracted_text:
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"error": "No readable text could be extracted from the document.", "user": user, "today": today_str},
                status_code=400,
            )

        # Save file to disk
        with dest.open("wb") as out:
            out.write(doc_bytes)

        cur = conn.execute(
            "INSERT INTO knowledge_entries (user_id, path, filename, uploaded_at, transcription_status, entry_type, recorded_date, raw_text) "
            "VALUES (?, ?, ?, ?, 'uploaded', 'text', ?, ?)",
            (user["id"], str(dest), doc.filename, utc_now_iso(), recorded_date, extracted_text),
        )
        conn.commit()
        return RedirectResponse(f"/files/{cur.lastrowid}/categorize", status_code=303)

    elif upload_type == "plain_text":
        if not plain_text or not plain_text.strip():
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"error": "Please enter some text.", "user": user, "today": today_str},
                status_code=400,
            )

        plain_text = plain_text.strip()
        stored_name = f"{uuid.uuid4().hex}.txt"
        dest = config.DOCS_DIR / stored_name

        with dest.open("w", encoding="utf-8") as out:
            out.write(plain_text)

        filename = f"Plain Text - {recorded_date}.txt"
        cur = conn.execute(
            "INSERT INTO knowledge_entries (user_id, path, filename, uploaded_at, transcription_status, entry_type, recorded_date, raw_text) "
            "VALUES (?, ?, ?, ?, 'uploaded', 'text', ?, ?)",
            (user["id"], str(dest), filename, utc_now_iso(), recorded_date, plain_text),
        )
        conn.commit()
        return RedirectResponse(f"/files/{cur.lastrowid}/categorize", status_code=303)

    return RedirectResponse("/upload", status_code=303)


@router.get("/files/{file_id}/categorize", response_class=HTMLResponse)
def categorize_form(
    file_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    row = conn.execute(
        "SELECT * FROM knowledge_entries WHERE id=? AND user_id=?", (file_id, user["id"])
    ).fetchone()
    if row is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "categorize.html",
        {
            "file": dict(row),
            "conversation_types": CONVERSATION_TYPES,
            "owner_roles": OWNER_ROLES,
            "user": user,
        },
    )


@router.post("/files/{file_id}/categorize")
def categorize_submit(
    file_id: int,
    background: BackgroundTasks,
    conversation_type: str = Form(""),
    owner_role: str = Form(""),
    objective: str = Form(""),
    context_note: str = Form(""),
    single_sided: str = Form("0"),
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    row = conn.execute(
        "SELECT id, entry_type, raw_text, filename FROM knowledge_entries WHERE id=? AND user_id=?", (file_id, user["id"])
    ).fetchone()
    if row is None:
        return RedirectResponse("/", status_code=303)

    if row["entry_type"] == "text":
        # Create transcript record
        cur = conn.execute(
            "INSERT INTO transcripts (audio_file_id, raw_deepgram_json, plain_text, created_at) "
            "VALUES (?, '{}', ?, ?)",
            (file_id, row["raw_text"], utc_now_iso()),
        )
        transcript_id = cur.lastrowid

        # Pre-populate owner speaker mapped to username
        conn.execute(
            "INSERT INTO speakers (transcript_id, speaker_label, is_owner, local_name) "
            "VALUES (?, 'speaker_1', 1, ?)",
            (transcript_id, user["username"]),
        )

        # Pre-populate single utterance
        conn.execute(
            "INSERT INTO utterances (transcript_id, speaker_label, start_sec, end_sec, text) "
            "VALUES (?, 'speaker_1', 0.0, 10.0, ?)",
            (transcript_id, row["raw_text"]),
        )

        # Update metadata and status to transcribing (which is used for analysis loading)
        conn.execute(
            """
            UPDATE knowledge_entries
               SET conversation_type=?, owner_role=?, objective=?, context_note=?,
                   single_sided=0,
                   transcription_status='transcribing', transcription_error=NULL
             WHERE id=? AND user_id=?
            """,
            (
                conversation_type,
                owner_role,
                objective.strip(),
                context_note.strip(),
                file_id,
                user["id"],
            ),
        )
        conn.commit()

        from ..web import export_transcript_to_file
        export_transcript_to_file(conn, transcript_id)

        # Kick off background analysis task
        from ..background import run_analysis_background
        background.add_task(run_analysis_background, file_id, transcript_id, user["id"])
        return RedirectResponse(f"/files/{file_id}/transcribing", status_code=303)

    else:
        conn.execute(
            """
            UPDATE knowledge_entries
               SET conversation_type=?, owner_role=?, objective=?, context_note=?,
                   single_sided=?,
                   transcription_status='transcribing', transcription_error=NULL
             WHERE id=? AND user_id=?
            """,
            (
                conversation_type,
                owner_role,
                objective.strip(),
                context_note.strip(),
                1 if single_sided == "1" else 0,
                file_id,
                user["id"],
            ),
        )
        conn.commit()

        # Kick off transcription off the request path.
        background.add_task(run_transcription, file_id)
        return RedirectResponse(f"/files/{file_id}/transcribing", status_code=303)


@router.get("/files/{file_id}/transcribing", response_class=HTMLResponse)
def transcribing_page(
    file_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    row = conn.execute(
        "SELECT * FROM knowledge_entries WHERE id=? AND user_id=?", (file_id, user["id"])
    ).fetchone()
    if row is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "transcribing.html", {"file": dict(row), "user": user}
    )


@router.post("/files/{file_id}/delete")
def delete_file(
    file_id: int, conn=Depends(db_dep), user=Depends(get_current_user)
):
    """Delete a recording: its audio file on disk plus every derived DB row.

    Children reference transcripts, which reference the audio file, so we delete
    depth-first (analyses/speakers/utterances → transcripts → audio_files) to
    respect the foreign keys. The stored audio file is removed from disk last.
    """
    row = conn.execute(
        "SELECT path FROM knowledge_entries WHERE id=? AND user_id=?",
        (file_id, user["id"]),
    ).fetchone()
    if row is None:
        return RedirectResponse("/", status_code=303)

    transcript_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM transcripts WHERE audio_file_id=?", (file_id,)
        ).fetchall()
    ]
    if transcript_ids:
        placeholders = ",".join("?" * len(transcript_ids))
        for table in ("analyses", "speakers", "utterances"):
            conn.execute(
                f"DELETE FROM {table} WHERE transcript_id IN ({placeholders})",
                transcript_ids,
            )
        conn.execute(
            f"DELETE FROM transcripts WHERE id IN ({placeholders})",
            transcript_ids,
        )
    conn.execute(
        "DELETE FROM knowledge_entries WHERE id=? AND user_id=?", (file_id, user["id"])
    )
    conn.commit()

    # Remove the stored audio file; a missing file must not fail the delete.
    Path(row["path"]).unlink(missing_ok=True)

    return RedirectResponse("/", status_code=303)


@router.get("/files/{file_id}/status")
def transcription_status(
    file_id: int, conn=Depends(db_dep), user=Depends(get_current_user)
):
    row = conn.execute(
        "SELECT transcription_status, transcription_error FROM knowledge_entries WHERE id=? AND user_id=?",
        (file_id, user["id"]),
    ).fetchone()
    if row is None:
        return JSONResponse({"status": "missing"}, status_code=404)

    transcript_row = conn.execute(
        "SELECT id FROM transcripts WHERE audio_file_id=? ORDER BY id DESC LIMIT 1",
        (file_id,),
    ).fetchone()
    return JSONResponse(
        {
            "status": row["transcription_status"],
            "error": row["transcription_error"],
            "transcript_id": transcript_row["id"] if transcript_row else None,
        }
    )
