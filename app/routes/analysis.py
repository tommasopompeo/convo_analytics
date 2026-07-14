"""Claude bridge: prompt emission, JSON paste-back ingestion, result + profile views."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import ValidationError

from .. import aggregate_merge, prompt_builder, profile_merge
from .. import metrics as metrics_mod
from ..db import utc_now_iso
from ..models import AggregateOutput, AnalysisOutput
from ..web import (
    db_dep,
    get_current_user,
    load_current_profile,
    load_utterances,
    owner_label_for,
    templates,
)

router = APIRouter()

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Defensively remove a wrapping ```json ... ``` fence if Claude added one."""
    stripped = text.strip()
    stripped = re.sub(r"^\s*```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```\s*$", "", stripped)
    return stripped.strip()


def _transcript_meta(conn, transcript_id: int, user_id: int):
    return conn.execute(
        """
        SELECT t.id, t.audio_file_id, a.conversation_type, a.owner_role,
               a.objective, a.context_note, a.single_sided
          FROM transcripts t JOIN audio_files a ON a.id = t.audio_file_id
         WHERE t.id = ? AND a.user_id = ?
        """,
        (transcript_id, user_id),
    ).fetchone()


def _build_prompt_for(conn, transcript_id: int, owner_label: str, user_id: int) -> str:
    meta = _transcript_meta(conn, transcript_id, user_id)
    utterances = load_utterances(conn, transcript_id)
    metrics = metrics_mod.compute_metrics(utterances, None)
    speaker_labels = sorted({u["speaker_label"] for u in utterances})
    return prompt_builder.build_prompt(
        metadata=dict(meta),
        owner_label=owner_label,
        speaker_labels=speaker_labels,
        metrics=metrics,
        utterances=utterances,
        profile_json=load_current_profile(conn, user_id),
        single_sided=bool(meta["single_sided"]),
    )


def _render_analyze(
    request,
    conn,
    transcript_id,
    user,
    *,
    pasted_text="",
    error=None,
    field_errors=None,
    status_code=200,
):
    owner_label = owner_label_for(conn, transcript_id)
    if owner_label is None:
        return RedirectResponse(
            f"/transcripts/{transcript_id}/speakers", status_code=303
        )
    prompt = _build_prompt_for(conn, transcript_id, owner_label, user["id"])
    return templates.TemplateResponse(
        request,
        "analyze.html",
        {
            "transcript_id": transcript_id,
            "prompt": prompt,
            "pasted_text": pasted_text,
            "error": error,
            "field_errors": field_errors or [],
            "user": user,
        },
        status_code=status_code,
    )


@router.get("/transcripts/{transcript_id}/analyze", response_class=HTMLResponse)
def analyze_page(
    transcript_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    if _transcript_meta(conn, transcript_id, user["id"]) is None:
        return RedirectResponse("/", status_code=303)
    return _render_analyze(request, conn, transcript_id, user)


@router.post("/transcripts/{transcript_id}/analyze")
async def ingest_analysis(
    transcript_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    if _transcript_meta(conn, transcript_id, user["id"]) is None:
        return RedirectResponse("/", status_code=303)

    owner_label = owner_label_for(conn, transcript_id)
    if owner_label is None:
        return RedirectResponse(
            f"/transcripts/{transcript_id}/speakers", status_code=303
        )

    prompt = _build_prompt_for(conn, transcript_id, owner_label, user["id"])

    from .. import gemini_client
    try:
        analysis = await gemini_client.analyze_conversation_async(prompt)
    except Exception as exc:
        return _render_analyze(
            request,
            conn,
            transcript_id,
            user,
            error=f"Gemini analysis failed: {exc}",
            status_code=500,
        )

    # Persist the analysis (store the serialized JSON string for audit) + metrics snapshot.
    utterances = load_utterances(conn, transcript_id)
    metrics = metrics_mod.compute_metrics(utterances, None)
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

    # Merge the owner-profile diff → write a NEW append-only profile row.
    current = load_current_profile(conn, user["id"])
    merged = profile_merge.merge(
        current, analysis.owner_profile_update.model_dump(), analysis_id
    )
    if merged is not current:  # merge applied (not a duplicate ingest)
        conn.execute(
            "INSERT INTO owner_profile (user_id, profile_json, archetype, archetype_notes, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                user["id"],
                json.dumps(merged, ensure_ascii=False),
                merged.get("current_archetype", ""),
                profile_merge.render_notes(merged),
                utc_now_iso(),
            ),
        )
    conn.commit()
    return RedirectResponse(f"/analyses/{analysis_id}", status_code=303)


@router.get("/analyses/{analysis_id}", response_class=HTMLResponse)
def result_view(
    analysis_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    row = conn.execute(
        """
        SELECT an.id, an.transcript_id, an.llm_output_json, an.created_at
          FROM analyses an
          JOIN transcripts t ON t.id = an.transcript_id
          JOIN audio_files af ON af.id = t.audio_file_id
         WHERE an.id = ? AND af.user_id = ?
        """,
        (analysis_id, user["id"]),
    ).fetchone()
    if row is None:
        return RedirectResponse("/", status_code=303)

    analysis = _safe_parse(row["llm_output_json"])
    profile = load_current_profile(conn, user["id"])
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "analysis": analysis.model_dump() if analysis else None,
            "transcript_id": row["transcript_id"],
            "created_at": row["created_at"],
            "profile": profile,
            "user": user,
        },
    )


def _safe_parse(raw_text: str):
    try:
        return AnalysisOutput.model_validate_json(_strip_fences(raw_text))
    except (ValidationError, ValueError):
        return None


@router.get("/profile", response_class=HTMLResponse)
def profile_view(
    request: Request, conn=Depends(db_dep), user=Depends(get_current_user)
):
    """The aggregate centerpiece. Renders the latest cross-corpus synthesis if one

    exists; otherwise falls back to the accumulated per-conversation profile with a
    nudge to run the (Stage-1, manual) 'Refresh overall insight' loop.
    """
    profile = load_current_profile(conn, user["id"])
    aggregate = aggregate_merge.load_latest_aggregate(conn, user["id"])
    analyses_count = conn.execute(
        """
        SELECT COUNT(*) FROM analyses an
          JOIN transcripts t ON t.id = an.transcript_id
          JOIN audio_files af ON af.id = t.audio_file_id
         WHERE af.user_id = ?
        """,
        (user["id"],),
    ).fetchone()[0]

    _, corpus_stats, _ = aggregate_merge.build_corpus_bundle(conn, user["id"])
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "profile": profile,
            "aggregate": aggregate,
            "corpus_stats": corpus_stats,
            "analyses_count": analyses_count,
            "user": user,
            "nav": "profile",
        },
    )


@router.get("/profile/refresh", response_class=HTMLResponse)
def refresh_page(
    request: Request, conn=Depends(db_dep), user=Depends(get_current_user)
):
    """Render the automated synthesis loading page."""
    analyses_count = conn.execute(
        """
        SELECT COUNT(*) FROM analyses an
          JOIN transcripts t ON t.id = an.transcript_id
          JOIN audio_files af ON af.id = t.audio_file_id
         WHERE af.user_id = ?
        """,
        (user["id"],),
    ).fetchone()[0]
    if analyses_count == 0:
        return RedirectResponse("/profile", status_code=303)

    _, corpus_stats, _ = aggregate_merge.build_corpus_bundle(conn, user["id"])
    return templates.TemplateResponse(
        request,
        "aggregate_refresh.html",
        {
            "corpus_stats": corpus_stats,
            "user": user,
        },
    )


@router.post("/profile/refresh")
async def ingest_aggregate(
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    """Stage 2: automatically synthesise using Gemini Pro client and save."""
    conversations, corpus_stats, current_aggregate = (
        aggregate_merge.build_corpus_bundle(conn, user["id"])
    )
    if not conversations:
        return JSONResponse({"error": "No conversations to analyze."}, status_code=400)

    prompt = prompt_builder.build_aggregate_prompt(
        conversations=conversations,
        current_aggregate=current_aggregate,
        corpus_stats=corpus_stats,
        synthesis_type="full",
    )

    from .. import gemini_client
    try:
        aggregate = await gemini_client.synthesize_profile_async(prompt)
    except Exception as exc:
        return JSONResponse({"error": f"Gemini analysis failed: {exc}"}, status_code=500)

    # Ensure metadata is accurate with platform ground truth
    aggregate.corpus_meta.conversation_count = corpus_stats["conversation_count"]
    aggregate.corpus_meta.synthesis_type = "full"
    aggregate.corpus_meta.source_analysis_ids = corpus_stats["source_analysis_ids"]

    aggregate_merge.store_aggregate(
        conn,
        user["id"],
        aggregate.model_dump(),
        synthesis_type="full",
        source_analysis_ids=corpus_stats["source_analysis_ids"],
        conversation_count=corpus_stats["conversation_count"],
    )
    return JSONResponse({"status": "success"})
