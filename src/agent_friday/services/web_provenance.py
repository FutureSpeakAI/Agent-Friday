"""web_provenance -- which sites a chat turn read, so a stopped site can be fully stopped.

Owner ruling 10 (2026-10-28): "Record the source site of each fact read in
chat, so a site can be fully stopped." When Friday reads web content in a
turn (search_web results, a page browse_web fetched, search_news hits), the
turn's reply carries a ``web_sources`` list: the REGISTERED DOMAINS of those
pages (example.com, bbc.co.uk), never a full URL, a path or a query. The list
rides with what is stored from the turn:

* the reply's transcript message (``meta.web_sources``) and its row in the
  conversation memory (metadata ``web_sources``, a comma string);
* the knowledge graph's facts extracted from that row (provenance
  ``web_sources``, merged like every other provenance list);
* a memory-card suggestion the background review queues from a window that
  read the web, and a memory suggestion (proposal) whose evidence is in such
  a reply.

Nothing is recorded off the record. A site the owner stopped
(forget_kinds.stop_using_source("web:<domain>")) is still opened when the
owner asks, but a reply that read it is not remembered: it is not indexed
for recall, not searched, not reviewed for the card, not proposed and not
taken into the graph (model_router._index_chat_turn_body, the graph indexer,
memory_review and memory_proposals ask ``stopped`` with ``fail_closed``: a
record that cannot be read holds back every reply that read the web).

What counts as read (a choice, recorded here): a page browse_web opened, and
EVERY result search_web or search_news returned, not only the ones the reply
cites. The model reads every result's title and snippet, and a fact can come
from a snippet the reply never links, so which results were used cannot be
known; recording only the cited ones would let a stopped site's snippet be
learned. The cost: one result from a stopped site among ten makes the whole
reply count as read there, so it is not remembered (it enters the index as
the stand-in line; the transcript keeps it). Over-forgetting is the safe side.

How a turn's domains are found: each web tool notes the pages it read under
the conversation it ran for (``note``; the tool handlers know it through
agent._CURRENT_CONVERSATION), and a chat turn's tool trace is read as well
(``from_trace``); ``turn_sources`` takes both, once, at the end of the turn.
"""
from __future__ import annotations

import ipaddress
import json
import re
import threading
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

#: Tools whose results are web page text.
WEB_TOOLS = frozenset({"search_web", "browse_web", "search_news"})
#: Most domains kept for one turn.
MAX_DOMAINS = 12
#: What a reply that read a stopped site is remembered as (the transcript keeps the reply itself).
STOPPED_STANDIN = "[A reply that used a site you stopped; Friday does not remember it.]"

_LOCK = threading.Lock()
#: conversation id -> domains the web tools noted during its current turn.
_NOTED: Dict[str, set] = {}
_NOTED_MAX = 256

_URL = re.compile(r"https?://[^\s<>\"'\])}]+", re.I)
_BROWSE_HEAD = re.compile(r"^\[(https?://[^\]\s]+)\]")


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return off_record.active()
    except Exception:
        return False


def registered_domain(url_or_host: Any) -> str:
    """The registered domain of a URL or host ("" when there is none): the
    host without "www.", cut to one label beyond its public suffix
    (news.bbc.co.uk -> bbc.co.uk, a.b.example.com -> example.com,
    dana.github.io stays). An IP address or a one-label host is no site."""
    s = str(url_or_host or "").strip()
    if not s:
        return ""
    try:
        host = (urlparse(s).hostname or "") if "//" in s else s.split("/")[0].split(":")[0]
    except ValueError:
        return ""
    host = host.lower().strip(".")
    if not host or not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2:
        return ""
    from agent_friday.services.forget_kinds import _PUBLIC_SUFFIXES
    last2 = ".".join(labels[-2:])
    if last2 in _PUBLIC_SUFFIXES:
        return ".".join(labels[-3:]) if len(labels) >= 3 else ""
    return last2


def _domains(urls: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for u in urls or ():
        d = registered_domain(u)
        if d and d not in out:
            out.append(d)
    return out[:MAX_DOMAINS]


def note(urls: Iterable[Any], conversation_id: Optional[str] = None) -> List[str]:
    """A web tool read these pages for this conversation's current turn.
    Nothing is kept off the record. Returns the domains noted."""
    if _off_record():
        return []
    ds = _domains(urls)
    if not ds:
        return []
    key = str(conversation_id or "")
    with _LOCK:
        cur = _NOTED.setdefault(key, set())
        cur.update(ds)
        while len(_NOTED) > _NOTED_MAX:
            _NOTED.pop(next(iter(_NOTED)))
    return ds


def consume(conversation_id: Optional[str] = None) -> List[str]:
    """The domains noted for this conversation since its last turn ended (and
    forgets them)."""
    with _LOCK:
        got = _NOTED.pop(str(conversation_id or ""), set())
    return sorted(got)[:MAX_DOMAINS]


def urls_in_result(name: str, result: Any) -> List[str]:
    """The page URLs a web tool's result shows it read; [] for a failure, a
    refusal or a gate's denial."""
    text = str(result or "")
    if name == "browse_web":
        m = _BROWSE_HEAD.match(text)
        return [m.group(1)] if m else []
    if name == "search_web":
        return _URL.findall(text) if text.startswith("Search results for") else []
    if name == "search_news":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        hits = data.get("hits") if isinstance(data, dict) else None
        return [str(h.get("url")) for h in hits or [] if isinstance(h, dict) and h.get("url")]
    return []


def from_trace(tool_trace: Any) -> List[str]:
    """The domains a turn's tool trace shows it read."""
    urls: List[str] = []
    for e in tool_trace or []:
        if isinstance(e, dict) and e.get("name") in WEB_TOOLS:
            urls += urls_in_result(str(e.get("name")), e.get("result"))
    return _domains(urls)


def turn_sources(tool_trace: Any = None, conversation_id: Optional[str] = None) -> List[str]:
    """The end of a turn: the domains its reply used (trace and notes), sorted.
    [] off the record (and what was noted is dropped either way)."""
    noted = consume(conversation_id)
    if _off_record():
        return []
    return sorted(set(noted) | set(from_trace(tool_trace)))[:MAX_DOMAINS]


def clean(domains: Any) -> List[str]:
    """A stored or passed list (or comma string) of domains, validated."""
    if isinstance(domains, str):
        domains = domains.split(",")
    out: List[str] = []
    for d in domains or []:
        r = registered_domain(str(d or "").strip())
        if r and r not in out:
            out.append(r)
    return out[:MAX_DOMAINS]


def stopped(domains: Any, *, fail_closed: bool = False) -> List[str]:
    """The domains among these the owner stopped. Never raises.

    ``fail_closed``: when the stopped-sources record cannot be read (or its
    key is missing), every one of these domains counts as stopped. The
    learning paths (model_router's indexing, the graph indexer, the memory
    review, the proposals) pass it: a reply that read the web is not learned
    from while it cannot be told whether its site was stopped. A turn that
    read no web page has no domains and is never held back by it. Without it
    the check fails open."""
    try:
        ds = clean(domains)
    except Exception:
        return []
    try:
        from agent_friday.services import forget_kinds as fk
        return [d for d in ds if fk.site_stopped(d, fail_closed=fail_closed)]
    except Exception:
        return list(ds) if fail_closed else []


def on_site(domains: Any, site: str) -> bool:
    """Whether any of these domains is the site or under it. Turns record
    registered domains, so a stopped subdomain (news.example.com) matches
    none of them; forget_kinds says so in its receipt."""
    site = str(site or "").lower().strip(".")
    return bool(site) and any(d == site or d.endswith("." + site) for d in clean(domains))


def _reset_for_tests() -> None:
    with _LOCK:
        _NOTED.clear()
