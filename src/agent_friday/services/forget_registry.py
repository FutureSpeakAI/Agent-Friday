"""Forget means forget: every store that names a person registers here.

`forget_person.forget()` used to purge a hard-coded list of files and
missed the ones added since: the contacts research notes, the learned
sender signals, and now the trust evidence log. A store registers a finder
and a purger once; forgetting a person then asks every registered store,
and a store that is missing from this registry is a bug a guard can catch
rather than a silent survivor.

A store entry is ``(name, find_fn, purge_fn)`` where both functions take
``(names, emails)`` (normalised lowercase names and aliases, and lowercase
emails) and return a count. A store that fails reports its error in the
receipt instead of stopping the others.

THE DELETION RECEIPT (north star NS-15.7-3)
-------------------------------------------
``purge_all_detailed`` gives one row per store: what was removed (a count and
what kind of thing, never the content), what could not be removed and why,
and what is kept on purpose and why (retained for integrity: the owner's own
transcript, an append-only record). A purge function may return a plain count
or a dict ``{"removed", "kind", "not_removable", "retained", "also"}`` where
``also`` is a list of further rows (one purge reaching several stores in a
fixed order, e.g. the recall indexes before the summaries made from them).

NAME MATCHING IN FREE TEXT (``subject_matcher``)
-----------------------------------------------
Stores that hold sentences rather than records (conversation turns, day
summaries, memory-card lines, user-model facts, dream logs, proposals) match
the person's name as a whole word, on NFC-normalised text:

* A name of two or more words ("Dana Okafor") matches in any case
  ("dana okafor"), with any run of spaces between the words.
* A one-word name or alias matches case-sensitively, in the forms the owner
  could mean: as typed (the tombstone keeps it), in title case, and with the
  letter after an apostrophe capitalised ("o'brien" -> "O'Brien"); a straight
  or curly apostrophe both match. Forgetting "Dana" removes "Dana said..." and
  never "dana" inside a file name; a lowercase one-word name would remove
  every ordinary word it collides with, and a turn removed is a turn the owner
  cannot get back into recall.
* An email matches case-insensitively and only whole: dana@x.com does not
  match inside dana@x.com.au.

``matcher.forms`` lists the forms matched (the receipt shows them: the owner
typed them). What this cannot tell apart: the name used as a word at the start
of a sentence, and a different person with the same name.

REFUSED NAMES (``name_guard``)
------------------------------
Some names would take far more than the person with them, so they are refused
(the receipt lists each as not removable, with the reason) while the full name
and the other aliases are still forgotten: the owner's own name (and each word
of it), Friday's name, and a one-word alias that is also a common word or
month or day ("Will", "May", "Grace", "Friday"). Names shorter than three
characters are not matched in free text either.

That refusal is for FREE TEXT. Record-level stores matched by an exact name
(the people graph, knowledge-graph entity titles, the tombstone) keep every
alias, a common word included (a node titled exactly "Will" is that person's
node), and refuse there only the owner's and Friday's names
(``exact_match_refused``).
"""
from __future__ import annotations

import re
import threading
import unicodedata
from typing import Any, Callable, Dict, List, Tuple

from agent_friday.user_errors import clip, exception_text

_LOCK = threading.Lock()
_STORES: Dict[str, Tuple[Callable, Callable]] = {}
_DEFAULTS_LOADED = False

#: Shortest name matched inside free text (see the module docstring).
MIN_NAME = 3

#: One-word aliases refused on their own (``name_guard``): ordinary words,
#: months and days that are also given names. Lowercase.
COMMON_WORDS = frozenset({
    "may", "mark", "will", "june", "april", "august", "bill", "grace", "hope", "faith", "joy",
    "rose", "summer", "autumn", "winter", "dawn", "eve", "ivy", "lily", "holly", "iris", "jade",
    "ruby", "pearl", "amber", "crystal", "honey", "sky", "rain", "storm", "river", "brook",
    "glen", "dale", "heath", "cliff", "rod", "pat", "sue", "don", "ray", "art", "bob", "rob",
    "jack", "frank", "sterling", "chase", "hunter", "carter", "mason", "miles", "max", "bud",
    "bunny", "chip", "buck", "earl", "duke", "king", "prince", "baron", "major", "sunny",
    "angel", "harmony", "liberty", "victory", "patience", "prudence", "charity", "mercy",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "july", "september", "october", "november", "december",
    "christian", "page", "price", "young", "long", "white", "black", "brown", "green", "gray",
    "grey", "rich", "noble", "golden", "cash", "penny", "sandy", "misty", "dusty", "rusty",
    "hazel", "olive", "violet", "daisy", "poppy", "heather", "willow", "sage", "drew", "grant",
    "lance", "nick", "gene", "reed", "wade", "lane", "ginger", "cherry",
})

_REASON_OWNER = "your own name: matching it would remove what you said about yourself"
_REASON_FRIDAY = "Friday's own name: matching it would remove her name from everything"
_REASON_COMMON = ("an ordinary word on its own (a word, month or day as well as a name): matching it "
                  "would remove unrelated text. Any fuller name and the other names were still matched")
_REASON_SHORT = "shorter than %d characters: too likely to match ordinary words" % MIN_NAME

#: What each store's count is a count of, for the receipt.
KINDS = {
    "contacts_research": "research notes",
    "sender_signals": "learned sender signals",
    "trust_log_people": "trust evidence events",
    "library": "Library passages and staged drafts",
    "laya_personal": "items Laya learned from",
    "meetings": "meeting records",
    "memory_proposals": "memory suggestions",
}


def register_store(name: str, find_fn: Callable, purge_fn: Callable) -> None:
    with _LOCK:
        _STORES[str(name)] = (find_fn, purge_fn)


def registered() -> List[str]:
    _ensure_defaults()
    with _LOCK:
        return sorted(_STORES)


def _count(value) -> int:
    if isinstance(value, dict):
        return int(value.get("removed") or 0)
    return int(value or 0)


def find_all(names, emails) -> Dict[str, int]:
    _ensure_defaults()
    out = {}
    for name, (find_fn, _purge) in list(_STORES.items()):
        try:
            out[name] = _count(find_fn(set(names), set(emails)))
        except Exception as e:
            out[name] = -1
            out[name + "_error"] = clip(exception_text(e), 120)
    return out


def _row(store: str, value=None, error: str = "") -> Dict[str, Any]:
    """One receipt row. Never carries the removed content."""
    row = {"store": store, "removed": 0, "kind": KINDS.get(store, "records"),
           "not_removable": [], "retained": []}
    if error:
        row["not_removable"].append({"what": "this store", "reason": error})
        return row
    if isinstance(value, dict):
        row["removed"] = int(value.get("removed") or 0)
        row["kind"] = str(value.get("kind") or row["kind"])
        row["not_removable"] = [dict(x) for x in (value.get("not_removable") or [])]
        row["retained"] = [dict(x) for x in (value.get("retained") or [])]
    else:
        row["removed"] = int(value or 0)
    if store == "trust_log_people" and row["removed"] > 0:
        row["retained"].append({
            "what": "one marker in the trust log that carries only a count", "count": 1,
            "reason": "the log is hash-chained; the marker keeps the chain verifiable"})
    return row


def purge_all_detailed(names, emails) -> List[Dict[str, Any]]:
    """Purge every store; one receipt row per store (and per `also` row)."""
    _ensure_defaults()
    rows: List[Dict[str, Any]] = []
    for name, (_find, purge_fn) in list(_STORES.items()):
        try:
            value = purge_fn(set(names), set(emails))
        except Exception as e:
            # Marked as exception text: the model reads it, and the HTTP
            # boundary (routes._errors.public_result) swaps it for an error id.
            err = clip(exception_text(e), 120)
            rows.append(dict(_row(name, error=err), error=err))
            continue
        rows.append(_row(name, value))
        if isinstance(value, dict):
            for extra in value.get("also") or []:
                if isinstance(extra, dict) and extra.get("store"):
                    rows.append(_row(str(extra["store"]), extra))
    return rows


def counts(rows) -> Dict[str, Any]:
    """The old `{store: count, store_error: text}` shape from receipt rows."""
    out: Dict[str, Any] = {}
    for r in rows:
        if r.get("error"):
            out[r["store"]] = -1
            out[r["store"] + "_error"] = r["error"]
        else:
            out[r["store"]] = int(r.get("removed") or 0)
    return out


def purge_all(names, emails) -> Dict[str, int]:
    return counts(purge_all_detailed(names, emails))


# ── matching a name inside free text ─────────────────────────────────────────

def _given_forms(names) -> set:
    """The names as the owner gave them (tombstone), for the lowercased `names`."""
    wanted = {_norm(n) for n in names}
    out = set()
    try:
        from agent_friday.services import forget_person as fp
        for entry in fp.list_forgotten():
            if not isinstance(entry, dict):
                continue
            for n in [entry.get("name", "")] + list(entry.get("aliases") or []):
                n = " ".join(str(n or "").split())
                if n and _norm(n) in wanted:
                    out.add(n)
    except Exception:
        pass
    return out


def _nfc(s) -> str:
    return unicodedata.normalize("NFC", str(s or ""))


def _lower(name) -> str:
    """A name as compared: NFC, whitespace collapsed, lowercase."""
    return " ".join(_nfc(name).split()).lower()


def _title(name: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in str(name).split())


_INNER_CAP = re.compile(r"(['’-])(\w)")


def _inner_capital(name: str) -> str:
    """O'brien -> O'Brien, Mary-jane -> Mary-Jane."""
    return _INNER_CAP.sub(lambda m: m.group(1) + m.group(2).upper(), name)


def _pattern_text(form: str) -> str:
    """Whole words, any run of spaces between them, either apostrophe."""
    words = []
    for w in form.split(" "):
        if w:
            words.append("".join("['’]" if ch in "'’" else re.escape(ch) for ch in w))
    return r"\s+".join(words)


def _owner_names() -> set:
    """The owner's own name and each word of it (setup: services/setup_profile),
    lowercased. Empty when none was given or it cannot be read."""
    try:
        from agent_friday.services import setup_profile
        n = _lower((setup_profile.load_profile() or {}).get("name") or "")
    except Exception:
        n = ""
    if not n:
        return set()
    return {n} | {w for w in n.split(" ") if len(w) >= MIN_NAME}


def _friday_names() -> set:
    """Friday's name as the owner set it (agent_name), and her default."""
    out = {"friday", "agent friday"}
    raw = []
    try:
        from agent_friday.services import agent_identity
        raw.append(agent_identity.assistant_name())
        raw.append(agent_identity._settings().get("agent_name") or "")
    except Exception:
        pass
    for r in raw:
        n = _lower(r)
        if n:
            out.add(n)
            out |= {w for w in n.split(" ") if len(w) >= MIN_NAME and w != "agent"}
    return out


def name_guard(names) -> Dict[str, str]:
    """{lowercased name: why it is refused} for the names that would take far
    more than the person (see REFUSED NAMES in the module docstring)."""
    lowered = {_lower(n) for n in names if _lower(n)}
    if not lowered:
        return {}
    owner, her = _owner_names(), _friday_names()
    out = {}
    for n in lowered:
        if n in owner:
            out[n] = _REASON_OWNER
        elif n in her:
            out[n] = _REASON_FRIDAY
        elif " " not in n and n in COMMON_WORDS:
            out[n] = _REASON_COMMON
    return out


def exact_match_refused(guard: Dict[str, str]) -> Dict[str, str]:
    """The part of a ``name_guard`` result that also applies to record-level
    exact-match stores (people graph, graph entity titles, the tombstone):
    only the owner's and Friday's names. A common word is refused in free
    text only."""
    return {n: r for n, r in (guard or {}).items() if r in (_REASON_OWNER, _REASON_FRIDAY)}


def owner_full_name() -> str:
    """The owner's whole name as set up, lowercased; "" when none is known."""
    names = _owner_names()
    return max(names, key=len) if names else ""


class _Matcher:
    """`.forms` the name forms matched (for the receipt), `.names` the
    lowercased names matched, `.skipped` names too short to match, `.refused`
    [{"name", "reason"}] names refused by ``name_guard``."""

    def __init__(self, patterns, skipped, refused=(), forms=(), names=()):
        self.patterns = patterns
        self.skipped = list(skipped)
        self.refused = list(refused)
        self.forms = list(forms)
        self.names = set(names)

    def __call__(self, text) -> bool:
        if not text or not isinstance(text, str):
            return False
        text = _nfc(text)
        return any(p.search(text) for p in self.patterns)

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def sub(self, text, replacement: str = "[removed]"):
        """`text` with every match replaced (prose scrubs: community reports).
        Same rules as matching; non-strings are returned unchanged."""
        if not text or not isinstance(text, str):
            return text
        out = _nfc(text)
        for p in self.patterns:
            out = p.sub(replacement, out)
        return out if out != _nfc(text) else text

    def notes(self) -> List[Dict[str, str]]:
        """The not-removable receipt entries for the refused and short names."""
        out = [{"what": "the name %r on its own" % r["name"], "reason": r["reason"]} for r in self.refused]
        out += [{"what": "the name %r in free text" % n, "reason": _REASON_SHORT} for n in self.skipped]
        return out


def subject_matcher(names, emails=()) -> "_Matcher":
    """A predicate: does this free text name the person? See the module
    docstring for the rule."""
    lowered = {_lower(n) for n in names if _lower(n)}
    guard = name_guard(lowered)
    given = _given_forms(lowered)
    pats, skipped, refused, forms, kept = [], [], [], set(), set()
    for n in sorted(lowered, key=len, reverse=True):
        if n in guard:
            refused.append({"name": n, "reason": guard[n]})
            continue
        if len(n) < MIN_NAME:
            skipped.append(n)
            continue
        kept.add(n)
        typed = sorted(_nfc(g) for g in given if _lower(g) == n)
        if " " in n:
            pats.append(re.compile(r"(?<!\w)" + _pattern_text(n) + r"(?!\w)", re.IGNORECASE))
            forms.add(typed[0] if typed else _title(n))
            continue
        variants = set(typed) | {_title(n), _inner_capital(_title(n))} | {_inner_capital(t) for t in typed}
        for v in sorted(variants):
            pats.append(re.compile(r"(?<!\w)" + _pattern_text(v) + r"(?!\w)"))
            forms.add(v)
    for e in emails or ():
        e = _nfc(e).strip()
        if "@" in e:
            # Whole address only: dana@x.com does not match inside dana@x.com.au.
            pats.append(re.compile(r"(?<![\w.+-])" + re.escape(e) + r"(?![\w.-]*\w)", re.IGNORECASE))
    return _Matcher(pats, sorted(skipped), sorted(refused, key=lambda r: r["name"]), sorted(forms), kept)


# ── the stores that exist today ──────────────────────────────────────────────

def _ensure_defaults() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    _DEFAULTS_LOADED = True
    register_store("contacts_research", _research_find, _research_purge)
    register_store("sender_signals", _signals_find, _signals_purge)
    register_store("trust_log_people", _trust_log_find, _trust_log_purge)
    from agent_friday.services.library import forget as _library_forget
    _library_forget.register()
    # What Laya kept from the owner's corrections names people too (services/laya_personal): the rows that
    # name the person go, with every saved version built from them.
    from agent_friday.services import laya_personal as _laya_personal
    register_store("laya_personal", _laya_personal.people_find, _laya_personal.people_purge)
    # Proposals (and a meeting's, which carry a decision or a name) sit in a plain table of their own.
    from agent_friday.services import memory_proposals as _memory_proposals
    # A meeting's own encrypted record (title, transcript, notes, bullets) names people too.
    from agent_friday.services import meeting_capture as _meeting_capture
    register_store("meetings", _meeting_capture.people_find, _meeting_capture.people_purge)
    register_store("memory_proposals", _memory_proposals.people_find, _memory_proposals.people_purge)
    # Imported items waiting for the owner's answer (services/import_staging) name people too.
    from agent_friday.services import import_staging as _import_staging
    register_store("import_staging", _import_staging.forget_find, _import_staging.forget_purge)
    # The last "What I see so far" shown (services/first_synthesis) can name a person.
    from agent_friday.services import first_synthesis as _first_synthesis
    register_store("first_synthesis", _first_synthesis.forget_find, _first_synthesis.forget_purge)
    # What was derived from the owner's conversations (NS-15.7-2): the recall indexes (vector memory and
    # conversation search), the day summaries and the graph's conversation-sourced nodes made from them,
    # the memory card and its review queue; the transcript and action receipts are listed as kept.
    from agent_friday.services import forget_derived as _forget_derived
    _forget_derived.register()
    # "Don't suggest things like this" rules keep a suggestion's words (or keyed hashes of a meeting's): a rule
    # carrying a token of the person's name goes.
    from agent_friday.services import memory_suppress as _memory_suppress
    register_store("memory_suppressions", _memory_suppress.people_find, _memory_suppress.people_purge)


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").replace("-", " ").split())


def _research_dir():
    from agent_friday.paths import friday_home
    return friday_home() / "contacts-research"


def _research_matches(names):
    d = _research_dir()
    if not d.exists():
        return []
    wanted = {_norm(n) for n in names}
    return [p for p in d.glob("*.md") if _norm(p.stem) in wanted]


def _research_find(names, emails) -> int:
    return len(_research_matches(names))


def _research_purge(names, emails) -> int:
    n = 0
    for p in _research_matches(names):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def _signals_find(names, emails) -> int:
    try:
        from agent_friday.services import message_triage as mt
        data = mt._load_signals()
    except Exception:
        return 0
    senders = (data or {}).get("senders") or {}
    return sum(1 for e in emails if str(e).lower() in senders)


def _signals_purge(names, emails) -> int:
    n = 0
    try:
        from agent_friday.services import message_triage as mt
    except Exception:
        return 0
    for e in emails:
        try:
            if mt.forget_sender(str(e)).get("removed"):
                n += 1
        except Exception:
            pass
    return n


def _trust_log_match(names, emails):
    wanted = {_norm(n) for n in names}

    def match(event):
        if event.get("entity_kind") != "person":
            return False
        ids = {_norm(event.get("entity_id"))}
        ids |= {_norm(a) for a in (event.get("provenance") or {}).get("names", [])}
        return bool(ids & wanted)
    return match


def _trust_log_find(names, emails) -> int:
    from agent_friday.trust import log as tlog
    m = _trust_log_match(names, emails)
    return sum(1 for r in tlog.read(tlog.people_path()) if m(r))


def _trust_log_purge(names, emails) -> int:
    from agent_friday.trust import log as tlog
    return tlog.forget_entity(tlog.people_path(), match=_trust_log_match(names, emails))
