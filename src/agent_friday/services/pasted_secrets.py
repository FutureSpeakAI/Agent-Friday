"""A key pasted into a conversation is withheld before anything keeps it.

Text people type to Friday is persisted and indexed in many places: the
conversation file, the legacy chat mirror, conversation memory (and its
full-text index), the context log, skill-capture trajectories, and the
knowledge graph built from conversation memory. A provider key pasted into a
chat box would otherwise sit in plain text in every one of them, searchable.

Two layers, one definition of a key (services/secret_patterns and
services/secret_shapes, the lists the egress classifier, the tool-output
redactor and the setup chat already share):

* Ingest. A chat request is cleaned before the turn starts, so the model, the
  turn's prompt, every store and every index see the withheld marker, never
  the value. The reply carries a ``key_form`` action that opens the secure
  field for the service the key belongs to (``key_form_action``).
* Stores. ``scrub`` runs again where text is written, so text that reaches a
  store by another road (a channel message, a model's reply quoting a key it
  was shown elsewhere) is withheld there too.

Nothing here returns or logs a value. ``withhold`` reports which kinds of key
it found (shape id, label, connection id), never the key.
"""
from __future__ import annotations

import re
from functools import wraps
from typing import Any

from agent_friday.services import secret_patterns, secret_shapes

#: What replaces a withheld key everywhere it would have been kept.
MARKER = secret_patterns.WITHHELD

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _kinds(text: str) -> list[dict]:
    """Every listed key shape in `text`, once each, as {id, label, target}."""
    out, seen = [], set()
    for sid, label, rx, target in secret_shapes._COMPILED:
        if sid in seen:
            continue
        if rx.search(text):
            seen.add(sid)
            out.append({"id": sid, "label": label, "target": target})
    # The OpenAI shape also matches Anthropic and OpenRouter keys; a key that
    # names its own vendor is reported as that vendor only.
    ids = {k["id"] for k in out}
    if "openai" in ids and ids & {"anthropic", "openrouter"}:
        rest = re.sub(r"\bsk-(?:ant-|or-)[A-Za-z0-9_\-]{20,}", "", text)
        if not re.search(r"\bsk-(?!ant-|or-)[A-Za-z0-9_\-]{20,}", rest):
            out = [k for k in out if k["id"] != "openai"]
    return out


def withhold(text: Any) -> tuple[Any, list[dict]]:
    """(`text` with every key withheld, the kinds of key found).

    A non-string is returned unchanged with no kinds. A message that is only
    one long unbroken key-like run (a key of a vendor the shape list does not
    name) is withheld whole, as the setup chat's guard already treats it.
    """
    if not isinstance(text, str) or not text:
        return text, []
    kinds = _kinds(text)
    clean = secret_patterns.redact(text)
    if clean == text and not kinds:
        hit = secret_shapes.looks_like_secret(text)
        # A bare hex run (a hash, a commit id) or a UUID is an identifier far
        # more often than a key in conversation; the hex keys that matter
        # have shapes of their own.
        if hit and hit["id"] == secret_shapes.GENERIC[0] \
                and not re.fullmatch(r"[0-9a-fA-F]+", text.strip()) \
                and not _UUID.fullmatch(text.strip()):
            return MARKER, [hit]
        return text, []
    if not kinds:
        # Withheld by a pattern the shape list does not carry (a private key
        # block, an encoded key): still a key, of a kind the card cannot name.
        kinds = [{"id": "credential", "label": "key or credential", "target": ""}]
    return clean, kinds


def scrub(value: Any) -> Any:
    """`value` with every key withheld: a string, or the strings inside a
    dict, list or tuple (keys of a dict are left as they are)."""
    if isinstance(value, str):
        return withhold(value)[0]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub(v) for v in value)
    return value


def key_form_action(kinds: list[dict], *, reason: str = "pasted") -> dict:
    """The desktop action that opens the secure key form.

    `target` names the services/setup_connections checklist item whose secure
    field takes this key (empty when the shape does not say which service it
    is for; the form then lets the person pick). `reason` is "pasted" for a
    key that was withheld from a message, or "needed" when Friday asks for
    one it does not have.
    """
    first = next((k for k in kinds if k.get("target")), kinds[0] if kinds else {})
    labels = [k.get("label") for k in kinds if k.get("label")]
    return {"type": "key_form", "reason": reason,
            "target": first.get("target") or "",
            "label": first.get("label") or "Key",
            "kinds": labels}


def _a(label: str) -> str:
    """`label` with its article: "an Anthropic API key", "a GitHub token"."""
    return ("an " if label[:1].lower() in "aeiou" else "a ") + label


def note(kinds: list[dict]) -> str:
    """The line shown with the form. Names the kind of key, never the value."""
    label = _a((kinds[0].get("label") if kinds else "") or "key")
    more = "" if len(kinds) < 2 else " (and %d more)" % (len(kinds) - 1)
    return ("That looked like %s%s. I withheld it: it isn't in this conversation, "
            "my memory or any log. Paste it in the secure field instead; it is "
            "stored encrypted on this computer." % (label, more))


def ask_for_key(target: str, label: str, why: str = "") -> dict:
    """Friday needs a key: open the secure form for `target` on the desktop.

    For server code that finds a key missing (a provider with no key, a
    connector asked for by name). The value goes from the form straight to
    the credential endpoint on the checklist card, never through a
    conversation. Sent through the notice registry (emitter PK1), like every
    desktop notice. Returns the bus's answer (whether a page took it).
    """
    from agent_friday.services import notice_registry
    act = key_form_action([{"id": "needed", "label": label, "target": target}],
                          reason="needed")
    if why:
        act["why"] = str(why)[:300]
    return notice_registry.bus_send("PK1", [act])


#: Where a request's withheld key kinds are kept (see routes/chat for why the
#: WSGI environ and not flask.g).
REQUEST_KEYS_ENV = "friday.pasted_key_kinds"


def withholds_request_body(fn):
    """A route whose JSON body carries the owner's text (a prompt, a steer, a
    schedule, a workflow step) keeps no key from it.

    Every string in the parsed body is withheld in place before the handler
    runs, in the object every later `request.get_json` returns. A JSON reply
    then carries `key_withheld` (kinds only) and a `key_form` action, as the
    chat routes' replies do.
    """
    @wraps(fn)
    def _inner(*args, **kwargs):
        from flask import jsonify, request
        data = request.get_json(silent=True)
        if isinstance(data, (dict, list)):
            kinds = _body_kinds(data)
            if kinds:
                clean = scrub(data)
                if isinstance(data, dict):
                    data.clear()
                    data.update(clean)
                else:
                    data[:] = clean
                request.environ.setdefault(REQUEST_KEYS_ENV, []).extend(kinds)
        rv = fn(*args, **kwargs)
        kinds = request.environ.get(REQUEST_KEYS_ENV)
        if not kinds:
            return rv
        resp, extra = (rv[0], rv[1:]) if isinstance(rv, tuple) else (rv, ())
        if getattr(resp, "mimetype", "") != "application/json":
            return rv
        d = resp.get_json(silent=True)
        if not isinstance(d, dict) or "key_withheld" in d:
            return rv
        d["key_withheld"] = {"kinds": [dict(k) for k in kinds], "note": note(kinds)}
        d["actions"] = list(d.get("actions") or []) + [key_form_action(kinds)]
        out = jsonify(d)
        if extra and isinstance(extra[0], int):
            out.status_code = extra[0]
            extra = extra[1:]
        return (out, *extra) if extra else out
    return _inner


def _body_kinds(value) -> list:
    """The kinds of key in every string inside `value`, once each."""
    found, seen = [], set()

    def walk(v):
        if isinstance(v, str):
            for k in withhold(v)[1]:
                if k["id"] not in seen:
                    seen.add(k["id"])
                    found.append(k)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(value)
    return found
