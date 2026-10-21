import json

import httpx
import pytest

from whitespace.extract.appid import AppIndex
from whitespace.extract.errors import ExtractorStop
from whitespace.extract.gemini import (
    PROMPT_VERSION,
    GeminiExtractor,
    list_models,
    quote_in_text,
)
from whitespace.extract.run import extract_all
from whitespace.extract.schema import WantItem
from whitespace.models import Post

TEXT = (
    "I wish there was some kind of checkpoint between the big chunks of the game, "
    "like some rogue likes have."
)
ITEM = {
    "request_type": "feature_wish",
    "subject": "mechanic",
    "want_summary": "checkpoints",
    "intensity": 2,
    "quote": "I wish there was some kind of checkpoint between the big chunks of the game",
    "confidence": 0.9,
    "referenced_games": ["Hades"],
}


def reply(items):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"items": items})}]}}]}


def make(db, handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    ex = GeminiExtractor(
        db, "KEY", model="gemini-test", client=client, rpm=0, sleep=sleeps.append, **kw
    )
    return ex, sleeps


def quota_429(per_day=False, retry="7s"):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry}]
    if per_day:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
            }
        )
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    return httpx.Response(429, json=body)


def test_request_shape(db):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=reply([]))

    ex, _ = make(db, handler)
    ex(TEXT)
    assert seen["url"].endswith("/models/gemini-test:generateContent") and seen["key"] == "KEY"
    body = seen["body"]
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["temperature"] == 0
    assert cfg["responseJsonSchema"]["required"] == ["items"]
    assert body["contents"][0]["parts"][0]["text"] == TEXT
    assert "systemInstruction" in body


def test_parses_validates_and_resolves_appids(db):
    index = AppIndex({1145360: "Hades"})
    ex, _ = make(db, lambda req: httpx.Response(200, json=reply([ITEM])), index=index)
    [item] = ex(TEXT)
    assert item.want_summary == "checkpoints" and item.request_type == "feature_wish"
    assert item.referenced_games == ["Hades"] and item.referenced_appids == [1145360]
    assert ex.version == f"{PROMPT_VERSION}:gemini-test" and ex.stats["items"] == 1


def test_invented_quotes_and_off_schema_items_are_dropped(db):
    fake = {**ITEM, "quote": "this sentence is not in the post at all"}
    bad_type = {**ITEM, "request_type": "nope"}
    ex, _ = make(db, lambda req: httpx.Response(200, json=reply([fake, bad_type, ITEM])))
    assert len(ex(TEXT)) == 1
    assert ex.stats["bad_quote"] == 1 and ex.stats["invalid"] == 1


def test_responses_are_cached_across_calls_and_instances(db):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=reply([ITEM]))

    ex, _ = make(db, handler)
    ex(TEXT)
    ex(TEXT)
    ex2, _ = make(db, handler)  # new instance, same database
    assert len(ex2(TEXT)) == 1
    assert len(calls) == 1 and ex.stats["cache_hits"] == 1 and ex2.stats["cache_hits"] == 1


def test_per_minute_429_waits_the_advertised_delay_then_retries(db):
    answers = iter([quota_429(retry="7s"), httpx.Response(200, json=reply([ITEM]))])
    ex, sleeps = make(db, lambda req: next(answers))
    assert len(ex(TEXT)) == 1
    assert sleeps == [8.0] and ex.stats["retries"] == 1


def test_daily_quota_429_stops_the_run(db):
    ex, _ = make(db, lambda req: quota_429(per_day=True))
    with pytest.raises(ExtractorStop, match="daily quota"):
        ex(TEXT)


def test_server_errors_retry_then_stop(db):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503)

    ex, _ = make(db, handler)
    with pytest.raises(ExtractorStop, match="giving up"):
        ex(TEXT)
    assert len(calls) == 3  # first try + 2 retries


def test_auth_and_unknown_model_errors_stop_immediately(db):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(403, json={"error": {"message": "API key not valid"}})

    ex, _ = make(db, handler)
    with pytest.raises(ExtractorStop, match="403"):
        ex(TEXT)
    assert len(calls) == 1


def test_blocked_prompt_is_empty_and_cached(db):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    ex, _ = make(db, handler)
    assert ex(TEXT) == [] and ex(TEXT) == [] and len(calls) == 1


def test_unparseable_output_is_empty_and_not_cached(db):
    answers = iter(
        [
            httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "oops"}]}}]}),
            httpx.Response(200, json=reply([ITEM])),
        ]
    )
    ex, _ = make(db, lambda req: next(answers))
    assert ex(TEXT) == [] and ex.stats["bad_json"] == 1
    assert len(ex(TEXT)) == 1  # retried because the failure wasn't cached


def test_markup_is_stripped_before_sending_and_quotes_still_verify(db):
    sent = {}

    def handler(req):
        sent["text"] = json.loads(req.content)["contents"][0]["parts"][0]["text"]
        item = {**ITEM, "quote": "I wish it had co-op"}
        return httpx.Response(200, json=reply([item]))

    ex, _ = make(db, handler)
    assert len(ex("[h1]I wish it had co-op[/h1] [b]please[/b]")) == 1
    assert sent["text"] == "I wish it had co-op please"


def test_quote_matching_is_forgiving_about_case_spacing_and_curly_quotes():
    text = "It doesn\u2019t have   a MAP.  Really."
    assert quote_in_text("it doesn't have a map", text)
    assert quote_in_text('"it doesn\'t have a map..."', text)
    assert not quote_in_text("", text) and not quote_in_text("totally different", text)


class FakeLLM:
    """Stands in for GeminiExtractor: stops the run on the third call."""

    version = "fake-v9"

    def __init__(self):
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        if self.calls == 3:
            raise ExtractorStop("quota")
        return [WantItem("feature_wish", "mechanic", f"want {self.calls}", 2, "q", 0.8)]


def _posts(db, n):
    for i in range(n):
        db.upsert_post(Post("s", str(i), "u", f"plain review number {i} with nothing special", i))
    for r in db.conn.execute("SELECT id FROM post").fetchall():
        db.set_flags(r["id"], "en", {"is_dup": False, "is_low_effort": False})


def test_extract_all_all_mode_sends_untriggered_posts_and_keeps_progress_on_stop(db):
    _posts(db, 4)
    llm = FakeLLM()
    stats = extract_all(db, llm, prefilter="all")
    assert stats["stopped"] == 1 and stats["items"] == 2 and stats["candidates"] == 2
    rows = db.conn.execute("SELECT extractor_version FROM want_item").fetchall()
    assert [r["extractor_version"] for r in rows] == ["fake-v9", "fake-v9"]
    # the cue prefilter would have skipped all of them (no trigger words)
    assert extract_all(db, lambda t: [], prefilter="cue")["candidates"] == 0


def test_list_models_returns_only_generate_capable_ids():
    body = {
        "models": [
            {
                "name": "models/gemini-2.5-flash-lite",
                "supportedGenerationMethods": ["generateContent"],
            },
            {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]},
        ]
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    assert list_models("KEY", client) == ["gemini-2.5-flash-lite"]


def test_cli_extract_without_a_key_explains_itself(monkeypatch, tmp_path, capsys):
    from whitespace.cli import main

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["extract", "--extractor", "gemini"]) == 2
    assert "aistudio.google.com" in capsys.readouterr().err
    assert main(["gemini-models"]) == 2


TEXT_B = "The game has no map. I would love a minimap, honestly."
ITEM_B = {
    "request_type": "feature_wish",
    "subject": "ux",
    "want_summary": "minimap",
    "intensity": 2,
    "quote": "I would love a minimap",
    "confidence": 0.8,
    "referenced_games": [],
}


def batch_reply(per_id):
    results = [{"id": k, "items": v} for k, v in per_id.items()]
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"results": results})}]}}]}


def sent_ids(req):
    text = json.loads(req.content)["contents"][0]["parts"][0]["text"]
    return [p["id"] for p in json.loads(text)["posts"]]


def test_batch_sends_several_posts_in_one_request_and_caches_each(db):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=batch_reply({0: [ITEM], 1: [ITEM_B]}))

    ex, _ = make(db, handler)
    [a, b] = ex.extract_batch([TEXT, TEXT_B])
    assert a[0].want_summary == "checkpoints" and b[0].want_summary == "minimap"
    assert len(seen) == 1
    assert "results" in seen[0]["generationConfig"]["responseJsonSchema"]["properties"]
    assert "BATCH MODE" in seen[0]["systemInstruction"]["parts"][0]["text"]
    assert len(ex(TEXT)) == 1 and len(seen) == 1  # single-post call hits the cache


def test_batches_respect_batch_size(db):
    calls = []

    def handler(req):
        ids = sent_ids(req)
        calls.append(ids)
        return httpx.Response(200, json=batch_reply({k: [] for k in ids}))

    ex, _ = make(db, handler, batch_size=2)
    ex.extract_batch([f"post number {i} has some words in it" for i in range(5)])
    assert calls == [[0, 1], [0, 1], [0]]


def test_batches_respect_the_character_budget(db):
    calls = []

    def handler(req):
        ids = sent_ids(req)
        calls.append(ids)
        return httpx.Response(200, json=batch_reply({k: [] for k in ids}))

    ex, _ = make(db, handler, batch_size=10, batch_chars=100)
    ex.extract_batch([("word " * 15) + str(i) for i in range(4)])  # ~77 chars each
    assert calls == [[0], [0], [0], [0]]


def test_a_quote_from_another_post_is_rejected(db):
    ex, _ = make(db, lambda req: httpx.Response(200, json=batch_reply({0: [ITEM_B], 1: [ITEM_B]})))
    [a, b] = ex.extract_batch([TEXT, TEXT_B])
    assert a == [] and len(b) == 1 and ex.stats["bad_quote"] == 1


def test_posts_missing_from_the_response_are_retried_next_time(db):
    calls = []

    def handler(req):
        ids = sent_ids(req)
        calls.append(ids)
        return httpx.Response(200, json=batch_reply({0: [ITEM]}))  # id 1 never answered

    ex, _ = make(db, handler)
    [a, b] = ex.extract_batch([TEXT, TEXT_B])
    assert len(a) == 1 and b == [] and ex.stats["missing"] == 1
    ex.extract_batch([TEXT, TEXT_B])  # A is cached, only B again
    assert calls == [[0, 1], [0]]


def test_unparseable_batch_falls_back_to_one_request_per_post(db):
    answers = iter(
        [
            httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "oops"}]}}]}),
            httpx.Response(200, json=reply([ITEM])),
            httpx.Response(200, json=reply([ITEM_B])),
        ]
    )
    ex, _ = make(db, lambda req: next(answers))
    [a, b] = ex.extract_batch([TEXT, TEXT_B])
    assert a[0].want_summary == "checkpoints" and b[0].want_summary == "minimap"
    assert ex.stats["bad_json"] >= 1


class FakeBatcher:
    batch_size = 3

    def __init__(self):
        self.sizes = []

    def __call__(self, text):
        raise AssertionError("single calls should not be used when batching")

    def extract_batch(self, texts):
        self.sizes.append(len(texts))
        if len(self.sizes) == 2:
            raise ExtractorStop("quota")
        return [[WantItem("feature_wish", "mechanic", "x", 2, "q", 0.8)] for _ in texts]


def test_extract_all_sends_groups_and_keeps_finished_groups_when_stopped(db):
    _posts(db, 7)
    ex = FakeBatcher()
    stats = extract_all(db, ex, prefilter="all")
    assert ex.sizes == [3, 3]
    assert stats["stopped"] == 1 and stats["candidates"] == 3 and stats["items"] == 3
    assert db.count("want_item") == 3
