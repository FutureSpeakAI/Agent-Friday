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
"medical", "cancer", "vaccine", "therapy", "dating partner" words), plus the
specific terms setup did not need, in the framing that makes them about a
person ("has cancer", "sees a therapist", "in recovery", "is disabled", "is
Black"). Finances, family and address are NOT in it: a card line such as
"Lives in Springfield" or "Has two kids" is ordinary memory, not a special
category. A miss is worse than a false alarm: an inferred line that is
dropped costs one memory, a kept one is the harm.

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
#: slow builds", "notifications disabled", "screen orientation", "medical
#: imaging", "Cancer Research", "vaccine scheduling", "retail therapy", "Wife is
#: a therapist", "coffee addiction"). The person-framed forms are added below.
_NOT_SPECIAL = frozenset({
    "health", "healthy", "ill", "condition", "recovery", "patient", "clinic",
    "chronic", "mental", "symptoms?", "hospital", "disabled", "diagnosis", "diagnosed",
    "medicine", "trans", "orientation", "dating", "boyfriend", "girlfriend",
    "medical", "cancer", "vaccin\\w*", "therapy", "therapist", "addiction",
})

#: Someone's therapist, counsellor, psychologist or psychiatrist (not a job).
_CARER = "(?:therapist|psychologist|counsell?or|psychiatrist)"
#: Small numbers in words, for "sentenced to two years", "sober for three months".
_NUM = "(?:\\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|many)"

SPECIAL_CATEGORIES = {
    "health": tuple(w for w in SENSITIVE_CATEGORIES["health"] if w not in _NOT_SPECIAL) + (
        "diagnosed with", "diagnosis of", "mental health", "mental illness",
        "medical (?:condition|conditions|history|leave|records?|issues?|problems?)",
        "(?:has|had|have|having|with|battling|beat|beating|fighting|survived|treated for|"
        "treatment for|died of|dying of) (?:\\w+ )?cancer(?!\\s+research)",
        "cancer (?:patient|survivor|diagnosis|treatment|scare|free)",
        "(?:my|their|his|her|your|our) (?:" + _CARER[3:-1] + "|therapy)",
        "(?:sees?|seeing|saw|visits?|visiting|talks? to|talking to) (?:a|the|an|their|his|her|my) " + _CARER,
        "in therapy", "therapy (?:sessions?|appointments?)", "goes to therapy", "going to therapy",
        "(?:couples|group|cognitive|behaviou?ral|physical|speech|occupational|hormone|"
        "electroconvulsive) therapy",
        "medicated", "medications", "prescribed", "antidepressants?", "ssris?",
        "adderall", "insulin", "chemo\\w*", "illnesses", "diseases?",
        "depressed", "depressive", "panic attacks?", "bipolar", "schizophreni\\w*",
        "psychosis", "psychiatri\\w*", "psychotherap\\w*", "adhd", "autis\\w*",
        "asperger\\w*", "ocd", "ptsd", "dyslexi\\w*", "dyspraxi\\w*", "epilep\\w*",
        "seizures?", "asthma\\w*", "allerg\\w*", "migraines?", "insomnia",
        "dementia", "alzheimer\\w*", "parkinson\\w*", "multiple sclerosis",
        "arthritis", "eating disorder", "anorexi\\w*", "bulimi\\w*",
        "self[- ]harm\\w*", "suicid\\w*", "alcoholi\\w*", "sobriety",
        # Sober as a person's state, not "sober about estimates".
        "(?:is|am|are|been|stayed|staying|stay|stays|getting|got|went|now) sober"
        "(?!\\s+(?:about|on|in|minded|-minded|assessment|look|view|reading|analysis))",
        "sober (?:for|since) " + _NUM, "sober (?:living|curious)",
        "(?:drug|alcohol|gambling|opioid|substance|heroin|cocaine) (?:addiction|addicts?|abuse)",
        "addiction (?:treatment|recovery|counsell?ing|clinic|centre|center)",
        "(?:recovering |former |ex-)(?:addicts?|alcoholics?)",
        "addicted to (?:alcohol|drugs|heroin|cocaine|crack|opioids?|opiates?|painkillers|"
        "gambling|meth\\w*|pills|ketamine|benzos)",
        "alcoholics anonymous", "narcotics anonymous", "gamblers anonymous",
        "twelve[- ]step program\\w*", "12[- ]step program\\w*",
        "(?:is|am|are|been|i'm|currently|now|still) in recovery(?!\\s+mode)",
        "overdose\\w*", "miscarriage\\w*", "ivf",
        "infertil\\w*", "fertility", "wheelchair", "hearing aids?", "deafness", "hard of hearing",
        "deaf (?:community|culture|school|children|people|person)",
        "registered (?:disabled|blind)", "(?:physically|mentally|learning|visually|hearing)[- ]disabled",
        "disabled (?:person|people|man|woman|veteran|child|son|daughter|badge|parking|since)",
        "chronic (?:pain|illness|fatigue)", "in hospital", "hospital stay",
        "sick leave", "on medication", "long[- ]covid",
        # Psychiatric, HIV and addiction medication by name.
        "prozac", "sertraline", "zoloft", "fluoxetine", "citalopram", "escitalopram", "lexapro",
        "paroxetine", "venlafaxine", "effexor", "wellbutrin", "bupropion", "mirtazapine",
        "lithium(?![\\s-]+(?:batter\\w*|ion|cells?|polymer|min\\w*|price\\w*|supply|deposits?|stocks?))",
        "lamotrigine", "lamictal", "quetiapine", "seroquel", "olanzapine", "risperidone",
        "aripiprazole", "abilify", "clozapine", "ritalin", "methylphenidate", "concerta",
        "vyvanse", "elvanse", "xanax", "alprazolam", "diazepam", "valium", "lorazepam",
        "ativan", "klonopin", "clonazepam", "benzodiazepines?", "antipsychotics?",
        "mood stabili[sz]ers?", "truvada", "descovy", "antiretroviral\\w*",
        "pre-exposure prophylaxis", "methadone", "suboxone", "buprenorphine", "naltrexone",
        "antabuse", "disulfiram",
    ),
    "sexuality": (
        "sex", "sexual\\w*", "sex life", "gay", "lesbian", "bisexual", "queer",
        "asexual", "pansexual", "homosexual\\w*", "heterosexual\\w*",
        "transgender", "trans (?:man|woman|person|people|men|women|kid|child)", "non-?binary",
        "lgbt\\w*", "coming out as", "came out as", "nude", "nudes",
        "kinky", "fetish\\w*",
    ),
    "politics": (
        "political (?:view|views|opinion|opinions|belief|beliefs|leaning|leanings|party|affiliation)",
        "politically", "votes? (?:labou?r|tory|conservative|republican|democrat\\w*|green|liberal)",
        "voted (?:labou?r|tory|conservative|republican|democrat\\w*|green|liberal|leave|remain)",
        "democrats?", "democratic party", "republicans?", "republican party",
        "tory", "tories", "labou?r party", "conservative party",
        "liberal democrats?", "lib dems?", "green party", "socialis[mt]s?",
        "communis[mt]s?", "marxis[mt]s?", "fascis[mt]s?", "anarchis[mt]s?",
        "libertarians?", "left-wing", "right-wing", "far-left", "far-right",
        "maga", "party member(?:ship)?", "brexiteer", "remainer",
        "(?:labou?r|conservative|tory|republican|democrat\\w*|green|liberal|lib dem|reform|snp|"
        "plaid|ukip|trump|biden) (?:voter|voters|supporter|supporters|member|members|activist|"
        "canvasser|donor)",
        "(?:supports?|supporting|supported|backs?|backing|backed) (?:the )?(?:labou?r|conservatives?|"
        "tories|tory|republicans?|democrats?|greens|green party|lib dems?|liberal democrats|"
        "reform uk|reform party|snp|plaid cymru|ukip|trump|biden|harris|maga)",
        "member of the (?:labou?r|conservative|green|liberal democrat|republican|democratic|reform|"
        "communist|socialist) party",
    ),
    "religion": (
        "religion", "religions", "religious", "christianity",
        "(?:is|a|devout|practising|practicing|born-again) christian", "catholic\\w*",
        "protestant\\w*", "evangelical\\w*", "baptist", "methodist", "anglican",
        "mormon\\w*", "muslims?", "islam\\w*", "jewish", "jews?", "judaism",
        "hindus?", "hinduism", "buddhis[mt]s?", "sikhs?", "sikhism", "atheis[mt]s?",
        "agnostic\\w*",
        # Church, not "Church Street" / "Church Road".
        "church(?:es)?(?!\\s+(?:st|street|rd|road|lane|ln|ave|avenue|close|hill|square|sq|walk|way|"
        "row|end|place|green|farm|court|drive|gate|yard|stretton)\\b)",
        "churchgoer\\w*", "mosque", "synagogue", "gurdwara", "mandir",
        "(?:goes|go|going|went|attends?|attending|attended) (?:to )?(?:the )?(?:temple|mass|chapel|shul|"
        "gurdwara|mandir|church|mosque|synagogue)",
        "prays?", "prayed", "praying(?!\\s+mantis)", "prayers?",
        "(?:the|holy|reads? the|reading the|studies the|studying the) bible(?!\\s+of)",
        "bible (?:study|studies|verses?|class\\w*|group|camp)",
        "quran", "koran", "torah", "ramadan",
        # Kosher as observance, not "that's not kosher".
        "(?:keeps?|keeping|kept|eats?|eating|only eats) (?:only )?kosher",
        "kosher (?:diet|home|kitchen|household)",
        "halal", "spiritual(?!\\s+(?:successor|sequel|home))\\w*", "jehovah", "scientolog\\w*",
    ),
    "ethnicity": (
        "racial\\w*", "ethnic\\w*", "ethnicity",
        "black (?:man|woman|men|women|person|people|british|american|caribbean|african|heritage|"
        "family|community)",
        "white (?:man|woman|person|people)", "african[- ]american", "caucasian",
        "hispanic", "latino", "latina", "latinx", "indigenous",
        # Asian as a person, not "Asian food" / "an Asian restaurant".
        "(?:is|am|are|being|as an?|an?) (?:south |east |southeast |south-east |central |west )?asian"
        "(?!\\s+(?:food|cuisine|restaurant\\w*|market\\w*|fusion|grocer\\w*|dish\\w*|recipes?|"
        "cooking|supermarket\\w*|stores?|shops?|takeaway|noodles|flavou?rs?))",
        "(?:south |east |southeast |south-east |central |west )?asian (?:heritage|descent|origin|"
        "background|ancestry|american|british|australian|canadian|man|woman|men|women|person|"
        "people|family|families|community)",
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
        "arrested", "arrest record", "jail", "jailed",
        "prison(?!\\s+architect)", "imprison\\w*",
        "incarcerat\\w*", "felony", "felon", "felons", "parole", "paroled",
        "misdemeanou?r", "dui", "dwi", "(?:was|were|been|being|got|get) sentenced(?!\\s+to\\b)",
        # Sentenced by a court, not "sentenced to refactor".
        "sentenced to (?:life|death|prison|jail|probation|community service|time|"
        + _NUM + " (?:\\w+ )?(?:days?|weeks?|months?|years?))",
        # Probation by a court, not a new job's probation period.
        "on probation(?!\\s+(?:at work|at (?:the|his|her|their|my|a) (?:new )?(?:job|work|company)|"
        "period|for (?:the|his|her|their|my) (?:new )?(?:job|role)))",
        "probation (?:officer|order|service|violation)",
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

#: A person attribute ("Is blind.", "my son is deaf", "she's trans", "he is
#: Black"): the word alone means something else too ("a blind spot", "Wifi is
#: disabled", "Mode is Black", "black coffee"), so it counts only with a PERSON
#: as its subject: the start of a line or sentence (card lines are third person
#: about the owner and drop the subject: "Is deaf.", "Is Black."), a personal
#: pronoun, or a family or relationship word. Any other noun ("Wifi", "Mic",
#: "Project", "Car") is not a person, so a name is not one either: "Sam is
#: blind" is a miss, the price of never reading "Laptop is Black" as an
#: ethnicity. "Is registered blind", "uses a wheelchair" and "Black
#: British/African/Caribbean/American" are caught by the families above
#: whatever the subject. Case-insensitive throughout ("He is black.").
_SUBJECT = ("(?:i|he|she|they|we|you|who|wife|husband|partner|spouse|son|daughter|child|kid|"
            "baby|mother|father|mum|mom|dad|brother|sister|friend|user|owner|boss|colleague|"
            "girlfriend|boyfriend|grandmother|grandfather|grandma|grandpa|aunt|uncle|cousin|"
            "nephew|niece|stepson|stepdaughter|fianc\\w*)"
            "\\s+(?:is|am|are|was|were|has been|have been)")
_PERSON_ATTR_RE = re.compile(
    "(?:(?:^|[.;!?]\\s+)(?:is|am|are|was|were|has been)\\s+|\\b" + _SUBJECT + "\\s+"
    "|\\b(?:i'm|he's|she's|they're|we're|you're)\\s+)"
    "(?:(?:legally|registered|partially|totally|profoundly|physically|severely|completely|"
    "visually)\\s+)?"
    "(?P<attr>disabled|blind|deaf|trans|transgender|black)\\b"
    "(?!\\s+(?:to|by|on|for|until|via|when|because|at|from|and white|belt|friday|spots?|"
    "eye|alley|date|test|box|mode|theme|coffee|tea|mirror|metal|hat|ops))",
    re.IGNORECASE)
_ATTR_CATEGORY = {"disabled": "health", "blind": "health", "deaf": "health",
                  "trans": "sexuality", "transgender": "sexuality", "black": "ethnicity"}

#: Written forms where the case is the meaning: PrEP (the HIV medicine; "prep"
#: is meal prep), AA (Alcoholics Anonymous; "AA batteries").
_CASED_RES = (
    ("health", re.compile(r"\bPrEP\b")),
    ("health", re.compile(r"\b(?i:in|attends?|attending|goes to|go to|going to|went to|joined|"
                          r"meetings? at|meeting) AA\b(?!\s+(?i:batter))")),
)


def special_category(text) -> str | None:
    """The first special category (NS-15.4-3) `text` states or implies, or None."""
    if not text:
        return None
    s = " ".join(str(text).split())
    for cat, rx in _SPECIAL_RES.items():
        if rx.search(s):
            return cat
    m = _PERSON_ATTR_RE.search(s)
    if m:
        return _ATTR_CATEGORY.get(m.group("attr").lower(), "health")
    for cat, rx in _CASED_RES:
        if rx.search(s):
            return cat
    return None


def loggable(text, limit: int = 200) -> str:
    """`text` cut to `limit` for a log line or an exception message, or a
    placeholder naming only the category when it touches a special category:
    model output an inferring path dropped must not reach a log either."""
    s = str(text or "")
    cat = special_category(s)
    if cat:
        return "[withheld: it touches %s]" % cat.replace("_", " ")
    return s[:max(0, int(limit))]


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
