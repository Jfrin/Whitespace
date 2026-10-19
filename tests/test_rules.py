import pytest

from whitespace.extract import rules


@pytest.mark.parametrize(
    ("text", "compound", "expected"),
    [
        ("Honestly I'd kill for a farming sim", 0.0, 3),  # strong intensifier phrase
        ("I need this so badly", 0.0, 3),
        ("PLEASE add a toggle", 0.0, 3),
        ("this is so exciting", 0.7, 3),  # strong sentiment magnitude
        ("I hate how it handles this", -0.7, 3),  # negative counts too
        ("I wish it had a photo mode", 0.1, 2),  # moderate verb
        ("Looking for a cozy game", 0.0, 2),
        ("Is there a game with co-op", 0.0, 1),  # neutral phrasing
    ],
)
def test_intensity_levels(text, compound, expected):
    assert rules.intensity_level(text, compound) == expected


def test_neutralize_strips_request_wording_but_keeps_emotion():
    out = rules.neutralize("Would be great if it had mod support, games like Hades are awesome")
    assert "great" not in out and "games like" not in out and "awesome" in out


@pytest.mark.parametrize(
    ("sentence", "target", "like", "expected"),
    [
        ("looking for games like Hades", "game", False, "games_like"),
        ("I wish it had a photo mode", "photo mode", False, "feature_wish"),
        ("would be great if the game had mod support", "mod support", False, "feature_wish"),
        ("why doesn't anyone make a cozy sim", "cozy sim", False, "genre_request"),
        ("I expected a campaign, this is misleading", "campaign", False, "unmet_expectation"),
        ("anything", "x", True, "games_like"),
    ],
)
def test_classify_request(sentence, target, like, expected):
    assert rules.classify_request(sentence, target, like) == expected


@pytest.mark.parametrize(
    ("phrase", "base", "expected"),
    [
        ("energy timer", False, "monetization"),
        ("controller support", False, "platform"),
        ("hard mode", False, "difficulty"),
        ("pixel art", False, "art_style"),
        ("space", False, "setting"),
        ("co-op", False, "mechanic"),
        ("farming sim", True, "genre"),  # genre wins for base targets
        ("farming", False, "mechanic"),
        ("zzz", False, "other"),
    ],
)
def test_classify_subject(phrase, base, expected):
    assert rules.classify_subject(phrase, is_base=base) == expected


def test_canonical_features_and_normalization():
    assert rules.canonical_features("a sim where my partner can play too") == ["co-op"]
    assert rules.canonical_features("I want Co-Op and mod support") == ["co-op", "mod support"]
    assert rules.canonical_features("nothing relevant") == []
    assert rules.normalize_text("Co-Op  and co op\n") == "coop and coop"


@pytest.mark.parametrize(
    ("text", "request_type", "has_game", "expected"),
    [
        ("Looking for games like Hades but with co-op", "games_like", True, "reference"),
        ("Why doesn't anyone make a cozy roguelike", "genre_request", False, "genre_wide"),
        ("Is there a game with deep automation", "feature_wish", True, "genre_wide"),
        ("I wish it had a photo mode", "feature_wish", True, "game_specific"),
        ("the devs should add a photo mode", "feature_wish", False, "game_specific"),
        ("I wish it had a photo mode", "feature_wish", False, "genre_wide"),
        ("I expected a campaign, this is misleading", "unmet_expectation", True, "game_specific"),
    ],
)
def test_classify_scope(text, request_type, has_game, expected):
    assert rules.classify_scope(text, request_type, has_game) == expected


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("progression system", "mechanic"),
        ("level up", "mechanic"),
        ("skill tree", "mechanic"),
        ("story", "content"),
        ("boss fights", "content"),
        ("levels", "content"),
        ("ui scaling", "ux"),
        ("camera controls", "ux"),
        ("tooltips", "ux"),
        ("hard mode", "difficulty"),          # earlier entries still win
        ("controller support", "platform"),
    ],
)
def test_richer_subject_vocabulary(phrase, expected):
    assert rules.classify_subject(phrase) == expected


def test_junk_helpers():
    assert rules.is_junk_target("Hours") and rules.is_junk_target("reason")
    assert not rules.is_junk_target("mode") and not rules.is_junk_target("game")
    assert rules.is_junk_feature("thing") and rules.is_junk_feature("fix")
    assert not rules.is_junk_feature("timer")


def test_like_is_comparison():
    assert rules.like_is_comparison("anything", "game")
    assert rules.like_is_comparison("looking for games like Hades", "look")
    assert not rules.like_is_comparison("it feels like this", "feel")


def test_adjust_confidence():
    assert rules.adjust_confidence(0.8, "other", "feature_wish") == 0.55
    assert rules.adjust_confidence(0.8, "mechanic", "feature_wish") == 0.8
    assert rules.adjust_confidence(0.8, "other", "games_like") == 0.8
    assert rules.adjust_confidence(0.95, "mechanic", "x") == 0.9
    assert rules.adjust_confidence(0.1, "other", "x") == 0.05


def test_normalize_text_strips_steam_bbcode():
    assert rules.normalize_text("[h1]I wish[/h1] it had [b]co-op[/b]") == "I wish it had coop"
    assert rules.normalize_text("[url=https://x.io]link[/url] [*] item [spoiler]x[/spoiler]") == (
        "link item x"
    )
    assert rules.normalize_text("a [sic] b") == "a b"


@pytest.mark.parametrize(
    ("lang", "expected"),
    [(None, True), ("", True), ("en", True), ("english", True), ("English", True),
     ("russian", False), ("und", False), ("schinese", False)],
)
def test_is_english(lang, expected):
    assert rules.is_english(lang) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I wish this wasn't a roguelite.", True),
        ("I wish it didn't have energy timers", True),
        ("i wish i hadn't bought it", True),
        ("I wish it had co-op", False),
        ("I wish I could recommend it", False),
        ("Why isn't there co-op, I wish", False),          # negation before the wish
    ],
)
def test_is_negated_wish(text, expected):
    assert rules.is_negated_wish(text) is expected


def test_normalize_text_straightens_curly_apostrophes():
    assert rules.normalize_text("I wish it wasn\u2019t this way") == "I wish it wasn't this way"


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("checkpoint", "mechanic"),
        ("fast restarts", "mechanic"),
        ("replayability", "content"),
        ("button rebinding", "ux"),
        ("instructions", "ux"),
        ("hard mode", "difficulty"),
    ],
)
def test_subject_vocabulary_from_the_gold_review(phrase, expected):
    assert rules.classify_subject(phrase) == expected


def test_off_topic_targets_are_junk():
    for word in ("steam", "sequel", "series", "genre", "masterpiece", "gut", "pc", "rating"):
        assert rules.is_junk_target(word), word
    for word in ("checkpoint", "progression", "mode", "story"):
        assert not rules.is_junk_target(word), word


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("fast restart", "fast restart"),
        ("restarts", "fast restart"),
        ("instruction", "in-game instructions"),
        ("tutorial", "in-game instructions"),
        ("wiki", "in-game instructions"),
        ("rebind control", "control rebinding"),
        ("checkpoint", "checkpoints"),
        ("qol mod", "quality-of-life features"),
        ("difficulty option", "difficulty options"),
        ("boss", "boss"),
    ],
)
def test_canonical_need(phrase, expected):
    assert rules.canonical_need(phrase) == expected


def test_ux_complaint_and_not_enough():
    text = "very hard to know what youre supposed to do"
    assert rules.ux_complaint(text) == "in-game instructions"
    assert rules.ux_complaint("it never explains anything") == "in-game instructions"
    assert rules.ux_complaint("great game") is None
    assert rules.has_not_enough("there is not enough content")
    assert rules.has_not_enough("that isn't enough")
    assert not rules.has_not_enough("enough already")


def test_normalize_text_restores_dropped_apostrophes():
    assert rules.normalize_text("I cant use it and dont know") == "I can't use it and don't know"
    assert rules.normalize_text("It doesnt work, isnt fun") == "It doesn't work, isn't fun"


def test_strip_markup_keeps_words_as_written():
    assert rules.strip_markup("[h1]I wish[/h1]   it had co-op\n[b]please[/b]") == (
        "I wish it had co-op please"
    )
    assert rules.strip_markup(None) == ""
