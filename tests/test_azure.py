import json

import httpx
import pytest

from whitespace.extract.azure import AzureExtractor
from whitespace.extract.errors import ExtractorStop

TEXT = "I wish the game had a minimap."
ITEM = {
    "request_type": "feature_wish",
    "subject": "ux",
    "want_summary": "minimap",
    "intensity": 2,
    "quote": "I wish the game had a minimap",
    "confidence": 0.9,
    "referenced_games": [],
}


def response(payload):
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]},
    )


def make(db, handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AzureExtractor(
        db, "secret", "https://resource.openai.azure.com/", client=client, rpm=0, **kwargs
    )


def test_request_and_cache(db):
    calls = []

    def handler(req):
        calls.append(req)
        return response({"items": [ITEM]})

    ex = make(db, handler)
    assert ex(TEXT)[0].want_summary == "minimap"
    assert len(ex(TEXT)) == 1 and len(calls) == 1
    req = calls[0]
    assert str(req.url) == "https://resource.openai.azure.com/openai/v1/chat/completions"
    assert req.headers["api-key"] == "secret"
    body = json.loads(req.content)
    assert body["model"] == "gpt-4.1-mini"
    assert body["messages"][1]["content"] == TEXT
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["items"]["items"]["additionalProperties"] is False
    assert ex.version == "azure-v3:gpt-4.1-mini"


def test_batch_validation_and_cache(db):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return response({"results": [{"id": 0, "items": [ITEM]}, {"id": 1, "items": [ITEM]}]})

    ex = make(db, handler)
    a, b = ex.extract_batch([TEXT, "A different post about games."])
    assert len(a) == 1 and b == [] and ex.stats["bad_quote"] == 1
    assert "results" in calls[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert len(ex(TEXT)) == 1 and len(calls) == 1


def test_rate_limit_retries_using_azure_header(db):
    answers = iter(
        [httpx.Response(429, headers={"retry-after-ms": "1500"}), response({"items": [ITEM]})]
    )
    sleeps = []
    ex = make(db, lambda req: next(answers), sleep=sleeps.append)
    assert len(ex(TEXT)) == 1 and sleeps == [1.5]


@pytest.mark.parametrize(
    "choice",
    [
        {"finish_reason": "length", "message": {"content": "{}"}},
        {"finish_reason": "stop", "message": {"refusal": "no", "content": None}},
    ],
)
def test_incomplete_and_refused_responses_stop_without_caching(db, choice):
    ex = make(db, lambda req: httpx.Response(200, json={"choices": [choice]}))
    with pytest.raises(ExtractorStop, match="invalid response"):
        ex(TEXT)
    assert db.llm_cache_get(ex._key(TEXT)) is None


def test_default_cli_requires_azure_credentials(monkeypatch, tmp_path, capsys):
    from whitespace.cli import main

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    assert main(["extract"]) == 2
    assert "AZURE_OPENAI_API_KEY" in capsys.readouterr().err
