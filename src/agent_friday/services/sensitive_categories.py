"""Sensitive categories: one place for the word lists Friday checks text against.

Two tables live here, for two different promises.

``SENSITIVE_CATEGORIES`` / ``sensitive_category``: what the opt-in setup
research (services/setup_research) never searches for and never keeps:
health, sexuality, finances, family members, a home address. It is broad on
purpose, because a public web page about the owner is not theirs to vouch
for. Moved here unchanged from setup_research, which imports it back under
the same names, so setup's behaviour is identical.

``SPECIAL_CATEGORIES`` / ``special_category``: what Friday never INFERS or
stores about the owner or anyone else without the owner's explicit content
and purpose (north star NS-15.4-3): health (diagnoses, conditions,
medication, mental health, disability, pregnancy), sexual orientation and
sex life, political opinions, religious or philosophical beliefs, racial or
ethnic origin, trade-union membership, genetic or biometric data, criminal
record, immigration status. Its health and sexuality families are setup's
words less the ones that mostly mean something else in a memory line
("condition", "recovery", "patient", "disabled", "orientation", "trans",
"dating partner" words), plus the specific terms setup did not need. Finances,
family and address are NOT in it: a card line such as "Lives in Springfield"
or "Has two kids" is ordinary memory, not a special category.

Deterministic keyword and regex families, no model call. A word is matched
whole (``\\b``), so "Python" is not "politics" and "race condition" is not
ethnicity. ``note_dropped`` counts what an inferring path dropped by category
and place; it never keeps the text.
"""
from __future__ import annotations

import logging
import re
import threading

_log = logging.getLogger("friday.sensitive_categories")

# ── Setup research's table (moved here unchanged) ───────────────────────────

SENSITIVE_CATEGORIES = {
    "health": (
        "health", "healthy", "medical", "medicine", "diagnosis", "diagnosed",
        "illness", "ill", "disease", "cancer", "tumou?r", "surgery", "hospital",
        "hospitali[sz]ed", "therapy", "therapist", "medication", "prescription",
        "pregnan\\w*", "disabilit\\w*", "disabled", "mental", "depression",
        "anxiety", "rehab", "addiction", "clinic", "patient", "symptoms?",
        "chronic", "condition", "recovery", "vaccin\\w*", "hiv", "diabet\\w*",
    ),
    "sexuality": (
        "sex", "sexual", "sexuality", "gay", "lesbian", "bisexual", "queer",
        "transgender", "trans", "lgbt\\w*", "dating", "boyfriend", "girlfriend",
        "orientation", "nude", "nudes",
    ),
    "finances": (
        "salary", "salaries", "income", "net worth", "wealth", "bank",
        "banking", "loan", "loans", "debt", "debts", "mortgage", "bankrupt\\w*",
        "credit score", "tax", "taxes", "earnings", "paycheck", "portfolio",
        "invest\\w*", "compensation", "bonus", "inheritance", "donation",
    ),
    "family": (
        "wife", "husband", "spouse", "partner", "son", "sons", "daughter",
        "daughters", "child", "children", "kid", "kids", "baby", "mother",
        "father", "mom", "mum", "dad", "parent", "parents", "sibling",
        "siblings", "brother", "sister", "family", "married", "marriage",
        "divorce", "divorced", "wedding", "fianc\\w*", "grandchild",
        "grandchildren", "nephew", "niece", "cousin", "stepson",
        "stepdaughter", "widow\\w*",
    ),
    "home_address": (
        "home address", "lives at", "lives in", "lives on", "resides",
        "residence", "resident of", "street address", "zip code", "zipcode",
        "postcode", "postal code", "apartment", "flat \\d", "neighbou?rhood",
        "hometown", "home town", "moved to",
    ),
}

_CATEGORY_RES = {cat: re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)
                 for cat, words in SENSITIVE_CATEGORIES.items()}
#: An amount of money is a finance signal whatever words surround it.
_MONEY_RE = re.compile(r"[$£€¥]\s?\d|\b\d[\d,.]*\s?(?:dollars|pounds|euros|usd|gbp|eur)\b",
                       re.IGNORECASE)


def sensitive_category(text) -> str | None:
    """The first sensitive category `text` touches, or None."""
    if not text:
        return None
    s = str(text)
    for cat, rx in _CATEGORY_RES.items():
        if rx.search(s):
            return cat
    if _MONEY_RE.search(s):
        return "finances"
    try:
        from agent_friday.services.sensitivity_classifier import _ADDRESS_RE
        if _ADDRESS_RE.search(s):
            return "home_address"
    except Exception:
        pass
    return None


#: Plain category words long enough to look for INSIDE a handle or domain,
#: where there are no word boundaries ("sam_health_diary", "samsalary").
_COMPACT_WORDS = tuple(sorted({w for words in SENSITIVE_CATEGORIES.values()
                               for w in words
                               if re.fullmatch(r"[a-z]{4,}", w)}))


def compact_sensitive(token) -> str | None:
    """sensitive_category for a handle or domain: also matches category words
    run together with other letters. Used for handles and sites only, where a
    false refusal costs one query; a person's name is never held to it."""
    s = str(token or "").lower()
    hit = sensitive_category(re.sub(r"[^a-z0-9]+", " ", s))
    if hit:
        return hit
    squashed = re.sub(r"[^a-z]", "", s)
    for w in _COMPACT_WORDS:
        if w in squashed:
            for cat, words in SENSITIVE_CATEGORIES.items():
                if w in words:
                    return cat
    return None


# ── Special categories: never inferred (NS-15.4-3) ──────────────────────────

#: Setup's health and sexuality words this table leaves out, because in a
#: memory line they mostly mean something else ("recovery plan", "patient with
#: slow builds", "notifications disabled", "screen orientation").
_NOT_SPECIAL = frozenset({
    "health", "healthy", "ill", "condition", "recovery", "patient", "clinic",
    "chronic", "mental", "symptoms?", "hospital", "disabled", "diagnosis", "diagnosed",
    "medicine", "trans", "orientation", "dating", "boyfriend", "girlfriend",
})

SPECIAL_CATEGORIES = {
    "health": tuple(w for w in SENSITIVE_CATEGORIES["health"] if w not in _NOT_SPECIAL) + (
        "diagnosed with", "diagnosis of", "mental health", "mental illness",
        "medicated", "medications", "prescribed", "antidepressants?", "ssris?",
        "adderall", "insulin", "chemo\\w*", "illnesses", "diseases?",
        "depressed", "depressive", "panic attacks?", "bipolar", "schizophreni\\w*",
        "psychosis", "psychiatri\\w*", "psychotherap\\w*", "adhd", "autis\\w*",
        "asperger\\w*", "ocd", "ptsd", "dyslexi\\w*", "dyspraxi\\w*", "epilep\\w*",
        "seizures?", "asthma\\w*", "allerg\\w*", "migraines?", "insomnia",
        "dementia", "alzheimer\\w*", "parkinson\\w*", "multiple sclerosis",
        "arthritis", "eating disorder", "anorexi\\w*", "bulimi\\w*",
        "self[- ]harm\\w*", "suicid\\w*", "alcoholi\\w*", "sober", "sobriety",
        "addicted", "addicts?", "overdose\\w*", "miscarriage\\w*", "ivf",
        "infertil\\w*", "fertility", "wheelchair", "hearing aids?", "deaf",
        "chronic (?:pain|illness|fatigue)", "in hospital", "hospital stay",
        "sick leave", "on medication",
    ),
    "sexuality": (
        "sex", "sexual\\w*", "sex life", "gay", "lesbian", "bisexual", "queer",
        "asexual", "pansexual", "homosexual\\w*", "heterosexual\\w*",
        "transgender", "trans (?:man|woman|person|people)", "non-?binary",
        "lgbt\\w*", "coming out as", "came out as", "nude", "nudes",
        "kinky", "fetish\\w*",
    ),
    "politics": (
        "political (?:view|views|opinion|opinions|belief|beliefs|leaning|leanings|party|affiliation)",
        "politically", "votes? (?:labou?r|tory|conservative|republican|democrat\\w*|green|liberal)",
        "voted (?:labou?r|tory|conservative|republican|democrat\\w*|green|liberal|leave|remain)",
        "democrats?", "democratic party", "republicans?", "republican party",
        "tory", "tories", "labou?r party", "labou?r voter", "conservative party",
        "liberal democrats?", "lib dems?", "green party", "socialis[mt]s?",
        "communis[mt]s?", "marxis[mt]s?", "fascis[mt]s?", "anarchis[mt]s?",
        "libertarians?", "left-wing", "right-wing", "far-left", "far-right",
        "maga", "party member(?:ship)?", "brexiteer", "remainer",
    ),
    "religion": (
        "religion", "religions", "religious", "christianity",
        "(?:is|a|devout|practising|practicing|born-again) christian", "catholic\\w*",
        "protestant\\w*", "evangelical\\w*", "baptist", "methodist", "anglican",
        "mormon\\w*", "muslims?", "islam\\w*", "jewish", "jews?", "judaism",
        "hindus?", "hinduism", "buddhis[mt]s?", "sikhs?", "sikhism", "atheis[mt]s?",
        "agnostic\\w*", "church(?:es)?", "churchgoer\\w*", "mosque", "synagogue", "prays?", "praying",
        "prayers?", "bible", "quran", "koran", "torah", "ramadan", "kosher",
        "halal", "spiritual\\w*", "jehovah", "scientolog\\w*",
    ),
    "ethnicity": (
        "racial\\w*", "ethnic\\w*", "ethnicity",
        "black (?:man|woman|person|people|british|american)",
        "white (?:man|woman|person|people)", "african[- ]american", "caucasian",
        "hispanic", "latino", "latina", "latinx", "asian", "indigenous",
        "aboriginal", "mixed[- ]race", "biracial",
    ),
    "trade_union": (
        "trade unions?", "labou?r unions?", "union member\\w*", "unioni[sz]\\w*",
        "union rep", "shop steward", "teamsters", "unite union",
    ),
    "genetic_biometric": (
        "genetic (?:test\\w*|condition\\w*|disorder\\w*|data|risk\\w*|marker\\w*|result\\w*)",
        "genetically", "dna test\\w*", "23andme", "ancestrydna", "genotype\\w*",
        "brca", "biometric\\w*", "retina scan", "iris scan", "voiceprint",
        "face ?print", "palm ?print",
    ),
    "criminal": (
        "criminal record", "criminal conviction", "criminal history", "convicted",
        "arrested", "arrest record", "jail", "jailed", "prison", "imprison\\w*",
        "incarcerat\\w*", "felony", "felon", "felons", "parole", "paroled",
        "misdemeanou?r", "sentenced", "dui", "dwi",
    ),
    "immigration": (
        "immigra\\w*", "visa (?:status|application|sponsorship|overstay|expir\\w*)",
        "green card", "asylum", "refugee\\w*", "undocumented", "deport\\w*",
        "residence permit", "residency permit", "work permit", "citizenship status",
        "illegal alien", "naturali[sz]ation",
    ),
}

_SPECIAL_RES = {cat: re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)
                for cat, words in SPECIAL_CATEGORIES.items()}


def special_category(text) -> str | None:
    """The first special category (NS-15.4-3) `text` states or implies, or None."""
    if not text:
        return None
    s = " ".join(str(text).split())
    for cat, rx in _SPECIAL_RES.items():
        if rx.search(s):
            return cat
    return None


def first_special(*texts) -> str | None:
    """special_category over several texts (a line and its evidence)."""
    for t in texts:
        cat = special_category(t)
        if cat:
            return cat
    return None


# ── What inferring paths dropped: counts only, never the text ───────────────

_LOCK = threading.Lock()
#: (where, category) -> how many candidates were dropped.
DROPPED: dict = {}


def note_dropped(where: str, category: str) -> None:
    """Count one inferred candidate dropped for a special category. The text is
    not passed in, so it cannot be kept or logged."""
    key = (str(where)[:40], str(category)[:40])
    with _LOCK:
        DROPPED[key] = DROPPED.get(key, 0) + 1
    _log.info("dropped an inferred %s candidate (%s); its text was not kept", key[1], key[0])


def dropped_counts() -> dict:
    """{"where:category": count}, for a status view or a test."""
    with _LOCK:
        return {f"{w}:{c}": n for (w, c), n in DROPPED.items()}


def _reset_for_tests() -> None:
    with _LOCK:
        DROPPED.clear()
