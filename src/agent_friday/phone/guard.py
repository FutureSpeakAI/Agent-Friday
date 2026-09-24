"""Rate limits and replay keys for the public phone ingress.

Every request that reaches the ingress is from the internet. Twilio's signature
proves authorship; it does not stop someone replaying a request they captured,
and it does not stop a flood of unsigned requests from costing CPU. This module
does both jobs, and FAILS CLOSED: an error in here rejects the request.

Rate limits are token buckets held in memory, keyed by the client address the
tunnel reports (`CF-Connecting-IP`). That header is used ONLY to share out the
budget fairly; it is never evidence of who the caller is, and a caller who lies
about it only moves themselves into a different bucket of the same global
limit.

Unsigned or badly signed requests draw from a much smaller bucket than signed
ones, so a scan of the public hostname runs out quickly without affecting
Twilio's own traffic.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Iterable, Tuple


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float):
        self.rate = float(rate_per_s)
        self.burst = float(burst)
        self._tokens: dict = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None, cost: float = 1.0) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._tokens.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= cost
            if ok:
                tokens -= cost
            self._tokens[key] = (tokens, now)
            if len(self._tokens) > 10000:            # bound memory under a scan
                self._tokens = {k: v for k, v in self._tokens.items()
                                if now - v[1] < 3600}
            return ok


class IngressGuard:
    """The limits one ingress applies. Defaults suit one person's phone line:
    a handful of events a minute is normal, hundreds is not."""

    def __init__(self, *, per_client_per_min: float = 60, global_per_min: float = 240,
                 bad_per_client_per_min: float = 6, bad_global_per_min: float = 30):
        self.client = TokenBucket(per_client_per_min / 60.0, per_client_per_min / 2)
        self.global_ = TokenBucket(global_per_min / 60.0, global_per_min / 2)
        self.bad_client = TokenBucket(bad_per_client_per_min / 60.0, bad_per_client_per_min)
        self.bad_global = TokenBucket(bad_global_per_min / 60.0, bad_global_per_min)

    def admit(self, client_key: str, now: float | None = None) -> bool:
        """Before any work: is there budget for one more request?"""
        return (self.client.allow(client_key, now)
                and self.global_.allow("*", now))

    def record_bad(self, client_key: str, now: float | None = None) -> bool:
        """After a failed signature. False means this client (or everyone) is
        over the bad-request budget; the ingress then answers 429 instead of
        403, and does no more work for it."""
        a = self.bad_client.allow(client_key, now)
        b = self.bad_global.allow("*", now)
        return a and b


def replay_key(url: str, params: Iterable[Tuple[str, str]], signature: str) -> str:
    """A stable hash of one signed request.

    Two deliveries of the same webhook (a replay, or Twilio retrying) hash the
    same; anything that changes the URL, a parameter or the signature does not.
    """
    h = hashlib.sha256()
    h.update(url.encode("utf-8"))
    for k, v in sorted(params):
        h.update(b"\0" + k.encode("utf-8") + b"=" + v.encode("utf-8"))
    h.update(b"\0sig=" + (signature or "").encode("utf-8"))
    return h.hexdigest()
