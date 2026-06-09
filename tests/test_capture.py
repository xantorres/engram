import json

from engram.capture.active import remember
from engram.capture.sessions import harvest_session
from engram.core.schema import Kind, LearnedBy, Status
from engram.core.store import MarkdownStore


class Stub:
    def __init__(self, raw: str):
        self.raw = raw

    def complete(self, system: str, user: str) -> str:
        return self.raw


def test_remember_stages_pending(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = remember(store, "I use neovim", kind=Kind.tooling)
    assert mem.status == Status.pending
    assert mem.learned_by == LearnedBy.remember
    assert store.get(mem.id) is not None


def test_harvest_session_claude_code(tmp_path):
    user_turn = {"message": {"role": "user", "content": "I prefer pnpm"}}
    asst_turn = {
        "message": {"role": "assistant", "content": [{"type": "text", "text": "noted"}]}
    }
    fixture = tmp_path / "session.jsonl"
    fixture.write_text("\n".join(json.dumps(r) for r in (user_turn, asst_turn)), encoding="utf-8")

    canned = '{"candidates":[{"fact":"prefers pnpm","kind":"tooling","confidence":0.9}]}'
    store = MarkdownStore(tmp_path / "store")
    staged = harvest_session(
        store, fixture, harness="claude-code", extractor=Stub(canned), min_confidence=0.5
    )
    assert len(staged) == 1
    assert store.get(staged[0].id).source == "harness:claude-code"


def test_harvest_session_rejects_unknown_harness(tmp_path):
    fixture = tmp_path / "s.jsonl"
    fixture.write_text("{}", encoding="utf-8")
    store = MarkdownStore(tmp_path / "store")
    try:
        harvest_session(store, fixture, harness="nope", extractor=Stub("{}"))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
