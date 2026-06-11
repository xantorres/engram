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


def test_claude_code_reader_skips_non_dict_records(tmp_path):
    from engram.capture.readers.claude_code import read_session

    lines = [
        json.dumps([1, 2]),
        json.dumps("hello"),
        json.dumps({"message": "not-a-dict", "role": "user"}),
        json.dumps({"message": {"role": "user", "content": "I prefer pnpm"}}),
    ]
    fixture = tmp_path / "session.jsonl"
    fixture.write_text("\n".join(lines), encoding="utf-8")

    turns = read_session(fixture)
    assert len(turns) == 1
    assert turns[0].role == "user"
    assert turns[0].text == "I prefer pnpm"


def test_remember_normalizes_whitespace(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = remember(store, "  uses   neovim\tbtw  ", kind=Kind.tooling)
    assert mem.fact == "uses neovim btw"


def test_harvest_session_claude_code(tmp_path):
    user_turn = {"message": {"role": "user", "content": "I prefer pnpm"}}
    asst_turn = {
        "message": {"role": "assistant", "content": [{"type": "text", "text": "noted"}]}
    }
    fixture = tmp_path / "session.jsonl"
    fixture.write_text("\n".join(json.dumps(r) for r in (user_turn, asst_turn)), encoding="utf-8")

    canned = (
        '{"candidates":[{"fact":"prefers pnpm over npm for package management",'
        '"kind":"tooling","confidence":0.9}]}'
    )
    store = MarkdownStore(tmp_path / "store")
    result = harvest_session(
        store, fixture, harness="claude-code", extractor=Stub(canned), min_confidence=0.5
    )
    assert result["staged"] == 1
    assert store.get(result["memories"][0].id).source.startswith("harness:claude-code")


def test_harvest_session_caps_input(tmp_path):
    fixture = tmp_path / "big.jsonl"
    fixture.write_text(
        json.dumps({"message": {"role": "user", "content": "x" * 50000}}), encoding="utf-8"
    )
    captured = {}

    class CapturingStub:
        def complete(self, system, user):
            captured["user"] = user
            return '{"candidates":[]}'

    harvest_session(
        MarkdownStore(tmp_path / "store"),
        fixture,
        harness="claude-code",
        extractor=CapturingStub(),
        max_chars=1000,
    )
    assert len(captured["user"]) <= 1000


def test_harvest_session_rejects_unknown_harness(tmp_path):
    fixture = tmp_path / "s.jsonl"
    fixture.write_text("{}", encoding="utf-8")
    store = MarkdownStore(tmp_path / "store")
    try:
        harvest_session(store, fixture, harness="nope", extractor=Stub("{}"))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_harvest_session_returns_dict(tmp_path):
    user_turn = {"message": {"role": "user", "content": "I prefer pnpm"}}
    fixture = tmp_path / "session.jsonl"
    fixture.write_text(json.dumps(user_turn), encoding="utf-8")
    canned = (
        '{"candidates":[{"fact":"prefers pnpm over npm for installs",'
        '"kind":"tooling","confidence":0.9}]}'
    )
    store = MarkdownStore(tmp_path / "store")
    result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
    assert isinstance(result, dict)
    assert "staged" in result and "memories" in result
