"""The A2UI data broker (D2): the only code that turns a workspace document's declared bindings into data.

Contract draft 3, section 3. A document declares bindings in `createSurface.metadata.friday.bindings` as
`{name: {path, query, params}}`; the profile (V7) already holds that every bound path sits under one of them. The
broker holds the rest:

* the query and every parameter must be in the closed VOCABULARY below, typed; anything else refuses the WHOLE
  document (rule V7) before anything is read. The vocabulary grows one reviewed query at a time (D-C);
* queries read through the same in-process adapter the device content routes use (tv_gateway/ws_knowledge), never
  a host path, and return plain rows;
* a row holding a URL is dropped and counted, so one odd page title can't blank the whole view; the renderer then
  re-checks every bound value anyway (V8/V11);
* the answer is keyed by the binding path (what a2ui_render.render takes as `data`), with `as_of` and `ttl_s`.

Who may ask (the device's read right) is the route's business. tests/unit/test_a2ui_broker.py holds each rule.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from agent_friday.services import a2ui_profile as prof

TTL_S = 30
SECTIONS_MAX = 40


class BrokerRefused(ValueError):
    """The document asks for data outside the closed vocabulary. `rule` is always V7 (data)."""

    def __init__(self, detail: str):
        super().__init__("V7: " + detail)
        self.rule, self.detail = "V7", detail


def _int_param(lo: int, hi: int) -> Callable:
    def check(v):
        return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
    return check


class _Run:
    """One resolve: who reads (the sensitivity the rows are stamped with), what was dropped, and each source read once."""

    def __init__(self, sensitivity: str):
        self.sensitivity, self.dropped, self._rows = sensitivity, 0, None

    def knowledge_rows(self) -> list:
        """The adapter's rows (read once per view), with every row that holds a URL anywhere dropped; each query counts its drops."""
        if self._rows is None:
            from agent_friday.tv_gateway import ws_knowledge
            self._rows = list(ws_knowledge.pages(self.sensitivity))
        out = []
        for row in self._rows:
            if any(isinstance(v, str) and prof.has_url(v) for v in row.values()):
                self.dropped += 1
                continue
            out.append(row)
        return out


def _sections(params: dict, run: _Run) -> list:
    counts: dict = {}
    for r in run.knowledge_rows():
        name = str(r.get("line") or "")
        counts[name] = counts.get(name, 0) + 1
    return [{"name": k, "count": n} for k, n in sorted(counts.items())][:SECTIONS_MAX]


def _recent(params: dict, run: _Run) -> list:
    limit = params.get("limit", 12)
    return [{"title": str(r.get("title") or ""), "section": str(r.get("line") or ""), "when": r.get("when")}
            for r in run.knowledge_rows()[:limit]]


#: query -> (allowed params with their checks, resolver). Reviewed additions only (contract D-C).
VOCABULARY: dict = {
    "knowledge.sections": ({}, _sections),
    "knowledge.recent": ({"limit": _int_param(1, 20)}, _recent),
}

#: query -> the device-block section its rows come from. A view is served only where every section its bindings read
#: is declared (test_a2ui_broker: test_every_query_names_the_section_it_reads).
READS: dict = {
    "knowledge.sections": "pages",
    "knowledge.recent": "pages",
}


def sections_read(doc) -> frozenset:
    """The device-block sections a document's declared bindings read. Raises BrokerRefused like check_bindings."""
    return frozenset(READS[query] for _path, query, _params in check_bindings(doc))


def check_bindings(doc) -> list:
    """The declared bindings, each checked against the vocabulary: [(path, query, params)]. Raises BrokerRefused."""
    meta = {}
    for message in doc if isinstance(doc, list) else []:
        body = message.get("createSurface") if isinstance(message, dict) else None
        if isinstance(body, dict):
            meta = body.get("metadata") or {}
    declared = ((meta.get("friday") or {}).get("bindings") or {}) if isinstance(meta, dict) else {}
    if not isinstance(declared, dict):
        raise BrokerRefused("bindings are a map of name to {path, query, params}")
    out = []
    for name, b in declared.items():
        if not isinstance(b, dict) or set(b) - {"path", "query", "params"}:
            raise BrokerRefused("binding %r is not {path, query, params}" % str(name)[:40])
        path, query, params = b.get("path"), b.get("query"), b.get("params", {})
        if not isinstance(path, str) or not path.startswith("/") or ".." in path:
            raise BrokerRefused("binding %r has no plain path" % str(name)[:40])
        if query not in VOCABULARY:
            raise BrokerRefused("query %r is not in the closed vocabulary" % str(query)[:60])
        allowed, _ = VOCABULARY[query]
        if not isinstance(params, dict) or set(params) - set(allowed):
            raise BrokerRefused("query %s takes no such parameter" % query)
        for key, value in params.items():
            if not allowed[key](value):
                raise BrokerRefused("query %s: parameter %s is out of range" % (query, key))
        out.append((path, query, params))
    return out


def _put(data: dict, path: str, value) -> None:
    parts = [p for p in path.split("/") if p]
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    if parts:
        cur[parts[-1]] = value


def resolve(doc, *, sensitivity: str) -> dict:
    """{data, as_of, ttl_s, dropped} for a document's declared bindings. Raises BrokerRefused before reading anything."""
    wanted = check_bindings(doc)
    run = _Run(sensitivity)
    data: dict = {}
    for path, query, params in wanted:
        _, resolver = VOCABULARY[query]
        _put(data, path, resolver(params, run))
    as_of = datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"data": data, "as_of": as_of, "ttl_s": TTL_S, "dropped": run.dropped}
