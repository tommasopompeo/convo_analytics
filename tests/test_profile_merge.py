"""Owner-profile merge: dedupe, idempotency, archetype trail, append-only."""
from app.profile_merge import empty_profile, merge


def test_first_merge_populates_and_dedupes():
    diff = {
        "recurring_topics_add": ["Travel", "travel", "Pricing"],  # dupe (case)
        "communication_style_notes": ["direct"],
        "goals_concerns_add": ["win the deal"],
        "archetype_signal": "The Closer",
    }
    p = merge(empty_profile(), diff, analysis_id=1)
    assert p["recurring_topics"] == ["Travel", "Pricing"]
    assert p["current_archetype"] == "The Closer"
    assert p["version"] == 1
    assert p["source_analysis_ids"] == [1]
    assert len(p["archetype_signal_trail"]) == 1


def test_idempotent_on_same_analysis_id():
    diff = {"recurring_topics_add": ["A"], "communication_style_notes": [],
            "goals_concerns_add": [], "archetype_signal": "X"}
    p1 = merge(empty_profile(), diff, analysis_id=7)
    p2 = merge(p1, diff, analysis_id=7)  # re-ingest same analysis
    assert p2 is p1  # unchanged — no double count
    assert p2["recurring_topics"] == ["A"]


def test_second_analysis_accumulates_and_updates_archetype():
    d1 = {"recurring_topics_add": ["Travel"], "communication_style_notes": ["direct"],
          "goals_concerns_add": [], "archetype_signal": "The Closer"}
    d2 = {"recurring_topics_add": ["Travel", "Pricing"], "communication_style_notes": ["warm"],
          "goals_concerns_add": ["retention"], "archetype_signal": "The Strategist"}
    p1 = merge(empty_profile(), d1, analysis_id=1)
    p2 = merge(p1, d2, analysis_id=2)
    assert p2["recurring_topics"] == ["Travel", "Pricing"]
    assert p2["communication_style_notes"] == ["direct", "warm"]
    assert p2["current_archetype"] == "The Strategist"
    assert len(p2["archetype_signal_trail"]) == 2
    assert p2["source_analysis_ids"] == [1, 2]
    # original object not mutated (append-only semantics)
    assert p1["current_archetype"] == "The Closer"


def test_empty_signal_keeps_prior_archetype():
    p1 = merge(empty_profile(), {"recurring_topics_add": [], "communication_style_notes": [],
                                 "goals_concerns_add": [], "archetype_signal": "The Closer"}, 1)
    p2 = merge(p1, {"recurring_topics_add": ["X"], "communication_style_notes": [],
                    "goals_concerns_add": [], "archetype_signal": ""}, 2)
    assert p2["current_archetype"] == "The Closer"
