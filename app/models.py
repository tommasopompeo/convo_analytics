"""Pydantic v2 models for the §10 analysis JSON contract (the paste-back schema).

Validation philosophy (per plan): structure, required keys, and types are
STRICT (fail loudly), but the enum-ish fields (salience / sentiment / gap type)
are kept as free strings so a whole valid analysis is never rejected because
Claude wrote "med" instead of "medium". Extra keys are ignored rather than
rejected — Claude may add harmless commentary fields we don't need.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class KeyTopic(_Base):
    topic: str
    salience: str = ""            # high | medium | low (free string, not enforced)
    driven_by_speaker: str = ""   # owner | speaker_1 | ...


class SentimentArcItem(_Base):
    segment: str = ""             # opening | mid | close | <timestamp>
    speaker: str = ""
    sentiment: str = ""           # positive | neutral | negative | mixed
    note: str = ""


class OverallSentiment(_Base):
    owner: str = ""
    conversation: str = ""


class PivotPoint(_Base):
    utterance_ref: str = ""       # approx timestamp or quote
    from_topic: str = ""
    to_topic: str = ""
    initiated_by: str = ""
    significance: str = ""


class ConversationGap(_Base):
    type: str = ""                # unexplored_topic | unasked_question | unmet_intent
    description: str = ""


class FollowUps(_Base):
    questions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)


class OwnerInsights(_Base):
    communication_style: str = ""
    notable_behaviors: list[str] = Field(default_factory=list)


class OwnerProfileUpdate(_Base):
    recurring_topics_add: list[str] = Field(default_factory=list)
    communication_style_notes: list[str] = Field(default_factory=list)
    goals_concerns_add: list[str] = Field(default_factory=list)
    archetype_signal: str = ""


class AnalysisOutput(_Base):
    """Top-level §10 contract. Required keys are enforced; nested defaults keep
    a slightly-incomplete-but-structurally-valid response usable."""
    summary: str
    key_topics: list[KeyTopic] = Field(default_factory=list)
    sentiment_arc: list[SentimentArcItem] = Field(default_factory=list)
    overall_sentiment: OverallSentiment
    pivot_points: list[PivotPoint] = Field(default_factory=list)
    conversation_gaps: list[ConversationGap] = Field(default_factory=list)
    follow_ups: FollowUps
    owner_insights: OwnerInsights
    owner_profile_update: OwnerProfileUpdate


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate (cross-corpus) synthesis contract.
#
# The SAME schema serves the manual Stage-1 paste loop, the Stage-2 incremental
# update, and the Stage-2 full re-synthesis — only the caller and the input
# bundle differ, never the output shape. Validation mirrors the analysis models:
# structure/required keys strict, everything else lenient (free strings, ignored
# extras) so a whole valid synthesis is never rejected over a wording nit.
# ─────────────────────────────────────────────────────────────────────────────


class ThroughLine(_Base):
    pattern: str = ""
    supporting_conversations: list[str] = Field(default_factory=list)
    note: str = ""


class ShowsUpDifferently(_Base):
    context: str = ""
    how: str = ""
    supporting_conversations: list[str] = Field(default_factory=list)


class RecurringTheme(_Base):
    theme: str = ""
    conversation_count: int = 0
    supporting_conversations: list[str] = Field(default_factory=list)


class Tension(_Base):
    stated: str = ""
    observed: str = ""
    supporting_conversations: list[str] = Field(default_factory=list)


class DriftPoint(_Base):
    conversation: str = ""
    date: str = ""
    signal: str = ""


class Drift(_Base):
    summary: str = ""
    points: list[DriftPoint] = Field(default_factory=list)


class CorpusMeta(_Base):
    conversation_count: int = 0
    synthesis_type: str = "manual"   # manual | incremental | full
    generated_at: str = ""
    source_analysis_ids: list[int] = Field(default_factory=list)


class AggregateOutput(_Base):
    """Top-level cross-corpus synthesis. `portrait` and `archetype` are the only
    hard-required prose; the rest default to empty so a thin-corpus synthesis
    (few conversations) still validates and renders gracefully."""
    portrait: str
    portrait_evidence: list[str] = Field(default_factory=list)
    through_lines: list[ThroughLine] = Field(default_factory=list)
    shows_up_differently: list[ShowsUpDifferently] = Field(default_factory=list)
    recurring_themes: list[RecurringTheme] = Field(default_factory=list)
    tensions: list[Tension] = Field(default_factory=list)
    drift: Drift = Field(default_factory=Drift)
    archetype: str = ""
    confidence: str = ""             # low | medium | high (free string)
    corpus_meta: CorpusMeta = Field(default_factory=CorpusMeta)
