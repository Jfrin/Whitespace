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
