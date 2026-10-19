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
_SUBJECT_LEXICON.extend(
    [
        (
            "mechanic",
            re.compile(
                r"progress|\bxp\b|level.?up|unlock|upgrade|skill tree|\bloot\b|\bweapons?\b"
                r"|\bclass(es)?\b|\bskills?\b|matchmaking|\bpvp\b|\bpve\b|\bstealth\b|\bparry\b",
                re.I,
            ),
        ),
        (
            "content",
            re.compile(
                r"\bstory\b|narrative|\bquests?\b|dialogue|campaign|ending|\bcontent\b|\blevels?\b"
                r"|\bboss(es)?\b|\bmaps?\b|\bchapters?\b|\blore\b|\bcharacters?\b",
                re.I,
            ),
        ),
        (
            "ux",
            re.compile(
                r"\bui\b|\bhud\b|\bmenus?\b|camera|tutorial|tooltip|\bcontrols?\b|readab|\bfont\b"
                r"|keybind|\bsettings\b|\bslider\b|\bzoom\b",
                re.I,
            ),
        ),
    ]
)
_SUBJECT_LEXICON.extend(
    [
        ("mechanic", re.compile(r"checkpoint|auto.?save|restart|respawn", re.I)),
        ("content", re.compile(r"replayab|variety", re.I)),
        (
            "ux",
            re.compile(
                r"rebind|remap|instruction|\bguide\b|walkthrough|\bwiki\b|\bqol\b|quality.of.life"
                r"|\b(pause|skip|undo|resume|retry|zoom|rotate|resize)\b|customi[sz]",
                re.I,
            ),
        ),
    ]
)
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


# Steam review markup: [h1], [/b], [url=...], [spoiler], [*] ...
BBCODE = re.compile(r"\[/?[a-z0-9*]+(?:=[^\]]*)?\]", re.I)


def normalize_text(text: str) -> str:
    """Pre-parse cleanup: stops the tokenizer splitting 'co-op' into three tokens."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = BBCODE.sub(" ", text)
    text = re.sub(
        r"\b(can|don|doesn|didn|won|isn|wasn|couldn|wouldn|shouldn|haven|hasn|aren|weren)t\b",
        r"\1't",
        text,
        flags=re.I,
    )
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
    "hour",
    "hours",
    "reason",
    "fix",
    "rating",
    "shiz",
    "shit",
    "crap",
    "stuff",
    "way",
    "lot",
    "bit",
    "review",
    "score",
    "deficiency",
    "sake",
    "waste",
    "point",
    "part",
    "problem",
    "issue",
    "bug",
    "feeling",
    "opinion",
    "thought",
    "steam",
    "sequel",
    "series",
    "genre",
    "masterpiece",
    "gut",
    "ground",
    "pc",
    "computer",
    "hardware",
    "price",
    "sale",
    "discount",
    "star",
    "stars",
    "icon",
    "thumb",
    "recommendation",
    "matter",
    "doubt",
    "question",
    "need",
    "wonder",
    "surprise",
    "fun",
    "joy",
    "sense",
    "value",
    "longer",
    "luck",
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


# --- precision helpers (pure, so they test without spaCy) ----------------------------------
COMPARISON_HEADS = {"game", "title", "something", "anything", "one", "stuff", "thing"}


def is_junk_target(lemma: str) -> bool:
    """A noun that can't be what someone wants from a game ('hour', 'reason', 'refund')."""
    return lemma.lower().strip() in NON_DESIGN_TARGETS


def is_junk_feature(lemma: str) -> bool:
    """Junk as a with/without feature: non-design nouns plus vague ones ('it', 'thing')."""
    low = lemma.lower().strip()
    return low in NON_DESIGN_TARGETS or low in GENERIC_TARGETS


def like_is_comparison(sentence: str, head_lemma: str) -> bool:
    """True for 'games like X' / 'something like X'; False for 'it feels like this'."""
    return head_lemma.lower() in COMPARISON_HEADS or bool(GAMES_LIKE.search(sentence))


def adjust_confidence(conf: float, subject: str, request_type: str) -> float:
    """Items we couldn't give a design subject are the likeliest junk: lower their confidence
    so consumers can filter with a threshold instead of us deleting rows."""
    if subject == "other" and request_type != "games_like":
        conf -= 0.25
    return round(min(max(conf, 0.05), 0.9), 2)


# --- language, polarity, clause helpers (pure, so they test without spaCy) -------------------
ENGLISH = {"en", "english", "eng"}


def is_english(lang: str | None) -> bool:
    """Unknown language counts as English (nothing to go on); 'und' and other languages don't."""
    return not lang or lang.strip().lower() in ENGLISH


NEGATED_WISH = re.compile(
    r"\bwish\b[^.!?]{0,40}?\b(wasn'?t|weren'?t|isn'?t|didn'?t|hadn'?t|wouldn'?t|did not|was not"
    r"|were not|had no)\b",
    re.I,
)


def is_negated_wish(text: str) -> bool:
    """'I wish this wasn't a roguelite': the thing named is what they do NOT want."""
    return bool(NEGATED_WISH.search(text))


QUANTITY_NOUNS = {"kind", "sort", "type", "bit", "lot", "couple", "piece", "variety"}
LESS_WORDS = {"less", "fewer", "lesser"}
CLAUSE_BREAKS = {"but", "however", "although", "though", "whereas"}


# --- implicit demand: complaints about what a game lacks -------------------------------------
NEG_HAVE_VERBS = {
    "have",
    "allow",
    "let",
    "support",
    "offer",
    "include",
    "give",
    "provide",
    "contain",
    "feature",
}
DESIGN_VERBS = {
    "save",
    "skip",
    "pause",
    "rebind",
    "remap",
    "customize",
    "customise",
    "adjust",
    "disable",
    "zoom",
    "undo",
    "resume",
    "retry",
    "reload",
    "rotate",
    "resize",
}
NEGATIVE_WORDS = {
    "unplayable",
    "impossible",
    "unusable",
    "painful",
    "frustrating",
    "unbearable",
    "tedious",
    "useless",
    "pointless",
    "miserable",
    "slog",
    "chore",
    "grind",
    "unenjoyable",
}
SCARCE_WORDS = {"little", "few", "no", "zero", "minimal", "barely"}

# Map the many ways of saying the same missing thing onto one label so they cluster together.
CANONICAL_NEEDS = [
    (
        re.compile(r"instruction|tutorial|guidance|explanation|walkthrough|\bwiki\b|onboard", re.I),
        "in-game instructions",
    ),
    (re.compile(r"rebind|remap|keybind|key bind", re.I), "control rebinding"),
    (re.compile(r"checkpoint|save point|auto.?save", re.I), "checkpoints"),
    (re.compile(r"restart", re.I), "fast restart"),
    (re.compile(r"\bqol\b|quality.of.life", re.I), "quality-of-life features"),
    (re.compile(r"difficulty (option|setting|mode)|easy mode|assist", re.I), "difficulty options"),
]


def canonical_need(phrase: str) -> str:
    for pat, name in CANONICAL_NEEDS:
        if pat.search(phrase):
            return name
    return phrase


UX_COMPLAINT = re.compile(
    r"\b(hard|difficult|impossible|unclear|confusing) to "
    r"(know|tell|figure out|understand|find out)\b"
    r"|\bno (idea|clue) what (to do|(i'?m|you'?re) (supposed|meant) to do)\b"
    r"|\bdoesn'?t (explain|tell you|teach)\b|\bnever (explain|tell|teach)s?\b",
    re.I,
)


def ux_complaint(text: str) -> str | None:
    """'very hard to know what you're supposed to do' -> the need behind it."""
    return "in-game instructions" if UX_COMPLAINT.search(text) else None


NOT_ENOUGH = re.compile(r"\bnot\s+enough\b|n't\s+enough\b", re.I)


def has_not_enough(text: str) -> bool:
    return bool(NOT_ENOUGH.search(text))


def strip_markup(text: str) -> str:
    """Steam BBCode removed and whitespace collapsed. Unlike normalize_text this keeps the
    words as written ("co-op" stays "co-op"), which is what an LLM and quote checks want."""
    return re.sub(r"\s+", " ", BBCODE.sub(" ", text or "")).strip()
