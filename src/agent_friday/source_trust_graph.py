"""
Source Trust Graph — reputation scoring for media outlets and agents.

This is the real innovation split out of the old monolithic trust graph: a
graph that scores *information sources* (news domains, and eventually peer
agents) on how trustworthy their claims are, learned continuously from how
their reporting holds up against the rest of the field.

Six dimensions, each in [0, 1]
------------------------------
The keys are kept for the stored file and the federation protocol; what each
one actually MEASURES on this install is in ``DIMENSION_INFO`` (label + basis),
and that, not the key, is what the UI and the tools show.

factual_accuracy       Shown as "Primary-source corroboration": the outlet
                       covered a story with one or two others and the coverage
                       cites a primary document. Peer attestations (federation)
                       can add claim checks. Nothing here checks that a claim
                       is TRUE, so it is not called accuracy.
correction_behavior    Corrections the outlet ran of its own earlier articles.
source_attribution     Shown as "Attribution (writing style)": attribution words
                       or links in the headline/snippet; a long snippet naming
                       no source counts against.
prediction_accuracy    NOT MEASURED locally. Only a peer attestation can supply
                       it; until one does it has no score and stays out of the
                       composite.
opinion_separation     Shown as "Opinion labelling (writing style)": opinion
                       filed under an opinion/analysis section counts for;
                       "should"/"must"-style words in a news headline against.
narrative_independence NOT MEASURED locally; like predictions, only a peer
                       attestation can supply it.

Composite ``evidence_score`` is the weighted mean of the MEASURED dimensions
(weights renormalised); a dimension that is not measured contributes nothing,
never a placeholder 0.5. ``trust_score`` is that, adjusted by the owner's
explicit Boost/Ban (``owner_preference``), shown as its own line.

Storage
-------
~/.friday/source_trust.json::

    {"sources": {"<domain>": {domain, name, scores{6}, observations[],
                              trust_score, article_count, first_seen,
                              last_updated, user_actions{}}},
     "meta": {"version", "updated_at"}}

Each observation is dated and carries a per-dimension ``signal`` in [0, 1].
Scores are recomputed as a *decayed weighted mean* of the relevant
observations — weight ``0.95 ** weeks_ago`` — so recent accuracy dominates
(decay factor 0.95 per week, per spec). A small seed prior keeps a single
observation from swinging a score to an extreme.
"""

import json
import re
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

from agent_friday.paths import friday_home

# ── Dimensions + composite weights (sum to 1.0) ────────────────────
DIMENSIONS = (
    "factual_accuracy",
    "correction_behavior",
    "source_attribution",
    "prediction_accuracy",
    "opinion_separation",
    "narrative_independence",
)
_WEIGHTS = {
    "factual_accuracy": 0.35,
    "correction_behavior": 0.15,
    "source_attribution": 0.15,
    "prediction_accuracy": 0.05,   # counts only once a peer attestation measures it
    "opinion_separation": 0.15,
    "narrative_independence": 0.15,
}

#: What each dimension is, said plainly. ``local`` is whether this install
#: measures it itself; a dimension that is not measured locally is "not
#: measured" (no score, out of the composite) until evidence for it arrives.
DIMENSION_INFO = {
    "factual_accuracy": {
        "label": "Primary-source corroboration",
        "based_on": ("Stories this outlet covered alongside one or two others where the "
                     "coverage cites a primary document (a filing, ruling, official "
                     "statement or study); claim checks from peer agents if federation "
                     "is on. It does not check whether a claim is true."),
        "local": True},
    "correction_behavior": {
        "label": "Corrections issued",
        "based_on": ("Headlines that correct or retract an article this outlet ran "
                     "earlier; at most two a week count."),
        "local": True},
    "source_attribution": {
        "label": "Attribution (writing style)",
        "based_on": ("Writing-style signal: attribution words ('said', 'according to') "
                     "or links in the headline and snippet count for; a snippet over "
                     "140 characters that names no source counts against."),
        "local": True},
    "prediction_accuracy": {
        "label": "Predictions",
        "based_on": ("Not measured: no forward-looking claim is scored on this install. "
                     "Only a verified peer attestation can supply it."),
        "local": False},
    "opinion_separation": {
        "label": "Opinion labelling (writing style)",
        "based_on": ("Writing-style signal: pieces filed under an opinion or analysis "
                     "section count for; opinion words ('should', 'must') in a headline "
                     "outside one count against."),
        "local": True},
    "narrative_independence": {
        "label": "Independence",
        "based_on": ("Not measured: nothing on this install scores it. Only a verified "
                     "peer attestation can supply it."),
        "local": False},
}
NOT_MEASURED = "not measured"

#: The owner's explicit Boost/Ban on an outlet. It is an input to the score in
#: its own right, shown as its own line, and never folded into a dimension:
#: a Ban caps the score, a Boost lifts it (and at least to the green band).
OWNER_BAN_CEILING = 0.15
OWNER_BOOST_LIFT = 0.15
OWNER_BOOST_FLOOR = 0.70


def owner_adjusted(evidence_score, user_actions):
    """(trust_score, preference) after the owner's Boost/Ban.

    preference is None, or {"state": "banned"|"boosted", "effect": text,
    "set_by": "owner"}. Ban wins over Boost when both are somehow set.
    """
    ua = user_actions if isinstance(user_actions, dict) else {}
    ev = float(evidence_score)
    if ua.get("banned"):
        return (round(min(ev, OWNER_BAN_CEILING), 4),
                {"state": "banned", "set_by": "owner",
                 "effect": "You banned this outlet: its score is held at or below %.2f "
                           "whatever the evidence says. Unban to remove." % OWNER_BAN_CEILING})
    if ua.get("boosted"):
        return (round(max(OWNER_BOOST_FLOOR, min(1.0, ev + OWNER_BOOST_LIFT)), 4),
                {"state": "boosted", "set_by": "owner",
                 "effect": "You boosted this outlet: +%.2f on the evidence score, and at "
                           "least %.2f. Unboost to remove." % (OWNER_BOOST_LIFT, OWNER_BOOST_FLOOR)})
    return round(ev, 4), None

# Decay: an observation N weeks old carries weight 0.95**N.
_WEEKLY_DECAY = 0.95
# Seed prior strength, in pseudo-observations, that anchors a dimension toward
# its seed value until enough real observations accumulate.
_PRIOR_STRENGTH = 3.0
# Cap stored observations per source so the file can't grow without bound.
_MAX_OBSERVATIONS = 400
#: An (article, evidence type) pair counts once, ever: the archiver hands the
#: whole pool to analyze_fetch every tick, and without this the same headline
#: was observed again every five minutes until it pushed older evidence out.
_MAX_EVIDENCE_KEYS = 2000
#: Titles a source has run, so a correction can be matched to the article
#: it corrects. A correction with no prior article is just a headline word.
_MAX_RECENT_TITLES = 300
_CORRECTIONS_PER_WEEK = 2
_CORRECTION_MATCH_JACCARD = 0.3

# Seed reputations so a brand-new graph still produces sensible badges. These
# mirror the static trust map in server.py; the live graph diverges from them
# as real observations accumulate. No city's local press belongs here: local
# outlets come from the owner's `news_local_sources` setting (see
# local_beat_sources), so no install trusts another city's press by default.
_SEED_HIGH = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "npr.org",
    "arstechnica.com", "theverge.com", "wired.com", "nature.com",
    "wsj.com", "nytimes.com", "bloomberg.com", "ft.com", "economist.com",
    "techcrunch.com", "axios.com", "propublica.org", "statnews.com",
    "technologyreview.com", "404media.co", "restofworld.org", "engadget.com",
    "theguardian.com", "politico.com", "theintercept.com", "talkingpointsmemo.com",
    "motherjones.com", "theatlantic.com", "fortune.com", "cnbc.com",
    "marketwatch.com", "businessinsider.com", "texastribune.org",
    "scientificamerican.com",
    "carbonbrief.org", "niemanlab.org", "cjr.org", "poynter.org",
}
_SEED_LOW = {
    "infowars.com", "breitbart.com", "dailybuzzlive.com", "naturalnews.com",
    "yournewswire.com", "beforeitsnews.com", "theonion.com",
}

# ── Heuristic lexicons for the cross-source comparison engine ──────
# Primary-source keywords: when a claim cites these, it's grounded in a
# document/record rather than vibes, which we treat as a factual-accuracy boost.
_PRIMARY_SOURCE_RX = re.compile(
    r"\b(court|lawsuit|sec\b|filing|filed|indictment|subpoena|transcript|"
    r"official|officials|statement|press release|affidavit|ruling|judge|"
    r"according to (?:the )?(?:document|report|filing|data)|"
    r"data show|study|peer-reviewed|earnings|10-k|10-q|prospectus|"
    r"federal register|gao|cbo|inspector general)\b", re.I)

# Attribution markers: signs a piece cites who/what it's reporting from.
_ATTRIBUTION_RX = re.compile(
    r"\b(according to|said|told|reported|cited|sources? (?:said|told|say)|"
    r"spokesperson|in a statement|confirmed|wrote|per |reuters|associated press|"
    r"\bap\b)\b", re.I)

# Correction/retraction markers in a headline or snippet.
_CORRECTION_RX = re.compile(
    r"\b(correction|corrected|retraction|retract(?:s|ed)?|we regret|"
    r"editor'?s note|clarif(?:y|ication|ies|ied)|updates? (?:an )?earlier)\b", re.I)

# Opinion/analysis section markers — usually in the URL path or category.
_OPINION_PATH_RX = re.compile(
    r"/(opinion|analysis|commentary|editorial|op-ed|perspective|column|blogs?)/",
    re.I)
_OPINION_WORD_RX = re.compile(
    r"\b(opinion|op-ed|commentary|editorial|analysis|i think|we believe|"
    r"should|must|the case for|why .* is wrong)\b", re.I)


def _today_str():
    return date.today().isoformat()


def _parse_date(s):
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return date.today()


def _extract_domain(url_or_text):
    """Collapse a URL/messy source string into a bare domain (mirrors the
    server helper so the two graphs key sources identically)."""
    s = (url_or_text or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    s = re.split(r"[\s/?#›»]", s)[0]
    if s.startswith("www."):
        s = s[4:]
    return s.strip(".")


#: Upper bound on the owner's Local beat list; a settings file is not a feed
#: directory, and every entry costs a feed request per fetch.
_MAX_LOCAL_SOURCES = 50
_DOMAIN_RX = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def normalize_local_sources(raw):
    """Bare, de-duplicated domains from a `news_local_sources` value.

    Accepts a list or a comma/newline separated string of domains or URLs.
    Anything that does not reduce to a dotted hostname is dropped, so a
    malformed setting yields fewer sources, never an exception.
    """
    if isinstance(raw, str):
        items = re.split(r"[,\r\n]", raw)
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        return ()
    out = []
    for item in items:
        if not isinstance(item, str):
            continue
        domain = _extract_domain(item)
        if domain and _DOMAIN_RX.match(domain) and domain not in out:
            out.append(domain)
        if len(out) >= _MAX_LOCAL_SOURCES:
            break
    return tuple(out)


def local_beat_sources():
    """The outlets the owner named for their Local beat (the
    `news_local_sources` setting), as bare domains in the owner's order.

    Nothing ships here: an install with no setting has no local outlets, and
    an unreadable settings file means none rather than an error. These
    domains seed at the same high trust as `_SEED_HIGH`.
    """
    try:
        from agent_friday.core import _load_settings
        raw = (_load_settings() or {}).get("news_local_sources")
    except Exception:
        return ()
    return normalize_local_sources(raw)


def trusted_outlets():
    """The outlets the owner trusts without tagging them Local (the
    `news_trusted_outlets` setting), as bare domains. They seed at the same
    high trust as `_SEED_HIGH` and are NOT fetched or labelled as Local news.
    Nothing ships here; an unreadable settings file means none."""
    try:
        from agent_friday.core import _load_settings
        raw = (_load_settings() or {}).get("news_trusted_outlets")
    except Exception:
        return ()
    return normalize_local_sources(raw)


def _owner_seed_lists():
    """(local beat outlets, trusted outlets) from ONE settings read."""
    try:
        from agent_friday.core import _load_settings
        st = _load_settings() or {}
    except Exception:
        return (), ()
    return (normalize_local_sources(st.get("news_local_sources")),
            normalize_local_sources(st.get("news_trusted_outlets")))


def seed_basis(domain):
    """Why a source starts where it does: (base value, plain reason)."""
    local, trusted = _owner_seed_lists()
    if domain in _SEED_HIGH:
        return 0.75, "built-in high-trust outlet list"
    if domain in trusted:
        return 0.75, "your trusted outlets (News > Customize)"
    if domain in local:
        return 0.75, "your Local beat outlets (News > Customize)"
    if domain in _SEED_LOW:
        return 0.30, "built-in low-trust outlet list"
    return 0.50, "unknown outlet: neutral start"


def _seed_for(domain):
    """Seed dimension scores for a source we've never observed.

    Seeds are chosen so the composite lands green (≥0.7) for the static
    high-trust set, the owner's trusted outlets and the owner's Local beat
    outlets, red (<0.4) for the known low-trust set, and neutral yellow
    for everything unknown — matching the old static badge behaviour until real
    observations move the needle.
    """
    base, _why = seed_basis(domain)
    seed = {d: base for d in DIMENSIONS}
    # prediction_accuracy is a placeholder dimension (no retroactive scoring
    # yet), so it stays neutral regardless of general reputation.
    seed["prediction_accuracy"] = 0.5
    return seed


def _initial_scores(domain):
    """The scores a source has before any evidence: the seed prior for every
    dimension this install measures, None ("not measured") for the rest."""
    seed = _seed_for(domain)
    return {d: (seed[d] if DIMENSION_INFO[d]["local"] else None) for d in DIMENSIONS}


def _evidence_counts(rec):
    counts = {d: 0 for d in DIMENSIONS}
    for o in (rec or {}).get("observations") or []:
        d = o.get("dimension") if isinstance(o, dict) else None
        if d in counts and isinstance(o.get("signal"), (int, float)):
            counts[d] += 1
    return counts


#: path -> ((mtime_ns, size), parsed data); see SourceTrustGraph._load_readonly.
_READ_CACHE: dict = {}


import hashlib as _hashlib
import logging as _logging
import unicodedata as _unicodedata
from urllib.parse import urlsplit as _urlsplit, parse_qsl as _parse_qsl, urlencode as _urlencode

_log = _logging.getLogger("friday.source_trust")

#: Query parameters that say how a reader arrived, never which article it is.
#: The same story reached through two newsletters is one article.
_TRACKING_PARAM_RX = re.compile(
    r"^(utm_[a-z0-9_]*|fbclid|gclid|dclid|gbraid|wbraid|msclkid|mc_cid|mc_eid|igshid|"
    r"yclid|_ga|_gl|ref|ref_src|ref_url|referrer|cmpid|ocid|smid|smtyp|sr_share|taid|"
    r"at_medium|at_campaign|at_custom\d*|mbid|spm|share|s_cid|icid|ito|guccounter)$", re.I)

#: Each per-article heuristic is one CHECK. Its two outcomes (attribution
#: present / absent, opinion labelled / unlabelled) are the same check, so an
#: article whose snippet changes between passes cannot be scored twice.
CHECK_OF = {
    "correction_issued": "correction",
    "attribution_present": "attribution",
    "attribution_absent": "attribution",
    "opinion_labeled": "opinion",
    "opinion_unlabeled": "opinion",
    "primary_corroborated": "primary",
}
_TYPES_OF_CHECK: dict = {}
for _t, _c in CHECK_OF.items():
    _TYPES_OF_CHECK.setdefault(_c, []).append(_t)


def normalize_article_url(url) -> str:
    """The article a URL names, without how the reader got there.

    Lower-cased host without ``www.``, no scheme, no fragment, no tracking
    parameters (utm_*, fbclid, ...), remaining parameters sorted, no trailing
    slash. Two links to the same story reduce to the same string; anything
    that is not an http(s) URL reduces to "".
    """
    s = (url or "").strip()
    if not s:
        return ""
    if "://" not in s:
        # mailto:, tel:, javascript: ... are not articles; host:port is.
        if re.match(r"^[a-z][a-z0-9+.\-]*:(?!\d)", s, re.I):
            return ""
        s = "https://" + s
    try:
        parts = _urlsplit(s)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    port = ""
    try:
        if parts.port and parts.port not in (80, 443):
            port = ":%d" % parts.port
    except ValueError:
        port = ""
    path = re.sub(r"/{2,}", "/", parts.path or "")
    if len(path) > 1:
        path = path.rstrip("/")
    if path == "/":
        path = ""
    query = sorted((k, v) for k, v in _parse_qsl(parts.query, keep_blank_values=True)
                   if not _TRACKING_PARAM_RX.match(k))
    q = ("?" + _urlencode(query)) if query else ""
    return f"{host}{port}{path}{q}"


def normalize_headline(title) -> str:
    """A headline reduced to its words: case, accents, punctuation and spacing
    differences between two passes of the same feed do not make a new article."""
    t = _unicodedata.normalize("NFKC", str(title or "")).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _sha(basis: str) -> str:
    return _hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def _article_key(art, domain=None) -> str:
    """A stable id for an article: its normalised URL, else (outlet,
    normalised headline). "" when there is neither."""
    url = normalize_article_url(art.get("url") or "")
    if url:
        return "u" + _sha(url)
    head = normalize_headline(art.get("title") or "")
    if not head:
        return ""
    outlet = _extract_domain(domain or art.get("source") or "")
    return "t" + _sha(f"{outlet}|{head}")


def _legacy_article_key(art) -> str:
    """The key the graph used before URLs were normalised (raw URL, else the
    lower-cased title). Still honoured, so an article counted under the old
    key is not counted again after an upgrade."""
    basis = (art.get("url") or "").strip() or (art.get("title") or "").strip().lower()
    if not basis:
        return ""
    return _sha(basis)


def _dedupe_key(obs):
    """(check, article) for an observation a local check produced, else None.

    Observations written before they carried an ``article_key`` are matched by
    their check and their normalised detail (the headline the check ran on).
    """
    check = CHECK_OF.get(obs.get("type"))
    if not check or (obs.get("signed_by") or "local") != "local":
        return None
    ak = obs.get("article_key")
    if ak:
        return (check, "k:" + str(ak))
    detail = normalize_headline(obs.get("detail") or "")
    if not detail:
        return None
    return (check, "d:" + detail)


#: Paths whose duplicate-evidence cleanup has already been logged this run.
_DEDUPE_LOGGED: set = set()
_EVIDENCE_DEDUPE_VERSION = 1
#: Bumped when the scoring rules change; stored scores are recomputed once.
_SCORING_VERSION = 2


_WORD_RX = re.compile(r"[a-z0-9]{3,}")
_CORRECTION_WORDS = frozenset({"correction", "corrected", "retraction", "retract", "retracts",
                               "retracted", "regret", "editor", "note", "clarify",
                               "clarification", "clarifies", "clarified", "updates", "update",
                               "earlier"})


def _title_words(title) -> set:
    return {w for w in _WORD_RX.findall((title or "").lower()) if w not in _CORRECTION_WORDS}


def _corrects_a_prior_article(rec, title) -> bool:
    """True when this correction resembles an article the source ran before."""
    words = _title_words(title)
    if not words:
        return False
    for prior in rec.get("recent_titles") or []:
        pw = _title_words(prior.get("t"))
        if not pw:
            continue
        j = len(words & pw) / float(len(words | pw))
        if j >= _CORRECTION_MATCH_JACCARD:
            return True
    return False


def _corrections_this_week(rec) -> int:
    cutoff = (datetime.now() - timedelta(days=7)).date()
    n = 0
    for o in rec.get("observations") or []:
        if o.get("type") != "correction_issued":
            continue
        try:
            d = datetime.strptime(str(o.get("date"))[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if d >= cutoff:
            n += 1
    return n


def _log_owner_preference(rec, action, prev_score, before):
    """One event in the sources log for an owner Boost/Ban change."""
    try:
        from agent_friday.trust import log as _tlog
        _tlog.append(_tlog.sources_path(), entity_id=rec.get("domain") or "",
                     entity_kind="source", kind="owner_rule", origin="owner",
                     dimension=None, signal=None,
                     provenance={"action": action},
                     detail=("trust %s -> %s" % (prev_score, rec.get("trust_score")))[:160],
                     effect=_tlog.effect_between(before, dict(rec.get("scores") or {})))
    except Exception:
        pass


def _log_source_event(rec, obs_type, dimension, signal, detail, before, *, origin,
                      provenance=None):
    """One event in the sources log for an observation that just landed."""
    try:
        from agent_friday.trust import log as _tlog
        after = dict(rec.get("scores") or {})
        _tlog.append(_tlog.sources_path(), entity_id=rec.get("domain") or "", entity_kind="source",
                     kind="observation", origin=origin, dimension=dimension, signal=signal,
                     provenance=dict(provenance or {}, type=obs_type),
                     detail=(detail or "")[:160],
                     effect=_tlog.effect_between(before, after, [dimension]))
    except Exception:
        pass


class SourceTrustGraph:
    def __init__(self, friday_dir=None):
        self.friday_dir = Path(friday_dir or friday_home())
        self.path = self.friday_dir / "source_trust.json"
        self._lock = threading.RLock()

    # ── persistence ────────────────────────────────────────────────

    def _load(self):
        if not self.path.exists():
            return {"sources": {}, "meta": {"version": "1.0"}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"sources": {}, "meta": {"version": "1.0"}}
            data.setdefault("sources", {})
            data.setdefault("meta", {"version": "1.0"})
            self._dedupe_evidence_once(data)
            self._rescore_once(data)
            return data
        except Exception:
            return {"sources": {}, "meta": {"version": "1.0"}}

    def _rescore_once(self, data):
        """Recompute every stored source once when the scoring rules change
        (here: not-measured dimensions leave the composite), so no stored
        score keeps a placeholder in it. Stamped in meta; persisted by the
        next write."""
        meta = data.setdefault("meta", {})
        if meta.get("scoring_version") == _SCORING_VERSION:
            return
        for rec in (data.get("sources") or {}).values():
            if isinstance(rec, dict):
                last = rec.get("last_updated")
                self._recompute(rec)
                if last:
                    rec["last_updated"] = last  # a rescore is not new evidence
        meta["scoring_version"] = _SCORING_VERSION

    def _dedupe_evidence_once(self, data):
        """One-time cleanup of evidence the same article produced more than once.

        Graphs written before every (article, check) pair counted once hold the
        same headline observed on every feed pass; with the observation cap,
        those repeats pushed older evidence out. Keep the EARLIEST observation
        of each (check, article) pair and drop the rest, recompute the touched
        sources, and stamp meta so it runs once. Readers see the cleaned view
        at once; the next write persists it. Only the local per-article checks
        are touched: owner, peer and federation observations are never removed.
        """
        meta = data.setdefault("meta", {})
        if not isinstance(meta, dict):
            meta = data["meta"] = {}
        done = meta.get("evidence_dedupe")
        if isinstance(done, dict) and done.get("version", 0) >= _EVIDENCE_DEDUPE_VERSION:
            return 0
        removed, touched = 0, 0
        for rec in (data.get("sources") or {}).values():
            if not isinstance(rec, dict):
                continue
            obs = rec.get("observations")
            if not isinstance(obs, list) or len(obs) < 2:
                continue
            seen, kept = set(), []
            for o in obs:
                k = _dedupe_key(o) if isinstance(o, dict) else None
                if k is not None:
                    if k in seen:
                        continue
                    seen.add(k)
                kept.append(o)
            if len(kept) != len(obs):
                removed += len(obs) - len(kept)
                touched += 1
                rec["observations"] = kept
                self._recompute(rec)
        meta["evidence_dedupe"] = {"version": _EVIDENCE_DEDUPE_VERSION,
                                   "date": _today_str(), "removed": removed,
                                   "sources": touched}
        if removed and str(self.path) not in _DEDUPE_LOGGED:
            _DEDUPE_LOGGED.add(str(self.path))
            _log.info("source trust: removed %d repeated observation(s) of the same "
                      "article and check across %d source(s); kept the earliest of each",
                      removed, touched)
        return removed

    def _load_readonly(self):
        """The parsed file, reused until it changes on disk. READERS ONLY.

        source_trust.json runs to megabytes, and the news feed looks up a
        trust score for every item it shows: parsing the whole file per lookup
        cost about 115 ms each, 15-20 s for one feed, and a spoken "what's in
        the news?" waited through all of it. The parse is now keyed on the
        file's mtime and size, so any write (ours or anyone's) is seen on the
        next read. Writers still call _load() for a private copy they mutate.
        Callers must not mutate what this returns.
        """
        try:
            st = self.path.stat()
        except OSError:
            return {"sources": {}, "meta": {"version": "1.0"}}
        key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = _READ_CACHE.get(str(self.path))
            if hit is not None and hit[0] == key:
                return hit[1]
        data = self._load()
        with self._lock:
            _READ_CACHE[str(self.path)] = (key, data)
        return data

    def _save(self, data):
        """Write the whole file, compact. Every reader parses it with
        json.loads, and indentation roughly doubled a multi-megabyte write."""
        data.setdefault("meta", {})["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.friday_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, separators=(",", ":"), default=str),
                             encoding="utf-8")

    # ── record management ──────────────────────────────────────────

    def _get_or_create(self, data, domain, name=None):
        domain = _extract_domain(domain)
        if not domain:
            return None
        rec = data["sources"].get(domain)
        if rec is None:
            rec = {
                "domain": domain,
                "name": name or domain,
                "scores": _initial_scores(domain),
                "observations": [],
                "trust_score": 0.0,
                "article_count": 0,
                "first_seen": _today_str(),
                "last_updated": _today_str(),
                "user_actions": {"banned": False, "boosted": False,
                                 "clicks": 0, "read_laters": 0},
            }
            rec["trust_score"] = self._composite(rec["scores"])
            data["sources"][domain] = rec
        elif name and rec.get("name") in (None, "", domain):
            rec["name"] = name
        return rec

    @staticmethod
    def _composite(scores):
        """Weighted mean of the dimensions that HAVE a score, weights
        renormalised over them. A dimension that is not measured (None) is
        left out; it never contributes a placeholder number."""
        num = den = 0.0
        for d in DIMENSIONS:
            v = (scores or {}).get(d, 0.5)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            num += _WEIGHTS[d] * v
            den += _WEIGHTS[d]
        return round(num / den, 4) if den else 0.5

    def _recompute(self, rec):
        """Recompute each dimension as a seed-anchored, time-decayed weighted
        mean of its observations, then the composite trust score.

        A dimension this install does not measure (DIMENSION_INFO local=False)
        stays None until at least one observation for it exists."""
        seed = _seed_for(rec.get("domain", ""))
        obs = rec.get("observations") or []
        today = date.today()
        scores = {}
        for dim in DIMENSIONS:
            num = _PRIOR_STRENGTH * seed[dim]
            den = _PRIOR_STRENGTH
            n = 0
            for o in obs:
                if o.get("dimension") != dim:
                    continue
                sig = o.get("signal")
                if not isinstance(sig, (int, float)):
                    continue
                weeks = max(0.0, (today - _parse_date(o.get("date"))).days / 7.0)
                w = _WEEKLY_DECAY ** weeks
                num += w * float(sig)
                den += w
                n += 1
            if not n and not DIMENSION_INFO[dim]["local"]:
                scores[dim] = None
                continue
            scores[dim] = round(num / den, 4) if den else seed[dim]
        rec["scores"] = scores
        rec["evidence_score"] = self._composite(scores)
        rec["trust_score"], rec["owner_preference"] = owner_adjusted(
            rec["evidence_score"], rec.get("user_actions"))
        rec["last_updated"] = _today_str()
        return rec

    # ── public mutation API ────────────────────────────────────────

    def observe(self, domain, obs_type, dimension, signal, detail="",
                counter_sources=None, signed_by="local", name=None):
        """Append one observation to a source and recompute its scores.

        signal is the per-dimension value in [0, 1] this observation implies
        (1.0 = fully positive evidence for the dimension, 0.0 = fully negative).
        """
        if dimension not in DIMENSIONS:
            return None
        with self._lock:
            data = self._load()
            rec = self._get_or_create(data, domain, name)
            if rec is None:
                return None
            try:
                signal = max(0.0, min(1.0, float(signal)))
            except (TypeError, ValueError):
                return None
            rec.setdefault("observations", []).append({
                "date": _today_str(),
                "type": obs_type,
                "dimension": dimension,
                "signal": signal,
                "detail": (detail or "")[:300],
                "counter_sources": list(counter_sources or []),
                "signed_by": signed_by,
            })
            # Trim oldest observations past the cap.
            if len(rec["observations"]) > _MAX_OBSERVATIONS:
                rec["observations"] = rec["observations"][-_MAX_OBSERVATIONS:]
            before = dict(rec.get("scores") or {})
            self._recompute(rec)
            self._save(data)
            _log_source_event(rec, obs_type, dimension, signal, detail, before,
                              origin=("peer" if signed_by not in ("local", "user") else
                                      ("owner" if signed_by == "user" else "system")),
                              provenance={"signed_by": signed_by})
            return rec

    def record_article_seen(self, domain, name=None):
        """Bump a source's article counter (and ensure it exists)."""
        self.record_articles_seen([domain], name=name)

    def record_articles_seen(self, domains, name=None):
        """Bump the article counter once per entry in ``domains``.

        One load and at most one save for the whole batch: the file runs to
        megabytes, so a caller counting a fetch cycle passes every domain here
        rather than calling record_article_seen per article. Blank or
        unparseable entries are skipped; nothing is written when none count.
        """
        with self._lock:
            data = self._load()
            counted = 0
            for domain in domains or ():
                if not domain:
                    continue
                rec = self._get_or_create(data, domain, name)
                if rec is None:
                    continue
                rec["article_count"] = int(rec.get("article_count", 0)) + 1
                counted += 1
            if counted:
                self._save(data)
            return counted

    def record_user_action(self, domain, action):
        """Mirror a user ban/boost/click/read_later into user_actions."""
        with self._lock:
            data = self._load()
            rec = self._get_or_create(data, domain)
            if rec is None:
                return
            ua = rec.setdefault("user_actions",
                                {"banned": False, "boosted": False,
                                 "clicks": 0, "read_laters": 0})
            # Ban and Boost are mutually exclusive, as the news routes keep
            # the two lists: banning un-boosts and boosting un-bans.
            if action == "ban":
                ua["banned"] = True
                ua["boosted"] = False
            elif action == "unban":
                ua["banned"] = False
            elif action == "boost":
                ua["boosted"] = True
                ua["banned"] = False
            elif action == "unboost":
                ua["boosted"] = False
            elif action == "click":
                ua["clicks"] = int(ua.get("clicks", 0)) + 1
            elif action in ("read_later", "read"):
                ua["read_laters"] = int(ua.get("read_laters", 0)) + 1
            if action in ("ban", "unban", "boost", "unboost"):
                # The owner's preference is an input to the score: apply it now.
                before = dict(rec.get("scores") or {})
                prev = rec.get("trust_score")
                self._recompute(rec)
                _log_owner_preference(rec, action, prev, before)
            self._save(data)

    # ── reads ──────────────────────────────────────────────────────

    def all_sources(self):
        data = self._load_readonly()
        return [dict(r) for r in data["sources"].values()]

    def get(self, domain):
        data = self._load_readonly()
        rec = data["sources"].get(_extract_domain(domain))
        return dict(rec) if rec is not None else None

    def score_for(self, domain):
        """Composite trust score for a domain (seed value if never observed)."""
        rec = self.get(domain)
        if rec:
            return float(rec.get("trust_score", 0.5))
        return self._composite(_initial_scores(_extract_domain(domain)))

    def dimensions_for(self, domain):
        """The six dimension scores for a domain (seed values if unobserved).
        A dimension that is not measured is None, never a placeholder."""
        rec = self.get(domain)
        if rec:
            scores = dict(rec.get("scores", {}))
            counts = _evidence_counts(rec)
            for d in DIMENSIONS:
                if not DIMENSION_INFO[d]["local"] and not counts[d]:
                    scores[d] = None
            return scores
        return _initial_scores(_extract_domain(domain))

    def explain(self, domain):
        """Every dimension of one outlet with what it is based on and how much
        evidence it rests on, plus the composite and how it was formed. The
        one place the tools and the UI get a breakdown from, so they say the
        same thing. Works for an outlet never observed (seed prior only)."""
        dom = _extract_domain(domain)
        if not dom:
            return None
        rec = self.get(dom)
        known = rec is not None
        rec = rec or {"domain": dom, "name": dom, "observations": [],
                      "scores": _initial_scores(dom), "article_count": 0}
        scores = self.dimensions_for(dom)
        counts = _evidence_counts(rec)
        dims = {}
        for d in DIMENSIONS:
            info = DIMENSION_INFO[d]
            v = scores.get(d)
            measured = v is not None
            dims[d] = {
                "label": info["label"],
                "score": (round(float(v), 3) if measured else None),
                "status": ("measured" if counts[d] else
                           ("prior only (no evidence yet)" if measured else NOT_MEASURED)),
                "based_on": info["based_on"],
                "evidence_count": counts[d],
                "in_composite": measured,
            }
        evidence_score = self._composite(scores)
        out = {
            "kind": "source",
            "domain": dom,
            "name": rec.get("name") or dom,
            "known": known,
            "trust_score": None,
            "evidence_score": round(evidence_score, 3),
            "composite_based_on": ("Weighted mean of the measured dimensions "
                                   "(weights renormalised); not-measured dimensions "
                                   "are left out. The owner's Boost/Ban, if any, is "
                                   "applied after, as owner_preference."),
            "owner_preference": None,
            "prior": {"value": seed_basis(dom)[0], "based_on": seed_basis(dom)[1],
                      "strength_observations": _PRIOR_STRENGTH},
            "dimensions": dims,
            "evidence_count": sum(counts.values()),
            "article_count": int(rec.get("article_count") or 0),
            "first_seen": rec.get("first_seen"),
            "last_updated": rec.get("last_updated"),
        }
        final, pref = owner_adjusted(evidence_score, rec.get("user_actions"))
        out["trust_score"] = round(final, 3)
        out["owner_preference"] = pref
        return out

    def leaderboard(self, limit=200):
        rows = sorted(self.all_sources(),
                      key=lambda r: r.get("trust_score", 0), reverse=True)
        out = []
        for r in rows[:limit]:
            out.append({
                "domain": r.get("domain"),
                "name": r.get("name") or r.get("domain"),
                "trust_score": r.get("trust_score", 0.0),
                "evidence_score": r.get("evidence_score", r.get("trust_score", 0.0)),
                "owner_preference": (r.get("owner_preference") or {}).get("state"),
                "scores": r.get("scores", {}),
                "article_count": r.get("article_count", 0),
                "observation_count": len(r.get("observations") or []),
                "first_seen": r.get("first_seen"),
                "user_actions": r.get("user_actions", {}),
            })
        return out

    # ── cross-source comparison engine ─────────────────────────────

    def analyze_fetch(self, pool, clusters, owner=None):
        """Run cross-source comparison over one fetch cycle and log
        observations. ``pool`` is the flat scored article list;
        ``clusters`` is the output of the title-Jaccard clusterer.

        Returns a small summary dict for logging.

        Heuristics (snippet/title-level, no model call):
          * Per-article attribution + opinion-separation signals.
          * Correction/retraction detection, counted only against an article
            the same source ran before, at most two a week.
          * Within a small cluster (2-3 sources) whose coverage cites primary
            documents, the covering sources get a factual_accuracy boost.

        Every (article, evidence type) pair counts once, ever, so handing the
        same pool back every tick adds nothing. Disagreeing with the pack is
        not evidence of anything: the sentiment-based minority penalty is
        gone, and a lonely framing waits for the claim to resolve.
        """
        summary = {"attribution": 0, "opinion": 0, "corrections": 0,
                   "minority_claims": 0, "primary_boosts": 0,
                   "independence": 0, "articles": 0}
        with self._lock:
            data = self._load()
            if owner is not None:
                self._sync_owner(data, owner)

            # ── 1. Per-article signals over the whole pool ──
            for art in pool or []:
                domain = _extract_domain(art.get("source") or art.get("url", ""))
                if not domain:
                    continue
                rec = self._get_or_create(data, domain)
                summary["articles"] += 1
                title = art.get("title") or ""
                snippet = art.get("snippet") or ""
                url = art.get("url") or ""
                text = f"{title} {snippet}"
                akey = _article_key(art, domain)
                lkey = _legacy_article_key(art)

                # Correction behaviour: a correction/retraction is positive
                # evidence that the source owns its mistakes -- when it
                # corrects something the source actually ran. A headline that
                # merely contains the word is not accountability, and a flood
                # of them is a way to farm the dimension: at most two a week.
                if _CORRECTION_RX.search(text):
                    if (_corrects_a_prior_article(rec, title)
                            and _corrections_this_week(rec) < _CORRECTIONS_PER_WEEK
                            and self._append(rec, "correction_issued", "correction_behavior",
                                             0.95, detail=title[:160], article_key=akey, legacy_key=lkey)):
                        summary["corrections"] += 1
                elif title:
                    self._remember_title(rec, title)

                # Source attribution: reward visible citation; lightly penalise
                # a substantive snippet that cites nothing.
                if _ATTRIBUTION_RX.search(text) or "http" in snippet:
                    if self._append(rec, "attribution_present", "source_attribution",
                                    0.9, detail=title[:160], article_key=akey, legacy_key=lkey):
                        summary["attribution"] += 1
                elif len(snippet) > 140:
                    self._append(rec, "attribution_absent", "source_attribution",
                                 0.3, detail=title[:160], article_key=akey, legacy_key=lkey)

                # Opinion separation: clearly-labelled opinion (section path) is
                # good practice. Opinion language with no label is a soft miss.
                cat = (art.get("category") or "").lower()
                if _OPINION_PATH_RX.search(url) or cat in ("opinion", "analysis"):
                    if self._append(rec, "opinion_labeled", "opinion_separation",
                                    0.9, detail=title[:160], article_key=akey, legacy_key=lkey):
                        summary["opinion"] += 1
                elif _OPINION_WORD_RX.search(title) and not _OPINION_PATH_RX.search(url):
                    self._append(rec, "opinion_unlabeled", "opinion_separation",
                                 0.4, detail=title[:160], article_key=akey, legacy_key=lkey)

            # ── 2. Cluster-level cross-source comparison ──
            for cl in clusters or []:
                arts = cl.get("articles") or []
                src_count = cl.get("source_count") or len({a.get("source") for a in arts})
                cluster_primary = any(
                    _PRIMARY_SOURCE_RX.search(f"{a.get('title','')} {a.get('snippet','')}")
                    for a in arts)

                # Small, well-sourced, primary-document-backed story: credit the
                # 2-3 sources that surfaced it for factual accuracy.
                if 2 <= src_count <= 3 and cluster_primary:
                    for a in arts:
                        domain = _extract_domain(a.get("source") or a.get("url", ""))
                        if not domain:
                            continue
                        rec = self._get_or_create(data, domain)
                        if self._append(
                                rec, "primary_corroborated", "factual_accuracy", 0.9,
                                detail=f"Primary-sourced story: {cl.get('headline','')[:120]}",
                                counter_sources=[], article_key=_article_key(a, domain),
                                legacy_key=_legacy_article_key(a)):
                            summary["primary_boosts"] += 1

                # A source that disagrees with a large consensus is NOT marked
                # down for it. Sentiment is not a claim and agreement is not
                # accuracy; scoops and early corrections of a wrong consensus
                # all start as the minority framing. Claim resolution (a later
                # phase) is what decides who was right.

            # Recompute every touched source and persist once.
            for domain in list(data["sources"].keys()):
                self._recompute(data["sources"][domain])
            self._save(data)
        return summary

    def _sync_owner(self, data, owner):
        """Make user_actions agree with the owner's banned/boosted lists (the
        news preference files are the source of truth; the mirror into the
        graph is fail-soft and older installs never had it). In memory; the
        caller recomputes and saves."""
        banned = {_extract_domain(d) for d in (owner.get("banned") or ()) if d}
        boosted = {_extract_domain(d) for d in (owner.get("boosted") or ()) if d}
        for dom in banned | boosted:
            self._get_or_create(data, dom)
        for dom, rec in (data.get("sources") or {}).items():
            ua = rec.setdefault("user_actions", {"banned": False, "boosted": False,
                                                 "clicks": 0, "read_laters": 0})
            ua["banned"] = dom in banned
            ua["boosted"] = dom in boosted

    def _append(self, rec, obs_type, dimension, signal, detail="",
                counter_sources=None, signed_by="local", article_key=None,
                legacy_key=None):
        """Internal: append an observation to an in-memory record (caller saves).

        Returns True when the observation was added. With an ``article_key``
        the pair (article, check) counts once, ever; a repeat returns False.
        The check is the heuristic, not its outcome (CHECK_OF): an article
        scored "attribution absent" on one pass is not scored "attribution
        present" on the next because its snippet grew. A key the graph used
        before URLs were normalised (``legacy_key``) is honoured as well.
        """
        check = CHECK_OF.get(obs_type, obs_type)
        if article_key:
            keys = rec.setdefault("evidence_keys", [])
            ek = f"{article_key}:{check}"
            have = set(keys)
            if ek in have:
                return False
            if legacy_key and any(f"{legacy_key}:{t}" in have
                                  for t in _TYPES_OF_CHECK.get(check, [obs_type])):
                return False
            keys.append(ek)
            if len(keys) > _MAX_EVIDENCE_KEYS:
                del keys[:-_MAX_EVIDENCE_KEYS]
        before = dict(rec.get("scores") or {})
        rec.setdefault("observations", []).append({
            "date": _today_str(),
            "type": obs_type,
            "dimension": dimension,
            "signal": max(0.0, min(1.0, float(signal))),
            "detail": (detail or "")[:300],
            "counter_sources": list(counter_sources or []),
            "signed_by": signed_by,
            "article_key": article_key or "",
            "check": check,
        })
        if len(rec["observations"]) > _MAX_OBSERVATIONS:
            rec["observations"] = rec["observations"][-_MAX_OBSERVATIONS:]
        # The effect is known only once the record is recomputed; the caller
        # recomputes every touched source at the end of the tick, so the log
        # records the movement this observation alone produces.
        self._recompute(rec)
        _log_source_event(rec, obs_type, dimension, signal, detail, before,
                          origin="system", provenance={"article_key": article_key or ""})
        return True

    @staticmethod
    def _remember_title(rec, title):
        """Keep the titles a source ran, so a later correction can be matched."""
        titles = rec.setdefault("recent_titles", [])
        t = (title or "")[:160]
        if any(x.get("t") == t for x in titles[-50:]):
            return
        titles.append({"t": t, "d": _today_str()})
        if len(titles) > _MAX_RECENT_TITLES:
            del titles[:-_MAX_RECENT_TITLES]


# ── singleton accessor ─────────────────────────────────────────────

_instance = None
_instance_lock = threading.Lock()


def get_source_trust_graph(friday_dir=None):
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = SourceTrustGraph(friday_dir=friday_dir)
    return _instance
