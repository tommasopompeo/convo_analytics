"""Claude bridge: prompt emission, JSON paste-back ingestion, result + profile views."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from .. import aggregate_merge
from .. import metrics as metrics_mod
from .. import prompt_builder, profile_merge
from ..db import utc_now_iso
from ..models import AggregateOutput, AnalysisOutput
from ..web import (
    db_dep,
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


def _transcript_meta(conn, transcript_id: int):
    return conn.execute(
        """
        SELECT t.id, t.audio_file_id, a.conversation_type, a.owner_role,
               a.objective, a.context_note, a.single_sided
          FROM transcripts t JOIN audio_files a ON a.id = t.audio_file_id
         WHERE t.id = ?
        """,
        (transcript_id,),
    ).fetchone()


def _build_prompt_for(conn, transcript_id: int, owner_label: str) -> str:
    meta = _transcript_meta(conn, transcript_id)
    utterances = load_utterances(conn, transcript_id)
    metrics = metrics_mod.compute_metrics(utterances, None)
    speaker_labels = sorted({u["speaker_label"] for u in utterances})
    return prompt_builder.build_prompt(
        metadata=dict(meta),
        owner_label=owner_label,
        speaker_labels=speaker_labels,
        metrics=metrics,
        utterances=utterances,
        profile_json=load_current_profile(conn),
        single_sided=bool(meta["single_sided"]),
    )


def _render_analyze(request, conn, transcript_id, *, pasted_text="", error=None, field_errors=None, status_code=200):
    owner_label = owner_label_for(conn, transcript_id)
    if owner_label is None:
        return RedirectResponse(f"/transcripts/{transcript_id}/speakers", status_code=303)
    prompt = _build_prompt_for(conn, transcript_id, owner_label)
    return templates.TemplateResponse(
        request, "analyze.html",
        {
            "transcript_id": transcript_id,
            "prompt": prompt,
            "pasted_text": pasted_text,
            "error": error,
            "field_errors": field_errors or [],
        },
        status_code=status_code,
    )


@router.get("/transcripts/{transcript_id}/analyze", response_class=HTMLResponse)
def analyze_page(transcript_id: int, request: Request, conn=Depends(db_dep)):
    if _transcript_meta(conn, transcript_id) is None:
        return RedirectResponse("/", status_code=303)
    return _render_analyze(request, conn, transcript_id)


@router.post("/transcripts/{transcript_id}/analyze")
def ingest_analysis(
    transcript_id: int,
    request: Request,
    analysis_json: str = Form(""),
    conn=Depends(db_dep),
):
    if _transcript_meta(conn, transcript_id) is None:
        return RedirectResponse("/", status_code=303)

    raw_text = analysis_json
    cleaned = _strip_fences(raw_text)

    # Stage 1: JSON parse — fail loudly, preserve the pasted text.
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return _render_analyze(
            request, conn, transcript_id,
            pasted_text=raw_text,
            error=f"That isn't valid JSON — {exc.msg} (line {exc.lineno}, column {exc.colno}). "
                  "Fix it and paste again.",
            status_code=400,
        )

    # Stage 2: schema validation — fail loudly with field-level detail.
    try:
        analysis = AnalysisOutput.model_validate(obj)
    except ValidationError as exc:
        field_errors = [
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors()
        ]
        return _render_analyze(
            request, conn, transcript_id,
            pasted_text=raw_text,
            error="The JSON is valid but doesn't match the required schema:",
            field_errors=field_errors,
            status_code=400,
        )

    # Persist the analysis (store the RAW pasted string for audit) + metrics snapshot.
    utterances = load_utterances(conn, transcript_id)
    metrics = metrics_mod.compute_metrics(utterances, None)
    cur = conn.execute(
        "INSERT INTO analyses (transcript_id, metrics_json, llm_output_json, created_at) VALUES (?, ?, ?, ?)",
        (transcript_id, json.dumps(metrics, ensure_ascii=False), raw_text, utc_now_iso()),
    )
    analysis_id = cur.lastrowid

    # Merge the owner-profile diff → write a NEW append-only profile row.
    current = load_current_profile(conn)
    merged = profile_merge.merge(current, analysis.owner_profile_update.model_dump(), analysis_id)
    if merged is not current:  # merge applied (not a duplicate ingest)
        conn.execute(
            "INSERT INTO owner_profile (profile_json, archetype, archetype_notes, updated_at) VALUES (?, ?, ?, ?)",
            (
                json.dumps(merged, ensure_ascii=False),
                merged.get("current_archetype", ""),
                profile_merge.render_notes(merged),
                utc_now_iso(),
            ),
        )
    conn.commit()
    return RedirectResponse(f"/analyses/{analysis_id}", status_code=303)


@router.get("/analyses/{analysis_id}", response_class=HTMLResponse)
def result_view(analysis_id: int, request: Request, conn=Depends(db_dep)):
    row = conn.execute(
        "SELECT id, transcript_id, llm_output_json, created_at FROM analyses WHERE id=?",
        (analysis_id,),
    ).fetchone()
    if row is None:
        return RedirectResponse("/", status_code=303)

    analysis = _safe_parse(row["llm_output_json"])
    profile = load_current_profile(conn)
    return templates.TemplateResponse(
        request, "result.html",
        {
            "analysis": analysis.model_dump() if analysis else None,
            "transcript_id": row["transcript_id"],
            "created_at": row["created_at"],
            "profile": profile,
        },
    )


def _safe_parse(raw_text: str):
    try:
        return AnalysisOutput.model_validate_json(_strip_fences(raw_text))
    except (ValidationError, ValueError):
        return None


@router.get("/profile", response_class=HTMLResponse)
def profile_view(request: Request, conn=Depends(db_dep)):
    """The aggregate centerpiece. Renders the latest cross-corpus synthesis if one
    exists; otherwise falls back to the accumulated per-conversation profile with a
    nudge to run the (Stage-1, manual) 'Refresh overall insight' loop."""
    profile = load_current_profile(conn)
    aggregate = aggregate_merge.load_latest_aggregate(conn)
    analyses_count = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    _, corpus_stats, _ = aggregate_merge.build_corpus_bundle(conn)
    return templates.TemplateResponse(
        request, "profile.html",
        {
            "profile": profile,
            "aggregate": aggregate,
            "corpus_stats": corpus_stats,
            "analyses_count": analyses_count,
        },
    )


def _render_refresh(request, conn, *, pasted_text="", error=None, field_errors=None, status_code=200):
    conversations, corpus_stats, current_aggregate = aggregate_merge.build_corpus_bundle(conn)
    prompt = prompt_builder.build_aggregate_prompt(
        conversations=conversations,
        current_aggregate=current_aggregate,
        corpus_stats=corpus_stats,
        synthesis_type="manual",
    )
    return templates.TemplateResponse(
        request, "aggregate_refresh.html",
        {
            "prompt": prompt,
            "corpus_stats": corpus_stats,
            "pasted_text": pasted_text,
            "error": error,
            "field_errors": field_errors or [],
        },
        status_code=status_code,
    )


@router.get("/profile/refresh", response_class=HTMLResponse)
def refresh_page(request: Request, conn=Depends(db_dep)):
    """Stage 1: bundle the whole corpus into one copy-ready prompt to take to Claude."""
    analyses_count = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    if analyses_count == 0:
        return RedirectResponse("/profile", status_code=303)
    return _render_refresh(request, conn)


@router.post("/profile/refresh")
def ingest_aggregate(request: Request, aggregate_json: str = Form(""), conn=Depends(db_dep)):
    """Stage 1: ingest the pasted aggregate synthesis JSON and store it."""
    cleaned = _strip_fences(aggregate_json)

    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return _render_refresh(
            request, conn, pasted_text=aggregate_json,
            error=f"That isn't valid JSON — {exc.msg} (line {exc.lineno}, column {exc.colno}). "
                  "Fix it and paste again.",
            status_code=400,
        )

    try:
        aggregate = AggregateOutput.model_validate(obj)
    except ValidationError as exc:
        field_errors = [
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors()
        ]
        return _render_refresh(
            request, conn, pasted_text=aggregate_json,
            error="The JSON is valid but doesn't match the required aggregate schema:",
            field_errors=field_errors, status_code=400,
        )

    _, corpus_stats, _ = aggregate_merge.build_corpus_bundle(conn)
    aggregate_merge.store_aggregate(
        conn, aggregate.model_dump(),
        synthesis_type="manual",
        source_analysis_ids=corpus_stats["source_analysis_ids"],
        conversation_count=corpus_stats["conversation_count"],
    )
    return RedirectResponse("/profile", status_code=303)
