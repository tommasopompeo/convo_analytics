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
  "who_i_am": "3-5 sentence holistic portrait/read of the owner across ALL conversations — a synthesis, NOT a restatement of the most recent one",
  "current_issues": ["a key issue, goal, conflict, or concern currently occupying the owner"],
  "recurrent_topics": ["topics that frequently surface across multiple conversations"],
  "strong_opinions": ["strongly held beliefs, values, or stances the owner has expressed"],
  "tone_and_sentiment": "summary of the owner's general communication tone and dominant sentiment/emotional register",
  "corpus_meta": { "conversation_count": 0, "synthesis_type": "manual", "generated_at": "", "source_analysis_ids": [] }
}"""

_HARD_RULE_AGG = (
    "You are synthesizing a person across MANY conversations at once. Surface "
    "patterns no single conversation reveals — who they are, their current issues, "
    "recurrent topics, strong opinions, and tone/sentiment. "
    "Regenerate the portrait from the WHOLE corpus; "
    "do NOT just echo the most recent conversation. Be honest about how little a "
    "small corpus can support. "
    "IMPORTANT: Respect any manual overrides provided in the 'Current user-edited profile' "
    "and individual conversation 'User comment/missing context override' fields. "
    "These represent the user's explicit corrections and ground truth."
)


def _fmt_ts(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _display_label(
    label: str,
    owner_label: str,
    speaker_mapping: dict[str, dict[str, Any]] | None = None,
) -> str:
    if speaker_mapping and label in speaker_mapping:
        info = speaker_mapping[label]
        if info.get("is_owner"):
            return "you"
        return info.get("local_name") or label
    return "you" if label == owner_label else label


def render_transcript(
    utterances: list[dict[str, Any]],
    owner_label: str,
    speaker_mapping: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Speaker-labeled transcript with hesitation fillers stripped."""
    lines: list[str] = []
    for u in utterances:
        text = strip_fillers(u["text"])
        if not text:
            continue
        who = _display_label(u["speaker_label"], owner_label, speaker_mapping)
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
    speaker_mapping: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Assemble the full copy-ready prompt string."""
    label_map = ", ".join(
        f"{lbl} = {_display_label(lbl, owner_label, speaker_mapping)}" for lbl in speaker_labels
    )

    meta_lines = [
        f"- Conversation type: {metadata.get('conversation_type') or 'unspecified'}",
        f"- Date of conversation: {metadata.get('recorded_date') or 'unspecified'}",
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
        f"The owner ('me'/'you') is: {owner_label}.",
        f"Refer to speakers in your output using these names: {label_map}.",
        "",
        "CRITICAL INSTRUCTIONS FOR SPEAKERS:",
        "1. You must refer to the owner (the logged-in user) in the first person as 'you' or 'your' throughout your entire output (e.g. 'you said', 'your tone', 'your objective'). When filling the speaker or driven_by_speaker fields in the JSON response, refer to the owner as 'you'.",
        "2. You must refer to other speakers using their actual mapped names (e.g. 'Costantino') instead of generic placeholders like 'speaker_2' or 'Speaker 2'.",
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
        render_transcript(utterances, owner_label, speaker_mapping) or "(no speech detected)",
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
    if conv.get("user_comment"):
        lines.append(f"- User comment/missing context override: {conv['user_comment']}")
    if m:
        lines.append(f"- Metrics: {m}")
    return "\n".join(lines)


def build_aggregate_prompt(
    *,
    conversations: list[dict[str, Any]],
    current_aggregate: dict[str, Any] | None,
    user_profile: dict[str, Any] | None = None,
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
    profile_block = []
    if user_profile:
        clean_profile = {
            "who_i_am": user_profile.get("who_i_am", ""),
            "current_issues": user_profile.get("current_issues", []),
            "recurrent_topics": user_profile.get("recurrent_topics", []),
            "strong_opinions": user_profile.get("strong_opinions", []),
            "tone_and_sentiment": user_profile.get("tone_and_sentiment", ""),
        }
        profile_block = [
            "## Current user-edited profile (MANUAL OVERRIDES — respect these edits/feedback)",
            "```json",
            json.dumps(clean_profile, ensure_ascii=False, indent=2),
            "```",
            ""
        ]
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
        *profile_block,
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
