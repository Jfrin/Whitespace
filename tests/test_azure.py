import json

import httpx
import pytest

from whitespace.extract.azure import AzureExtractor
from whitespace.extract.errors import ExtractorStop
from whitespace.extract.quota import DEFAULT_RPM, DEFAULT_TPM, estimated_tokens
from whitespace.extract.run import extract_all
from whitespace.models import Post

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


def test_usage_is_recorded_when_provider_reports_it(db):
    payload = response({"items": [ITEM]}).json()
    payload["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
    ex = make(db, lambda req: httpx.Response(200, json=payload))
    ex(TEXT)
    ex(TEXT)  # cache hit does not invent another usage record
    assert ex.stats["prompt_tokens"] == 100 and ex.stats["completion_tokens"] == 20
    assert ex.stats["usage_reported"] == 1 and ex.stats["usage_missing"] == 0


def test_missing_usage_is_distinct_from_zero_usage(db):
    ex = make(db, lambda req: response({"items": []}))
    ex(TEXT)
    assert ex.stats["usage_missing"] == 1 and ex.stats["usage_reported"] == 0


def test_long_posts_extract_late_feedback_and_cache_every_chunk(db):
    late = "I wish the game had a minimap."
    text = ("ordinary review words " * 180) + late
    calls = []

    def handler(req):
        body = json.loads(req.content)
        content = body["messages"][1]["content"]
        calls.append(content)
        return response({"items": [ITEM] if late in content else []})

    ex = make(db, handler, long_posts=True)
    assert ex.version.endswith(":long-v1")
    assert [item.want_summary for item in ex(text)] == ["minimap"]
    assert len(calls) > 1 and ex.is_cached(text)
    assert ex(text)[0].want_summary == "minimap" and len(calls) > 1


def test_long_post_retries_missing_chunk_without_saving_partial_wants(db):
    late = "I wish the game had a minimap."
    text = ("ordinary review words " * 180) + late
    attempts = []

    def handler(req):
        content = json.loads(req.content)["messages"][1]["content"]
        attempts.append(content)
        if late in content and sum(late in item for item in attempts) == 1:
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": {"content": "bad"}}]}
            )
        return response({"items": [ITEM] if late in content else []})

    ex = make(db, handler, long_posts=True)
    assert ex(text) == [] and not ex.is_cached(text)
    before = len(attempts)
    assert ex(text)[0].want_summary == "minimap" and ex.is_cached(text)
    assert len(attempts) == before + 1


def test_incomplete_long_post_remains_eligible_for_extraction(db):
    text = TEXT + " " + ("ordinary review words " * 180) + " final marker"
    db.upsert_post(Post("s", "1", "u", text, 1))
    db.set_flags(1, "en", {"is_dup": False, "is_low_effort": False})
    calls = []

    def handler(req):
        content = json.loads(req.content)["messages"][1]["content"]
        calls.append(content)
        if "final marker" in content and sum("final marker" in part for part in calls) == 1:
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": {"content": "bad"}}]}
            )
        return response({"items": [ITEM] if TEXT in content else []})

    ex = make(db, handler, long_posts=True, batch_size=1)
    assert extract_all(db, ex, prefilter="all")["items"] == 0
    assert db.count("want_item") == db.count("extraction_attempt") == 0
    assert extract_all(db, ex, prefilter="all")["items"] == 1
    assert db.count("want_item") == db.count("extraction_attempt") == 1


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


@pytest.mark.parametrize("bad", ["oops", "{}", '{"items": "invalid"}'])
def test_invalid_json_is_retried_on_the_next_extract_run(db, bad):
    db.upsert_post(Post("s", "1", "u", TEXT, 1))
    db.set_flags(1, "en", {"is_dup": False, "is_low_effort": False})
    answers = iter([bad, json.dumps({"items": []})])

    def handler(req):
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": next(answers)}}]},
        )

    ex = make(db, handler, batch_size=1)
    assert extract_all(db, ex, prefilter="all")["candidates"] == 1
    assert db.count("extraction_attempt") == 0
    assert extract_all(db, ex, prefilter="all")["candidates"] == 1
    assert db.count("extraction_attempt") == 1


def test_malformed_cached_result_is_replaced_only_after_success(db):
    calls = []

    def handler(req):
        calls.append(req)
        return response({"items": [ITEM]})

    ex = make(db, handler)
    db.llm_cache_put(ex._key(TEXT), ex.model, "{}")
    assert ex.cache_outcome(TEXT) is None
    assert ex(TEXT)[0].want_summary == "minimap"
    assert ex.cache_outcome(TEXT) == "completed"
    assert ex(TEXT)[0].want_summary == "minimap" and len(calls) == 1


def test_rate_limit_retries_using_azure_header(db):
    answers = iter(
        [httpx.Response(429, headers={"retry-after-ms": "1500"}), response({"items": [ITEM]})]
    )
    sleeps = []
    ex = make(db, lambda req: next(answers), sleep=sleeps.append)
    assert len(ex(TEXT)) == 1 and sleeps == [1.5]


def test_retries_reserve_token_budget_for_every_request(db):
    answers = iter([httpx.Response(429), response({"items": [ITEM]})])
    ex = make(db, lambda req: next(answers), sleep=lambda _: None)
    reservations = []
    ex.token_limiter.wait = reservations.append
    assert ex(TEXT)[0].want_summary == "minimap"
    assert reservations == [estimated_tokens(ex._body(TEXT))] * 2


def test_cli_passes_quota_settings_to_extractor(monkeypatch, tmp_path, capsys):
    from whitespace.cli import main

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.delenv("AZURE_OPENAI_RPM", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_TPM", raising=False)
    calls = []

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)
            self.stats = {}

    monkeypatch.setattr("whitespace.extract.azure.AzureExtractor", FakeExtractor)
    monkeypatch.setattr("whitespace.extract.run.extract_all", lambda *a, **kw: {})
    assert main(["extract"]) == 0
    assert calls[-1]["rpm"] == DEFAULT_RPM and calls[-1]["tpm"] == DEFAULT_TPM
    monkeypatch.setenv("AZURE_OPENAI_RPM", "33")
    monkeypatch.setenv("AZURE_OPENAI_TPM", "20000")
    assert main(["extract"]) == 0
    assert calls[-1]["rpm"] == 33 and calls[-1]["tpm"] == 20000
    assert main(["extract", "--rpm", "44", "--tpm", "30000"]) == 0
    assert calls[-1]["rpm"] == 44 and calls[-1]["tpm"] == 30000
    assert main(["extract", "--tpm", "-1"]) == 2
    assert "non-negative" in capsys.readouterr().err


def test_cli_cost_uses_only_explicit_prices_and_reported_tokens(monkeypatch, tmp_path):
    from whitespace.cli import main
    from whitespace.storage import Database

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.delenv("AZURE_OPENAI_INPUT_PRICE_PER_MILLION", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_OUTPUT_PRICE_PER_MILLION", raising=False)

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            self.stats = {
                "usage_reported": 1,
                "usage_missing": 1,
                "prompt_tokens": 1000,
                "completion_tokens": 500,
            }

    monkeypatch.setattr("whitespace.extract.azure.AzureExtractor", FakeExtractor)
    monkeypatch.setattr("whitespace.extract.run.extract_all", lambda *a, **kw: {"candidates": 1})
    assert main(["extract"]) == 0
    assert main(["extract", "--input-price", "2", "--output-price", "4"]) == 0
    db = Database(tmp_path / "whitespace.sqlite")
    rows = db.conn.execute("SELECT metrics FROM stage_run ORDER BY id").fetchall()
    first, second = (json.loads(row[0])["provider"] for row in rows)
    assert "known_estimated_cost_usd" not in first
    assert second["known_estimated_cost_usd"] == pytest.approx(0.004)
    assert second["usage_missing"] == 1  # price covers reported calls only


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


@pytest.mark.parametrize(
    "error",
    [
        {"code": "content_filter"},
        {"innererror": {"code": "ResponsibleAIPolicyViolation"}},
        {"message": "The response was filtered due to Azure's content management policy."},
    ],
)
def test_content_filtered_single_post_is_skipped_and_cached(db, error):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(400, json={"error": error})

    ex = make(db, handler)
    assert ex(TEXT) == [] and ex(TEXT) == []
    assert len(calls) == 1 and ex.stats["filtered_posts"] == 1
    assert json.loads(db.llm_cache_get(ex._key(TEXT)))["filtered"] is True
    assert ex.cache_outcome(TEXT) == "filtered"


def test_filtered_attempt_is_not_counted_as_a_completed_empty_result(db):
    db.upsert_post(Post("s", "1", "u", TEXT, 1))
    db.set_flags(1, "en", {"is_dup": False, "is_low_effort": False})
    ex = make(db, lambda req: httpx.Response(400, json={"error": {"code": "content_filter"}}))
    assert extract_all(db, ex, prefilter="all")["candidates"] == 1
    assert db.conn.execute("SELECT outcome FROM extraction_attempt").fetchone()[0] == "filtered"


def test_filtered_batch_recovers_other_posts_and_extraction_continues(db):
    blocked = "This review triggers Azure's filter."
    another = "An ordinary review with no specific requests."
    for i, text in enumerate([TEXT, blocked, another]):
        db.upsert_post(Post("s", str(i), "u", text, i))
    for row in db.conn.execute("SELECT id FROM post").fetchall():
        db.set_flags(row["id"], "en", {"is_dup": False, "is_low_effort": False})

    calls = []

    def handler(req):
        body = json.loads(req.content)
        content = body["messages"][1]["content"]
        calls.append(content)
        if blocked in content:
            return httpx.Response(400, json={"error": {"code": "content_filter"}})
        if content == TEXT:
            return response({"items": [ITEM]})
        if content.startswith('{"posts":'):
            return response({"results": [{"id": 0, "items": []}]})
        return response({"items": []})

    ex = make(db, handler, batch_size=2)
    stats = extract_all(db, ex, prefilter="all", english_only=False)
    assert stats["candidates"] == 3 and stats["items"] == 1 and "stopped" not in stats
    assert ex.stats["filtered_batches"] == 1 and ex.stats["filtered_posts"] == 1
    assert db.count("want_item") == 1
    assert len(calls) == 4  # blocked batch, two individual posts, then the next batch
    assert ex(blocked) == [] and len(calls) == 4


def test_filtered_completion_is_skipped(db):
    ex = make(
        db,
        lambda req: httpx.Response(
            200, json={"choices": [{"finish_reason": "content_filter", "message": {}}]}
        ),
    )
    assert ex(TEXT) == [] and ex.stats["filtered_posts"] == 1


@pytest.mark.parametrize(
    "payload", [{"error": {"message": "bad request"}}, {"error": {"code": "invalid_api_key"}}, []]
)
def test_other_bad_requests_still_stop(db, payload):
    ex = make(db, lambda req: httpx.Response(400, json=payload))
    with pytest.raises(ExtractorStop, match="400"):
        ex(TEXT)
    assert ex.stats["filtered_posts"] == 0
