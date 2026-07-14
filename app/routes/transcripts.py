"""Speaker-tagging and metrics/transcript routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import metrics as metrics_mod
from ..web import (
    db_dep,
    fmt_ts,
    get_current_user,
    load_utterances,
    owner_label_for,
    templates,
)

router = APIRouter()


def _transcript_or_none(conn, transcript_id: int, user_id: int):
    return conn.execute(
        """
        SELECT t.id, t.audio_file_id, t.plain_text, a.duration_sec, a.filename,
               a.conversation_type, a.owner_role, a.objective, a.context_note
          FROM transcripts t JOIN audio_files a ON a.id = t.audio_file_id
         WHERE t.id = ? AND a.user_id = ?
        """,
        (transcript_id, user_id),
    ).fetchone()


@router.get("/transcripts/{transcript_id}/speakers", response_class=HTMLResponse)
def speakers_form(
    transcript_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    transcript = _transcript_or_none(conn, transcript_id, user["id"])
    if transcript is None:
        return RedirectResponse("/", status_code=303)

    speaker_rows = conn.execute(
        "SELECT speaker_label, is_owner, local_name FROM speakers WHERE transcript_id=? ORDER BY speaker_label",
        (transcript_id,),
    ).fetchall()

    speakers = []
    for s in speaker_rows:
        samples = conn.execute(
            """
            SELECT start_sec, text FROM utterances
              WHERE transcript_id=? AND speaker_label=? AND length(trim(text)) > 0
              ORDER BY (end_sec - start_sec) DESC LIMIT 3
            """,
            (transcript_id, s["speaker_label"]),
        ).fetchall()
        speakers.append(
            {
                "speaker_label": s["speaker_label"],
                "is_owner": s["is_owner"],
                "local_name": s["local_name"] or "",
                "samples": [
                    {"ts": fmt_ts(r["start_sec"]), "text": r["text"]}
                    for r in samples
                ],
            }
        )

    return templates.TemplateResponse(
        request,
        "speakers.html",
        {"transcript": dict(transcript), "speakers": speakers, "user": user},
    )


@router.post("/transcripts/{transcript_id}/speakers")
async def speakers_submit(
    transcript_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    transcript = _transcript_or_none(conn, transcript_id, user["id"])
    if transcript is None:
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    owner = form.get("owner")

    labels = [
        r["speaker_label"]
        for r in conn.execute(
            "SELECT speaker_label FROM speakers WHERE transcript_id=?",
            (transcript_id,),
        ).fetchall()
    ]

    for label in labels:
        local_name = (form.get(f"local_name__{label}") or "").strip()
        conn.execute(
            "UPDATE speakers SET is_owner=?, local_name=? WHERE transcript_id=? AND speaker_label=?",
            (
                1 if label == owner else 0,
                local_name or None,
                transcript_id,
                label,
            ),
        )
    conn.commit()

    if owner is None or owner not in labels:
        # Re-render with a gentle prompt to pick exactly one owner.
        return RedirectResponse(
            f"/transcripts/{transcript_id}/speakers", status_code=303
        )

    return RedirectResponse(f"/transcripts/{transcript_id}", status_code=303)


@router.get("/transcripts/{transcript_id}", response_class=HTMLResponse)
def metrics_view(
    transcript_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    transcript = _transcript_or_none(conn, transcript_id, user["id"])
    if transcript is None:
        return RedirectResponse("/", status_code=303)

    utterances = load_utterances(conn, transcript_id)
    metrics = metrics_mod.compute_metrics(utterances, transcript["duration_sec"])
    owner_label = owner_label_for(conn, transcript_id)

    # If this recording has already been reflected on, expose its saved analysis
    # so the single-recording reflection is reachable — not just the aggregate.
    analysis_row = conn.execute(
        "SELECT id FROM analyses WHERE transcript_id=? ORDER BY id DESC LIMIT 1",
        (transcript_id,),
    ).fetchone()
    analysis_id = analysis_row["id"] if analysis_row else None

    local_names = {
        r["speaker_label"]: r["local_name"]
        for r in conn.execute(
            "SELECT speaker_label, local_name FROM speakers WHERE transcript_id=?",
            (transcript_id,),
        ).fetchall()
    }

    transcript_lines = [
        {
            "ts": fmt_ts(u["start_sec"]),
            "speaker_label": u["speaker_label"],
            "is_owner": u["speaker_label"] == owner_label,
            "text": u["text"],
        }
        for u in utterances
    ]

    return templates.TemplateResponse(
        request,
        "metrics.html",
        {
            "transcript": dict(transcript),
            "metrics": metrics,
            "owner_label": owner_label,
            "local_names": local_names,
            "transcript_lines": transcript_lines,
            "analysis_id": analysis_id,
            "user": user,
        },
    )
