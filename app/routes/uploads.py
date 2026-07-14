"""Home / library, upload, categorization, and transcription-status routes."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

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
          FROM audio_files a
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
    return templates.TemplateResponse(request, "upload.html", {"user": user})


@router.post("/upload")
async def upload_file(
    request: Request,
    audio: UploadFile,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    ext = Path(audio.filename or "").suffix.lower()
    if ext not in config.ALLOWED_EXT:
        allowed = ", ".join(sorted(config.ALLOWED_EXT))
        return templates.TemplateResponse(
            request,
            "upload.html",
            {
                "error": f"Unsupported file type '{ext or 'unknown'}'. Allowed: {allowed}.",
                "user": user,
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
            {"error": f"File is too large (limit {limit_mb} MB).", "user": user},
            status_code=400,
        )

    cur = conn.execute(
        "INSERT INTO audio_files (user_id, path, filename, uploaded_at, transcription_status) "
        "VALUES (?, ?, ?, ?, 'uploaded')",
        (user["id"], str(dest), audio.filename, utc_now_iso()),
    )
    conn.commit()
    return RedirectResponse(f"/files/{cur.lastrowid}/categorize", status_code=303)


@router.get("/files/{file_id}/categorize", response_class=HTMLResponse)
def categorize_form(
    file_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    row = conn.execute(
        "SELECT * FROM audio_files WHERE id=? AND user_id=?", (file_id, user["id"])
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
        "SELECT id FROM audio_files WHERE id=? AND user_id=?", (file_id, user["id"])
    ).fetchone()
    if row is None:
        return RedirectResponse("/", status_code=303)

    conn.execute(
        """
        UPDATE audio_files
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
        "SELECT * FROM audio_files WHERE id=? AND user_id=?", (file_id, user["id"])
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
        "SELECT path FROM audio_files WHERE id=? AND user_id=?",
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
        "DELETE FROM audio_files WHERE id=? AND user_id=?", (file_id, user["id"])
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
        "SELECT transcription_status, transcription_error FROM audio_files WHERE id=? AND user_id=?",
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
