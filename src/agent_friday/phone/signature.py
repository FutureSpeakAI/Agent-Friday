"""X-Twilio-Signature: Twilio's proof that a webhook came from Twilio.

WHICH CREDENTIAL. Twilio signs webhooks with the account's primary AUTH TOKEN
(HMAC-SHA1, base64). An API key and its secret cannot validate a signature; they
authenticate REST calls this machine makes to Twilio, which is the other
direction. So the phone needs two secrets, both kept in the vault: the API key
secret (outbound) and the auth token (inbound).

THE ALGORITHM, as Twilio's own `RequestValidator` implements it:

    s = <the full URL Twilio requested, query string included>
    for name in sorted(unique param names):
        for value in sorted(unique values of that name):
            s += name + value
    signature = base64(HMAC-SHA1(auth_token, s))

JSON bodies are not in the param list. Twilio adds a `bodySHA256` query
parameter (hex SHA-256 of the raw body), signs the URL alone, and the receiver
checks the hash separately. A WebSocket upgrade (Media Streams) is signed as a
request with no params.

Twilio is inconsistent about whether the URL it signs carries the default port,
so a URL is accepted with or without ":443" / ":80". Both forms are checked
against the same token; neither weakens the check.

THE URL IS NEVER TAKEN FROM THE REQUEST'S Host HEADER. Behind a tunnel the
origin sees `http://` and whatever Host the tunnel forwards; a URL rebuilt from
those would not match what Twilio signed, and trusting a request header to name
the URL being verified would let the request choose what it is checked against.
The ingress passes the public base URL from settings plus the request path.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Iterable, Mapping, Sequence, Tuple, Union
from urllib.parse import parse_qs, urlparse

Params = Union[Mapping[str, object], Sequence[Tuple[str, str]], None]


def _pairs(params: Params) -> list:
    """(name, value) pairs from a dict, a Flask MultiDict, or a list of pairs."""
    if not params:
        return []
    if hasattr(params, "items") and hasattr(params, "getlist"):
        return [(str(k), str(v)) for k, v in params.items(multi=True)]  # type: ignore[call-arg]
    if isinstance(params, Mapping):
        out = []
        for k, v in params.items():
            if isinstance(v, (list, tuple)):
                out.extend((str(k), str(x)) for x in v)
            else:
                out.append((str(k), str(v)))
        return out
    return [(str(k), str(v)) for k, v in params]


def compute(token: str, url: str, params: Params = None) -> str:
    """The signature Twilio would send for this URL and these params."""
    grouped: dict = {}
    for k, v in _pairs(params):
        grouped.setdefault(k, set()).add(v)
    s = url
    for name in sorted(grouped):
        for value in sorted(grouped[name]):
            s += name + value
    mac = hmac.new(token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("ascii")


def _url_variants(url: str) -> list:
    """The URL as given, plus with the default port added or removed."""
    p = urlparse(url)
    host = p.hostname or ""
    if ":" in host:                                  # IPv6 literal
        host = "[%s]" % host
    userinfo = p.netloc.rsplit("@", 1)[0] + "@" if "@" in p.netloc else ""
    default = 443 if p.scheme == "https" else 80
    port = p.port or default
    with_port = p._replace(netloc="%s%s:%d" % (userinfo, host, port)).geturl()
    without = p._replace(netloc="%s%s" % (userinfo, host)).geturl()
    out = [url]
    for u in (without, with_port):
        if u not in out:
            out.append(u)
    return out


def body_hash_ok(url: str, raw_body: bytes) -> bool:
    """For a JSON webhook: the body matches the `bodySHA256` Twilio signed."""
    q = parse_qs(urlparse(url).query)
    want = (q.get("bodySHA256") or [""])[0]
    if not want:
        return False
    got = hashlib.sha256(raw_body or b"").hexdigest()
    return hmac.compare_digest(got, want.lower())


def validate(tokens: Union[str, Iterable[str]], url: str, params: Params,
             signature: str, *, raw_json_body: bytes | None = None) -> bool:
    """True only when `signature` is Twilio's signature of this request.

    `tokens` may hold more than one auth token so a rotation (old and new both
    live for a short window) does not drop calls. An empty token, an empty
    signature, or no token at all is False: this fails closed.

    For a JSON body pass `raw_json_body` and no params; the body must match the
    URL's `bodySHA256`, which is itself covered by the signature.
    """
    if isinstance(tokens, str):
        tokens = [tokens]
    tokens = [t for t in (tokens or []) if t]
    sig = (signature or "").strip()
    if not tokens or not sig or not url:
        return False
    if raw_json_body is not None:
        if not body_hash_ok(url, raw_json_body):
            return False
        params = None
    for token in tokens:
        for candidate in _url_variants(url):
            if hmac.compare_digest(compute(token, candidate, params), sig):
                return True
    return False
