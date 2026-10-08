"""Shared Sites and domain operations for governed chat and voice callers."""
from __future__ import annotations

import json


SITE_ACTIONS = ("list", "inspect", "open", "save", "build", "preview", "prepare_publish",
                "deployment_status", "hosting_requirements", "operation")
DOMAIN_ACTIONS = ("accounts", "inventory", "open", "sync", "inspect", "records", "verify", "operation",
                  "reconcile", "prepare_dns", "prepare_renewal", "prepare_autorenew")

SITE_GUIDE = {
    "purpose": "One saved site connects a project repository, build, preview, published version and domain.",
    "procedure": [
        "List or inspect the site in the current conversation and project before changing it.",
        "Use open with the exact site_id to show its Sites panel. Report the returned navigation acknowledgement honestly.",
        "Use an existing managed codebase and a named GitHub Pages hosting connection. Connect credentials in Sites, never in conversation text.",
        "Save the build root, explicit build command and static output directory. Inspect the returned revision before the next change.",
        "Save hosting or domain as null to remove that local binding; this does not delete a hosted site or change DNS.",
        "Build raises a review for the exact command and source. An approved build runs separately from the source checkout.",
        "Use preview with site_id and build_id to open the exact completed build in Sites before preparing publication. A navigation acknowledgement does not verify its rendering.",
        "Publishing requires its own review of the fixed files and destination. Check deployment_status afterward.",
        "Use hosting_requirements to obtain the host's domain setup, then domain_action to review the exact DNS change in the selected account.",
        "A previous immutable build can be published again through prepare_publish. A failed new build does not remove the previous live version.",
    ],
    "limits": [
        "Saved Sites publish static output to GitHub Pages. A repository requiring a running backend needs a compatible hosting adapter.",
        "Preview opens frozen local files in an isolated panel for five minutes. External APIs, browser storage, service workers, forms and popups are unavailable there; a fresh preview reopens the same saved build.",
        "Cloudflare Pages is unavailable for saved Sites until its required asset-upload protocol is implemented.",
        "A clean repository or pushed commit does not prove the website is live. Report provider, DNS and HTTPS evidence separately.",
        "Scoped background callers cannot acquire repository or registrar authority through these tools.",
    ],
}

DOMAIN_GUIDE = {
    "purpose": "Manage existing Name.com domains with separate named accounts and explicit change reviews.",
    "procedure": [
        "Inspect accounts and use the exact account_id. A Google sign-in email is not an API username; connect each account privately in Sites.",
        "In a project chat, use the saved site's site_id and site_revision with its bound account and domain. Account-wide browsing belongs in Sites or an unfiled chat.",
        "Use open with account_id and optionally domain to show the selected inventory. A project chat also needs the exact site binding.",
        "Read inventory or sync the chosen account. User-imported domains and renewal dates remain unverified until authenticated synchronization.",
        "Inspect the domain and authoritative DNS before preparing a record change. Read records to obtain exact IDs for updates and deletions.",
        "Use prepare_dns for one exact record change, or prepare_autorenew for the selected domain's auto-renew switch. Both require the owner's approval.",
        "Use operation or reconcile after an uncertain response; do not submit a duplicate mutation as a retry.",
        "Use verify with expected records for a time-stamped public resolver comparison. It does not verify a site deployment, DNSSEC, authoritative propagation, or HTTPS.",
        "prepare_renewal prepares the domain, term and available price for Name.com checkout. Tax is not an enforceable all-in quote; payment is completed at the registrar, then reconciled.",
    ],
    "limits": [
        "Never infer an account from a domain label, email address or prior selection. Use its authenticated inventory and current account revision.",
        "Renews, Expires, auto-renew enabled and a successful paid renewal are different facts. Keep their source and last-check time visible.",
        "Do not change unrelated DNS records, nameservers, DNSSEC, contacts or registrar locks. Purchases and transfers are outside these tools.",
        "Provider acceptance is not proof of public DNS propagation or a valid HTTPS certificate.",
    ],
}


def _schema(name):
    return next(item["input_schema"] for item in TOOL_SCHEMAS if item["name"] == name)


def _open(name, args, context, service, *, preview=False):
    """Navigation carries only identities accepted by the owning service."""
    from agent_friday.services import desktop_bus, sites_privacy
    allowed = ({"site_id", "build_id"} if preview else {"site_id"}) if name == "site_action" else {"account_id", "domain", "site_id", "site_revision"}
    if set(args) - allowed:
        raise ValueError("Unsupported inputs for opening Sites. Inspect the selected item before changing it.")
    target = {"workspace": "futurespeak"}
    if name == "site_action":
        if preview:
            result = service.execute("preview", args, context)
            target.update(site_id=result["site_id"], preview_build_id=result["build_id"])
            key = "preview_build_id"
        else:
            result = service.execute("inspect", {"site_id": args.get("site_id")}, context)
            target["site_id"] = result["site"]["site_id"]
            key = "site_id"
    else:
        account_id = args.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("Choose the exact domain account before opening its inventory.")
        if args.get("site_id"):
            result = service.execute("inspect", {key: args[key] for key in
                                     ("site_id", "site_revision", "account_id", "domain") if key in args}, context)
            target.update(site_id=args["site_id"], account_id=result["domain"]["account_id"],
                          domain=result["domain"]["domain"])
        else:
            result = service.execute("inventory", {"account_id": account_id}, context)
            target["account_id"] = account_id
            if args.get("domain"):
                from agent_friday.services.namecom import domain_name
                domain = domain_name(args["domain"])
                if not any(row.get("domain") == domain for row in result["inventory"]):
                    raise ValueError("That domain is not in the selected account's saved inventory. Sync or import it in Sites.")
                target["domain"] = domain
        key = "domain_ref" if target.get("domain") else "account_id"
    expected = target["account_id"] + "/" + target["domain"] if key == "domain_ref" else target[key]
    sites_privacy.admit(context)
    navigation = dict(target, type="navigate")
    if preview:
        from agent_friday.services import site_previews
        navigation["preview_request_id"] = site_previews.prepare_navigation(
            result["site_id"], result["site_revision"], result["build_id"], origin=context["_sites_origin"])
    sent = desktop_bus.send([navigation],
                            verify={"workspace": "futurespeak", "key": key, "value": expected})
    ack = sent.get("ack") or {}
    if not sent.get("delivered") or ack.get("opened") is False:
        status, message = "not_opened", "Sites did not open on the desktop."
    elif sent.get("acked") and ack.get("matched") is True:
        status, message = "opened", "The desktop confirmed the selected item is showing in Sites."
    else:
        status, message = "unconfirmed", "The navigation was sent, but the desktop has not confirmed the selected item."
    if preview and status == "opened":
        message = "The desktop opened the selected build's preview panel. Its rendering has not been verified."
    return {"status": status, "message": message, "target": target}


def _execute(name, inp):
    from agent_friday.services import agent
    from agent_friday.user_errors import UserFacingValueError
    if not isinstance(inp, dict):
        raise UserFacingValueError("Use named inputs for this action.")
    unknown = set(inp) - set(_schema(name)["properties"])
    if unknown:
        raise UserFacingValueError("Unsupported action inputs; inspect the tool's schema before retrying.")
    context = agent._workflow_caller_context()
    if not context.get("conversation_id"):
        raise UserFacingValueError("Open a conversation before managing its site or domains.")
    if context.get("nested_execution"):
        raise UserFacingValueError("Manage sites and domains from the owner's conversation.")
    from agent_friday.services import sites_privacy
    context["_sites_origin"] = sites_privacy.require_tool_origin()
    if name == "site_action":
        from agent_friday.services import sites_operations as service
    else:
        from agent_friday.services import domain_operations as service
    args = dict(inp)
    action = args.pop("action", None)
    try:
        if action == "open" or (name == "site_action" and action == "preview"):
            result = _open(name, args, context, service, preview=action == "preview")
        else:
            result = service.execute(action, args, context)
        sites_privacy.admit(context)
    except ValueError as exc:
        raise UserFacingValueError(str(exc)) from None
    except Exception:
        raise UserFacingValueError("This Sites action could not be confirmed. Inspect its operation before trying again.") from None
    return json.dumps(result, ensure_ascii=False, default=str)


def site_action(inp):
    return _execute("site_action", inp)


def domain_action(inp):
    return _execute("domain_action", inp)


RECORD_SCHEMA = {"type": "object", "properties": {
    "host": {"type": "string"},
    "type": {"type": "string", "enum": ["A", "AAAA", "ANAME", "CNAME", "MX", "SRV", "TXT"]},
    "answer": {"type": "string"}, "ttl": {"type": "integer", "minimum": 300},
    "priority": {"type": "integer", "minimum": 0, "maximum": 65535}},
    "required": ["host", "type", "answer", "ttl"]}


TOOL_SCHEMAS = [
    {"name": "site_action", "description":
     "Manage repository-backed Sites in this conversation: inspect/save, build/preview, prepare publishing and check deployment or domain requirements. Builds and publication require exact reviews. Git sync is not deployment proof; inspect operation results. Use discover_capabilities for the full procedure.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(SITE_ACTIONS)},
         "site_id": {"type": "string"}, "revision": {"type": "integer"},
         "build_id": {"type": "string"}, "operation_id": {"type": "string"},
         "codebase_id": {"type": "string"}, "name": {"type": "string"},
         "build_root": {"type": "string"}, "build_command": {"type": "string"},
         "output_dir": {"type": "string"},
         "hosting": {"type": ["object", "null"], "properties": {"connection_id": {"type": "string"}}, "required": ["connection_id"]},
         "domain": {"type": ["object", "null"], "properties": {
             "account_id": {"type": "string"}, "account_revision": {"type": "integer"},
             "domain": {"type": "string"}, "hostname": {"type": "string"}},
             "required": ["account_id", "account_revision", "domain", "hostname"]},
     }, "required": ["action"]}},
    {"name": "domain_action", "description":
     "Inspect named Name.com accounts, domains, renewal dates and DNS. Sync the chosen account, prepare an exact DNS or auto-renew review, prepare registrar renewal checkout, or reconcile an operation. No credentials in inputs. Use account_id and exact record IDs; do not replay uncertain writes. Use discover_capabilities for the full procedure.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(DOMAIN_ACTIONS)},
         "account_id": {"type": "string"}, "domain": {"type": "string"},
         "site_id": {"type": "string"}, "site_revision": {"type": "integer"},
         "operation_id": {"type": "string"}, "record_id": {"type": "integer", "minimum": 1},
         "delete": {"type": "boolean"}, "enabled": {"type": "boolean"},
         "years": {"type": "integer", "minimum": 1, "maximum": 10},
         "record": RECORD_SCHEMA,
         "records": {"type": "array", "items": RECORD_SCHEMA, "minItems": 1, "maxItems": 20},
     }, "required": ["action"]}},
]

TOOL_HANDLERS = {"site_action": site_action, "domain_action": domain_action}
