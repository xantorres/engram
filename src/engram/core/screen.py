"""Capture-time screens: what must never reach the store.

Extraction is cheap and indiscriminate, so the store fills with two kinds of
noise that no amount of downstream review fixes:

* **trivial** - facts anyone could derive from a file path or a `uname` call.
  They cost review attention and crowd out real signal.
* **credential-adjacent** - not secrets themselves, but a map to where the
  secrets live: what the token is called, which keychain service holds it.
  A memory layer is read by every agent the user runs; it is the wrong place
  for a directory of their credential names.

Both screens are conservative. A false positive silently drops a true fact, so
each pattern anchors on structure ("home directory ...", "<credential> is named
<literal>") rather than on a bare keyword.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from engram.core import dedup
from engram.core.schema import Memory, Status

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# "(inferred from launchd jobs and file paths)" - a hedge about provenance that
# disguises an otherwise trivial fact.
_HEDGE = re.compile(r"\s*\([^)]*\)")

# Nearly every harvested fact opens with the same generic subject; stripping it
# lets one pattern cover "uses macOS", "The user runs on macOS", "User uses ...".
_SUBJECT = re.compile(r"(?i)^(?:the\s+)?users?(?:'s|')?\s+")


def _core(fact: str) -> str:
    """The assertion itself, with hedges, generic subject and end punctuation gone."""
    text = _HEDGE.sub("", fact).strip()
    return _SUBJECT.sub("", text).strip().rstrip(". ").strip()


# ---------------------------------------------------------------------------
# Triviality
# ---------------------------------------------------------------------------

# Fewer than this many characters → no durable signal
_TRIVIAL_MIN_CHARS = 20

# Bare filesystem paths (home-dir rooted) that echo where the agent ran
_TRIVIAL_PATH = re.compile(r"^(?:/Users\/|/home/|~/)[\w/.\-]*$")

# The operating system, however it is phrased. Derivable from any absolute path.
_TRIVIAL_PLATFORM = re.compile(
    r"(?i)^(?:uses?|runs?|running|is|works?|develops?)\s+(?:on\s+)?"
    r"(?:mac\s?os\s?x|macos|osx|mac|windows|linux|ubuntu|debian|fedora|arch)\b"
)

# Anything about the home directory: its path, its username, its layout.
_TRIVIAL_HOME = re.compile(r"(?i)^home\s+(?:directory|dir|folder)\b")

# "User identifier is <X>" / "Username is <X>" patterns extracted by naive prompts.
# Three branches:
#   1. Explicit identity prefix (identifier/identity/id/username/login/name) → filter any token.
#   2. Bare "user is <token>" → filter only when token contains at least one digit, @, or .
#      (unambiguous username signal).  All-alpha tokens — including hyphenated role names like
#      "the-project-lead" — survive as legitimate role-description facts.
#   3. Bare "username is <anything>".
_TRIVIAL_USER_ID = re.compile(
    r"(?i)^(?:"
    r"user\s+(?:identifier|identity|id|username|login|name)\s+is\s+\S+"
    r"|"
    r"user\s+is\s+[^\s]*(?:\d|@|\.)[^\s]*"
    r"|"
    r"username\s+is\s+\S+"
    r")$"
)


def triviality(fact: str) -> str | None:
    """Why ``fact`` carries no durable signal, or ``None`` if it does."""
    raw = fact.strip()
    if len(raw) < _TRIVIAL_MIN_CHARS:
        return "too short to carry a durable fact"
    if _TRIVIAL_PATH.match(raw):
        return "a bare filesystem path"
    if _TRIVIAL_USER_ID.match(raw):
        return "a username, derivable from any path"
    core = _core(raw)
    if _TRIVIAL_PLATFORM.match(core):
        return "the operating system, derivable from any path"
    if _TRIVIAL_HOME.match(core):
        return "the home directory, derivable from any path"
    return None


# ---------------------------------------------------------------------------
# Credential-adjacent facts
# ---------------------------------------------------------------------------

# Words that mean a credential and nothing else.
_CREDENTIAL_TERM = re.compile(
    r"(?i)\b(?:passwords?|passphrases?|secrets?|api[\s_-]?keys?|access\s+keys?"
    r"|private\s+keys?|ssh\s+keys?|keychains?|keyrings?|credentials?)\b"
)

# "token" is shared vocabulary - design tokens, CSS tokens, tokenizers - so it
# counts only when something in the phrase makes it an auth token. The same goes
# for "vault": a password vault, not HashiCorp Vault the deployment target.
_QUALIFIED_TOKEN = re.compile(
    r"(?i)\b(?:access|auth\w*|api|bearer|refresh|session|personal\s+access|oauth"
    r"|github|gitlab|npm|pypi|secret)\s+tokens?\b"
    r"|\btokens?\s+(?:is\s+)?(?:named|called)\b"
    r"|\bpassword\s+vault\b"
)

# A credential word alone is still just vocabulary ("prefers token-based auth").
# It becomes a map only when the fact also names or locates the thing. A
# backticked literal is deliberately NOT a locator: half the store uses code
# formatting, and treating it as one flagged design tokens and env vars.
_LOCATOR = re.compile(
    r"(?i)\bis\s+named\b|\bnamed\b|\bcalled\b|\bstored\s+in\b|\blives?\s+in\b"
    r"|\bservice\s+name\b|\bkey\s+name\b|\bunder\s+the\b|\bis\s+in\b|\bkept\s+in\b"
)


def sensitivity(fact: str) -> str | None:
    """Why ``fact`` maps the user's credentials, or ``None`` if it does not."""
    names_a_credential = _CREDENTIAL_TERM.search(fact) or _QUALIFIED_TOKEN.search(fact)
    if names_a_credential and _LOCATOR.search(fact):
        return "names or locates a credential; engram is not a place to map secrets"
    return None


# ---------------------------------------------------------------------------
# Duplication
# ---------------------------------------------------------------------------


def duplicate_of(fact: str, existing: Iterable[Memory]) -> str | None:
    """The id of a live memory ``fact`` restates, if any.

    Rejected memories are ignored: a fact the user retracted must stay
    re-learnable, otherwise one rejection silences a subject forever.
    """
    for memory in existing:
        if memory.status == Status.rejected:
            continue
        if dedup.compare(fact, memory.fact) == "duplicate":
            return memory.id
    return None


# ---------------------------------------------------------------------------
# The composed gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    admitted: bool
    reason: str = ""
    category: str = ""
    duplicate_of: str | None = None


def assess(
    fact: str,
    *,
    existing: Iterable[Memory] = (),
    check_trivial: bool = True,
) -> Verdict:
    """Screen a candidate before it is staged.

    ``check_trivial`` is off for deliberate capture: when a human or an agent
    names a fact on purpose, engram is not better placed than they are to call
    it worthless. The credential and duplicate screens always apply.
    """
    reason = sensitivity(fact)
    if reason:
        return Verdict(False, reason, "sensitive")
    if check_trivial:
        reason = triviality(fact)
        if reason:
            return Verdict(False, reason, "trivial")
    duplicate = duplicate_of(fact, existing)
    if duplicate:
        return Verdict(False, f"already known as {duplicate}", "duplicate", duplicate)
    return Verdict(True)
