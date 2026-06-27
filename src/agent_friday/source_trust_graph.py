"""
Source Trust Graph — reputation scoring for media outlets and agents.

This is the real innovation split out of the old monolithic trust graph: a
graph that scores *information sources* (news domains, and eventually peer
agents) on how trustworthy their claims are, learned continuously from how
their reporting holds up against the rest of the field.

Six dimensions, each in [0, 1]
------------------------------
factual_accuracy       How often their claims hold up when cross-referenced
                       against other sources. Updated every news fetch.
correction_behavior    When they get something wrong, do they issue
                       corrections/retractions?
source_attribution     Do they cite primary sources, or launder opinion as
                       reporting?
prediction_accuracy    Forward-looking claims scored retroactively
                       (infrastructure ready; scoring is a placeholder).
opinion_separation     Do they clearly label opinion vs. reporting?
narrative_independence Do they break from their expected editorial line when
                       the facts demand it?

Composite ``trust_score`` is the weighted mean of the six dimensions.

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
from datetime import datetime, date
from pathlib import Path

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
    "prediction_accuracy": 0.05,   # placeholder dimension — low weight
    "opinion_separation": 0.15,
    "narrative_independence": 0.15,
}

# Decay: an observation N weeks old carries weight 0.95**N.
_WEEKLY_DECAY = 0.95
# Seed prior strength, in pseudo-observations, that anchors a dimension toward
# its seed value until enough real observations accumulate.
_PRIOR_STRENGTH = 3.0
# Cap stored observations per source so the file can't grow without bound.
_MAX_OBSERVATIONS = 400

# Seed reputations so a brand-new graph still produces sensible badges. These
# mirror the static trust map in server.py; the live graph diverges from them
# as real observations accumulate.
_SEED_HIGH = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "npr.org",
    "arstechnica.com", "theverge.com", "wired.com", "nature.com",
    "wsj.com", "nytimes.com", "bloomberg.com", "ft.com", "economist.com",
    "techcrunch.com", "axios.com", "propublica.org", "statnews.com",
    "technologyreview.com", "404media.co", "restofworld.org", "engadget.com",
    "theguardian.com", "politico.com", "theintercept.com", "talkingpointsmemo.com",
    "motherjones.com", "theatlantic.com", "fortune.com", "cnbc.com",
    "marketwatch.com", "businessinsider.com", "texastribune.org",
    "texasmonthly.com", "austinmonitor.com", "kut.org", "scientificamerican.com",
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


def _seed_for(domain):
    """Seed dimension scores for a source we've never observed.

    Seeds are chosen so the composite lands green (≥0.7) for the static
    high-trust set, red (<0.4) for the known low-trust set, and neutral yellow
    for everything unknown — matching the old static badge behaviour until real
    observations move the needle.
    """
    if domain in _SEED_HIGH:
        base = 0.75
    elif domain in _SEED_LOW:
        base = 0.30
    else:
        base = 0.50
    seed = {d: base for d in DIMENSIONS}
    # prediction_accuracy is a placeholder dimension (no retroactive scoring
    # yet), so it stays neutral regardless of general reputation.
    seed["prediction_accuracy"] = 0.5
    return seed


class SourceTrustGraph:
    def __init__(self, friday_dir=None):
        self.friday_dir = Path(friday_dir or Path.home() / ".friday")
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
            return data
        except Exception:
            return {"sources": {}, "meta": {"version": "1.0"}}

    def _save(self, data):
        data.setdefault("meta", {})["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.friday_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

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
                "scores": _seed_for(domain),
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
        total = sum(_WEIGHTS[d] * float(scores.get(d, 0.5)) for d in DIMENSIONS)
        return round(total, 4)

    def _recompute(self, rec):
        """Recompute each dimension as a seed-anchored, time-decayed weighted
        mean of its observations, then the composite trust score."""
        seed = _seed_for(rec.get("domain", ""))
        obs = rec.get("observations") or []
        today = date.today()
        scores = {}
        for dim in DIMENSIONS:
            num = _PRIOR_STRENGTH * seed[dim]
            den = _PRIOR_STRENGTH
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
            scores[dim] = round(num / den, 4) if den else seed[dim]
        rec["scores"] = scores
        rec["trust_score"] = self._composite(scores)
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
            self._recompute(rec)
            self._save(data)
            return rec

    def record_article_seen(self, domain, name=None):
        """Bump a source's article counter (and ensure it exists)."""
        with self._lock:
            data = self._load()
            rec = self._get_or_create(data, domain, name)
            if rec is None:
                return
            rec["article_count"] = int(rec.get("article_count", 0)) + 1
            self._save(data)

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
            if action == "ban":
                ua["banned"] = True
            elif action == "unban":
                ua["banned"] = False
            elif action == "boost":
                ua["boosted"] = True
            elif action == "unboost":
                ua["boosted"] = False
            elif action == "click":
                ua["clicks"] = int(ua.get("clicks", 0)) + 1
            elif action in ("read_later", "read"):
                ua["read_laters"] = int(ua.get("read_laters", 0)) + 1
            self._save(data)

    # ── reads ──────────────────────────────────────────────────────

    def all_sources(self):
        data = self._load()
        return list(data["sources"].values())

    def get(self, domain):
        data = self._load()
        return data["sources"].get(_extract_domain(domain))

    def score_for(self, domain):
        """Composite trust score for a domain (seed value if never observed)."""
        rec = self.get(domain)
        if rec:
            return float(rec.get("trust_score", 0.5))
        return self._composite(_seed_for(_extract_domain(domain)))

    def dimensions_for(self, domain):
        """The six dimension scores for a domain (seed values if unobserved)."""
        rec = self.get(domain)
        if rec:
            return dict(rec.get("scores", {}))
        return _seed_for(_extract_domain(domain))

    def leaderboard(self, limit=200):
        rows = sorted(self.all_sources(),
                      key=lambda r: r.get("trust_score", 0), reverse=True)
        out = []
        for r in rows[:limit]:
            out.append({
                "domain": r.get("domain"),
                "name": r.get("name") or r.get("domain"),
                "trust_score": r.get("trust_score", 0.0),
                "scores": r.get("scores", {}),
                "article_count": r.get("article_count", 0),
                "observation_count": len(r.get("observations") or []),
                "first_seen": r.get("first_seen"),
                "user_actions": r.get("user_actions", {}),
            })
        return out

    # ── cross-source comparison engine ─────────────────────────────

    def analyze_fetch(self, pool, clusters):
        """Run cross-source comparison over one fetch cycle and log
        observations. ``pool`` is the flat scored article list;
        ``clusters`` is the output of the title-Jaccard clusterer.

        Returns a small summary dict for logging.

        Heuristics (snippet/title-level, no model call):
          * Per-article attribution + opinion-separation signals.
          * Correction/retraction detection.
          * Within a cluster of 7+ sources, a source holding the *minority*
            sentiment is logged as a minority_claim (factual_accuracy down) —
            unless its snippet cites a primary source, in which case it's
            credited as narrative_independence instead.
          * Within a small cluster (2-3 sources) whose coverage cites primary
            documents, the covering sources get a factual_accuracy boost.
        """
        summary = {"attribution": 0, "opinion": 0, "corrections": 0,
                   "minority_claims": 0, "primary_boosts": 0,
                   "independence": 0, "articles": 0}
        with self._lock:
            data = self._load()

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

                # Correction behaviour: a correction/retraction is positive
                # evidence that the source owns its mistakes.
                if _CORRECTION_RX.search(text):
                    self._append(rec, "correction_issued", "correction_behavior",
                                 0.95, detail=title[:160])
                    summary["corrections"] += 1

                # Source attribution: reward visible citation; lightly penalise
                # a substantive snippet that cites nothing.
                if _ATTRIBUTION_RX.search(text) or "http" in snippet:
                    self._append(rec, "attribution_present", "source_attribution",
                                 0.9, detail=title[:160])
                    summary["attribution"] += 1
                elif len(snippet) > 140:
                    self._append(rec, "attribution_absent", "source_attribution",
                                 0.3, detail=title[:160])

                # Opinion separation: clearly-labelled opinion (section path) is
                # good practice. Opinion language with no label is a soft miss.
                cat = (art.get("category") or "").lower()
                if _OPINION_PATH_RX.search(url) or cat in ("opinion", "analysis"):
                    self._append(rec, "opinion_labeled", "opinion_separation",
                                 0.9, detail=title[:160])
                    summary["opinion"] += 1
                elif _OPINION_WORD_RX.search(title) and not _OPINION_PATH_RX.search(url):
                    self._append(rec, "opinion_unlabeled", "opinion_separation",
                                 0.4, detail=title[:160])

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
                        self._append(
                            rec, "primary_corroborated", "factual_accuracy", 0.9,
                            detail=f"Primary-sourced story: {cl.get('headline','')[:120]}",
                            counter_sources=[])
                        summary["primary_boosts"] += 1

                # Large consensus story with a 1-2 source minority: the minority
                # is either contradicting the field (factual ding) or breaking
                # from the pack with documents (independence credit).
                if src_count >= 7:
                    sentiments = [a.get("sentiment") for a in arts if a.get("sentiment")]
                    if sentiments:
                        majority = max(set(sentiments), key=sentiments.count)
                        maj_count = sentiments.count(majority)
                        minority = [a for a in arts
                                    if a.get("sentiment") and a.get("sentiment") != majority]
                        # Only treat as a genuine minority when the consensus is
                        # strong (majority is most of the field) and the dissent
                        # is small (1-2 outlets).
                        if maj_count >= src_count - 2 and 1 <= len(minority) <= 2:
                            consensus_srcs = sorted({a.get("source") for a in arts
                                                     if a.get("sentiment") == majority})
                            for a in minority:
                                domain = _extract_domain(a.get("source") or a.get("url", ""))
                                if not domain:
                                    continue
                                rec = self._get_or_create(data, domain)
                                txt = f"{a.get('title','')} {a.get('snippet','')}"
                                if _PRIMARY_SOURCE_RX.search(txt):
                                    self._append(
                                        rec, "narrative_break", "narrative_independence",
                                        0.85,
                                        detail=f"Broke from {len(consensus_srcs)}-source consensus with primary sourcing: {a.get('title','')[:100]}",
                                        counter_sources=consensus_srcs[:8])
                                    summary["independence"] += 1
                                else:
                                    self._append(
                                        rec, "minority_claim", "factual_accuracy",
                                        0.25,
                                        detail=f"Minority framing vs {len(consensus_srcs)}-source consensus: {a.get('title','')[:100]}",
                                        counter_sources=consensus_srcs[:8])
                                    summary["minority_claims"] += 1

            # Recompute every touched source and persist once.
            for domain in list(data["sources"].keys()):
                self._recompute(data["sources"][domain])
            self._save(data)
        return summary

    def _append(self, rec, obs_type, dimension, signal, detail="",
                counter_sources=None, signed_by="local"):
        """Internal: append an observation to an in-memory record (caller saves)."""
        rec.setdefault("observations", []).append({
            "date": _today_str(),
            "type": obs_type,
            "dimension": dimension,
            "signal": max(0.0, min(1.0, float(signal))),
            "detail": (detail or "")[:300],
            "counter_sources": list(counter_sources or []),
            "signed_by": signed_by,
        })
        if len(rec["observations"]) > _MAX_OBSERVATIONS:
            rec["observations"] = rec["observations"][-_MAX_OBSERVATIONS:]


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
