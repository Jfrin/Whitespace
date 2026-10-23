import httpx

from whitespace.catalog import parse_appdetails, referenced_appids, refresh_catalog
from whitespace.storage import RawStore

STARDEW = {
    "413150": {
        "success": True,
        "data": {
            "name": "Stardew Valley",
            "is_free": False,
            "price_overview": {"final": 1499},
            "genres": [
                {"id": "23", "description": "Indie"},
                {"id": "3", "description": "RPG"},
                {"id": "28", "description": "Simulation"},
            ],
            "categories": [
                {"id": 1, "description": "Multi-player"},
                {"id": 38, "description": "Co-op"},
            ],
            "release_date": {"coming_soon": False, "date": "26 Feb, 2016"},
        },
    }
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_appdetails_extracts_genres_categories_price():
    g = parse_appdetails(413150, STARDEW)
    assert g.name == "Stardew Valley"
    assert g.genres == ["Indie", "RPG", "Simulation"]
    assert g.tags == ["Multi-player", "Co-op"]
    assert g.price == 14.99 and g.release_date == "26 Feb, 2016"


def test_parse_appdetails_free_game_and_failures():
    free = {"1": {"success": True, "data": {"name": "F2P", "is_free": True}}}
    assert parse_appdetails(1, free).price == 0.0
    assert parse_appdetails(2, {"2": {"success": False}}) is None
    assert parse_appdetails(3, {}) is None
    assert parse_appdetails(4, []) is None  # Steam sometimes returns a bare list


def test_refresh_fetches_stubs_misses_and_caches(db, limiter):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        appid = req.url.params["appids"]
        calls.append(appid)
        body = STARDEW if appid == "413150" else {appid: {"success": False}}
        return httpx.Response(200, json=body)

    with _client(handler) as client:
        s1 = refresh_catalog(db, client, limiter, [413150, 999])
        s2 = refresh_catalog(db, client, limiter, [413150, 999])
    assert s1["fetched"] == 1 and s1["missing"] == 1
    assert db.catalog_genres(413150) == ["Indie", "RPG", "Simulation"]
    assert db.catalog_genres(999) == []  # stub row, no genres
    assert db.count("catalog_game") == 2
    assert s2["fresh"] == 2 and len(calls) == 2  # second run made no requests


def test_rate_limit_stops_the_run_and_keeps_earlier_work(db, limiter):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params["appids"] == "777":
            return httpx.Response(429)
        return httpx.Response(200, json=STARDEW)

    with _client(handler) as client:
        stats = refresh_catalog(db, client, limiter, [413150, 777, 555])
    assert stats["fetched"] == 1 and stats["rate_limited"] == 1
    assert db.count("catalog_game") == 1  # 555 never attempted


def test_http_errors_are_counted_and_not_stubbed(db, limiter):
    with _client(lambda req: httpx.Response(500)) as client:
        stats = refresh_catalog(db, client, limiter, [500])
    assert stats["failed"] == 1 and db.count("catalog_game") == 0  # retried next run


def test_limit_caps_new_fetches(db, limiter):
    with _client(lambda req: httpx.Response(200, json=STARDEW)) as client:
        stats = refresh_catalog(db, client, limiter, [413150, 2, 3], limit=1)
    assert stats["fetched"] == 1


def test_raw_response_is_saved_untouched(db, limiter, tmp_path):
    with _client(lambda req: httpx.Response(200, json=STARDEW)) as client:
        refresh_catalog(db, client, limiter, [413150], raw=RawStore(tmp_path))
    assert len(list(tmp_path.rglob("413150.json"))) == 1


def test_referenced_appids_unions_context_and_comparison_games(db, add_want):
    add_want("1", game_appid=413150, referenced=[1145360])
    add_want("2", game_appid=None, referenced=[1145360, 105600])
    add_want("3", game_appid=892970)
    assert referenced_appids(db) == [105600, 413150, 892970, 1145360]
