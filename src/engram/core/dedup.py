"""Heuristic duplicate / conflict detection between two memory facts.

The goal is not perfect semantics but a safe gate: catch obvious restatements
(so we do not store the same fact twice) and catch value divergences on the same
subject (so a changed number forces human review instead of a silent overwrite).
"""

from __future__ import annotations

import re

# Precision tokens carry exact values that must match or signal a conflict.
_PRECISION_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO date
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),  # IBAN-ish
    re.compile(r"\b\d+[.,]\d{2}\b"),  # money
    # Identifiers (TIC/VAT/passport/...). A digit is required: an identifier
    # carries a value, while a run of capitals with none is just a word being
    # shouted ("CODEOWNER"), and reading one as a value makes every fact that
    # mentions the same subject disagree with every other.
    re.compile(r"\b(?![A-Z]+\b)[A-Z0-9]{7,}\b"),
]

_STOPWORDS = frozenset(
    "the a an of to in on for and or is are was were be been being with at by"
    " my i you he she it we they this that".split()
)

# Facts name the same thing with different punctuation ("code-graph" vs
# "codegraph"), so each compound also yields its joined spelling. The parts are
# kept: a compound carries most of a fact's discriminating weight, and replacing
# "a-b-c" with one token leaves only sentence boilerplate to compare on, at
# which point every fact sharing a template looks like a duplicate of every
# other. Emitting both forms buys the match without paying that.
_SEPARATORS = re.compile(r"[-_]")
_COMPOUND = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)+")

_DUP_THRESHOLD = 0.5
_CONFLICT_OVERLAP = 0.34

# Words that assert a role rather than name a subject. Two facts agreeing on
# nothing but these have matched a sentence frame, not a topic.
MARKER_TOKENS = frozenset(
    {
        "primary",
        "sole",
        "only",
        "main",
        "default",
        "preferred",
        "current",
        "currently",
        "replace",
        "replaces",
        "replaced",
        "replacing",
        "supersede",
        "supersedes",
        "instead",
    }
)

# The vocabulary every fact in a personal store is built from. These words say
# that a sentence is about the user and what they like; they never say which
# thing it is about. Two facts agreeing on nothing but these agree on nothing.
# Stems, because that is what the tokenizer emits.
FRAME_TOKENS = frozenset(
    {
        "user",
        "prefer",
        "preferr",
        "preferenc",
        "preference",
        "use",
        "used",
        "using",
        "work",
        "has",
        "have",
        "their",
        "them",
    }
)

# Short words are too common to prove two facts share a subject ("tool", "user").
MIN_ANCHOR_LEN = 6

# Two short facts built from the same sentence frame - "the user runs on X" and
# "the user runs on Y" - overlap on everything except the one word that *is* the
# fact, which is enough ratio to look identical. Requiring a few shared words in
# absolute terms means agreement has to rest on more than the frame.
_MIN_SHARED_TOKENS = 3


def precision_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for pat in _PRECISION_PATTERNS:
        out.update(m.group(0) for m in pat.finditer(text))
    return out


def _keep(word: str) -> bool:
    return len(word) >= 3 and word not in _STOPWORDS


def _stem(word: str) -> str:
    """Strip a common inflection so ``prefers`` and ``prefer`` compare equal.

    Deliberately crude: both facts are tokenized the same way, so an over-eager
    strip costs nothing as long as it is consistent. Without it a plural and its
    singular look like two different words, which reads as a substituted value.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def salient_tokens(text: str) -> set[str]:
    lowered = text.lower()
    out = {_stem(w) for w in re.findall(r"[a-z0-9]+", lowered) if _keep(w)}
    out.update(
        _stem(joined)
        for compound in _COMPOUND.findall(lowered)
        if _keep(joined := _SEPARATORS.sub("", compound))
    )
    return out


def subject_tokens(text: str) -> set[str]:
    """The tokens naming what a fact is *about*, with the frame stripped out."""
    return salient_tokens(text) - FRAME_TOKENS - MARKER_TOKENS


def _anchor(ta: set[str], tb: set[str]) -> str | None:
    candidates = [t for t in (ta & tb) - MARKER_TOKENS if len(t) >= MIN_ANCHOR_LEN]
    return max(candidates, key=len) if candidates else None


def shared_anchor(a: str, b: str) -> str | None:
    """The longest distinctive token both facts name, if any."""
    return _anchor(salient_tokens(a), salient_tokens(b))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compare(a: str, b: str) -> str:
    """Return ``"duplicate"``, ``"conflict"``, or ``"distinct"`` for two facts."""
    ta, tb = salient_tokens(a), salient_tokens(b)
    overlap = _jaccard(ta, tb)
    shared = len(ta & tb)
    pa, pb = precision_tokens(a), precision_tokens(b)

    # The floor guards sameness only. A conflict already rests on two precise
    # values disagreeing, which is evidence in itself - "VAT is 123" against
    # "VAT is 999" shares little else, and should still be caught. Both sides
    # must carry one: a value the other fact never mentions is added detail, and
    # two applications to different companies, one of them dated, are not two
    # answers to the same question.
    if overlap >= _CONFLICT_OVERLAP and pa and pb and pa != pb:
        return "conflict"
    if overlap >= _DUP_THRESHOLD and shared >= _MIN_SHARED_TOKENS and pa == pb:
        # Agreeing on the frame is not agreeing on the answer. When each fact
        # holds a word the other lacks and nothing distinctive is shared, the
        # subject itself is what differs: one value was substituted for another.
        # Calling that a duplicate drops the correction and leaves the fact it
        # corrected in recall, which is how a retired tool stays "current".
        if (ta - tb) and (tb - ta) and _anchor(ta, tb) is None:
            return "conflict"
        return "duplicate"
    return "distinct"
