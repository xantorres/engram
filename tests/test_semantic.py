import httpx
import pytest

from engram.core import semantic
from engram.extract.client import Extractor, ExtractorConfig


def _extractor(handler):
    return Extractor(ExtractorConfig(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def _answering(text):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    return _extractor(handler)


def test_judge_reports_a_contradiction():
    judge = semantic.Judge(_answering("CONTRADICTS"))
    assert judge("The user lives in Barcelona", "The user lives in Madrid") is not None


def test_judge_says_nothing_about_compatible_facts():
    judge = semantic.Judge(_answering("COMPATIBLE"))
    assert judge("Uses pnpm for the web app", "Uses pnpm for the API") is None


def test_judge_tolerates_surrounding_prose():
    judge = semantic.Judge(_answering("Answer: CONTRADICTS -- the city changed."))
    assert judge("lives in Barcelona", "lives in Madrid") is not None


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), id="offline"),
        pytest.param(lambda r: httpx.Response(500, text="boom"), id="http-error"),
        pytest.param(
            lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "dunno"}}]}),
            id="unparseable",
        ),
    ],
)
def test_judge_fails_open(handler):
    # A stopped LM Studio must degrade detection, never break a capture.
    assert semantic.Judge(_extractor(handler))("lives in Barcelona", "lives in Madrid") is None


def test_consult_is_a_no_op_without_a_judge():
    assert semantic.consult("lives in Barcelona", "lives in Madrid", judge=None) is None


@pytest.mark.parametrize(
    "new,old,asked",
    [
        ("The user lives in Barcelona", "The user lives in Madrid", True),
        ("Prefers pnpm over npm", "Allergic to shellfish", False),
    ],
)
def test_only_plausibly_related_pairs_reach_the_model(new, old, asked):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "COMPATIBLE"}}]})

    semantic.consult(new, old, judge=semantic.Judge(_extractor(handler)))
    assert bool(calls) is asked
