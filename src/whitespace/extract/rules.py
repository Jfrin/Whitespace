"""Deterministic extraction rules: no model, no network. Pure Python so they test without spaCy."""

from __future__ import annotations

import re

# --- intensity ---------------------------------------------------------------------------
STRONG = re.compile(
    r"\b(kill|die|dying|dies) for\b|desperat|\bso badly\b|\bbadly\b|\bbeg(ging)?\b"
    r"|\bplease\b|\bfinally\b|\bfor the love of\b",
    re.I,
)
MEDIUM = re.compile(
    r"\bwish\b|\blooking for\b|\bwant\b|\bneed\b|\bhope\b|\bwould (love|like)\b|\bif only\b"
    r"|\bwhy (doesn'?t|won'?t|hasn'?t|does no ?one)\b|\bwould be (great|perfect|amazing|nice)\b",
    re.I,
)


COMPOUND_STRONG = 0.6  # tune on the gold set


def neutralize(text: str) -> str:
    """Remove the request wording itself before sentiment scoring, so 'would be great if' or
    'I wish' don't count as emotion. Only the surrounding emotion should move the score."""
    return GAMES_LIKE.sub(" ", MEDIUM.sub(" ", text))


def intensity_level(text: str, compound: float) -> int:
    """3 = strong, 2 = moderate, 1 = casual.

    `compound` is VADER's -1..1 score. VADER measures emotional charge, not demand, so we use
    its magnitude: an angry or excited request is a stronger one.
    """
    if STRONG.search(text) or abs(compound) >= COMPOUND_STRONG:
        return 3
    if MEDIUM.search(text):
        return 2
    return 1


# --- request type ------------------------------------------------------------------------
GAMES_LIKE = re.compile(
    r"\b(games?|something|anything|one) (like|similar to)\b|\bsimilar to\b", re.I
)
UNMET = re.compile(
    r"\b(expected|supposed to|thought it (would|was|had)|advertised|misleading|should (have|be))\b",
    re.I,
)
FEATURE_NOUN = {
    "mode",
    "support",
    "option",
    "options",
    "system",
    "setting",
    "settings",
    "feature",
    "toggle",
    "slider",
    "menu",
    "button",
    "difficulty",
    "save",
    "map",
    "editor",
}
FEATURE_VERB = re.compile(
    r"\b(had|has|have|add|adds|added|include|includes|included|needs?)\b", re.I
)


def classify_request(sentence: str, target: str, has_like: bool) -> str:
    if has_like or GAMES_LIKE.search(sentence):
        return "games_like"
    if UNMET.search(sentence):
        return "unmet_expectation"
    head = target.split()[-1] if target else ""
    if head in FEATURE_NOUN or FEATURE_VERB.search(sentence):
        return "feature_wish"
    return "genre_request"


# --- subject -----------------------------------------------------------------------------
_SUBJECT_LEXICON: list[tuple[str, re.Pattern[str]]] = [
    (
        "monetization",
        re.compile(
            r"energy|timer|microtransaction|pay.?to.?win|gacha|loot ?box|dlc|subscription|paywall"
            r"|\bads?\b|season pass|battle pass|monetiz|premium currency",
            re.I,
        ),
    ),
    (
        "platform",
        re.compile(
            r"\bswitch\b|mobile|\bios\b|android|ps[45]|playstation|xbox|console|steam deck|linux"
            r"|\bmac\b|controller|offline",
            re.I,
        ),
    ),
    ("difficulty", re.compile(r"difficult|\bhard\b|\beasy\b|grind|challenge|accessib", re.I)),
    ("session_length", re.compile(r"short session|quick|pick.?up|bite.?sized|long session", re.I)),
    (
        "art_style",
        re.compile(r"pixel|art style|hand.?drawn|graphic|low.?poly|cel.?shad|voxel", re.I),
    ),
    (
        "setting",
        re.compile(
            r"medieval|space|cyberpunk|post.?apocalyp|western|fantasy|sci.?fi|noir|viking|pirate"
            r"|underwater|cozy|cosy",
            re.I,
        ),
    ),
    (
        "mechanic",
        re.compile(
            r"co-?op|multiplayer|automation|crafting|base.?building|combat|inventory|mod\b|mods\b"
            r"|mod support|photo mode|save|building|farming|trading|romance|permadeath|procedural",
            re.I,
        ),
    ),
]
GENRE = re.compile(
    r"\b(sim|simulator|simulation|rpg|roguelike|roguelite|platformer|shooter|strategy|puzzle"
    r"|survival|tycoon|metroidvania|souls.?like|4x|rts|jrpg|visual novel|deckbuilder|game)\b",
    re.I,
)


def classify_subject(phrase: str, is_base: bool = False) -> str:
    if is_base and GENRE.search(phrase):
        return "genre"
    for subject, pat in _SUBJECT_LEXICON:
        if pat.search(phrase):
            return subject
    return "other"


# --- canonical features the parser can't reliably isolate --------------------------------
_PARTNER = r"(partner|wife|husband|girlfriend|boyfriend|spouse|friends?|kids?|son|daughter|brother)"
CANONICAL: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bco-?op\b|\bmultiplayer\b", re.I), "co-op"),
    (
        re.compile(
            rf"\b{_PARTNER}\b[^.!?]*\b(play|together)\b|\bplay (together|with (my|a) {_PARTNER})",
            re.I,
        ),
        "co-op",
    ),
    (re.compile(r"\bmod(ding)? support\b|\bworkshop support\b", re.I), "mod support"),
    (re.compile(r"\bcontroller support\b", re.I), "controller support"),
    (re.compile(r"\bphoto mode\b", re.I), "photo mode"),
]


def canonical_features(text: str) -> list[str]:
    seen: list[str] = []
    for pat, name in CANONICAL:
        if pat.search(text) and name not in seen:
            seen.append(name)
    return seen


def normalize_text(text: str) -> str:
    """Pre-parse cleanup: stops the tokenizer splitting 'co-op' into three tokens."""
    text = re.sub(r"\bco[- ]?op\b", "coop", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


# --- phrase cleanup ----------------------------------------------------------------------
FILLER = {
    "honestly",
    "really",
    "actual",
    "actually",
    "proper",
    "real",
    "good",
    "decent",
    "damn",
    "garbage",
    "freaking",
    "freakin",
    "literally",
    "just",
    "simply",
    "bloody",
    "stupid",
    "fucking",
    "shitty",
    "crappy",
    "terrible",
    "awful",
    "basic",
    "kind",
    "sort",
    "lol",
    "tbh",
}
NON_DESIGN_TARGETS = {
    "refund",
    "money",
    "time",
    "life",
    "answer",
    "reply",
    "break",
    "rest",
    "vacation",
    "job",
    "day",
    "week",
    "hug",
    "miracle",
    "chance",
    "help",
    "clue",
    "idea",
}
GENERIC_TARGETS = {"game", "games", "one", "thing", "things", "something", "anything", "it", ""}


# --- scope: is the want about THIS game, a whole genre, or "like <other game>"? ---------
THIS_GAME = re.compile(
    r"\b(this|the) (game|title|port|version|update|dlc)\b|\bthe devs?\b|\bdevelopers?\b"
    r"|\bplease add\b",
    re.I,
)
GENRE_WIDE = re.compile(
    r"\bwhy (doesn'?t|won'?t|hasn'?t|does no ?one) (anyone|anybody|someone)\b"
    r"|\bis there (a|any|an) (game|games)\b|\blooking for (a |an |some )?(game|games)\b"
    r"|\banyone make\b",
    re.I,
)


def classify_scope(text: str, request_type: str, has_context_game: bool) -> str:
    """game_specific | genre_wide | reference.

    reference:     "games like Y but with X"
    genre_wide:    "why doesn't anyone make X", "is there a game with X"
    game_specific: a wish/complaint said about the game the post is attached to
    """
    if request_type == "games_like":
        return "reference"
    if GENRE_WIDE.search(text) or request_type == "genre_request":
        return "genre_wide"
    if THIS_GAME.search(text) or has_context_game:
        return "game_specific"
    return "genre_wide"
