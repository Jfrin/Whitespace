"""spaCy extraction. Skipped when spaCy / the model isn't installed (they are in the dev image)."""

import pytest

spacy = pytest.importorskip("spacy")
pytest.importorskip("vaderSentiment")
if not spacy.util.is_package("en_core_web_sm"):
    pytest.skip("en_core_web_sm not installed", allow_module_level=True)

from whitespace.extract.appid import AppIndex  # noqa: E402
from whitespace.extract.nlp import RuleExtractor, build_nlp  # noqa: E402

INDEX = AppIndex(
    {413150: "Stardew Valley", 1145360: "Hades", 105600: "Terraria"},
    overrides={"Stardew": 413150},
)


@pytest.fixture(scope="module")
def extract():
    return RuleExtractor(build_nlp(INDEX), INDEX)


def summaries(items):
    return {(i.request_type, i.subject, i.want_summary) for i in items}


def test_the_motivating_example_is_normalized_and_split(extract):
    items = extract(
        "Honestly I'd kill for a farming sim where my partner can play too, "
        "but without the energy timer garbage."
    )
    assert summaries(items) == {
        ("genre_request", "monetization", "farming sim without energy timer"),
        ("genre_request", "mechanic", "farming sim with co-op"),
    }
    assert all(i.intensity == 3 for i in items)  # "kill for"
    assert "honestly" not in items[0].want_summary and "garbage" not in items[0].want_summary


def test_with_and_no_markers(extract):
    items = extract("I want a farming sim with co-op but no energy timer.")
    assert summaries(items) == {
        ("genre_request", "mechanic", "farming sim with co-op"),
        ("genre_request", "monetization", "farming sim without energy timer"),
    }
    assert all(i.intensity == 2 for i in items)


def test_genre_request_with_modifiers(extract):
    items = extract("Why doesn't anyone make a cozy space trading game?")
    assert summaries(items) == {("genre_request", "genre", "cozy space trading game")}


def test_games_like_resolves_gazetteer_and_alias(extract):
    a = extract("Looking for games like Stardew Valley but with deeper automation.")[0]
    assert a.request_type == "games_like" and a.referenced_appids == [413150]
    assert a.want_summary == "like Stardew Valley with deeper automation"
    alias = extract("Looking for something like Stardew but with combat.")[0]
    assert alias.referenced_appids == [413150]


def test_unknown_game_name_is_kept_and_unresolved(extract):
    item = extract("Is there a game like Dwarf Fortress with a co-op mode?")[0]
    assert item.request_type == "games_like" and item.referenced_appids == []
    assert any("Dwarf Fortress" in g for g in item.referenced_games)


def test_feature_wishes_and_conjoined_targets(extract):
    items = extract("I wish it had controller support and a hard mode.")
    assert summaries(items) == {
        ("feature_wish", "platform", "controller support"),
        ("feature_wish", "difficulty", "hard mode"),
    }


def test_negation_wins_over_with(extract):
    items = extract("Looking for something like Stardew but with combat and no fishing minigame.")
    texts = {i.want_summary for i in items}
    assert "like Stardew with combat" in texts and "like Stardew without fishing minigame" in texts
    assert not any("with fishing" in t for t in texts)


def test_request_wording_does_not_inflate_intensity(extract):
    assert extract("Would be great if the game had mod support.")[0].intensity == 2


@pytest.mark.parametrize(
    "text",
    [
        "Great game, 10/10, played for 200 hours.",
        "I want to play this game with my friends.",
        "I need a break from this game.",
        "I want a refund.",
        "",
    ],
)
def test_non_requests_produce_nothing(extract, text):
    assert extract(text) == []


def test_multi_sentence_dedupes_and_keeps_quote(extract):
    items = extract("I wish it had a photo mode. Seriously, I wish it had a photo mode.")
    assert len(items) == 1 and items[0].quote.startswith("I wish it had")


def test_single_word_gazetteer_entries_are_case_sensitive():
    nlp = build_nlp(INDEX)
    doc = nlp("I love hades, but Hades is great. Stardew Valley too")
    ents = [(e.text, e.ent_id_) for e in doc.ents]
    assert ("Hades", "1145360") in ents and ("hades", "1145360") not in ents
    assert ("Stardew Valley", "413150") in ents  # multi-word names ignore case
    assert [e.text for e in nlp("play stardew valley").ents] == ["stardew valley"]


def test_like_context_resolves_lowercase_names(extract):
    # inside "games like X" the object is surely a game, so case doesn't matter
    assert extract("Is there a game like hades with co-op?")[0].referenced_appids == [1145360]
