"""Shared helpers for turning harness session files into plain text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Turn:
    role: str
    text: str


def turns_to_text(turns: list[Turn]) -> str:
    return "\n".join(f"{t.role}: {t.text}" for t in turns if t.text.strip())


def flatten_content(content: object) -> str:
    """Flatten a string or a list of content blocks into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text"):
                parts.append(str(block.get("text", "")))
        return " ".join(p for p in parts if p)
    return ""


def generic_jsonl_turns(path: str | Path) -> list[Turn]:
    """Best-effort reader for JSONL transcripts of unknown-but-common shape."""
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
        message = message if isinstance(message, dict) else record
        role = message.get("role") or record.get("role")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content")
        if content is None:
            content = record.get("text")
        turns.append(Turn(role=role, text=flatten_content(content)))
    return turns
