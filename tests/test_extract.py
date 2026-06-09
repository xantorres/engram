import httpx

from engram.core.schema import Kind, LearnedBy
from engram.extract.client import Extractor, ExtractorConfig
from engram.extract.harvest import harvest


class Stub:
    """A fake extractor returning a canned completion."""

    def __init__(self, raw: str):
        self.raw = raw

    def complete(self, system: str, user: str) -> str:
        return self.raw


def test_extractor_complete_parses_openai_shape():
    def handler(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    extractor = Extractor(ExtractorConfig(model="m"), client=client)
    assert extractor.complete("sys", "usr") == "hi"


def test_harvest_parses_candidates():
    raw = '{"candidates":[{"fact":"prefers pnpm","kind":"tooling","confidence":0.9}]}'
    mems = harvest("transcript", Stub(raw))
    assert len(mems) == 1
    assert mems[0].kind == Kind.tooling
    assert mems[0].learned_by == LearnedBy.harvest


def test_harvest_tolerates_code_fenced_json():
    raw = '```json\n{"candidates":[{"fact":"uses neovim","kind":"tooling"}]}\n```'
    mems = harvest("t", Stub(raw))
    assert mems[0].fact == "uses neovim"


def test_harvest_filters_below_min_confidence():
    raw = '{"candidates":[{"fact":"x","kind":"tooling","confidence":0.2}]}'
    assert harvest("t", Stub(raw), min_confidence=0.5) == []


def test_harvest_unknown_kind_falls_back():
    raw = '{"candidates":[{"fact":"y","kind":"banana","confidence":0.9}]}'
    assert harvest("t", Stub(raw))[0].kind == Kind.preference


def test_harvest_returns_empty_on_garbage():
    assert harvest("t", Stub("not json at all")) == []
