# -*- coding: utf-8 -*-
"""Public web/news query text is not private because of its topic.

SENSITIVE SUBSYSTEM (AGENTS.md). This narrows what the cloud-egress gates check
on one kind of text. Read the rule before changing a line of it.

THE OWNER'S DECISION (a), 2026-10-15
------------------------------------
Public web/news search text is NOT classified private because of its topic.
With or without cloud consent, provider-authored public query text is checked
only against the never-send floor (credentials, keys, ID/account numbers, the
owner's never-send list) and the owner's personal identifiers. Health and
civil-rights topics are no longer blocked from cloud seats.

WHAT WENT WRONG
---------------
A cloud seat asked ``search_web`` for "disability OR Medicaid OR Social
Security" and for "transgender rights Texas court ruling". The per-action
vault gate (vault_access.check_action) keyword-classified the JSON of the tool
input, tiered both TIER_3, and refused them; the search layer's own gate
(web_search._gate_search_query -> egress_gate._gate_text) would have refused
them again. The query was written BY the cloud provider -- it already holds
every word -- and it is going to a public search engine. Tiering it by topic
withholds nothing and breaks ordinary research about the world.

THE RULE
--------
Text in a public query field (``PUBLIC_QUERY_TOOLS``) is refused only when it
carries:

* ``never_send_list``      -- a token on the owner's never-send list, a raw
                              vault-document marker, or a deny-marked span
                              (judgment_gate.never_send_hits), unless every
                              such paragraph is covered by a file-grant
                              override (the egress gate's own semantics);
* ``credential``           -- a key, token, password or private key
                              (secret_patterns.contains_secret, and the
                              judgment gate's api_key shape);
* ``id_or_account_number`` -- an SSN, a 9-digit routing/account number or a
                              Luhn-valid card number (judgment_gate
                              .hard_identifier_hits);
* ``owner_identifier``     -- one of the owner's own identifiers: the privacy
                              watchlist, the owner's email addresses and
                              identities (user_email / owner_email /
                              owner_identities) and the owner's verified cell
                              number (phone config ``owner_cell``).

Nothing here uses keyword or topic classification. If the floor itself cannot
run, the text is refused (``floor_unavailable``): a floor that fails open is
not a floor.

Encoded text is checked in every form it could take on the wire: the text as
written, each pass of URL/form decoding (``unquote_plus`` until stable, at most
three passes, so a double-encoded token is still seen), and the digits-only
form of every separated digit run (so ``123.45.6789`` is checked as the SSN /
routing / card shape it spells). Every form is also checked NFKC-normalised
with format characters (category Cf, e.g. zero-width spaces) removed, so
fullwidth digits and a zero-width-split token are seen.

A refusal never carries the matched value -- only its reason, kinds and a
count -- so it is safe to log, to show the model and to show the user.

WHOSE TEXT THIS COVERS -- the author, not the backend
-----------------------------------------------------
The decision covers text a CLOUD provider authored. A cloud seat already holds
every word of its own query; a local seat's query, or one a background job
(the deep-research harness, setup research) wrote from local context, does
not -- the topic check is the reason that text stays on the device. So:

* ``vault_access.check_action`` applies the floor to a cloud provider's tool
  call (that provider IS the author), and on a pass records (provider, exact
  string) in the replay provenance registry (``record_cloud_query``);
* the agent loops (services/agent.py, both dispatch sites) and the live voice
  dispatcher run a cloud seat's public-query tool inside
  ``for_tool_call(provider, model, tool, args)``, which records the exact
  public-field strings AS THE CLOUD SEAT SENT THEM -- before
  ``_execute_tool`` puts real values back in place of ``[PII:...]`` tags.
  The search layers (web_search._gate_search_query, firecrawl's search gate)
  use ``gate_outbound`` only when ``cloud_author_of(query)`` finds the
  outgoing query to be exactly one of those strings; every other query (a
  rehydrated one included) keeps the egress gate's ordinary
  topic-classifying path;
* the egress gate's history replay (``egress_gate._gate_tool_use`` /
  ``_gate_tool_calls``) applies the floor to a replayed public field only
  when ``is_cloud_query(provider, value)``: the provider it is replayed to
  wrote exactly that string and check_action passed it. A query a local seat
  wrote, another provider's, or a refused one keeps the ordinary gate.

``browse_web.url`` is NOT public query text: a URL is an address the model
composes, and its query string can carry anything from the context, so a URL
keeps the ordinary policy everywhere (check_action, firecrawl scrape).
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import unquote_plus

_log = logging.getLogger("friday.privacy.public_query")

COMPONENT = "privacy.public_query"

#: Tool name -> the argument names whose values are public query text, sent to
#: the open web (a search engine, a news feed match, a research plan's
#: searches). Every other argument of these tools is still judged by the
#: caller's ordinary policy. browse_web is deliberately absent: a URL is not
#: search text, and its query string can carry context out (see the module
#: docstring).
PUBLIC_QUERY_TOOLS: dict[str, frozenset] = {
    "search_web": frozenset({"query"}),
    "search_news": frozenset({"query"}),
    "deep_research": frozenset({"question", "sub_questions"}),
}

#: How many rounds of URL/form decoding are applied when looking for a hidden
#: token. Three covers double and triple encoding; anything deeper is not text
#: a search engine would decode either.
_MAX_DECODE_PASSES = 3

#: A digit run with single separators between digits (dots, dashes,
#: underscores, slashes): "123.45.6789", "4111_1111_1111_1111". Whitespace is
#: left out on purpose: the SSN and card patterns already accept spaces, and
#: joining space-separated numbers ("top 100 of 2024") would invent IDs.
_DIGIT_RUN = re.compile(r"\d(?:[.\-_/]?\d)+")


# ── who authored the text ───────────────────────────────────────────────────
#
# The relaxation covers ONLY the exact strings a cloud seat wrote. A tool call
# is not enough: agent._execute_tool puts real values back in place of the
# [PII:kind:hash] tags a cloud seat uses (_restore_placeholders) BEFORE the
# search layer gates the query, so the query the search gate sees can carry a
# contact's name the cloud seat never saw. The mark therefore records the
# public-field strings AS THE CLOUD SEAT SENT THEM, and the search gates relax
# only for a query that is exactly one of those strings (cloud_author_of).
# Anything else -- a rehydrated query, a query a tool built from the
# arguments -- keeps the ordinary topic gate.

#: (cloud provider, frozenset of the exact public strings it sent) for the
#: tool call executing right now, or None. Set ONLY through
#: cloud_authored()/for_tool_call().
_CLOUD_AUTHOR: contextvars.ContextVar = contextvars.ContextVar(
    "friday_public_query_cloud_author", default=None)


def _norm_query(text: Any) -> str:
    """The comparison form of a query: the string with outer whitespace
    stripped (the search layers strip the same way). Nothing else is folded:
    the match is exact."""
    return text.strip() if isinstance(text, str) else ""


@contextlib.contextmanager
def cloud_authored(provider: str, texts: Iterable = ()):
    """Mark `texts` -- the exact public query strings a CLOUD seat sent -- as
    authored by `provider` inside this block. Only the agent loops' and the
    voice dispatcher's run of a public-query tool sets this (for_tool_call).
    With no texts, nothing is relaxed."""
    recorded = frozenset(t for t in (_norm_query(x) for x in (texts or ())) if t)
    tok = _CLOUD_AUTHOR.set((str(provider or "cloud"), recorded))
    try:
        yield
    finally:
        _CLOUD_AUTHOR.reset(tok)


def authored_by_cloud() -> str:
    """The cloud provider whose public-query tool call is executing, or "".

    Informational only: it does NOT make a query floor-only. The search gates
    ask cloud_author_of(query), which also requires the exact string."""
    try:
        cur = _CLOUD_AUTHOR.get()
        return str(cur[0]) if cur else ""
    except Exception:
        return ""


def cloud_author_of(text: Any) -> str:
    """The cloud provider when `text` (stripped) is EXACTLY one of the public
    query strings that provider sent for the tool call in flight; else "".

    A query that differs in any way from what the cloud seat wrote -- a
    [PII:...] tag put back as the real value, an appended term -- returns ""
    and so keeps the ordinary topic gate."""
    try:
        cur = _CLOUD_AUTHOR.get()
        if not cur:
            return ""
        provider, recorded = cur
        t = _norm_query(text)
        return str(provider) if t and t in recorded else ""
    except Exception:
        return ""


def for_tool_call(provider: str, model: str, tool: str, args: Any = None):
    """The context a tool call executes in.

    cloud_authored(provider, <the public-field strings of `args`>) when a
    CLOUD seat (VaultAccessControl.can_access is False) calls a public-query
    tool; otherwise a no-op. `args` MUST be the tool input as the cloud seat
    sent it, before _restore_placeholders. Any failure deciding is a no-op,
    which keeps the stricter ordinary policy downstream."""
    try:
        if str(tool or "") not in PUBLIC_QUERY_TOOLS:
            return contextlib.nullcontext()
        parts = split_args(str(tool), args)
        texts = parts[0] if parts else []
        if not texts:
            return contextlib.nullcontext()
        from agent_friday.privacy.vault_access import VaultAccessControl
        if VaultAccessControl().can_access(provider, model):
            return contextlib.nullcontext()
        return cloud_authored(provider, texts)
    except Exception:
        return contextlib.nullcontext()


# ── provenance of replayed queries ──────────────────────────────────────────
#
# History replay (egress_gate._gate_tool_args) sends earlier tool calls back
# to a cloud provider. A replayed public query is floor-only ONLY when that
# same provider wrote it and check_action let it run; a query a local seat
# wrote, one a different provider wrote, or one that was refused keeps the
# ordinary arg-value gate. Written only by VaultAccessControl.check_action.
# Bounded like egress_gate._PROVIDER_ECHO (oldest entry evicted first).

_CLOUD_QUERIES: "OrderedDict[tuple, None]" = OrderedDict()
_CLOUD_QUERIES_MAX = 5000
_CLOUD_QUERIES_LOCK = threading.Lock()


def _norm_provider(provider: Any) -> str:
    return str(provider or "").strip().lower()


def record_cloud_query(provider: str, texts: Iterable) -> None:
    """Record that cloud `provider` authored these public query strings and
    check_action passed them. Call ONLY from check_action's allow path."""
    p = _norm_provider(provider)
    if not p:
        return
    with _CLOUD_QUERIES_LOCK:
        for x in texts or ():
            t = _norm_query(x)
            if not t or len(t) > 4000:
                continue
            key = (p, t)
            _CLOUD_QUERIES[key] = None
            _CLOUD_QUERIES.move_to_end(key)
            while len(_CLOUD_QUERIES) > _CLOUD_QUERIES_MAX:
                _CLOUD_QUERIES.popitem(last=False)


def is_cloud_query(provider: str, text: Any) -> bool:
    """True when `provider` itself authored exactly `text` and check_action
    passed it (record_cloud_query)."""
    p, t = _norm_provider(provider), _norm_query(text)
    if not p or not t:
        return False
    with _CLOUD_QUERIES_LOCK:
        return (p, t) in _CLOUD_QUERIES


def clear_cloud_queries() -> None:
    """Forget every recorded cloud query (tests)."""
    with _CLOUD_QUERIES_LOCK:
        _CLOUD_QUERIES.clear()

#: Refusal reasons, in the order they are reported when several apply.
REASONS = ("floor_unavailable", "never_send_list", "credential",
           "id_or_account_number", "owner_identifier")

_REASON_TEXT = {
    "floor_unavailable": "the privacy floor check could not run, so nothing was sent",
    "never_send_list": "it contains material on the owner's never-send list",
    "credential": "it contains a credential or key",
    "id_or_account_number": "it contains an ID, card or account number",
    "owner_identifier": "it contains one of the owner's own personal identifiers",
}


@dataclass(frozen=True)
class Refusal:
    """Why public query text was withheld. Never holds the matched value."""
    reason: str
    kinds: tuple = ()
    count: int = 0
    component: str = COMPONENT

    def message(self, what: str = "this query") -> str:
        return (f"{self.component} withheld {what}: "
                f"{_REASON_TEXT.get(self.reason, self.reason)}"
                f"{f' ({self.count} match(es))' if self.count else ''}. "
                f"Nothing was sent. Rephrase without that part, or ask the "
                f"user to supply it to a local model.")


def public_fields(action: str) -> frozenset:
    return PUBLIC_QUERY_TOOLS.get(str(action or ""), frozenset())


def split_args(action: str, args: Any) -> Optional[tuple[list, dict]]:
    """Split a public-query tool's arguments into (public texts, the rest).

    Returns None when `action` is not a public-query tool or `args` is not a
    mapping -- the caller then keeps its ordinary policy for the whole call.
    Underscore-prefixed keys are server-injected and never sent anywhere; they
    are in neither part. A public field holding something other than text (or
    a list of text) is treated as "the rest", so it keeps the ordinary policy.
    """
    fields = public_fields(action)
    if not fields or not isinstance(args, dict):
        return None
    texts: list = []
    rest: dict = {}
    for k, v in args.items():
        key = str(k)
        if key.startswith("_"):
            continue
        if key in fields:
            if v is None:
                continue
            if isinstance(v, str):
                if v.strip():
                    texts.append(v)
                continue
            if isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
                texts.extend(x for x in v if x.strip())
                continue
        rest[key] = v
    return texts, rest


# ── the floor's pieces ──────────────────────────────────────────────────────

def _owner_identifiers() -> tuple[list, list, str]:
    """(watchlist tokens, owner emails/identities, owner cell digits).

    Raises when the settings cannot be read: the caller turns that into a
    ``floor_unavailable`` refusal.
    """
    from agent_friday import core as _core
    watch = [str(t).strip() for t in (_core._load_privacy_watchlist() or [])
             if str(t).strip()]
    settings = _core._load_settings() or {}
    idents = []
    for key in ("user_email", "owner_email"):
        val = settings.get(key)
        if isinstance(val, str) and val.strip():
            idents.append(val.strip())
    extra = settings.get("owner_identities") or []
    if isinstance(extra, list):
        idents += [x.strip() for x in extra if isinstance(x, str) and x.strip()]
    cell = ""
    try:
        from agent_friday.phone import config as _pc
        cell = re.sub(r"\D", "", str(_pc.load().get("owner_cell") or ""))
    except ImportError:
        cell = ""
    return watch, idents, cell


def _identifier_pattern(token: str) -> "re.Pattern":
    esc = re.escape(token)
    left = r"\b" if token[:1].isalnum() else ""
    right = r"\b" if token[-1:].isalnum() else ""
    return re.compile(left + esc + right, re.IGNORECASE)


def _fold(text: str) -> str:
    """NFKC-normalised with every format character (Unicode category Cf:
    zero-width space/joiners, soft hyphen, bidi marks, BOM) removed, so
    fullwidth digits and dashes read as the ID number they spell and a
    zero-width character cannot split a never-send token or an identifier."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")


def _variants(text: str) -> list:
    """The text as written plus each distinct URL/form-decoding pass of it,
    each also in its folded form (_fold).

    unquote_plus is applied until the text stops changing, at most
    _MAX_DECODE_PASSES times: "%2531" decodes to "%31" and only then to "1",
    so one pass would miss a double-encoded token. Folding is applied before
    and after decoding (a percent-encoded zero-width space only appears once
    decoded).
    """
    decoded = []
    for start in (text, _fold(text)):
        cur = start
        decoded.append(cur)
        for _ in range(_MAX_DECODE_PASSES):
            try:
                nxt = unquote_plus(cur)
            except Exception:
                break
            if nxt == cur:
                break
            decoded.append(nxt)
            cur = nxt
    variants: list = []
    for v in decoded:
        for form in (v, _fold(v)):
            if form not in variants:
                variants.append(form)
    return variants


def _hits(text: str) -> dict:
    """reason -> list of kinds found in `text` (never the values)."""
    from agent_friday.services import judgment_gate as _jg
    from agent_friday.services import secret_patterns as _sp

    found: dict = {}
    variants = _variants(text)

    for v in variants:
        never = _jg.never_send_hits(v)
        if never:
            covered = False
            try:
                from agent_friday.services import egress_gate as _eg
                covered = bool(_eg._never_send_covered_by_override(v))
            except ImportError:
                covered = False
            if not covered:
                found.setdefault("never_send_list", []).extend(
                    ["never_send"] * len(never))
        if _sp.contains_secret(v):
            found.setdefault("credential", []).append("secret")
        for kind in _jg.hard_identifier_hits(v):
            if kind == "api_key":
                found.setdefault("credential", []).append(kind)
            else:
                found.setdefault("id_or_account_number", []).append(kind)
        # The digits-only form of each separated digit run: "123.45.6789"
        # spells an SSN that the hyphen/space pattern does not see.
        for run in _DIGIT_RUN.findall(v):
            digits = re.sub(r"\D", "", run)
            if digits == run:
                continue            # already checked as written, above
            for kind in _jg.hard_identifier_hits(digits):
                if kind != "api_key":
                    found.setdefault("id_or_account_number", []).append(kind)

    watch, idents, cell = _owner_identifiers()
    for v in variants:
        low = v.lower()
        for tok in watch:
            if _identifier_pattern(tok).search(v):
                found.setdefault("owner_identifier", []).append("watchlist")
        for ident in idents:
            if ident.lower() in low and _identifier_pattern(ident).search(v):
                found.setdefault("owner_identifier", []).append(
                    "email" if "@" in ident else "identity")
        if len(cell) >= 7:
            digits = re.sub(r"\D", "", v)
            if cell[-10:] in digits:
                found.setdefault("owner_identifier", []).append("phone")
    return found


def check(text_or_args: Any, *, action: str = "", provider: str = "",
          surface: str = "", log: bool = True) -> Optional[Refusal]:
    """None when the public query text may go; a Refusal when it may not.

    `text_or_args` is one string, a list of strings, or a public-query tool's
    argument mapping (then `action` names the tool and only its public fields
    are read). Topic is never consulted. Fails closed: any error in the floor
    is a ``floor_unavailable`` refusal.
    """
    try:
        if isinstance(text_or_args, dict):
            parts = split_args(action, text_or_args)
            texts = parts[0] if parts else []
        elif isinstance(text_or_args, str):
            texts = [text_or_args]
        elif isinstance(text_or_args, Iterable):
            texts = [t for t in text_or_args if isinstance(t, str)]
        else:
            texts = []
        found: dict = {}
        for t in texts:
            if not t or not t.strip():
                continue
            for reason, kinds in _hits(t).items():
                found.setdefault(reason, []).extend(kinds)
    except Exception as e:      # the floor itself failed: refuse
        refusal = Refusal("floor_unavailable", kinds=(type(e).__name__,))
        if log:
            log_refusal(refusal, action=action, provider=provider, surface=surface)
        return refusal

    for reason in REASONS:
        if found.get(reason):
            kinds = found[reason]
            refusal = Refusal(reason, kinds=tuple(sorted(set(kinds))),
                              count=len(kinds))
            if log:
                log_refusal(refusal, action=action, provider=provider,
                            surface=surface)
            return refusal
    return None


def log_refusal(refusal: Refusal, *, action: str = "", provider: str = "",
                surface: str = "") -> None:
    """One WARNING line in friday.log naming the component and the reason.

    Kinds and a count only -- never the matched value. Reason and kinds are
    written as constants looked up from fixed tables (anything unknown is
    logged as "other"), so no text derived from the inspected value can reach
    the log line.
    """
    reason_code = _LOG_REASON_CODES.get(str(refusal.reason), "other")
    kind_codes = sorted({_LOG_KIND_CODES.get(str(k), "other")
                         for k in refusal.kinds})
    try:
        count = int(refusal.count)
    except (TypeError, ValueError):
        count = 0
    _log.warning("REFUSED component=%s reason=%s kinds=%s count=%d tool=%s "
                 "provider=%s surface=%s",
                 COMPONENT, reason_code, ",".join(kind_codes),
                 count, action or "-", provider or "-", surface or "-")


#: Fixed log vocabulary for log_refusal: the logged reason/kind is always one
#: of these constant strings, never text taken from the refusal itself.
_LOG_REASON_CODES = {r: r for r in REASONS}
_LOG_KIND_CODES = {
    # what _hits() reports
    "never_send": "never_send",
    "secret": "secret",
    "api_key": "api_key",
    "ssn": "ssn",
    "routing": "routing",
    "card": "card",
    "watchlist": "watchlist",
    "email": "email",
    "identity": "identity",
    "phone": "phone",
    # floor_unavailable carries the failing exception's type name
    "ImportError": "ImportError",
    "ModuleNotFoundError": "ModuleNotFoundError",
    "AttributeError": "AttributeError",
    "TypeError": "TypeError",
    "ValueError": "ValueError",
    "KeyError": "KeyError",
    "OSError": "OSError",
    "RuntimeError": "RuntimeError",
    "RecursionError": "RecursionError",
    "UnicodeError": "UnicodeError",
}


def gate_outbound(text: str, provider: str, field: str) -> tuple[str, str]:
    """The search/fetch layers' gate for public query text.

    Returns (text, refusal_detail): refusal_detail is "" when the text may go
    unchanged. Writes the decision to the egress audit log (best effort --
    the audit trail is not the floor) and refusals to friday.log.
    """
    refusal = check(text, action=field.split(".", 1)[0], provider=provider,
                    surface=field)
    try:
        from agent_friday.services import egress_gate as _eg
        _eg._log(provider, field, _eg.Tier.PUBLIC if refusal is None
                 else _eg.Tier.SENSITIVE,
                 "allow" if refusal is None else "block",
                 ("public query text: never-send floor and owner identifiers "
                  "checked, topic not classified (owner decision 2026-10-15)")
                 if refusal is None else
                 f"{refusal.component} refused: {refusal.reason}")
    except Exception:
        pass
    if refusal is None:
        return text, ""
    return "", refusal.message()
