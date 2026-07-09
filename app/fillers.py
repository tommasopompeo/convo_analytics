"""Filler-word handling with three explicit, independently-tunable sets.

The key rule (per owner instruction): only genuine NON-LEXICAL hesitation sounds
are ever stripped from the Claude-facing transcript or counted toward the filler
rate. Meaning-bearing discourse markers (cioè, insomma, tipo, "you know", ...)
carry content and must NEVER be scrubbed or counted — inflating the rate with
real words would make the metric misleading.

Two functions, two sets:
  - strip_fillers()  uses STRIP_FROM_TRANSCRIPT  (prompt builder)
  - count_fillers()  uses COUNT_AS_FILLER        (filler-rate metric)
NEVER_STRIP_NEVER_COUNT documents the discourse markers deliberately excluded
from both, so a future editor doesn't "helpfully" add them to either set.
"""
from __future__ import annotations

import re

# Non-lexical hesitation sounds removed from the transcript Claude reads.
STRIP_FROM_TRANSCRIPT: frozenset[str] = frozenset({
    "ehm", "eh", "uhm", "um", "uh", "er", "mm", "hmm", "mah", "boh",
})

# Genuine hesitation markers counted for the (approximate) filler rate.
# Kept as its own set so it can diverge from the strip set later if needed.
COUNT_AS_FILLER: frozenset[str] = frozenset({
    "ehm", "eh", "uhm", "um", "uh", "er", "mm", "hmm", "mah", "boh",
})

# Discourse / content markers that CARRY MEANING: never strip, never count.
# Documented here so the exclusion is explicit and auditable.
NEVER_STRIP_NEVER_COUNT: frozenset[str] = frozenset({
    "cioè", "insomma", "diciamo", "tipo", "like", "you know",
})

# A token = a run of letters/apostrophes; we normalise by casefolding and
# stripping surrounding punctuation before membership tests.
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def _norm(token: str) -> str:
    return token.casefold().strip(".,!?;:…\"'()[]—-")


def count_fillers(text: str) -> int:
    """Count genuine hesitation markers in `text` (case-insensitive).

    Only tokens in COUNT_AS_FILLER are counted; discourse markers are excluded.
    """
    return sum(1 for tok in _TOKEN_RE.findall(text) if _norm(tok) in COUNT_AS_FILLER)


def strip_fillers(text: str) -> str:
    """Remove only non-lexical hesitation sounds, preserving everything else.

    Discourse markers are left in place. Collapses the whitespace and stray
    punctuation left behind by removed tokens so the transcript stays readable.
    """
    def repl(match: re.Match) -> str:
        return "" if _norm(match.group(0)) in STRIP_FROM_TRANSCRIPT else match.group(0)

    cleaned = _TOKEN_RE.sub(repl, text)
    # Tidy artefacts: doubled spaces, space-before-punctuation, leading commas.
    cleaned = re.sub(r"\s+([,.!?;:…])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:]\s*){2,}", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"^[\s,;:]+", "", cleaned)
    return cleaned.strip()
