"""Read Claude Code session transcripts (``~/.claude/projects/*/*.jsonl``).

Each line is a JSON record; user/assistant entries carry a ``message`` with a
``role`` and ``content`` (a string or a list of typed blocks).
"""

from __future__ import annotations

import json
from pathlib import Path

from engram.capture.readers.base import Turn, flatten_content


def read_session(path: str | Path) -> list[Turn]:
    turns: list[Turn] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        if message is not None and not isinstance(message, dict):
            continue
        message = message or {}
        role = message.get("role") or record.get("role")
        if role not in ("user", "assistant"):
            continue
        turns.append(Turn(role=role, text=flatten_content(message.get("content"))))
    return turns
