"""Media diet notes: the owner's media preferences, heard in conversation,
proposed for approval, and enforced everywhere News reaches them.

Friday hears a preference ("never cite Fox News") and calls
`media_diet_note`. That only *proposes*: an approval card in the Media diet
shows the change as a diff. Nothing is applied until the owner approves it;
a rejected proposal is dropped. An approved rule is written here, with a
governance receipt, and from then on `enforce` removes what it names from
every edition, briefing, podcast and Discuss answer, appending a receipt of
what it removed (`diet_receipts.jsonl`).

Rules: "block" (never fetch, cite or suggest the outlet) and "prefer" (the
outlet is offered first). An outlet is a domain; a spoken name ("Fox News")
is resolved to one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

_log = logging.getLogger("friday.media_diet")

APPROVAL_KIND = "media_diet_rule"
SUBJECT_TYPE = "media_diet"
KINDS = {"block": "Never cite", "prefer": "Prefer"}
#: Where an approved rule holds.
APPLIES_TO = "the Front Page, the Briefing, the Weekly, podcasts and Discuss"


def _dir() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "news"


def rules_path() -> Path:
    return _dir() / "media_diet.json"


def receipts_path() -> Path:
    return _dir() / "diet_receipts.jsonl"


# ── outlets ─────────────────────────────────────────────────────────────────

def outlet_domain(name: str) -> str:
    """A domain for an outlet named by its domain, a link, or its spoken name."""
    n = (name or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?:/|$)", n.lower())
    if m:
        return m.group(1)
    from agent_friday.services.podcast_quality import OUTLET_NAMES
    low = n.lower()
    for dom, names in OUTLET_NAMES.items():
        if low in (x.lower() for x in names):
            return dom
    squashed = re.sub(r"[^a-z0-9]", "", low)
    return (squashed + ".com") if squashed else ""


def _spoken(rule: dict) -> str:
    return "%s %s" % (KINDS.get(rule["kind"], rule["kind"]), rule.get("name") or rule["outlet"])


def _matches(item: dict, domain: str, name: str) -> bool:
    """An item is the outlet's when its source or link is the domain, or an
    aggregator's item names the outlet as its publisher."""
    src = (item.get("source") or item.get("outlet") or "").lower().removeprefix("www.")
    url = (item.get("url") or "").lower()
    if src == domain or src.endswith("." + domain) or re.search(r"//(?:[a-z0-9-]+\.)*%s(?:/|$)" % re.escape(domain), url):
        return True
    if name:
        tail = (item.get("snippet") or item.get("text") or "").rstrip().lower()
        return tail.endswith(name.lower())
    return False


# ── rules ───────────────────────────────────────────────────────────────────

def rules() -> list[dict]:
    try:
        data = json.loads(rules_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in data.get("rules") or [] if isinstance(r, dict)]


def _save(rs: list[dict]) -> None:
    p = rules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"rules": rs}, indent=2), encoding="utf-8")


def blocked() -> list[dict]:
    return [r for r in rules() if r.get("kind") == "block"]


def enforce(items: list[dict], where: str) -> tuple[list[dict], list[dict]]:
    """(kept, removed) for one place a rule holds; a removal leaves a receipt."""
    rs = blocked()
    if not rs:
        return list(items), []
    kept, removed, why = [], [], {}
    for it in items:
        hit = next((r for r in rs if _matches(it, r["outlet"], r.get("name") or "")), None)
        if hit is None:
            kept.append(it)
        else:
            removed.append(it)
            why.setdefault(_spoken(hit), []).append(it.get("title") or it.get("url") or "")
    for rule, titles in why.items():
        _receipt({"where": where, "rule": rule, "removed": titles})
    return kept, removed


def enforce_docs(docs: list[dict], where: str) -> list[dict]:
    """A podcast's or Discuss's source documents with blocked outlets removed;
    Friday's own notes and the calendar are never an outlet's."""
    news = [d for d in docs if d.get("outlet")]
    kept, _removed = enforce(news, where)
    keep = {id(d) for d in kept}
    return [d for d in docs if not d.get("outlet") or id(d) in keep]


def _receipt(entry: dict) -> None:
    try:
        p = receipts_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(entry, at=time.strftime("%Y-%m-%dT%H:%M:%S")), ensure_ascii=False) + "\n")
    except OSError as e:
        _log.warning("media diet receipt not written: %s", e)


def recent_receipts(limit: int = 20) -> list[dict]:
    try:
        lines = receipts_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return list(reversed(out))


# ── proposals ───────────────────────────────────────────────────────────────

def describe(rule: dict) -> str:
    return "\n".join([
        "Media diet",
        "+ %s (%s)" % (_spoken(rule), rule["outlet"]),
        "Heard: “%s”" % rule["said"] if rule.get("said") else "",
        "Applies to %s, from the next run." % APPLIES_TO,
        "Approve to apply it; reject to drop it. Nothing changes until you approve.",
    ]).replace("\n\n", "\n")


def propose(kind: str, outlet: str, *, said: str = "",
            requested_by: str = "friday:media_diet_note") -> dict:
    """Raise (or return the pending) approval card for one rule. Writes nothing."""
    from agent_friday.services import approvals as ap
    kind = kind if kind in KINDS else "block"
    domain = outlet_domain(outlet)
    if not domain:
        raise ValueError("name the outlet: a name like “Fox News” or a site like foxnews.com")
    name = outlet.strip() if not re.search(r"\.[a-z]{2,}$", outlet.strip().lower()) else ""
    rule = {"kind": kind, "outlet": domain, "name": name, "said": (said or "").strip()[:200]}
    fp = hashlib.sha256(json.dumps([kind, domain], sort_keys=True).encode()).hexdigest()[:16]
    existing = ap.find_for_subject(SUBJECT_TYPE, fp, APPROVAL_KIND)
    subject = fp
    if existing is not None:
        if existing.get("status") == "pending":
            return existing
        subject = "%s:%d" % (fp, int(time.time()))
    text = describe(rule)
    return ap.create_approval(kind=APPROVAL_KIND, subject_type=SUBJECT_TYPE, subject_id=subject,
                              title="Media diet: %s" % _spoken(rule), description=text,
                              action_description=text, force_gate=True,
                              payload={"handler": "media_diet", "rule": rule}, requested_by=requested_by)


def pending() -> list[dict]:
    from agent_friday.services import approvals as ap
    return ap.list_approvals(status="pending", kind=APPROVAL_KIND)


def apply_approved(approval_id: str, actor: str = "owner") -> dict:
    """Write the rule an approved card names. The only writer of rules."""
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals as ap
    rec = ap.get_approval(approval_id) or {}
    if rec.get("kind") != APPROVAL_KIND or rec.get("status") != "approved" or rec.get("consumed"):
        raise ValueError("that card is not an approved, unused media diet rule; nothing was applied")
    rule = (rec.get("payload") or {}).get("rule") or {}
    if rule.get("kind") not in KINDS or not rule.get("outlet"):
        raise ValueError("the card's rule is malformed; nothing was applied")
    action_gate.record_external("news:media_diet_rule", surface="media_diet",
                                approval_id=approval_id, target=_spoken(rule))
    rs = [r for r in rules() if not (r.get("outlet") == rule["outlet"])]
    rs.append(dict(rule, approval_id=approval_id, approved_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
    _save(rs)
    ap.mark_used(approval_id, actor, {"rule": _spoken(rule)})
    return rule


def remove(outlet: str) -> bool:
    """The owner takes a rule back (from the Media diet panel)."""
    domain = outlet_domain(outlet)
    rs = rules()
    left = [r for r in rs if r.get("outlet") != domain]
    if len(left) == len(rs):
        return False
    _save(left)
    return True


def _on_decision(record: dict) -> None:
    if record.get("kind") != APPROVAL_KIND or (record.get("status") or "").lower() != "approved":
        return                          # a rejection is simply dropped
    try:
        apply_approved(record["approval_id"], actor=record.get("decided_by") or "owner")
    except Exception as e:
        _log.warning("media diet rule not applied: %s", e)


def register_hooks() -> None:
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook(APPROVAL_KIND, _on_decision)
    except Exception as e:
        _log.warning("could not register the media diet hook: %s", e)


register_hooks()


# ── the tool ────────────────────────────────────────────────────────────────

def tool_media_diet_note(inp: dict) -> str:
    try:
        rec = propose(str(inp.get("kind") or "block"), str(inp.get("outlet") or ""),
                      said=str(inp.get("said") or ""))
    except ValueError as e:
        return json.dumps({"status": "error", "say": str(e)})
    return json.dumps({"status": "proposed", "approval_id": rec.get("approval_id"),
                       "say": "I've put that in your Media diet for you to approve: %s."
                              % rec.get("title", "").removeprefix("Media diet: ")})


TOOLS = [{
    "name": "media_diet_note",
    "description": ("Note a media preference the user states in conversation (\"never cite Fox "
                    "News\", \"I prefer Reuters\") as a proposal in their Media diet. It is only "
                    "a proposal: an approval card; nothing changes until they approve it. Use it "
                    "whenever they say which outlets to use or avoid."),
    "input_schema": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["block", "prefer"]},
        "outlet": {"type": "string", "description": "the outlet's name or site, e.g. \"Fox News\" or foxnews.com"},
        "said": {"type": "string", "description": "what the user said, in their words"}},
        "required": ["outlet"]},
}]
HANDLERS = {"media_diet_note": tool_media_diet_note}
#: Ring 1: it writes only a proposal in the owner's own approvals.
RINGS = {"media_diet_note": 1}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
