"""Household basics the owner states in plain words: who lives with them and how old.

"I am 52, my husband is 50, our son is 9" is the kind of fact every later
plan for "my family" needs, and the kind a model is least reliable at saving
(on a cloud seat the vault's per-call gate refuses a model-authored memory
write that names a daughter or a family). This module reads the owner's own
typed message with fixed patterns and returns exactly what it states: the
relation, an ordinal word ("oldest") if one was said, a name only when the
owner wrote one, and the age. Nothing is inferred; a statement it cannot read
with certainty yields nothing, and the model's own tool path is unchanged.

`extract` never reads tool results, pages, mail or recalled text: the caller
passes the message the owner typed. Whether the result saves silently is
decided by services/core_memory_tools.capture_household, under the memory
card's rule (only the owner's own words, and nothing read from outside in
this conversation).

Leaf module: no I/O, never raises.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: The household relations this reader recognises, in the owner's wording.
RELATIONS = (
    "wife", "husband", "partner", "spouse", "fiancee", "fiance",
    "son", "daughter", "child", "kid", "baby", "stepson", "stepdaughter", "stepchild",
    "mother", "father", "mom", "mum", "dad", "stepmother", "stepfather",
    "brother", "sister", "grandmother", "grandfather", "grandma", "grandpa",
    "grandson", "granddaughter", "mother-in-law", "father-in-law",
)
_ORDINALS = ("oldest", "eldest", "youngest", "older", "younger", "middle", "little", "baby")

#: Words in a search that ask about the household (services/agent search_wiki).
FAMILY_QUERY_WORDS = frozenset({
    "family", "household", "kids", "kid", "children", "child", "ages", "age",
    "wife", "husband", "partner", "spouse", "son", "daughter", "sons", "daughters",
})

#: The label every household line starts with, so a later statement finds it.
LABEL = "Household"

_REL = "|".join(sorted((re.escape(r) for r in RELATIONS), key=len, reverse=True))
_ORD = "|".join(_ORDINALS)
# A name only when the owner wrote one, capitalised, right after the relation.
_NAME = r"(?:,?\s+(?P<name>(?-i:[A-Z][a-z]+(?:-[A-Z][a-z]+)?)),?)?"
_YEARS = r"(?:\s*(?:years?|yrs?)(?:[\s-]*old)?)?"
# What may follow an age: the end of a clause, never a unit ("9 minutes").
_AFTER = r"(?=\s*(?:$|[,.;:!)]|\band\b|&|\bplus\b))"

#: "my husband is 50", "our oldest son, Sam, is 9", "my daughter's 6", "my wife turned 41"
_MEMBER_IS = re.compile(
    r"\b(?P<poss>my|our)\s+(?:(?P<ord>" + _ORD + r")\s+)?(?P<rel>" + _REL + r")\b" + _NAME
    + r"(?:\s+is|\s+just\s+turned|\s+turned|'s|’s)\s+(?P<age>\d{1,3})" + _YEARS + _AFTER,
    re.I)
#: "my 9-year-old son", "we have a 9 year old son", "I have an 8-year-old daughter"
_MEMBER_OLD = re.compile(
    r"\b(?:(?P<poss>my|our)|(?:i|we)\s+(?:have|also\s+have)\s+(?:a|an|one))\s+"
    r"(?P<age>\d{1,2})[\s-]*(?:years?|yrs?)[\s-]*old\s+(?:(?P<ord>" + _ORD + r")\s+)?"
    r"(?P<rel>" + _REL + r")\b" + _NAME,
    re.I)
#: "I am 52", "I'm 52 years old"
_SELF = re.compile(r"\bi(?:\s+am|'m|’m)\s+(?P<age>\d{1,3})" + _YEARS + _AFTER, re.I)

#: A clause that only supposes, asks or quotes says nothing about the household.
_HYPOTHETICAL = re.compile(r"\b(?:if|whether|suppose|supposing|imagine|pretend|what\s+if|unless)\b", re.I)
_QUOTED = re.compile(r"[\"“”][^\"“”]*[\"“”]")


def _sentences(text: str) -> List[str]:
    """The statements in `text`: quoted passages dropped, questions and
    hypotheticals skipped."""
    t = _QUOTED.sub(" ", str(text or ""))
    out = []
    for m in re.finditer(r"[^.!?;\n]+[.!?;\n]?", t):
        s = m.group(0)
        if s.rstrip().endswith("?") or _HYPOTHETICAL.search(s):
            continue
        out.append(s)
    return out


def _age(raw: str, low: int, high: int) -> Optional[int]:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def extract(text: Any) -> Optional[Dict[str, Any]]:
    """{"self_age": int|None, "members": [{"relation", "ordinal", "name", "age"}]} for
    the household the owner states in `text`, or None when it states none.

    At least one household member must be stated: "I am 52" on its own is not
    a household statement. The owner's own age rides along only as an adult's
    (18 to 120); a member's age is 0 to 120."""
    members: List[Dict[str, Any]] = []
    self_age: Optional[int] = None
    seen = set()
    for s in _sentences(text):
        hits = []
        for rx in (_MEMBER_IS, _MEMBER_OLD):
            for m in rx.finditer(s):
                hits.append((m.start(), m))
        for _, m in sorted(hits, key=lambda h: h[0]):
            age = _age(m.group("age"), 0, 120)
            if age is None:
                continue
            rel = m.group("rel").lower()
            ordinal = (m.group("ord") or "").lower()
            name = m.group("name") or ""
            key = (rel, ordinal, name.lower(), age)
            if key in seen:
                continue
            seen.add(key)
            members.append({"relation": rel, "ordinal": ordinal, "name": name, "age": age})
        if self_age is None:
            sm = _SELF.search(s)
            if sm:
                self_age = _age(sm.group("age"), 18, 120)
    if not members:
        return None
    return {"self_age": self_age, "members": members}


def _member_words(m: Dict[str, Any]) -> str:
    who = " ".join(w for w in (m.get("ordinal"), m.get("relation")) if w)
    if m.get("name"):
        who += f" {m['name']}"
    return f"{who} is {m['age']}"


def entry_text(facts: Dict[str, Any], when: str) -> str:
    """One memory-card line holding exactly the stated facts, dated, since ages move.

    `when` is the month the owner said it ("Oct 2026")."""
    parts = []
    if facts.get("self_age") is not None:
        parts.append(f"the user is {facts['self_age']}")
    parts += [_member_words(m) for m in facts.get("members") or []]
    return f"{LABEL} (as the user stated, {when}): " + "; ".join(parts) + "."


def is_household_line(line: Any) -> bool:
    return str(line or "").startswith(LABEL + " (")


def facts_of(line: Any) -> set:
    """The stated facts of a household line ("husband is 50", ...), without its date."""
    s = str(line or "")
    s = s.split("): ", 1)[1] if "): " in s else s
    return {p.strip().rstrip(".").lower() for p in s.split(";") if p.strip()}
