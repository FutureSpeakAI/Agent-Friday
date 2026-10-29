"""What off the record does not cover: records other services keep.

North star NS-15.8-3: off the record must say plainly whether connected tools
still create records outside Friday. Off the record stops Friday writing the
conversation on this PC (services/off_record.py); it cannot stop Gmail keeping
a sent mail, Google Calendar keeping an event, Twilio keeping a call record, or
a cloud model provider receiving what Friday sends it.

`external_records()` builds one plain line per service that is connected or
enabled right now, read from the same connection checklist the privacy map
uses (services/setup_connections.checklist via privacy_map.services). Nothing
that is not connected is listed. The cloud-model line follows the routing
mode: under local-only routing chat is not sent to a cloud model, so the
general cloud line is not said; but routine background jobs the owner allowed
onto a cloud model (`scheduled_cloud.allow`, cloud_intent.background_route)
can still go there when no local model is serving, so that narrower line is
said instead, naming the provider and model. A custom OpenAI-compatible
endpoint whose address is on this PC or the local network is not an outside
service and is not named. Google names Drive when the connection covers it;
a running Cloudflare tunnel for published pages is named.

Known gap: an Ollama model with a ``:cloud`` tag is relayed by Ollama to
Ollama's own cloud service, although Friday sees it as a local endpoint; it
is not named here (the local runtime is skipped as never an outside service).

Never a secret: lines are built from service names only (the checklist never
holds a secret value, and labels are scrubbed of addresses, links and anything
token-shaped before use). Reads only; nothing here writes or sends.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

#: Always said, last: what off the record does, and what it cannot do.
ALWAYS = ("Off the record stops Friday keeping or learning from this conversation on this PC; "
          "it cannot delete records other services keep.")

#: Said when the connected services could not be read: never "nothing".
UNREADABLE = ("I couldn't tell which services are connected right now; any you connected can "
              "still keep their own records of what Friday asks them to do.")

#: Friday can open web pages whatever is connected (services/web_fetch).
WEB = "Web pages Friday opens see the request, as any website sees a visit."

SHORT_PREFIX = "Still recorded elsewhere: "

_CONNECTED = "connected"
_MAX_LABEL = 60
#: Checklist items that are forms or local-only, never an outside service.
_NOT_SERVICES = ("mcp:add", "custom:openai", "local", "local:ollama", "local:lmstudio")
_MESSAGING_CONNECTORS = ("slack", "discord")

_SCRUB = (
    re.compile(r"\S+@\S+"),                          # an address
    re.compile(r"[a-z][a-z0-9+.-]*://\S*", re.I),    # a link
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"),          # anything token-shaped
    re.compile(r"\b(?:AC|SK)[0-9a-f]{8,}\b", re.I),  # account / key ids
)


def _clean(label: Any, fallback: str) -> str:
    s = str(label or "")
    for rx in _SCRUB:
        s = rx.sub("", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip(" :;,-")
    return (s[:_MAX_LABEL].strip() or fallback)


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def routing_mode(settings: Optional[dict]) -> str:
    """The owner's model routing mode, read the way the router reads it."""
    mr = (settings or {}).get("model_routing")
    raw = mr.get("mode") if isinstance(mr, dict) else None
    try:
        from agent_friday.services import cloud_intent
        return cloud_intent.normalize_mode(raw)
    except Exception:
        return str(raw or "cloud_only").strip().lower() or "cloud_only"


def _items() -> Optional[List[dict]]:
    """The connection checklist the privacy map reads, or None if unreadable."""
    try:
        from agent_friday.services import setup_connections
        return list(setup_connections.checklist().get("items") or [])
    except Exception:
        return None


def _provider_of(model: str) -> str:
    """The display name of the service that serves `model`, or "" if unknown."""
    try:
        import agent_friday.core as core
        name = core._provider_for_model(model) or ""
    except Exception:
        return ""
    if not name:
        return ""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        label = (get_provider_registry().get_provider(name) or {}).get("label")
        if label:
            return _clean(label, name)
    except Exception:
        pass
    return _clean(name, name)


def scheduled_cloud_targets(settings: Optional[dict]) -> List[tuple]:
    """(provider, model) for each routine job the owner allowed onto a cloud
    model (`scheduled_cloud.allow`, an answered opt-in), in job order, without
    repeats. Read from `settings` the way scheduled_cloud.settings() reads it."""
    try:
        from agent_friday.services import scheduled_cloud as sc
        cfg = sc.defaults()
        jobs = list(sc.JOBS)
    except Exception:
        cfg, jobs = {}, [("sch_daily_creation", "Daily creation"), ("sch_heartbeat", "Heartbeat")]
    blk = (settings or {}).get("scheduled_cloud")
    if isinstance(blk, dict):
        cfg.update(blk)
    if not (bool(cfg.get("allow")) and bool(cfg.get("answered"))):
        return []
    out: List[tuple] = []
    for sid, _label in jobs:
        model = str(cfg.get("heartbeat_model" if sid == "sch_heartbeat" else "job_model") or "").strip()
        if not model:
            continue                 # background_route skips a job with no model
        model = re.sub(r"\s+", " ", model)[:_MAX_LABEL]
        provider = _provider_of(model)
        target = (provider, model)
        if target not in out:
            out.append(target)
    return out


def _custom_is_private(it: dict) -> bool:
    """True when a custom OpenAI-compatible endpoint's address is on this PC
    or the local network (provider_descriptors.is_private_host, no DNS lookup:
    an unresolved name stays listed). Unknown address: not private."""
    iid = str(it.get("id") or "")
    url = it.get("base_url")
    if not url:
        try:
            from agent_friday.services.provider_registry import get_provider_registry
            url = (get_provider_registry().get_provider(iid.split(":", 1)[1]) or {}).get("base_url")
        except Exception:
            url = None
    if not url or not str(url).strip():
        return False
    try:
        from agent_friday.routing.provider_descriptors import is_private_host
        return bool(is_private_host(str(url), resolve_dns=False))
    except Exception:
        return False


def _tunnel_running() -> bool:
    """True when the Cloudflare quick tunnel for published pages is up
    (services/publish_hosting). Never starts or probes anything."""
    try:
        from agent_friday.services import publish_hosting as ph
        return bool((getattr(ph, "_STATE", {}) or {}).get("tunnel")) and bool(ph._alive(getattr(ph, "_TUNNEL", None)))
    except Exception:
        return False


def _join(names: List[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _rec(key: str, services: List[str], line: str) -> dict:
    return {"key": key, "services": list(services), "line": line}


def _covers_drive(it: dict) -> bool:
    """The Google connection covers Drive: its label or a permission's scope
    or words name Drive."""
    if "drive" in str(it.get("label") or "").lower():
        return True
    for p in it.get("permissions") or []:
        if isinstance(p, dict) and ("drive" in str(p.get("scope") or "").lower()
                                    or "drive" in str(p.get("text") or "").lower()):
            return True
    return False


#: Said when a Cloudflare tunnel carries this PC's published pages.
TUNNEL = ("Published pages reach the internet through a Cloudflare tunnel; Cloudflare "
          "carries every visit and keeps its own records of them.")


def external_records(items: Optional[Iterable[dict]] = None,
                     settings: Optional[dict] = None,
                     tunnel: Optional[bool] = None) -> List[dict]:
    """One plain line per connected or enabled service that keeps its own
    record, then the line that is always said. `items` is the connection
    checklist (read live when not given); `settings` the owner's settings;
    `tunnel` whether the published-pages Cloudflare tunnel is up (read live
    when not given)."""
    settings = _settings() if settings is None else settings
    if items is None:
        items = _items()
    out: List[dict] = []
    if items is None:
        out.append(_rec("unreadable", [], UNREADABLE))
        out.append(_rec("web", [], WEB))
        out.append(_rec("always", [], ALWAYS))
        return out
    local_only = routing_mode(settings) == "local_only"
    cloud_ai: List[str] = []
    voice: List[str] = []
    search: List[str] = []
    mcp: List[str] = []
    later: List[dict] = []
    has_tunnel_line = False
    for it in items:
        if not isinstance(it, dict) or it.get("status") != _CONNECTED:
            continue
        iid = str(it.get("id") or "")
        group = str(it.get("group") or "")
        if iid in _NOT_SERVICES or iid.startswith("local:"):
            continue
        if iid == "google":
            out.append(_rec("gmail", ["Gmail"],
                            "Mail you send through Gmail is kept in your Gmail Sent folder, "
                            "and by the people you send it to."))
            out.append(_rec("google_calendar", ["Google Calendar"],
                            "Calendar events Friday creates or changes stay in Google Calendar, "
                            "and invitations reach the people invited."))
            if _covers_drive(it):
                out.append(_rec("google_other", ["Google Drive", "Google Tasks", "Google Contacts"],
                                "Files Friday creates or changes in Google Drive, and changes it makes "
                                "to Google Tasks and Contacts, stay in your Google account."))
            else:
                out.append(_rec("google_other", ["Google Tasks", "Google Contacts"],
                                "Changes Friday makes to Google Tasks and Contacts stay in your "
                                "Google account."))
        elif iid == "cloudflare":
            has_tunnel_line = True
        elif iid == "twilio":
            later.append(_rec("twilio", ["Twilio"],
                              "Phone calls and texts go through Twilio, which keeps its own call and "
                              "message records; the person you call or text keeps theirs."))
        elif group == "ai" and (iid.startswith("provider:") or iid.startswith("custom:")):
            if iid.startswith("custom:") and _custom_is_private(it):
                continue                 # on this PC or the local network
            cloud_ai.append(_clean(it.get("label"), "a cloud AI provider"))
        elif group == "voice" and iid.startswith("provider:"):
            voice.append(_clean(it.get("label"), "a cloud voice service"))
        elif group == "search" and iid.startswith("provider:"):
            search.append(_clean(it.get("label"), "a web search service"))
        elif iid.startswith("channel:"):
            name = _clean(re.sub(r"\s*\(talk to Friday\)\s*$", "", str(it.get("label") or "")),
                          "that messaging service")
            later.append(_rec(iid, [name], "Messages sent through %s are kept by %s and by the "
                                           "person who receives them." % (name, name)))
        elif iid.startswith("connector:"):
            key = iid.split(":", 1)[1]
            name = _clean(it.get("label"), "that service")
            if key == "discord":
                name = "Discord"
            if key in _MESSAGING_CONNECTORS:
                later.append(_rec(iid, [name], "Messages Friday posts to %s are kept by %s and seen by "
                                               "the people in that channel." % (name, name)))
            else:
                later.append(_rec(iid, [name], "Anything Friday creates or changes in %s stays in %s."
                                  % (name, name)))
        elif iid.startswith("platform:"):
            name = _clean(it.get("label"), "that platform")
            later.append(_rec(iid, [name], "Posts Friday publishes to %s stay on %s, where others can "
                                           "see and copy them." % (name, name)))
        elif iid.startswith("mcp:") and (it.get("connect") or {}).get("remote"):
            mcp.append(_clean(it.get("label"), "a tool server"))
    if cloud_ai and not local_only:
        out.append(_rec("cloud_ai", cloud_ai,
                        "Cloud AI providers you connected (%s) receive what Friday sends them and keep "
                        "it under their own retention rules." % _join(cloud_ai)))
    if local_only:
        # Local-only routing still lets the routine jobs the owner allowed onto
        # a cloud model go there when no local model is serving
        # (cloud_intent.background_route), so that much is said.
        targets = scheduled_cloud_targets(settings)
        if targets:
            names: List[str] = []
            for prov, model in targets:
                if (prov or model) not in names:
                    names.append(prov or model)
            out.append(_rec("scheduled_cloud", names,
                            "Scheduled jobs you allowed onto %s are sent there when no local "
                            "model is serving, and kept under that provider's own retention "
                            "rules." % _join(["%s/%s" % (p, m) if p else m for p, m in targets])))
    if voice and not local_only:
        out.append(_rec("cloud_voice", voice,
                        "Text Friday speaks in a cloud voice is sent to %s, which keeps it under its "
                        "own rules." % _join(voice)))
    out.extend(later)
    if search:
        out.append(_rec("search", search, "Web searches go to %s, which sees what was searched."
                        % _join(search)))
    if mcp:
        out.append(_rec("mcp", mcp, "Tool servers you connected over the internet (%s) see what "
                                    "Friday asks them and may keep their own record." % _join(mcp)))
    if tunnel is None:
        tunnel = _tunnel_running()
    if has_tunnel_line or tunnel:
        out.append(_rec("cloudflare_tunnel", ["Cloudflare"], TUNNEL))
    # Said in full, but not named in the short banner list: a visit is seen,
    # not a record Friday made somewhere.
    out.append(_rec("web", [], WEB))
    out.append(_rec("always", [], ALWAYS))
    return out


def view(items: Optional[Iterable[dict]] = None, settings: Optional[dict] = None,
         tunnel: Optional[bool] = None) -> Dict[str, Any]:
    """The records, the short "Still recorded elsewhere" line for the banner,
    and whether off the record is on now. `items` may be the checklist the
    caller already read (privacy_map.view), so it is not built twice."""
    settings = _settings() if settings is None else settings
    records = external_records(items, settings, tunnel)
    names: List[str] = []
    for r in records:
        for n in r["services"]:
            if n not in names:
                names.append(n)
    try:
        from agent_friday.services import off_record
        on = bool(off_record.active(settings))
    except Exception:
        on = None
    return {"on": on, "records": records, "services": names,
            "short": (SHORT_PREFIX + ", ".join(names)) if names else "",
            "lines": [r["line"] for r in records], "always": ALWAYS,
            "readable": not any(r["key"] == "unreadable" for r in records)}


#: Said first when a write that is not the on-screen switch turns it on.
TURNED_ON = "You're off the record."


def turned_on(before: Optional[dict], after: Optional[dict]) -> bool:
    """True when this change switched off the record from off to on."""
    return (not bool((before or {}).get("off_record"))) and bool((after or {}).get("off_record"))


def reply(before: Optional[dict], after: Optional[dict], items: Optional[Iterable[dict]] = None,
          tunnel: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """Owner ruling 12 (2026-10-28): turning off the record on first shows the
    services that keep their own records. On screen that is a confirm before
    the setting is written (index.html, FridayOffRecordConfirmHost); a write
    from anywhere else (the settings API a phone or script calls) is not
    blocked, but what it gets back says the same short line and the line about
    what off the record cannot delete. None when off the record was not just
    turned on. Never raises; an unreadable list is said, never skipped."""
    if not turned_on(before, after):
        return None
    try:
        v = view(items, after or {}, tunnel)
    except Exception:
        v = {"short": "", "lines": [UNREADABLE, ALWAYS], "readable": False}
    short = str(v.get("short") or "")
    readable = bool(v.get("readable"))
    parts = [TURNED_ON]
    if short:
        parts.append(short + ".")
    if not readable:
        parts.append(UNREADABLE)
    parts.append(ALWAYS)
    return {"said": " ".join(parts), "short": short, "lines": list(v.get("lines") or []),
            "readable": readable, "always": ALWAYS}
