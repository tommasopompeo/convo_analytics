"""§10 contract validation: strict structure, lenient enum vocab, extras ignored."""
import pytest
from pydantic import ValidationError

from app.models import AnalysisOutput


def _minimal():
    return {
        "summary": "A neutral summary.",
        "overall_sentiment": {"owner": "neutral", "conversation": "positive"},
        "follow_ups": {"questions": [], "actions": []},
        "owner_insights": {"communication_style": "direct", "notable_behaviors": []},
        "owner_profile_update": {
            "recurring_topics_add": [], "communication_style_notes": [],
            "goals_concerns_add": [], "archetype_signal": "",
        },
    }


def test_minimal_valid_object_parses():
    a = AnalysisOutput.model_validate(_minimal())
    assert a.summary == "A neutral summary."
    assert a.key_topics == []  # optional list defaults


def test_missing_required_key_fails_loudly():
    obj = _minimal()
    del obj["summary"]
    with pytest.raises(ValidationError):
        AnalysisOutput.model_validate(obj)


def test_off_vocab_enum_is_accepted():
    obj = _minimal()
    obj["key_topics"] = [{"topic": "pricing", "salience": "med", "driven_by_speaker": "owner"}]
    a = AnalysisOutput.model_validate(obj)  # "med" not rejected
    assert a.key_topics[0].salience == "med"


def test_extra_keys_ignored():
    obj = _minimal()
    obj["unexpected_field"] = {"anything": 1}
    a = AnalysisOutput.model_validate(obj)
    assert not hasattr(a, "unexpected_field")


def test_full_schema_from_spec_parses():
    obj = {
        "summary": "s",
        "key_topics": [{"topic": "t", "salience": "high", "driven_by_speaker": "owner"}],
        "sentiment_arc": [{"segment": "opening", "speaker": "owner", "sentiment": "positive", "note": "n"}],
        "overall_sentiment": {"owner": "positive", "conversation": "neutral"},
        "pivot_points": [{"utterance_ref": "02:10", "from_topic": "a", "to_topic": "b",
                          "initiated_by": "owner", "significance": "high"}],
        "conversation_gaps": [{"type": "unasked_question", "description": "d"}],
        "follow_ups": {"questions": ["q1"], "actions": ["a1"]},
        "owner_insights": {"communication_style": "direct", "notable_behaviors": ["listens"]},
        "owner_profile_update": {"recurring_topics_add": ["travel"],
                                 "communication_style_notes": ["concise"],
                                 "goals_concerns_add": ["trust"], "archetype_signal": "The Closer"},
    }
    a = AnalysisOutput.model_validate(obj)
    assert a.owner_profile_update.archetype_signal == "The Closer"
