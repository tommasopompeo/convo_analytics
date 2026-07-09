"""Filler handling: hesitation sounds are stripped/counted; discourse markers
that carry meaning (cioè, insomma, tipo, ...) are NEVER stripped or counted."""
from app.fillers import count_fillers, strip_fillers


def test_strip_removes_hesitation_sounds():
    out = strip_fillers("Ehm, io penso, uh, di sì")
    assert "ehm" not in out.lower()
    assert "uh" not in out.lower().split()
    assert "penso" in out


def test_strip_preserves_discourse_markers():
    text = "Cioè, insomma, diciamo che tipo è così, you know"
    out = strip_fillers(text)
    for keeper in ("cioè", "insomma", "diciamo", "tipo"):
        assert keeper in out.lower()
    assert "you know" in out.lower()


def test_count_counts_only_hesitation():
    # ehm + uh = 2; cioè / insomma must NOT inflate the count.
    assert count_fillers("ehm io, cioè, insomma, uh sì") == 2


def test_count_ignores_discourse_markers_entirely():
    assert count_fillers("cioè insomma diciamo tipo like") == 0


def test_count_is_case_and_punctuation_insensitive():
    assert count_fillers("Ehm... UH! um?") == 3
