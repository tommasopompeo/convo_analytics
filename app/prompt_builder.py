"""Copy-ready Claude prompt builder (spec §8a).

Assembles a single plain-text prompt, instruction-bracketed (hard rule first and
last) to minimize prose leakage. The transcript shown to Claude has non-lexical
hesitation sounds stripped (discourse markers preserved); the deterministic
metrics are injected as ground truth so Claude cites real numbers rather than
inventing them; the current owner profile is included so Claude returns a diff,
not a rewrite.
"""
from __future__ import annotations

import json
from typing import Any

from .fillers import strip_fillers

# The exact §10 contract, embedded verbatim so the paste-back matches the schema.
SCHEMA_BLOCK = """{
  "summary": "2-4 sentence neutral summary",
  "key_topics": [
    { "topic": "", "salience": "high|medium|low", "driven_by_speaker": "owner|speaker_1|..." }
  ],
  "sentiment_arc": [
    { "segment": "opening|mid|close|<timestamp>", "speaker": "", "sentiment": "positive|neutral|negative|mixed", "note": "" }
  ],
  "overall_sentiment": { "owner": "", "conversation": "" },
  "pivot_points": [
    { "utterance_ref": "<approx timestamp or quote>", "from_topic": "", "to_topic": "", "initiated_by": "", "significance": "" }
  ],
  "conversation_gaps": [
    { "type": "unexplored_topic|unasked_question|unmet_intent", "description": "" }
  ],
  "follow_ups": {
    "questions": [""],
    "actions": [""]
  },
  "owner_insights": {
    "communication_style": "",
    "notable_behaviors": [""]
  },
  "owner_profile_update": {
    "recurring_topics_add": [""],
    "communication_style_notes": [""],
    "goals_concerns_add": [""],
    "archetype_signal": ""
  }
}"""

_HARD_RULE = (
    "Return ONLY a single JSON object matching the schema below. "
    "No prose, no explanation, no markdown code fences — just the JSON."
)

# The aggregate (cross-corpus) contract — mirrors AggregateOutput in models.py.
# The SAME block is used by the manual Stage-1 loop and, later, the Stage-2 API
# calls; only the caller changes.
SCHEMA_BLOCK_AGG = """{
  "portrait": "3-5 sentence holistic read of the owner across ALL conversations — a synthesis, NOT a restatement of the most recent one",
  "portrait_evidence": ["short reference to the conversation(s) supporting the portrait"],
  "through_lines": [
    { "pattern": "a trait/dynamic that holds across conversations", "supporting_conversations": ["<conv ref>"], "note": "" }
  ],
  "shows_up_differently": [
    { "context": "conversation type/setting, e.g. 'feedback 1-on-1' | 'interview'", "how": "how the owner presents in this setting", "supporting_conversations": ["<conv ref>"] }
  ],
  "recurring_themes": [
    { "theme": "", "conversation_count": 0, "supporting_conversations": ["<conv ref>"] }
  ],
  "tensions": [
    { "stated": "what the owner says they value/intend", "observed": "how they actually behave across conversations", "supporting_conversations": ["<conv ref>"] }
  ],
  "drift": {
    "summary": "how the read has evolved as evidence accumulated (be honest about corpus size)",
    "points": [ { "conversation": "<conv ref>", "date": "YYYY-MM-DD", "signal": "one-line archetype signal at this point" } ]
  },
  "archetype": "a short emergent label/sentence — the regenerated current archetype (this REPLACES last-write-wins)",
  "confidence": "low|medium|high — honest given the corpus size",
  "corpus_meta": { "conversation_count": 0, "synthesis_type": "manual", "generated_at": "", "source_analysis_ids": [] }
}"""

_HARD_RULE_AGG = (
    "You are synthesizing a person across MANY conversations at once. Surface "
    "patterns no single conversation reveals — recurring dynamics, how they show "
    "up differently by setting, tensions between what they say and how they "
    "behave, and drift over time. Regenerate the archetype from the WHOLE corpus; "
    "do NOT just echo the most recent conversation. Be honest about how little a "
    "small corpus can support."
)


def _fmt_ts(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _display_label(label: str, owner_label: str) -> str:
    return "owner" if label == owner_label else label


def render_transcript(utterances: list[dict[str, Any]], owner_label: str) -> str:
    """Speaker-labeled transcript with hesitation fillers stripped."""
    lines: list[str] = []
    for u in utterances:
        text = strip_fillers(u["text"])
        if not text:
            continue
        who = _display_label(u["speaker_label"], owner_label)
        lines.append(f"[{_fmt_ts(float(u['start_sec']))}] {who}: {text}")
    return "\n".join(lines)


def build_prompt(
    *,
    metadata: dict[str, Any],
    owner_label: str,
    speaker_labels: list[str],
    metrics: dict[str, Any],
    utterances: list[dict[str, Any]],
    profile_json: dict[str, Any],
    single_sided: bool = False,
) -> str:
    """Assemble the full copy-ready prompt string."""
    label_map = ", ".join(
        f"{lbl} = {_display_label(lbl, owner_label)}" for lbl in speaker_labels
    )

    meta_lines = [
        f"- Conversation type: {metadata.get('conversation_type') or 'unspecified'}",
        f"- Owner's role: {metadata.get('owner_role') or 'unspecified'}",
        f"- Owner's objective: {metadata.get('objective') or '(none provided)'}",
        f"- Additional context: {metadata.get('context_note') or '(none provided)'}",
    ]

    # When only the owner's microphone was captured, the transcript holds one side
    # of a real dialogue. Claude must be told explicitly, or it will misread the
    # silences and the lopsided metrics.
    single_sided_note = [
        "",
        "## IMPORTANT — one-sided recording",
        "Only the owner's side was captured (the other participant was audible only "
        "in the owner's headphones and is NOT in the transcript). The silent gaps are "
        "the other person speaking. Infer their side ONLY as context from the owner's "
        "words — never fabricate or quote their exact words. Speaker-comparison metrics "
        "(talk-time %, word %, turns, speaker switches) reflect only the captured audio "
        "and are NOT a fair two-way comparison — do not read dominance into them.",
    ] if single_sided else []

    return "\n".join([
        "You are analyzing one transcribed conversation.",
        _HARD_RULE,
        "",
        "## Conversation metadata",
        *meta_lines,
        *single_sided_note,
        "",
        "## Speakers",
        f"The owner ('me') is: {owner_label}.",
        f"Refer to speakers in your output using these names: {label_map}.",
        "",
        "## Deterministic metrics (GROUND TRUTH — cite these, do not invent or recompute)",
        "```json",
        json.dumps(metrics, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Current owner profile (propose an incremental owner_profile_update diff — do NOT rewrite it)",
        "```json",
        json.dumps(profile_json, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Transcript (speaker-labeled; hesitation fillers removed)",
        render_transcript(utterances, owner_label) or "(no speech detected)",
        "",
        "## Output schema — return EXACTLY this JSON object, nothing else",
        SCHEMA_BLOCK,
        "",
        _HARD_RULE,
    ])


def _fmt_conversation_block(conv: dict[str, Any], idx: int) -> str:
    """One conversation's condensed evidence for the aggregate prompt.

    Feeds Claude the interpretive layer (summary, topics, sentiment, owner
    insights) plus the three surviving deterministic metrics (balance, pace,
    length) as ground truth — NOT the full transcript, which would blow up the
    corpus prompt. `ref` is a stable handle Claude cites in its output.
    """
    a = conv.get("analysis") or {}
    m = conv.get("metric_summary") or {}
    topics = ", ".join(
        t.get("topic", "") for t in a.get("key_topics", []) if t.get("topic")
    ) or "(none captured)"
    behaviors = a.get("owner_insights", {}).get("notable_behaviors", []) or []
    sentiment = a.get("overall_sentiment", {}) or {}
    lines = [
        f"### Conversation {conv.get('ref', idx)} — {conv.get('conversation_type') or 'unspecified'}"
        f" (owner role: {conv.get('owner_role') or 'unspecified'}; {conv.get('date') or 'undated'})",
        f"- Summary: {a.get('summary', '(no summary)')}",
        f"- Key topics: {topics}",
        f"- Owner's communication style: {a.get('owner_insights', {}).get('communication_style', '(none)')}",
    ]
    if behaviors:
        lines.append("- Notable behaviors: " + "; ".join(behaviors))
    lines.append(
        f"- Sentiment — owner: {sentiment.get('owner', '?')}; conversation: {sentiment.get('conversation', '?')}"
    )
    if m:
        lines.append(f"- Metrics: {m}")
    return "\n".join(lines)


def build_aggregate_prompt(
    *,
    conversations: list[dict[str, Any]],
    current_aggregate: dict[str, Any] | None,
    corpus_stats: dict[str, Any],
    synthesis_type: str = "manual",
) -> str:
    """Assemble the copy-ready corpus-level prompt (Stage 1; identical for Stage 2).

    `conversations` is a list of condensed per-conversation dicts (see
    aggregate_merge.build_corpus_bundle). `current_aggregate` is the previous
    synthesis (for continuity / incremental context) or None on first run.
    """
    convo_blocks = [
        _fmt_conversation_block(c, i + 1) for i, c in enumerate(conversations)
    ]
    prior = (
        ["## Previous overall synthesis (refine and re-read holistically — do not merely append)",
         "```json",
         json.dumps(current_aggregate, ensure_ascii=False, indent=2),
         "```",
         ""]
        if current_aggregate else
        ["## Previous overall synthesis", "(none yet — this is the first synthesis)", ""]
    )
    return "\n".join([
        "You are building an owner's cross-conversation portrait.",
        _HARD_RULE,
        _HARD_RULE_AGG,
        "",
        "## Corpus at a glance (GROUND TRUTH — these counts are authoritative)",
        "```json",
        json.dumps(corpus_stats, ensure_ascii=False, indent=2),
        "```",
        "",
        *prior,
        f"## The conversations ({len(conversations)} total)",
        "",
        "\n\n".join(convo_blocks) or "(no analysed conversations yet)",
        "",
        "## Output schema — return EXACTLY this JSON object, nothing else",
        SCHEMA_BLOCK_AGG,
        "",
        f"(synthesis_type for this run: {synthesis_type})",
        _HARD_RULE,
    ])
