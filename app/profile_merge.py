"""Owner-profile accumulation (spec §9) — deterministic, append-only, idempotent.

The spec defines the update *diff* (owner_profile_update) but not the stored
accumulator shape; this module defines it. Merging never rewrites: it appends
and dedupes, records each archetype signal into an auditable evidence trail, and
carries the latest signal as the current archetype (emergent — the platform
never names the archetype itself). An idempotency guard keyed on analysis_id
prevents double-counting if the same analysis is ingested twice.
"""
from __future__ import annotations

from typing import Any

from .db import utc_now_iso


def empty_profile() -> dict[str, Any]:
    return {
        "version": 0,
        "recurring_topics": [],
        "communication_style_notes": [],
        "goals_concerns": [],
        "archetype_signal_trail": [],
        "current_archetype": "",
        "source_analysis_ids": [],
        # new schema fields:
        "who_i_am": "",
        "current_issues": [],
        "recurrent_topics": [],
        "strong_opinions": [],
        "tone_and_sentiment": "",
    }


def _dedupe_extend(existing: list[str], additions: list[str]) -> list[str]:
    """Order-preserving, case-insensitive union; keeps first-seen casing; drops empties."""
    result = list(existing)
    seen = {item.casefold().strip() for item in existing}
    for item in additions:
        norm = (item or "").casefold().strip()
        if norm and norm not in seen:
            seen.add(norm)
            result.append(item.strip())
    return result


def render_notes(profile: dict[str, Any]) -> str:
    """Human-readable archetype evidence trail for the owner_profile.archetype_notes column."""
    lines = []
    for entry in profile.get("archetype_signal_trail", []):
        lines.append(f"[{entry.get('at', '')}] (analysis {entry.get('analysis_id')}): {entry.get('signal', '')}")
    return "\n".join(lines)


def merge(current: dict[str, Any], diff: dict[str, Any], analysis_id: int) -> dict[str, Any]:
    """Return a NEW merged profile dict (does not mutate `current`).

    `diff` is a validated OwnerProfileUpdate (as a dict). If this analysis_id was
    already merged, `current` is returned unchanged (idempotent).
    """
    if analysis_id in current.get("source_analysis_ids", []):
        return current  # already applied — no double-count

    recurring_topics = _dedupe_extend(
        current.get("recurring_topics", []), diff.get("recurring_topics_add", [])
    )
    communication_style_notes = _dedupe_extend(
        current.get("communication_style_notes", []), diff.get("communication_style_notes", [])
    )
    goals_concerns = _dedupe_extend(
        current.get("goals_concerns", []), diff.get("goals_concerns_add", [])
    )

    merged = {
        "version": current.get("version", 0) + 1,
        "recurring_topics": recurring_topics,
        "communication_style_notes": communication_style_notes,
        "goals_concerns": goals_concerns,
        "archetype_signal_trail": list(current.get("archetype_signal_trail", [])),
        "current_archetype": current.get("current_archetype", ""),
        "source_analysis_ids": current.get("source_analysis_ids", []) + [analysis_id],
        
        # New fields (derived from diff or kept as is)
        "who_i_am": current.get("who_i_am", ""),
        "current_issues": _dedupe_extend(
            current.get("current_issues", []), diff.get("goals_concerns_add", [])
        ),
        "recurrent_topics": _dedupe_extend(
            current.get("recurrent_topics", []), diff.get("recurring_topics_add", [])
        ),
        "strong_opinions": current.get("strong_opinions", []),
        "tone_and_sentiment": current.get("tone_and_sentiment", ""),
    }

    if diff.get("communication_style_notes"):
        existing_tone = current.get("tone_and_sentiment", "")
        new_notes = [n.strip() for n in diff["communication_style_notes"] if n.strip()]
        if new_notes:
            if existing_tone:
                merged["tone_and_sentiment"] = existing_tone + "\n" + "\n".join(new_notes)
            else:
                merged["tone_and_sentiment"] = "\n".join(new_notes)

    signal = (diff.get("archetype_signal") or "").strip()
    if signal:
        merged["archetype_signal_trail"].append({
            "signal": signal,
            "analysis_id": analysis_id,
            "at": utc_now_iso(),
        })
        merged["current_archetype"] = signal

    return merged
