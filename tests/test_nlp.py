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


def test_generic_like_objects_are_not_treated_as_games(extract):
    items = extract("Is there a game like this with a co-op mode?")
    assert not any(i.want_summary.startswith("like this") for i in items)
    assert all(i.referenced_appids == [] for i in items)


def test_junk_nouns_do_not_become_features(extract):
    items = extract("I want a farming sim without any fix or reason to quit.")
    assert not any(word in i.want_summary for i in items for word in ("fix", "reason"))


def test_items_without_a_design_subject_get_lower_confidence(extract):
    items = extract("I wish it had a unicorn.")
    assert items and all(i.subject == "other" and i.confidence < 0.6 for i in items)


def test_comparison_without_a_named_game_is_dropped(extract):
    assert extract("There are so many games like this.") == []


def test_comparison_without_a_game_but_with_a_feature_becomes_a_genre_request(extract):
    items = extract("Looking for games like this but with co-op.")
    assert all(i.request_type != "games_like" for i in items)
    assert any("co-op" in i.want_summary for i in items)


def test_steam_markup_does_not_leak_into_wants(extract):
    items = extract("[h1]Why doesn't anyone make a roguelite?[/h1]")
    assert not any("[" in i.want_summary or "h1" in i.want_summary for i in items)


def test_negated_wish_means_the_absence_of_the_thing(extract):
    items = extract("I wish this wasn't a roguelite.")
    assert any(
        i.want_summary == "without roguelite" and i.request_type != "complaint" for i in items
    )


def test_some_kind_of_x_resolves_to_x(extract):
    items = extract("i wish there was some kind of checkpoint between the big chunks of the game")
    assert any("checkpoint" in i.want_summary for i in items)
    assert not any(i.want_summary.strip() == "" for i in items)


def test_less_keeps_its_polarity(extract):
    items = extract("It needs a faster progression system and less confusing mechanics.")
    assert "less confusing mechanic" in {i.want_summary for i in items}


def test_a_later_clause_cannot_hijack_the_target(extract):
    items = extract(
        "So I wish I could recommend it purely for that, but for what it is, a rogue-lite, "
        "it needs more replayability."
    )
    assert not any("rogue" in i.want_summary for i in items)


def test_every_game_in_a_comparison_list_is_captured(extract):
    text = "Devs could learn a thing or two from games like Binding of Isaac or Dead Cells."
    items = extract(text)
    names = " ".join(g for i in items for g in i.referenced_games)
    assert "Dead Cells" in names


def _summaries(items):
    return {i.want_summary for i in items}


def test_no_x_becomes_a_complaint_in_positive_form(extract):
    items = extract("There are no fast restarts and no instructions.")
    assert {"fast restart", "in-game instructions"} <= _summaries(items)
    assert all(i.request_type == "complaint" for i in items)


def test_no_way_to_verb_object(extract):
    assert "control rebinding" in _summaries(extract("There is no way to rebind the controls."))


def test_lacks_and_missing(extract):
    assert "checkpoints" in _summaries(extract("The game lacks checkpoints."))
    assert "checkpoints" in _summaries(extract("Checkpoints are missing."))


def test_cannot_verb_object(extract):
    assert "skip dialogue" in _summaries(extract("You cannot skip the dialogue."))


def test_without_x_with_a_negative_word(extract):
    text = "Without QoL mods, this game is a huge slog."
    assert "quality-of-life features" in _summaries(extract(text))


def test_not_enough_x_means_more_x(extract):
    assert "more content" in _summaries(extract("There is not enough content."))


def test_hard_to_know_what_to_do_means_instructions(extract):
    text = "It is very hard to know what you are supposed to do."
    assert "in-game instructions" in _summaries(extract(text))


def test_complaint_noise_is_dropped_or_penalised(extract):
    assert extract("There is no refund and no reason to play.") == []
    soup = extract("There is no soup.")
    assert soup and all(i.confidence < 0.6 for i in soup)
