"""Explicit public DNS observation. No returned address is ever contacted.

Uses Google's fixed JSON DoH endpoint, documented at
developers.google.com/speed/public-dns/docs/doh/json. This is one recursive
resolver's observation, not proof of worldwide propagation or HTTPS health.
"""
from __future__ import annotations

import ipaddress
import re
import time

from agent_friday.user_errors import UserFacingValueError
from agent_friday.services import namecom, sites_privacy

TYPES = {"A": 1, "AAAA": 28, "CNAME": 5, "MX": 15, "TXT": 16, "SRV": 33}


def _query(hostname, kind, *, generation):
    import requests
    sites_privacy.require_generation(generation)
    try:
        response = requests.get("https://dns.google/resolve", params={"name": hostname, "type": TYPES[kind], "edns_client_subnet": "0.0.0.0/0"},
                                timeout=(3, 5), allow_redirects=False)
        try:
            sites_privacy.require_generation(generation)
            if response.status_code != 200 or len(response.content) > 100_000:
                raise UserFacingValueError("The public DNS resolver did not return a usable response.")
            data = response.json()
        finally:
            try:
                response.close()
            finally:
                sites_privacy.require_generation(generation)
    except requests.RequestException:
        sites_privacy.require_generation(generation)
        raise UserFacingValueError("The public DNS resolver is unavailable. No DNS result was verified.") from None
    if not isinstance(data, dict) or data.get("Status") not in {0, 3} or data.get("TC"):
        raise UserFacingValueError("Public DNS is unresolved or returned an incomplete answer.")
    return [r["data"] for r in data.get("Answer", []) if isinstance(r, dict) and r.get("type") == TYPES[kind]
            and str(r.get("name", "")).rstrip(".").lower() == hostname and isinstance(r.get("data"), str)]


def _answer(kind, value):
    if kind in {"A", "AAAA"}:
        return str(ipaddress.ip_address(value))
    if kind == "CNAME":
        return value.rstrip(".").lower()
    if kind in {"MX", "SRV"}:
        return " ".join(value.lower().rstrip(".").split())
    if kind == "TXT":
        parts = re.findall(r'"((?:\\.|[^"\\])*)"', value)
        if not parts:
            return value
        def unescape(match):
            text = match.group(1)
            return chr(int(text)) if text.isdigit() and len(text) == 3 and int(text) <= 255 else text
        return re.sub(r"\\([0-9]{3}|.)", unescape, "".join(parts))
    return value


def verify(domain, required, *, generation):
    sites_privacy.require_generation(generation)
    domain = namecom.domain_name(domain)
    if not isinstance(required, list) or not 1 <= len(required) <= 20:
        raise UserFacingValueError("Check between 1 and 20 explicit DNS records.")
    records = [namecom.record_payload(r) for r in required]
    grouped = {}
    for record in records:
        host = (record["host"] + "." if record["host"] else "") + domain
        if len(host) > 253:
            raise UserFacingValueError("The complete DNS hostname is too long.")
        grouped.setdefault((host, record["type"]), []).append(record)
    checks = []
    for (host, kind), wanted in grouped.items():
        sites_privacy.require_generation(generation)
        if kind not in TYPES:
            checks.append({"hostname": host, "type": kind, "matches": False, "status": "not_directly_observable",
                           "message": "ANAME is a provider-specific alias; inspect its synthesized A/AAAA records separately."})
            continue
        answers = _query(host, kind, generation=generation)
        sites_privacy.require_generation(generation)
        observed = sorted({_answer(kind, answer) for answer in answers})
        expected = sorted({_answer(kind, (str(r["priority"]) + " " if kind in {"MX", "SRV"} else "") + r["answer"]) for r in wanted})
        checks.append({"hostname": host, "type": kind, "required": expected, "observed": observed,
                       "matches": observed == expected, "status": "matches" if observed == expected else "pending_or_different"})
    sites_privacy.require_generation(generation)
    return {"status": "ok", "matches": all(c["matches"] for c in checks), "checks": checks, "checked_at": time.time(),
            "resolver": "Google Public DNS", "source": "https://dns.google/resolve", "https": "not_checked",
            "expectation_source": "supplied_records", "site_verification_updated": False,
            "message": "This checks one public recursive DNS resolver. It does not prove worldwide propagation or HTTPS readiness."}
