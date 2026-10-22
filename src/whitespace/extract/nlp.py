"""spaCy-based extraction: dependency parse + marker rules, a game gazetteer, VADER intensity.

Design note: the small English parser is noisy ("without" often comes back as a conjunction,
"energy timer garbage" makes "garbage" the head). So rules anchor on markers (with / without /
no) and clean phrases by dropping filler, instead of trusting exact dependency labels.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from whitespace.extract import rules
from whitespace.extract.appid import AppIndex
from whitespace.extract.prefilter import find_trigger, has_lack_cue
from whitespace.extract.schema import WantItem

EXTRACTOR_VERSION = "rules-v2"
MODEL = "en_core_web_sm"
PEOPLE = {
    "friend",
    "partner",
    "wife",
    "husband",
    "kid",
    "family",
    "people",
    "girlfriend",
    "boyfriend",
    "son",
    "daughter",
    "brother",
    "me",
    "i",
    "it",
    "you",
}


def build_nlp(index: AppIndex | None = None):
    """Load spaCy (parser only, no statistical NER) and add the game gazetteer."""
    import spacy

    nlp = spacy.load(MODEL, disable=["ner"])
    ruler = nlp.add_pipe("entity_ruler", last=True, config={"phrase_matcher_attr": "LOWER"})
    stop = nlp.Defaults.stop_words
    patterns: list[dict] = []
    for name, appid in index.patterns() if index else []:
        if " " in name.strip():
            patterns.append({"label": "GAME", "pattern": name, "id": str(appid)})  # case-insens.
        elif name.lower() not in stop:
            # single words ("Hades", "Control") match case-sensitively to limit false positives
            patterns.append({"label": "GAME", "pattern": [{"ORTH": name}], "id": str(appid)})
    ruler.add_patterns(patterns)
    return nlp


def build_sentiment() -> Callable[[str], float]:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    analyzer = SentimentIntensityAnalyzer()
    return lambda text: analyzer.polarity_scores(text)["compound"]


@dataclass
class _Feature:
    relation: str  # "with" | "without"
    phrase: str


def _clean(tokens) -> str:
    words = [
        t.text.lower()
        for t in tokens
        if t.pos_ not in {"DET", "PUNCT", "PRON", "SPACE"}
        and t.lower_ not in rules.FILLER
        and not (t.pos_ == "ADP")
    ]
    return " ".join(words).replace("coop", "co-op").strip()


def _phrase(tok) -> str:
    """Head noun + its LEFT modifiers (compounds/adjectives). Lemmatize the head; if the head
    itself is filler ("energy timer garbage"), keep only its modifiers."""
    left = sorted(
        (
            t
            for t in tok.subtree
            if t.i < tok.i and t.dep_ in {"compound", "amod", "nmod", "nummod"}
        ),
        key=lambda t: t.i,
    )
    parts = [_clean([t]) for t in left]
    if tok.lower_ not in rules.FILLER:
        parts.append(tok.lemma_.lower() if tok.pos_ == "NOUN" else tok.text.lower())
    return " ".join(p for p in parts if p).replace("coop", "co-op")


def _is_people(tok) -> bool:
    return tok.pos_ == "PRON" or tok.lemma_.lower() in PEOPLE


def _marker_features(sent) -> tuple[list[_Feature], set[int]]:
    """Find 'with X' / 'without X' / 'no X' phrases; return them plus the token ids they cover."""
    feats: list[_Feature] = []
    covered: set[int] = set()
    for tok in sent:
        low = tok.lower_
        if low in {"with", "without"}:
            obj = next((c for c in tok.children if c.dep_ == "pobj"), None)
            if obj is None:  # parser attached it oddly: take the next noun within 4 tokens
                obj = next(
                    (t for t in sent[tok.i - sent.start + 1 :][:4] if t.pos_ in {"NOUN", "PROPN"}),
                    None,
                )
            if obj is None or _is_people(obj):
                continue
            heads = [
                obj,
                *(c for c in obj.conjuncts if not any(k.lower_ == "no" for k in c.children)),
            ]
            for h in heads:
                if h.pos_ in {"NOUN", "PROPN"} and not _is_people(h):
                    covered.update(t.i for t in h.subtree)
                    if rules.is_junk_feature(h.lemma_):
                        continue
                    if any(c.lower_ in rules.SCARCE_WORDS for c in h.children):
                        continue  # 'with little content' is a lack, not a wanted feature
                    phrase = _phrase(h)
                    if phrase:
                        feats.append(_Feature(low, phrase))
            covered.add(tok.i)
        elif low == "no" and tok.dep_ == "det" and tok.head.pos_ == "NOUN":
            covered.update(t.i for t in tok.head.subtree if t.i <= tok.head.i)
            phrase = "" if rules.is_junk_feature(tok.head.lemma_) else _phrase(tok.head)
            if phrase:
                feats.append(_Feature("without", phrase))
    return feats, covered


def _find_target(sent, after: int, skip: set[int], games: set[int], before: int | None = None):
    for tok in sent:
        if before is not None and tok.i >= before:
            break
        if tok.i < after or tok.i in skip or tok.i in games:
            continue
        if tok.dep_ in {"dobj", "attr", "pobj", "nsubjpass"} and tok.pos_ in {"NOUN", "PROPN"}:
            if _is_people(tok) or rules.is_junk_target(tok.lemma_):
                continue
            if tok.head.lower_ == "like":  # that's the reference game, not the target
                continue
            return tok
    return None


def _descend(tok):
    """'some kind of checkpoint' -> checkpoint: skip a quantity noun to the thing it describes."""
    if tok.lemma_.lower() in rules.QUANTITY_NOUNS:
        for c in tok.children:
            if c.dep_ == "prep" and c.lower_ == "of":
                for p in c.children:
                    if p.dep_ == "pobj" and p.pos_ in {"NOUN", "PROPN"}:
                        return p
    return tok


def _with_less(sent, tok) -> str:
    """'less confusing mechanics' keeps its 'less': the want is the opposite of the phrase."""
    phrase = _phrase(tok)
    lo = max(sent.start, tok.i - 4)
    if any(t.lower_ in rules.LESS_WORDS for t in sent.doc[lo : tok.i]):
        return f"less {phrase}"
    return phrase


def _clause_end(sent, after: int) -> int:
    """Where the wanted thing's clause ends (', but ...'), so a later clause can't hijack it."""
    return next(
        (t.i for t in sent if t.i > after and (t.lower_ in rules.CLAUSE_BREAKS or t.text == ";")),
        sent.end,
    )


def _good_noun(tok) -> bool:
    """A noun worth reporting as something missing."""
    return tok.pos_ == "NOUN" and not _is_people(tok) and not rules.is_junk_feature(tok.lemma_)


def _lacks_no(sent) -> list[str]:
    """'no fast restarts' / 'no way to rebind controls'."""
    out: list[str] = []
    for tok in sent:
        if tok.lower_ != "no" or tok.dep_ != "det" or tok.head.pos_ != "NOUN":
            continue
        head = tok.head
        if head.lemma_.lower() in {"way", "option", "ability"}:
            for c in head.children:  # 'no way to VERB OBJ' -> 'VERB OBJ'
                if c.dep_ in {"acl", "relcl"} and c.pos_ == "VERB":
                    out += _verb_object(c)
            continue
        out += [_phrase(h) for h in (head, *head.conjuncts) if _good_noun(h)]
    return out


def _verb_object(verb) -> list[str]:
    """'skip the dialogue' -> ['skip dialogue']; a bare design verb ('save') -> ['save']."""
    lemma = verb.lemma_.lower()
    objs = [c for c in verb.children if c.dep_ == "dobj"]
    heads = [h for o in objs for h in (o, *o.conjuncts) if _good_noun(h)]
    if heads:
        return [f"{lemma} {_phrase(h)}" for h in heads]
    return [lemma] if lemma in rules.DESIGN_VERBS else []


def _lacks_verbs(sent) -> list[str]:
    """'lacks X', 'lack of X', 'is missing X', 'X is missing'."""
    out: list[str] = []
    for tok in sent:
        if tok.lower_ not in {"lack", "lacks", "lacked", "lacking", "missing"}:
            continue
        objs = [c for c in tok.children if c.dep_ == "dobj"]
        for c in tok.children:
            if c.dep_ == "prep" and c.lower_ in {"of", "in"}:
                objs += [p for p in c.children if p.dep_ == "pobj"]
        if tok.lower_ == "missing":
            holder = tok.head if tok.dep_ in {"acomp", "attr"} else tok
            objs += [c for c in holder.children if c.dep_ in {"nsubj", "nsubjpass"}]
        out += [_phrase(h) for o in objs for h in (o, *o.conjuncts) if _good_noun(h)]
    return out


def _lacks_negated(sent) -> list[str]:
    """"can't skip the dialogue", "doesn't have a map", "doesn't let me use the axe"."""
    out: list[str] = []
    for tok in sent:
        if tok.pos_ not in {"VERB", "AUX"}:
            continue
        kids = list(tok.children)
        if not any(c.dep_ == "neg" for c in kids):
            continue
        lemma = tok.lemma_.lower()
        if any(c.lemma_.lower() in {"can", "could"} for c in kids):
            out += _verb_object(tok)
        elif lemma in rules.NEG_HAVE_VERBS:
            for c in kids:
                if c.dep_ == "dobj":
                    out += [_phrase(h) for h in (c, *c.conjuncts) if _good_noun(h)]
                elif c.dep_ == "xcomp" and c.pos_ == "VERB":
                    out += _verb_object(c)
        elif lemma in rules.DESIGN_VERBS:
            out.append(lemma)
    return out


def _lacks_without(sent) -> list[str]:
    """'unplayable without the wiki', 'without QoL mods this is a slog'."""
    if not any(t.lower_ in rules.NEGATIVE_WORDS for t in sent):
        return []
    out: list[str] = []
    for tok in sent:
        if tok.lower_ != "without":
            continue
        obj = next((c for c in tok.children if c.dep_ == "pobj"), None)
        if obj is None:
            obj = next(
                (t for t in sent[tok.i - sent.start + 1 :][:4] if t.pos_ in {"NOUN", "PROPN"}), None
            )
        if obj is not None:
            out += [
                _phrase(h)
                for h in (obj, *obj.conjuncts)
                if h.pos_ in {"NOUN", "PROPN"} and not _is_people(h)
                and not rules.is_junk_feature(h.lemma_)
            ]
    return out


def _lacks_scarce(sent) -> list[str]:
    """'not enough content', 'very little content' -> 'more content'."""
    out: list[str] = []
    not_enough = rules.has_not_enough(sent.text)
    for tok in sent:
        prev = sent.doc[tok.i - 1].lower_ if tok.i > sent.start else ""
        scarce = tok.lower_ in {"little", "few"} and prev not in {"a", "quite", "only"}
        if not (scarce or (tok.lower_ == "enough" and not_enough)):
            continue
        head = tok.head
        if tok.dep_ in {"amod", "det", "advmod", "nummod", "quantmod"} and _good_noun(head):
            phrase = " ".join(w for w in _phrase(head).split() if w != tok.lower_)
            if phrase:
                out.append(f"more {phrase}")
    return out


class RuleExtractor:
    """text -> list[WantItem], deterministic."""

    def __init__(
        self, nlp, index: AppIndex | None = None, sentiment: Callable[[str], float] | None = None
    ) -> None:
        self.nlp, self.index = nlp, index
        self.sentiment = sentiment or build_sentiment()

    def __call__(self, text: str) -> list[WantItem]:
        doc = self.nlp(rules.normalize_text(text)[:4000])
        out: list[WantItem] = []
        seen: set[str] = set()
        for sent in doc.sents:
            for item in self._sentence(sent) or self._complaints(sent):
                if item.want_summary not in seen:
                    seen.add(item.want_summary)
                    out.append(item)
        return out

    def _sentence(self, sent) -> list[WantItem]:
        trig = find_trigger(sent.text)
        if trig is None:
            return []
        trig_tok = next((t.i for t in sent if t.idx - sent.start_char >= trig), sent.start)
        games = [e for e in sent.ents if e.label_ == "GAME"]
        game_tokens = {t.i for e in games for t in e}
        feats, covered = _marker_features(sent)
        like_obj = next(
            (
                c
                for t in sent
                if t.lower_ == "like"
                and t.dep_ == "prep"
                and rules.like_is_comparison(sent.text, t.head.lemma_)
                for c in t.children
                if c.dep_ == "pobj"
                and c.pos_ not in {"PRON", "DET"}
                and not rules.is_junk_feature(c.lemma_)
            ),
            None,
        )
        if like_obj is not None:
            game_tokens |= {t.i for t in like_obj.subtree if t.i <= like_obj.i}

        target = _find_target(sent, trig_tok, covered, game_tokens, _clause_end(sent, trig_tok))
        if target is not None:
            target = _descend(target)
        bases = [
            _with_less(sent, t)
            for t in ([target, *target.conjuncts] if target is not None else [])
            if t.pos_ in {"NOUN", "PROPN"}
            and t.i not in covered
            and not _is_people(t)
            and not rules.is_junk_target(t.lemma_)
        ]
        base = bases[0] if bases else ""
        # lexicon features (e.g. "my partner can play too" -> co-op) the parser can't isolate
        for name in rules.canonical_features(sent.text.replace("coop", "co-op")):
            if not any(name in f.phrase for f in feats) and name != base:
                feats.append(_Feature("with", name))
        if base in rules.GENERIC_TARGETS and not feats and like_obj is None:
            return []  # "I want to play this game" says nothing about a niche
        if not base and not feats and like_obj is None:
            return []

        names = [e.text for e in games]
        appids = sorted({int(e.ent_id_) for e in games if e.ent_id_})
        ent_tokens = {t.i for e in games for t in e}
        if like_obj is not None:
            # "games like A or B": every compared game, not just the first
            for head in [like_obj, *(t for t in like_obj.subtree if t.dep_ == "conj")]:
                if head.i in ent_tokens or head.pos_ not in {"NOUN", "PROPN"}:
                    continue
                if rules.is_junk_feature(head.lemma_):
                    continue
                raw = " ".join(
                    t.text for t in head.subtree if t.i <= head.i and t.pos_ not in {"DET", "PUNCT"}
                )
                names.append(raw)
                if self.index and (aid := self.index.resolve(raw)):
                    appids.append(aid)
        appids = sorted(set(appids))

        is_like = like_obj is not None or bool(games and "like" in sent.text.lower())
        subject_base = base or "game"
        req = rules.classify_request(sent.text, base, is_like)
        if req == "games_like" and not names:
            # 'many games like this' names no game, so there is nothing to compare against
            if not feats:
                return []
            req = "genre_request"
        negated = bool(base) and not is_like and rules.is_negated_wish(sent.text)
        ref = f"like {', '.join(names)}" if is_like and names else base
        stem = ref or base

        intensity = rules.intensity_level(sent.text, self.sentiment(rules.neutralize(sent.text)))
        quote = sent.text.strip()[:200]
        conf = (
            0.5
            + (0.2 if target is not None else 0)
            + (0.15 if feats else 0)
            + (0.1 if names else 0)
        )

        items: list[WantItem] = []

        def make(summary: str, subject: str) -> None:
            items.append(
                WantItem(
                    request_type=req,
                    subject=subject,
                    want_summary=summary,
                    intensity=intensity,
                    quote=quote,
                    confidence=rules.adjust_confidence(conf, subject, req),
                    referenced_games=list(names),
                    referenced_appids=list(appids),
                )
            )

        # a phrase can't be both wanted and unwanted: "without" wins
        lacks = {f.phrase for f in feats if f.relation == "without"}
        feats = [f for f in feats if not (f.relation == "with" and f.phrase in lacks)]
        if feats:
            for f in feats:
                subj = rules.classify_subject(f.phrase) if f.phrase else "other"
                make(f"{stem} {f.relation} {f.phrase}".strip(), subj)
            for extra in bases[1:]:
                make(extra, rules.classify_subject(extra))
        else:
            for b in bases or [stem]:
                stem_i = f"like {', '.join(names)}" if is_like and names else b
                if negated:
                    stem_i = f"without {b}"
                subj = (
                    "genre"
                    if is_like and names
                    else rules.classify_subject(b or subject_base, is_base=True)
                )
                make(stem_i, subj)
        return items

    def _complaints(self, sent) -> list[WantItem]:
        """Implicit demand: what players say the game lacks. One item per missing thing, in
        positive form ("no fast restarts" -> "fast restart"), so it clusters with the wishes."""
        if not has_lack_cue(sent.text):
            return []
        phrases = [
            *_lacks_no(sent),
            *_lacks_verbs(sent),
            *_lacks_negated(sent),
            *_lacks_without(sent),
            *_lacks_scarce(sent),
        ]
        if ux := rules.ux_complaint(sent.text):
            phrases.append(ux)
        if not phrases:
            return []
        games = [e for e in sent.ents if e.label_ == "GAME"]
        names = [e.text for e in games]
        appids = sorted({int(e.ent_id_) for e in games if e.ent_id_})
        intensity = rules.intensity_level(sent.text, self.sentiment(rules.neutralize(sent.text)))
        quote = sent.text.strip()[:200]
        out: list[WantItem] = []
        seen: set[str] = set()
        for phrase in phrases:
            summary = rules.canonical_need(phrase)
            if not summary or summary in seen:
                continue
            seen.add(summary)
            subject = rules.classify_subject(summary)
            out.append(
                WantItem(
                    request_type="complaint",
                    subject=subject,
                    want_summary=summary,
                    intensity=intensity,
                    quote=quote,
                    confidence=rules.adjust_confidence(0.65, subject, "complaint"),
                    referenced_games=list(names),
                    referenced_appids=list(appids),
                )
            )
        return out
