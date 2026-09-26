# -*- coding: utf-8 -*-
"""A business's published contact details are not the owner's private data.

SENSITIVE SUBSYSTEM (AGENTS.md). This narrows one denial in the cloud-egress
gate. Read the whole rule before changing a line of it.

WHAT WENT WRONG
---------------
2026-09-25. Friday built a weekend itinerary and was asked to put it on the
owner's calendar. The first event was refused as ``cloud_denied_tier_TIER_2``:
the venue's street address, read minutes earlier from the restaurant's own public
website, was tagged ``[PII:addr]``, and an above-PUBLIC tier may not go to a
cloud seat. The theatre and concert events in the same batch went through, which
is what marks this a false positive and not a policy.

The gate is right in general and wrong here for a specific reason, and the reason
is already written down a few lines away in vault_access.LOCAL_SINK_TOOLS: *tool
arguments are authored BY the provider -- it has already seen them.* That is why
`write_file` is exempt there. `create_calendar_event` cannot join that set,
because it really does transmit off-device and can invite people. So instead of
exempting the tool, this exempts ONE VALUE on proven public provenance, and only
when it is the only thing causing the block.

THE RULE
--------
A value is treated as non-personal FOR THE CLOUD-EGRESS DECISION ONLY when all
six hold:

1. **Sole cause.** Strip the value and the payload classifies TIER_1. A payload
   still above PUBLIC without it is denied exactly as before.
2. **Public web provenance, positively attributed.** `taint.origin_of` says
   ``content`` from a web page or web search results. Unknown, model-invented
   and user-typed values are NOT exempt -- an unattributable value fails closed.
3. **The provider already holds it.** These are the arguments that provider just
   authored, so refusing them withholds nothing that has not already left.
4. **Logged, never silent.** The gate writes ``public_web_provenance_<TIER>``
   where it would have written ``cloud_denied_tier_<TIER>``, in the same access
   log, so every use of this exemption sits beside every denial.
5. **Business-contact types only.** Street addresses and place names, business
   phone numbers, URLs. NEVER a government id, a card or account number, a
   credential or key, a health term, or a personal email -- published or not,
   because putting one of those into a calendar invite spreads it further.
6. **Never the owner's own records.** A value matching the owner's profile,
   contacts or known addresses is not exempt whatever the web says. This covers
   both the owner's address appearing on some public page and a mis-attribution
   by the ledger, and if the records cannot be read the exemption is refused.

What this does NOT do: it does not touch a detector. The address is still
detected, still tiered, still redacted everywhere else. Local seats were never
gated here and are unaffected.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Tuple

_log = logging.getLogger(__name__)

#: A value shorter than this carries too little to attribute or match.
MIN_LEN = 8

#: Where the owner's own records live, for condition 6. The encrypted vault is
#: not enumerated here and does not need to be: a vault-sourced value fails
#: condition 2, because its provenance is the vault and not a web page. What
#: this corpus adds is the other case -- the owner's OWN details appearing on a
#: public page, where provenance says "web" and the answer is still no.
_OWNER_WIKI_DIR = "wiki/identity"
_CONTACTS_FILE = "contacts_meta.json"


def _norm(text: Any) -> str:
    """Compare on letters and digits only, lowercased.

    Addresses are written a dozen ways -- "Blvd" and "Boulevard", commas, double
    spaces, a ZIP or not. Comparing raw strings would let a trivial reformatting
    walk past condition 6, which is the check that protects the owner's home.
    """
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _friday_dir() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR)


def _owner_corpus() -> set:
    """Normalised fragments of the owner's own records.

    Raises on failure. The caller turns any exception into a refusal: condition
    6 is a safeguard, so losing the ability to check removes the exemption
    rather than waving it through.
    """
    out = set()
    root = _friday_dir()
    ident = root / _OWNER_WIKI_DIR
    if ident.is_dir():
        for p in sorted(ident.glob("*.md")):
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                n = _norm(line)
                if len(n) >= MIN_LEN:
                    out.add(n)
    contacts = root / _CONTACTS_FILE
    if contacts.is_file():
        data = json.loads(contacts.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for name, rec in data.items():
                out.add(_norm(name))
                if isinstance(rec, dict):
                    for field in ("email", "phone", "address", "notes"):
                        n = _norm(rec.get(field))
                        if len(n) >= MIN_LEN:
                            out.add(n)
    return {n for n in out if len(n) >= MIN_LEN}


def _matches_owner(value: str, corpus: Iterable[str]) -> bool:
    n = _norm(value)
    if len(n) < MIN_LEN:
        return True          # too short to clear: treat as a match, fail closed
    for entry in corpus:
        if n in entry or entry in n:
            return True
    return False


# ── condition 5: which types may ever be exempt ────────────────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s]+", re.I)
_PHONE_RE = re.compile(r"(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}")
_PLACE_RE = re.compile(
    r"\b(theatre|theater|restaurant|cafe|bar|grill|kitchen|hall|centre|center|"
    r"museum|park|stadium|arena|hotel|club|brewery|winery|gallery|library)\b",
    re.I)


def _never_exempt(value: str) -> str:
    """Why this value can never be exempt, or "" if none of those apply."""
    from agent_friday.services import sensitivity_classifier as sc
    checks = (
        ("a government id", getattr(sc, "_SSN_RE", None)),
        ("a card number", getattr(sc, "_CC_RE", None)),
        ("a credential or key", getattr(sc, "_API_KEY_RE", None)),
        ("an account number", getattr(sc, "_ACCT_TAIL_RE", None)),
        ("an issued id", getattr(sc, "_ISSUED_ID_RE", None)),
        ("a routing number", getattr(sc, "_ROUTING_RE", None)),
    )
    for label, rx in checks:
        try:
            if rx is not None and rx.search(value):
                return label
        except Exception:
            return label       # a check that cannot run is a refusal
    low = value.lower()
    for kw in getattr(sc, "_TIER3_STRONG", ()):
        if kw in low:
            return "a health, financial or identity term (%s)" % kw
    if _EMAIL_RE.search(value):
        # A business contact form is a URL; a bare address is a person's mailbox
        # far more often than not, and this rule does not need to guess.
        return "an email address"
    return ""


def _is_business_contact(value: str) -> bool:
    """Is this the shape of a business's published contact detail?"""
    from agent_friday.services import sensitivity_classifier as sc
    if _URL_RE.search(value) or _PHONE_RE.search(value):
        return True
    if _PLACE_RE.search(value):
        return True
    addr = getattr(sc, "_ADDRESS_RE", None)
    try:
        return bool(addr is not None and addr.search(value))
    except Exception:
        return False


# ── condition 2: provenance ────────────────────────────────────────────────

_PUBLIC_SOURCES = ("a web page", "web search results")


def _from_public_web(taint_key: str, value: str) -> bool:
    from agent_friday.services import taint as _taint
    o = _taint.origin_of(taint_key, value)
    if o.kind != "content":
        return False
    return str(o.source or "").startswith(_PUBLIC_SOURCES)


# ── the rule ───────────────────────────────────────────────────────────────

def _leaves(args: Any) -> list:
    out = []
    if isinstance(args, dict):
        for v in args.values():
            out.extend(_leaves(v))
    elif isinstance(args, (list, tuple)):
        for v in args:
            out.extend(_leaves(v))
    elif isinstance(args, str):
        out.append(args)
    return out


def _tier(text: str) -> int:
    from agent_friday.services.sensitivity_classifier import classify
    return int(classify(text, egress=True))


def exempt(args: Any, *, action: str, taint_key: str) -> Tuple[bool, str]:
    """May this payload go to a cloud seat despite its tier? (allowed, why_not).

    Returns (True, detail) only when every condition in this module's docstring
    holds. Any uncertainty returns False: this widens what may leave the machine,
    so it is the one place in the file where "not proven" must mean "no".
    """
    if not taint_key:
        return False, "no provenance ledger for this call"
    # The gate hands this the tool arguments already serialised. Parse them back,
    # because the rule works VALUE BY VALUE: judging one big JSON blob as a
    # single value would ask whether the whole payload was read off a web page,
    # which is never true, and the exemption would never apply.
    parsed = args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except Exception:
            parsed = args
    try:
        blob = args if isinstance(args, str) else json.dumps(args, default=str)
    except Exception:
        return False, "the arguments could not be read"

    try:
        corpus = _owner_corpus()
    except Exception as e:
        # Condition 6 is the safeguard against the owner's own details being
        # exempted. Without it there is no exemption.
        return False, "the owner's own records could not be checked (%s)" % e

    triggers = []
    for value in _leaves(parsed):
        if len(_norm(value)) < MIN_LEN:
            continue
        try:
            if _tier(value) <= 1:
                continue                      # not a reason for the block
        except Exception:
            return False, "a value could not be classified"

        bad = _never_exempt(value)
        if bad:
            return False, "it contains %s, which is never exempt" % bad
        if not _is_business_contact(value):
            return False, "a blocking value is not a business-contact detail"
        if _matches_owner(value, corpus):
            return False, "a blocking value matches the owner's own records"
        if not _from_public_web(taint_key, value):
            return False, "a blocking value is not attributable to public web content"
        triggers.append(value)

    if not triggers:
        return False, "nothing exemptable is causing the block"

    # Condition 1, checked by removal rather than by trusting a detector's
    # findings: whatever remains must be PUBLIC on its own.
    rest = blob
    for value in triggers:
        rest = rest.replace(value, " ")
    try:
        if _tier(rest) > 1:
            return False, "something other than the business details is sensitive"
    except Exception:
        return False, "the remainder could not be classified"

    return True, "public business details from the web (%d value(s))" % len(triggers)
