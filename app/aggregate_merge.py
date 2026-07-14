"""Cross-corpus aggregate synthesis — bundle builder + storage (Stage 1).

The per-conversation loop (spec §8/§9) grows a profile by dedupe-union and sets
the archetype last-write-wins. That is bookkeeping, not synthesis. This module
adds the corpus level: it bundles every analysed conversation's interpretive
layer + the three surviving deterministic metrics into ONE prompt, and stores
the aggregate JSON the user (Stage 1) or the API (Stage 2) hands back.

The stored shape is `models.AggregateOutput`; the same contract serves the
manual paste loop and, later, the automated incremental/full re-synthesis —
only the caller changes.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import metrics as metrics_mod
from .db import utc_now_iso


def _safe_analysis(raw: str) -> dict[str, Any]:
    """Parse a stored llm_output_json defensively (it may carry a code fence)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text
        text = text.lstrip("json").strip("` \n")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


def _metric_summary(utterances: list[dict[str, Any]], owner_label: Optional[str],
                    duration_sec: Optional[float]) -> dict[str, Any]:
    """The three surviving deterministic metrics, condensed for the prompt:
    balance (talk-time share, two-sided only), pace (owner wpm), shape (length)."""
    m = metrics_mod.compute_metrics(utterances, duration_sec)
    speakers = m.get("speakers", [])
    two_sided = len(speakers) > 1
    owner = next((s for s in speakers if s["speaker_label"] == owner_label), None)
    summary: dict[str, Any] = {
        "length": _fmt_dur(m["conversation"]["total_duration_sec"]),
        "speaking_time": _fmt_dur(m["conversation"]["total_speech_sec"]),
        "two_sided": two_sided,
    }
    if owner and owner.get("wpm") is not None:
        summary["owner_pace_wpm"] = owner["wpm"]
    if two_sided and owner and owner.get("talk_time_pct") is not None:
        summary["owner_talk_share_pct"] = owner["talk_time_pct"]
    return summary


def _fmt_dur(seconds: float) -> str:
    total = int(round(float(seconds or 0)))
    return f"{total // 60}m {total % 60:02d}s"


def build_corpus_bundle(conn, user_id: int) -> tuple[list[dict[str, Any]], dict[str, Any], Optional[dict[str, Any]]]:
    """Assemble everything the aggregate prompt needs from the DB.

    Returns (conversations, corpus_stats, current_aggregate). Each conversation
    dict carries a stable `ref`, its metadata, the parsed analysis, and the
    condensed metric summary. `corpus_stats` counts are authoritative ground
    truth (the platform owns them; Claude never invents them).
    """
    rows = conn.execute(
        """
        SELECT an.id AS analysis_id, an.transcript_id, an.llm_output_json,
               an.created_at,
               af.filename, af.conversation_type, af.owner_role,
               af.duration_sec, af.single_sided
          FROM analyses an
          JOIN transcripts t   ON t.id = an.transcript_id
          JOIN audio_files af  ON af.id = t.audio_file_id
         WHERE af.user_id = ?
         ORDER BY an.created_at ASC, an.id ASC
        """,
        (user_id,)
    ).fetchall()

    conversations: list[dict[str, Any]] = []
    source_ids: list[int] = []
    types: list[str] = []
    total_speech = 0.0
    total_dur = 0.0
    dates: list[str] = []

    for r in rows:
        owner_row = conn.execute(
            "SELECT speaker_label FROM speakers WHERE transcript_id=? AND is_owner=1",
            (r["transcript_id"],),
        ).fetchone()
        owner_label = owner_row["speaker_label"] if owner_row else None
        utts = conn.execute(
            "SELECT speaker_label, start_sec, end_sec, text FROM utterances "
            "WHERE transcript_id=? ORDER BY start_sec, end_sec",
            (r["transcript_id"],),
        ).fetchall()
        utts = [dict(u) for u in utts]
        m = metrics_mod.compute_metrics(utts, r["duration_sec"])

        conversations.append({
            "ref": short_ref(r["filename"], r["analysis_id"]),
            "analysis_id": r["analysis_id"],
            "conversation_type": r["conversation_type"],
            "owner_role": r["owner_role"],
            "date": (r["created_at"] or "")[:10],
            "single_sided": bool(r["single_sided"]),
            "analysis": _safe_analysis(r["llm_output_json"]),
            "metric_summary": _metric_summary(utts, owner_label, r["duration_sec"]),
        })
        source_ids.append(r["analysis_id"])
        if r["conversation_type"]:
            types.append(r["conversation_type"])
        total_speech += m["conversation"]["total_speech_sec"]
        total_dur += m["conversation"]["total_duration_sec"]
        if r["created_at"]:
            dates.append(r["created_at"][:10])

    corpus_stats = {
        "conversation_count": len(conversations),
        "conversation_types": sorted(set(types)),
        "total_length": _fmt_dur(total_dur),
        "total_speaking_time": _fmt_dur(total_speech),
        "date_range": [min(dates), max(dates)] if dates else [],
        "source_analysis_ids": source_ids,
    }
    return conversations, corpus_stats, load_latest_aggregate(conn, user_id)


def short_ref(filename: str, analysis_id: int) -> str:
    """A stable, human handle for one conversation the synthesis can cite."""
    stem = (filename or "").rsplit(".", 1)[0]
    return stem or f"analysis-{analysis_id}"


def store_aggregate(conn, user_id: int, aggregate: dict[str, Any], *, synthesis_type: str,
                    source_analysis_ids: list[int], conversation_count: int) -> int:
    """Append a new aggregate_insight row and return its id."""
    cur = conn.execute(
        """
        INSERT INTO aggregate_insight
            (user_id, insight_json, archetype, synthesis_type, conversation_count,
             source_analysis_ids, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            json.dumps(aggregate, ensure_ascii=False),
            aggregate.get("archetype", ""),
            synthesis_type,
            conversation_count,
            json.dumps(source_analysis_ids),
            utc_now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def load_latest_aggregate(conn, user_id: int) -> Optional[dict[str, Any]]:
    """The most recent aggregate synthesis, or None if none exists yet."""
    row = conn.execute(
        "SELECT insight_json, created_at FROM aggregate_insight WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["insight_json"])
    except (json.JSONDecodeError, ValueError):
        return None
    data["_created_at"] = row["created_at"]
    return data

