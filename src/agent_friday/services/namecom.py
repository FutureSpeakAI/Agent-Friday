"""Narrow Name.com Core v1 transport. Credentials never enter URLs or errors.

Wire contract: docs.name.com/api/v1/reference/{domains,dns}, Core v1.
Only the operations below are exposed; registrar acquisition and transfers
are deliberately absent. Mutation failures are never automatically retried.
"""
from __future__ import annotations

import ipaddress
import re
import threading
import time
from collections import deque
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

BASES = {"production": "https://api.name.com/core/v1", "sandbox": "https://api.dev.name.com/core/v1"}
_LIMIT_LOCK = threading.Lock()
_CALLS: dict[str, deque] = {}


class ProviderError(ValueError):
    def __init__(self, message, *, status=None, ambiguous=False, retry_after=None):
        super().__init__(message)
        self.status = status
        self.ambiguous = ambiguous
        self.retry_after = retry_after


def domain_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Choose a domain name.")
    try:
        name = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("That domain name is not valid.") from None
    if len(name) > 253 or "." not in name or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in name.split(".")):
        raise ValueError("Use a domain name without a URL, path or port.")
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise ValueError("Use a registered domain, not an IP address.")


def record_id(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) < 2**31:
        raise ValueError("Choose a valid DNS record ID.")
    return int(value)


def record_payload(value):
    if not isinstance(value, dict) or set(value) - {"host", "type", "answer", "ttl", "priority"}:
        raise ValueError("Supply host, type, answer, TTL and, when needed, priority.")
    if not isinstance(value.get("host", ""), str) or not isinstance(value.get("type"), str):
        raise ValueError("Supply a text hostname and DNS record type.")
    host = value.get("host", "").strip().lower()
    if host == "@":
        host = ""
    if len(host) > 253 or (host and any(not re.fullmatch(r"(?:\*|_?[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?)", p) for p in host.split("."))):
        raise ValueError("Use a relative DNS hostname.")
    kind = value["type"].upper()
    # NS mutations change delegation and are outside this operation surface.
    if kind not in {"A", "AAAA", "ANAME", "CNAME", "MX", "SRV", "TXT"}:
        raise ValueError("Choose A, AAAA, ANAME, CNAME, MX, SRV or TXT.")
    answer = value.get("answer")
    if not isinstance(answer, str) or not answer or len(answer) > 4096 or any(ord(c) < 32 for c in answer):
        raise ValueError("Supply a valid DNS answer.")
    if kind in {"A", "AAAA"}:
        try:
            ip = ipaddress.ip_address(answer)
        except ValueError:
            raise ValueError("That DNS answer is not a valid IP address.") from None
        if ip.version != (4 if kind == "A" else 6):
            raise ValueError("The IP version does not match the DNS record type.")
        answer = str(ip)
    elif kind in {"ANAME", "CNAME", "MX"}:
        answer = domain_name(answer) + "."
    elif kind == "SRV":
        parts = answer.split()
        if len(parts) != 3 or not all(p.isdigit() and int(p) <= 65535 for p in parts[:2]):
            raise ValueError("SRV answer must contain weight, port and target.")
        answer = "%s %s %s." % (int(parts[0]), int(parts[1]), domain_name(parts[2]))
    ttl = value.get("ttl", 300)
    if type(ttl) is not int or not 300 <= ttl <= 2147483647:
        raise ValueError("DNS TTL must be an integer of at least 300 seconds.")
    result = {"host": host, "type": kind, "answer": answer, "ttl": ttl}
    if kind in {"MX", "SRV"}:
        priority = value.get("priority")
        if type(priority) is not int or not 0 <= priority <= 65535:
            raise ValueError("MX and SRV records need a priority between 0 and 65535.")
        result["priority"] = priority
    return result


def amount(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("The provider did not return a valid renewal price.") from None
    if not number.is_finite() or number < 0 or number > 1000000:
        raise ValueError("The provider did not return a valid renewal price.")
    return str(number.quantize(Decimal("0.01")))


def _admit(account_id):
    now = time.monotonic()
    with _LIMIT_LOCK:
        calls = _CALLS.setdefault(account_id, deque())
        while calls and calls[0] < now - 3600:
            calls.popleft()
        if len(calls) >= 3000 or sum(t > now - 1 for t in calls) >= 20:
            raise ProviderError("Name.com request limit reached. Try again later.", status=429)
        calls.append(now)


class Client:
    def __init__(self, account_id, username, token, environment="production", *, _request_guard=None):
        if environment not in BASES:
            raise ValueError("Choose production or sandbox.")
        self.account_id, self.username, self._token = account_id, username, token
        self.base = BASES[environment]
        self._request_guard = _request_guard

    def _check_current(self):
        if self._request_guard is not None:
            self._request_guard()

    def _request(self, method, path, *, params=None, body=None, idempotency_key=None):
        import requests
        self._check_current()
        _admit(self.account_id)
        headers = {"Accept": "application/json", "User-Agent": "Friday-Domains/1"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        self._check_current()
        try:
            response = requests.request(method, self.base + path, params=params, json=body,
                auth=(self.username, self._token), headers=headers, timeout=(5, 20), allow_redirects=False)
        except (requests.RequestException, UnicodeError):
            self._check_current()
            raise ProviderError("Name.com did not confirm the request. Check its recorded result before retrying.", ambiguous=method != "GET") from None
        try:
            self._check_current()
            status = response.status_code
            if not 200 <= status < 300:
                retry = response.headers.get("Retry-After", "")
                retry = min(int(retry), 3600) if str(retry).isdigit() else None
                labels = {401: "Name.com could not authenticate this account.", 403: "Name.com refused this operation. Check API access in account settings.",
                          404: "Name.com did not find that domain or record in this account.", 429: "Name.com rate limit reached. Try again later."}
                raise ProviderError(labels.get(status, "Name.com returned an unsuccessful response (HTTP %s)." % status),
                    status=status, ambiguous=method != "GET" and (status >= 500 or status in {408, 409}), retry_after=retry)
            if status == 204 or not response.content:
                return {}
            if len(response.content) > 4_000_000:
                raise ProviderError("Name.com returned more data than this operation allows.", ambiguous=method != "GET")
            try:
                data = response.json()
            except ValueError:
                raise ProviderError("Name.com returned an unreadable response.", ambiguous=method != "GET") from None
            if not isinstance(data, dict):
                raise ProviderError("Name.com returned an unexpected response.", ambiguous=method != "GET")
            self._check_current()
            return data
        finally:
            try:
                response.close()
            finally:
                self._check_current()

    def _pages(self, path, key):
        rows, page, seen = [], 1, set()
        for _ in range(100):
            self._check_current()
            if page in seen:
                raise ProviderError("Name.com pagination did not complete.")
            seen.add(page)
            data = self._request("GET", path, params={"page": page, "perPage": 1000})
            self._check_current()
            values = data.get(key)
            if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                raise ProviderError("Name.com returned an incomplete inventory.")
            rows.extend(values)
            next_page = data.get("nextPage")
            if not next_page:
                self._check_current()
                return rows
            if type(next_page) is not int or next_page <= page:
                raise ProviderError("Name.com pagination did not complete.")
            page = next_page
        raise ProviderError("Name.com inventory exceeded the supported page limit.")

    def hello(self):
        self._request("GET", "/hello")

    def domains(self):
        return self._pages("/domains", "domains")

    def domain(self, domain):
        return self._request("GET", "/domains/" + quote(domain_name(domain), safe=""))

    def records(self, domain):
        return self._pages("/domains/" + domain_name(domain) + "/records", "records")

    def put_record(self, domain, record, rid=None):
        payload = record_payload(record)
        path = "/domains/" + domain_name(domain) + "/records"
        if rid is not None:
            payload["id"] = record_id(rid)
            path += "/" + str(payload["id"])
        return self._request("PUT" if rid is not None else "POST", path, body=payload)

    def delete_record(self, domain, rid):
        return self._request("DELETE", "/domains/" + domain_name(domain) + "/records/" + str(record_id(rid)))

    def pricing(self, domain, years):
        if type(years) is not int or not 1 <= years <= 10:
            raise ValueError("Choose a renewal term from 1 to 10 years.")
        return self._request("GET", "/domains/" + domain_name(domain) + ":getPricing", params={"years": years})

    def autorenew(self, domain, enabled):
        if type(enabled) is not bool:
            raise ValueError("Choose whether auto-renew is on or off.")
        return self._request("PATCH", "/domains/" + domain_name(domain), body={"autorenewEnabled": enabled})
