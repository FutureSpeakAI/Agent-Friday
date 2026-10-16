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
  call (that provider IS the author), and on a pass records (provider, model,
  exact string) in the replay provenance registry (``record_cloud_query``);
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
  when ``is_cloud_query(provider, value, model)``: the seat it is replayed to
  (same provider, same model) wrote exactly that string and check_action
  passed it. A query a local seat wrote, another provider's or another
  model's, or a refused one keeps the ordinary gate.

``browse_web.url`` is NOT public query text: a URL is an address the model
composes, and its query string can carry anything from the context, so a URL
keeps the ordinary policy everywhere (check_action, firecrawl scrape).

ASK WITH PREVIEW -- the owner decides what leaves (owner decision 2026-10-16)
---------------------------------------------------------------------------
"No more blanket rules; the user is the determining factor, so long as we've
been transparent with them." A cloud seat's public query is therefore one of
three things:

* a public-topic query sharing nothing private from the conversation: it goes
  through, no card (everything above);
* a query that shares private text from the conversation (``needs_approval``
  returns a ``Share``): an approval card shows the owner, in plain language,
  the exact text that would go out and the search engine(s) it goes to, with
  Send and Don't send. Nothing is sent until the owner chooses Send; Don't
  send gives the model a plain note. The card is an ordinary ``tool_confirm``
  card raised by services/agent._public_query_hold and run by
  services/approval_executor, inside ``approved_scope`` so the search gates
  treat exactly the approved strings as floor-only;
* a query carrying a floor item (``check``): refused. The never-send floor is
  the owner's own standing choice and no card can override it -- an approved
  card's query meets the floor again at the search gate.

"Private text" is either (1) a run of ``MIN_SHARED_RUN_WORDS`` or more words
that appears verbatim in non-public context sent to that seat in the request
being answered -- the system prompt, the owner's messages and tool results,
less the paragraphs the egress gate holds as published (search rows, news),
as Friday's own constants, or as that seat's own echo -- e.g. an email line or
a health note; or (2) a ``[PII:...]`` tag the seat used that would be filled
back in with the real value (someone's name, a number) before the query is
sent. The comparison is on folded, lower-cased words, so case, punctuation
and spacing do not hide a copied run.

Which tools ask (``CARD_TOOLS``): ``search_web`` (the query goes to the
configured search backend chain) and ``deep_research`` (the question and
sub-questions seed a research job that searches that same chain).
``search_news`` does not ask: its query is matched against the news feed
already on this computer and is not sent anywhere. A cloud voice seat asks
too (services/voice_engine: the card waits on screen and the seat says so);
local seats never ask (nothing leaves the device).

A paraphrase does not ask: the check is verbatim runs and filled-in tags, so a
seat that restates private context in its own words is not caught here. That
is a known limit of a deterministic check, not a promise.

The card must show all of what Send sends, faithfully. A card's text is every
public query string plus, for deep_research, its ``context``
(``card_text_for``); a text longer than ``CARD_TEXT_MAX`` or holding invisible
format characters (Unicode Cf, bidi controls) is not carded and does not run
(``card_problem``). An approved card runs only when its stored text is still
exactly ``card_text_for`` its input (``approved_texts``). Only the Send button
on screen approves one (approvals.PUBLIC_QUERY_CARD_APPROVERS): not voice, a
text message, a chat reply or a batch. For deep_research, Send starts the job
with the text shown; the searches the job derives from it each go through the
ordinary privacy gate, and the card says so.
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
# same seat -- the same provider AND the same model -- wrote it and
# check_action let it run; a query a local seat wrote, one a different
# provider or a different model of the same provider wrote, or one that was
# refused keeps the ordinary arg-value gate. Written only by
# VaultAccessControl.check_action. Bounded like egress_gate._PROVIDER_ECHO
# (oldest entry evicted first).

_CLOUD_QUERIES: "OrderedDict[tuple, None]" = OrderedDict()
_CLOUD_QUERIES_MAX = 5000
_CLOUD_QUERIES_LOCK = threading.Lock()


def _norm_provider(provider: Any) -> str:
    return str(provider or "").strip().lower()


def _norm_model(model: Any) -> str:
    return str(model or "").strip().lower()


def record_cloud_query(provider: str, texts: Iterable, model: str = "") -> None:
    """Record that the cloud seat (`provider`, `model`) authored these public
    query strings and check_action passed them. Call ONLY from check_action's
    allow path."""
    p, m = _norm_provider(provider), _norm_model(model)
    if not p:
        return
    with _CLOUD_QUERIES_LOCK:
        for x in texts or ():
            t = _norm_query(x)
            if not t or len(t) > 4000:
                continue
            key = (p, m, t)
            _CLOUD_QUERIES[key] = None
            _CLOUD_QUERIES.move_to_end(key)
            while len(_CLOUD_QUERIES) > _CLOUD_QUERIES_MAX:
                _CLOUD_QUERIES.popitem(last=False)


def is_cloud_query(provider: str, text: Any, model: str = "") -> bool:
    """True when the seat (`provider`, `model`) itself authored exactly
    `text` and check_action passed it (record_cloud_query). Another model of
    the same provider is another seat."""
    p, m, t = _norm_provider(provider), _norm_model(model), _norm_query(text)
    if not p or not t:
        return False
    with _CLOUD_QUERIES_LOCK:
        return (p, m, t) in _CLOUD_QUERIES


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


def _identifier_pattern(word: str) -> "re.Pattern":
    """A watchlist word or owner identifier (never a credential), matched
    whole where its ends are word characters."""
    esc = re.escape(word)
    left = r"\b" if word[:1].isalnum() else ""
    right = r"\b" if word[-1:].isalnum() else ""
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
    # Each logged word is SELECTED from a constant table by comparison, never
    # copied from the refusal (CodeQL py/clear-text-logging): the refusal was
    # built while inspecting text that may hold a secret. No count either.
    reason_code = "other"
    for code in _LOG_REASON_CODES:
        if refusal.reason == code:
            reason_code = code
    kind_codes = [code for code in _LOG_KIND_CODES if code in refusal.kinds]
    if any(k not in _LOG_KIND_CODES for k in refusal.kinds):
        kind_codes.append("other")
    _log.warning("REFUSED component=%s reason=%s kinds=%s tool=%s "
                 "provider=%s surface=%s",
                 COMPONENT, reason_code, ",".join(kind_codes),
                 action or "-", provider or "-", surface or "-")


#: Fixed log vocabulary for log_refusal: the logged reason/kind is always one
#: of these constant strings, never text taken from the refusal itself.
_LOG_REASON_CODES = tuple(REASONS)
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


# ── ask with preview: a query that shares private text ──────────────────────

#: The public-query tools whose query, when it shares private text from the
#: conversation, waits on the owner's Send / Don't send. search_news is not
#: here: its query is matched against the feed on this computer and never
#: leaves it.
CARD_TOOLS = frozenset({"search_web", "deep_research"})

#: How many consecutive words copied from non-public context make a query
#: "share private text". Long enough that a short topic phrase the owner also
#: typed ("Texas court ruling") does not ask; short enough that an email line
#: or a sentence from a note does.
MIN_SHARED_RUN_WORDS = 6

_WORD = re.compile(r"\w+")

#: Plain names for the search backends (web_search.backend_chain).
_ENGINE_NAMES = {
    "wigolo": "Wigolo (runs on this computer and searches the web)",
    "firecrawl": "Firecrawl",
    "brave": "Brave Search",
    "duckduckgo-scrape": "DuckDuckGo",
}

#: Why a card was raised, in the owner's words. Fixed text; never the value.
_SHARE_REASON_TEXT = {
    "conversation_text": "it repeats words from this conversation",
    "contact_detail": ("it fills in a name or detail that was kept hidden "
                       "from the cloud model"),
}


@dataclass(frozen=True)
class Share:
    """A cloud seat's public query that shares private text: what the
    approval card shows and what an approved card sends."""
    tool: str
    provider: str
    model: str
    texts: tuple        # exactly what would go out, in argument order
    args: dict          # the tool input as it would run (tags filled in)
    reasons: tuple      # keys of _SHARE_REASON_TEXT
    engine: str         # where it goes, in plain words


def _request_texts(request: Any) -> list:
    """Every string a model request carries that the seat did not write: the
    system prompt, and each non-assistant message's text and tool results.

    `request` is an Anthropic request (``{"system", "messages"}``) or an
    OpenAI-shape message list. Assistant turns are the seat's own words and
    are left out, tool_use arguments with them."""
    out: list = []

    def add(v):
        if isinstance(v, str) and v.strip():
            out.append(v)

    def blocks(content):
        if isinstance(content, str):
            add(content)
            return
        if isinstance(content, list):
            for b in content:
                if isinstance(b, str):
                    add(b)
                elif isinstance(b, dict):
                    kind = b.get("type")
                    if kind == "text":
                        add(b.get("text"))
                    elif kind == "tool_result":
                        blocks(b.get("content"))

    if isinstance(request, dict):
        blocks(request.get("system"))
        msgs = request.get("messages") or []
    elif isinstance(request, list):
        msgs = request
    else:
        return out
    for m in msgs:
        if isinstance(m, dict) and str(m.get("role") or "") != "assistant":
            blocks(m.get("content"))
    return out


def _context_paragraphs(request: Any, provider: str) -> list:
    """The non-public paragraphs of `request`: every paragraph except those
    the egress gate holds as published (search rows, news, fetched pages), as
    Friday's own constants, or as `provider`'s own echo."""
    try:
        from agent_friday.services import egress_gate as _eg
    except ImportError:
        _eg = None
    paras: list = []
    for text in _request_texts(request):
        for p in re.split(r"\n\s*\n", text):
            ps = p.strip()
            if not ps:
                continue
            if _eg is not None:
                with _eg._TRUSTED_LOCK:
                    known = (ps in _eg._PUBLIC_PARAS or ps in _eg._TRUSTED_PARAS
                             or provider in _eg._PROVIDER_ECHO.get(ps, ()))
                if known:
                    continue
            paras.append(ps)
    return paras


def _words(text: str) -> list:
    return _WORD.findall(_fold(text).lower())


def shares_conversation_text(texts: Iterable, paragraphs: Iterable,
                             n: int = MIN_SHARED_RUN_WORDS) -> bool:
    """True when any of `texts` holds a run of `n` consecutive words that
    appears, in the same order, in one of `paragraphs`."""
    runs = set()
    for t in texts or ():
        w = _words(t) if isinstance(t, str) else []
        for i in range(len(w) - n + 1):
            runs.add(tuple(w[i:i + n]))
    if not runs:
        return False
    for p in paragraphs or ():
        w = _words(p)
        for i in range(len(w) - n + 1):
            if tuple(w[i:i + n]) in runs:
                return True
    return False


def engine_for(tool: str) -> str:
    """The search engine(s) `tool`'s query goes to, by name, in the order
    the search layer tries them (web_search.backend_chain)."""
    try:
        from agent_friday.services import web_search as _ws
        chain = [_ENGINE_NAMES.get(n, n) for n in _ws.backend_chain()]
    except Exception:
        chain = []
    if not chain:
        return "the configured web search"
    if len(chain) == 1:
        return chain[0]
    return f"{chain[0]} (then {', then '.join(chain[1:])}, if it does not answer)"


def needs_approval(provider: str, model: str, tool: str, sent_args: Any,
                   outgoing_args: Any, request: Any = None):
    """Whether a cloud seat's public query may go as it is.

    `sent_args` is the tool input as the seat sent it; `outgoing_args` the
    same input with ``[PII:...]`` tags filled in (what would actually run);
    `request` the model request the seat is answering (``_request_texts``).

    Returns None when the query may go with no card (not a CARD_TOOLS tool,
    a local seat, or nothing private shared); a ``Refusal`` when the text
    that would go out meets the floor (no card can override it); otherwise a
    ``Share`` for the approval card. Raises on an internal failure: the
    caller fails closed.
    """
    tool = str(tool or "")
    if tool not in CARD_TOOLS:
        return None
    from agent_friday.privacy.vault_access import VaultAccessControl
    if VaultAccessControl().can_access(provider, model):
        return None
    out_parts = split_args(tool, outgoing_args)
    out_texts = list(out_parts[0]) if out_parts else []
    if not out_texts:
        return None
    sent_parts = split_args(tool, sent_args)
    sent_texts = list(sent_parts[0]) if sent_parts else []
    reasons = []
    if out_texts != sent_texts:
        reasons.append("contact_detail")
    if shares_conversation_text(out_texts,
                                _context_paragraphs(request, str(provider or ""))):
        reasons.append("conversation_text")
    if not reasons:
        return None
    refusal = check(out_texts, action=tool, provider=str(provider or ""),
                    surface="public_query.card")
    if refusal is not None:
        return refusal
    return Share(tool=tool, provider=str(provider or ""), model=str(model or ""),
                 texts=tuple(out_texts), args=dict(outgoing_args),
                 reasons=tuple(reasons), engine=engine_for(tool))


def card_title(share: Share) -> str:
    if share.tool == "deep_research":
        return "Start deep research with this text?"
    return "Send this web search?"


#: The most text a card may show. The approvals store keeps at most this many
#: characters of a card's action text (approvals.create_approval), and a card
#: must show all of what Send would send, so a longer query is not carded.
CARD_TEXT_MAX = 1000


def card_text_for(tool: str, args: Any) -> str:
    """Exactly what a card shows for `tool` run with `args`: every public
    query string, one per line, and for deep_research the background
    (`context`) the job also starts from. approved_scope recomputes this from
    the card's payload and runs it only when it equals what the card showed."""
    parts = split_args(str(tool or ""), args)
    lines = list(parts[0]) if parts else []
    if str(tool or "") == "deep_research" and isinstance(args, dict):
        ctx = args.get("context")
        if isinstance(ctx, str) and ctx.strip():
            lines.append("Background: " + ctx)
    return "\n".join(lines)


def card_text(share: Share) -> str:
    return card_text_for(share.tool, share.args)


def card_problem(share: Share) -> str:
    """Why `share` cannot be shown faithfully on a card ("" when it can): text
    too long for the card to hold, or invisible formatting characters
    (Unicode Cf: zero-width and bidi controls) that would make the shown text
    read differently from what is sent."""
    text = card_text(share)
    if len(text) > CARD_TEXT_MAX:
        return ("the query is too long to show on an approval card; shorten "
                "the query")
    if any(unicodedata.category(ch) == "Cf" for ch in text):
        return ("the query holds invisible formatting characters that would "
                "make the card read differently from what is sent; rewrite it "
                "without them")
    return ""


def card_why(share: Share) -> str:
    why = " and ".join(_SHARE_REASON_TEXT.get(r, r) for r in share.reasons)
    if share.tool == "deep_research":
        return (f"This research request {why}. Send starts a research job with "
                f"exactly the text shown. The job plans its own searches from "
                f"it; each search it derives goes through the standard privacy "
                f"gate before it is sent to {share.engine}, and it reads the "
                f"pages it finds. Don't send starts nothing.")
    return (f"This search {why}. Send sends exactly the text shown to "
            f"{share.engine}. Don't send sends nothing.")


def card_note(share: Share) -> str:
    """What the model is told while the card waits. No query text."""
    why = " and ".join(_SHARE_REASON_TEXT.get(r, r) for r in share.reasons)
    return (f"[APPROVAL CARD RAISED] '{share.tool}' was NOT executed and "
            f"nothing was sent: the query {why}, so the user decides on an "
            f"approval card on screen whether to send it to {share.engine}. "
            f"Do NOT call it again this turn and do not ask for a yes in chat "
            f"or by voice instead: only the Send button on the card approves "
            f"it. Tell the user plainly that a card is waiting on screen "
            f"(Approvals) and what it is for. If they choose Send, the search "
            f"runs and its result is posted into this conversation.")


def declined_note(tool: str) -> str:
    """What the model is told after the owner chose Don't send."""
    return (f"[DECLINED] The user chose Don't send for this '{tool}' query on "
            f"its approval card. Nothing was sent. Do not retry it; carry on "
            f"without it, or ask the user how they would like to go on.")


def is_public_query_card(record: Any) -> bool:
    """A card raised by agent._public_query_card (payload.public_query)."""
    return (isinstance(record, dict)
            and isinstance((record.get("payload") or {}).get("public_query"), dict))


def approved_texts(record: Any) -> Optional[list]:
    """The strings an approved public-query card may send, or None when the
    card does not hold up: its input must still hold exactly the strings it
    listed, AND its stored action text must be exactly card_text_for(its
    input) -- what the owner saw is all of what runs. services/approval_executor
    does not run a public-query card for which this is None."""
    try:
        payload = (record or {}).get("payload") or {}
        pq = payload.get("public_query")
        tool = str(payload.get("tool") or "")
        if not isinstance(pq, dict) or tool not in CARD_TOOLS:
            return None
        args = payload.get("input")
        parts = split_args(tool, args)
        texts = list(parts[0]) if parts else []
        shown = [x for x in (pq.get("texts") or []) if isinstance(x, str)]
        if not texts or [_norm_query(t) for t in texts] != [_norm_query(t) for t in shown]:
            return None
        if (record or {}).get("action_description") != card_text_for(tool, args):
            return None
        return texts
    except Exception:
        return None


def approved_scope(record: Any):
    """The context an approved public-query card runs in:
    cloud_authored(provider, approved_texts(record)), so the search gates
    apply the floor (gate_outbound) to exactly those strings -- an approval
    never lets a floor item out. A no-op for any other card (or one that does
    not hold up), which keeps the ordinary topic gate."""
    texts = approved_texts(record)
    if not texts:
        return contextlib.nullcontext()
    pq = ((record or {}).get("payload") or {}).get("public_query") or {}
    return cloud_authored(str(pq.get("provider") or "cloud"), texts)
