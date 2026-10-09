"""Shared domain actions and exact, once-only approval execution.

Paid renewals finish at the registrar: its quote excludes VAT and does not
provide an enforceable all-in maximum. Reconciliation never repeats a write.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from contextlib import ExitStack
from datetime import datetime

from agent_friday.user_errors import UserFacingValueError
from agent_friday.services import domain_accounts as accounts, namecom, sites_privacy

KIND = "domain_change"
SERVICE_ACTIONS = ("accounts", "inventory", "sync", "inspect", "records", "verify", "operation", "reconcile",
                   "prepare_dns", "prepare_renewal", "prepare_autorenew")
_DOMAIN_ARGUMENTS = {"account_id", "domain", "site_id", "site_revision"}
SERVICE_ARGUMENTS = {
    "accounts": set(), "inventory": {"account_id"}, "sync": {"account_id"},
    "inspect": _DOMAIN_ARGUMENTS, "records": _DOMAIN_ARGUMENTS, "verify": _DOMAIN_ARGUMENTS | {"records"},
    "operation": {"operation_id"}, "reconcile": {"operation_id"},
    "prepare_dns": _DOMAIN_ARGUMENTS | {"record_id", "record", "delete"},
    "prepare_renewal": _DOMAIN_ARGUMENTS | {"years"}, "prepare_autorenew": _DOMAIN_ARGUMENTS | {"enabled"},
}
_REGISTERED = False
_BACKGROUND = ("nested_execution", "task_id", "schedule_id", "agent_id", "agent_profile_id", "is_background_task", "grant_scope", "scope", "scheduled")


def _owner(context, *, check_recording=True):
    context = context or {}
    if check_recording:
        accounts.require_recording()
        generation = sites_privacy.admit(context)
    else:
        generation = context.get("privacy_generation")
        sites_privacy.require_generation(generation)
    if any(context.get(k) for k in _BACKGROUND):
        raise UserFacingValueError("Domain accounts can only be managed directly from Sites or the owning chat, not from a background or scoped task.")
    cid = context.get("conversation_id")
    if not cid:
        if context.get("surface") != "sites_ui":
            raise UserFacingValueError("Open the owning chat or Sites before managing domains.")
        return {"surface": "sites_ui", "conversation_id": None, "project_id": None, "privacy_generation": generation}
    from agent_friday.services import conversations, projects
    conv = conversations.load(cid)
    if not conv or conv.get("status") == "archived":
        raise UserFacingValueError("The originating chat is unavailable.")
    pid = conv.get("project") or None
    if "project_id" in context and (context.get("project_id") or None) != pid:
        raise UserFacingValueError("The originating chat has moved to a different project.")
    if pid and (not projects.load(pid) or projects.load(pid).get("archived")):
        raise UserFacingValueError("The owning project is unavailable.")
    return {"surface": context.get("surface", "chat"), "conversation_id": cid, "project_id": pid, "privacy_generation": generation}


def _site(args, owner):
    site_id = args.get("site_id")
    if not site_id:
        if owner.get("project_id"):
            raise UserFacingValueError("Choose this project's saved site before accessing domain accounts.")
        return None
    from agent_friday.services import sites_operations as sites
    site = sites.get_site(site_id)
    if not site:
        raise UserFacingValueError("That saved site is unavailable.")
    sites.validate_site_owner(site, None if owner.get("surface") == "sites_ui" and not owner.get("conversation_id") else
        dict(owner, _sites_origin=sites_privacy.require_generation(owner["privacy_generation"])))
    if args.get("site_revision") is not None and (type(args["site_revision"]) is not int or args["site_revision"] != site["revision"]):
        raise UserFacingValueError("This site changed. Refresh before changing its domain.")
    binding = site.get("domain") or {}
    if binding.get("account_id") != args.get("account_id") or binding.get("domain") != namecom.domain_name(args.get("domain")):
        raise UserFacingValueError("This domain and account are not bound to the selected site.")
    return {"site_id": site_id, "revision": site["revision"], "domain": copy.deepcopy(binding),
            "conversation_id": site.get("conversation_id"), "project_id": site.get("project_id") or None}


def _domain(client, domain, *, generation):
    sites_privacy.require_generation(generation)
    raw = client.domain(domain)
    sites_privacy.require_generation(generation)
    view = accounts.domain_view(raw)
    if view["domain"] != domain:
        raise namecom.ProviderError("Name.com returned a different domain; nothing was changed.")
    return view


def _site_host(site, domain):
    if not site:
        return None
    hostname = namecom.domain_name(site["domain"].get("hostname"))
    if hostname == domain:
        return ""
    if not hostname.endswith("." + domain):
        raise UserFacingValueError("This site's hostname is outside its bound domain.")
    return hostname[:-(len(domain) + 1)]


def _check_record_scope(record, site, domain):
    host = _site_host(site, domain)
    if host is not None and record is not None and record["host"] != host:
        raise UserFacingValueError("This site can manage DNS only for its saved hostname. Use Sites account management for other records.")


def validate_binding(account_id, domain, context):
    owner = _owner(context)
    domain = namecom.domain_name(domain)
    with accounts.LOCK:
        rec = accounts.account(account_id)
        value = _domain(accounts.client(rec, generation=owner["privacy_generation"]), domain, generation=owner["privacy_generation"])
        sites_privacy.require_generation(owner["privacy_generation"])
        return dict(value, account_id=account_id, account_revision=rec["revision"], verified_at=time.time())


def _records(client, domain, *, generation):
    sites_privacy.require_generation(generation)
    values = client.records(domain)
    sites_privacy.require_generation(generation)
    out = []
    for raw in values:
        rid = namecom.record_id(raw.get("id"))
        if raw.get("domainName") and namecom.domain_name(raw["domainName"]) != domain:
            raise namecom.ProviderError("Name.com returned a record for a different domain.")
        # Preserve provider state for the exact comparison. Provider values
        # remain untrusted data; only allowlisted fields reach the application.
        if not all(k in raw for k in ("host", "type", "answer", "ttl")):
            raise namecom.ProviderError("Name.com returned an incomplete DNS record.")
        record = {k: raw[k] for k in ("host", "type", "answer", "ttl", "priority") if k in raw}
        if not isinstance(record["host"], str) or not isinstance(record["type"], str) or not isinstance(record["answer"], str):
            raise namecom.ProviderError("Name.com returned an invalid DNS record.")
        record["host"] = "" if record["host"] == "@" else record["host"].lower()
        record["type"] = record["type"].upper()
        if record["type"] in {"MX", "SRV"}:
            record.setdefault("priority", 0)
        else:
            record.pop("priority", None)
        record["id"] = rid
        out.append(record)
    if len({r["id"] for r in out}) != len(out):
        raise namecom.ProviderError("Name.com returned duplicate record IDs.")
    return sorted(out, key=lambda r: r["id"])


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _record_body(record):
    return {k: v for k, v in record.items() if k != "id"}


def _same_record(actual, desired):
    try:
        return namecom.record_payload(_record_body(actual)) == desired
    except ValueError:
        return False


def _operation(operation_id, state=None):
    op = (state or accounts.read())["operations"].get(str(operation_id))
    if not op:
        raise UserFacingValueError("That domain operation is unavailable.")
    return copy.deepcopy(op)


def _public(op):
    plan = op["plan"]
    status, message = op["status"], op.get("message")
    if status in {"awaiting_approval", "applying"}:
        try:
            sites_privacy.require_operation(plan["owner"].get("privacy_generation"), plan.get("privacy_boot"))
        except ValueError:
            status = "unknown" if status == "applying" else "refused"
            message = ("The privacy session changed or Friday restarted during this operation. Check the provider state; no change was replayed." if status == "unknown" else
                       "The privacy session changed or Friday restarted after this review. Prepare a fresh review; this one cannot make a change.")
    return {"operation_id": op["operation_id"], "status": status, "approval_id": op.get("approval_id"),
            "account_id": plan["account_id"], "account_revision": plan["account_revision"], "account_label": plan["account_label"],
            "domain": plan["domain"], "action": plan["action"], "before": plan.get("before"), "after": plan.get("after"),
            "quote": plan.get("quote"), "years": plan.get("years"), "expires_at": plan["expires_at"],
            "checked_at": op.get("checked_at"), "evidence": op.get("evidence"), "message": message,
            "checkout_url": op.get("checkout_url"), "created_at": plan["created_at"]}


def _access_operation(op, owner):
    recorded = op["plan"]["owner"]
    account_management = owner.get("surface") == "sites_ui" and not owner.get("conversation_id")
    if not account_management and (owner.get("conversation_id"), owner.get("project_id")) != (recorded.get("conversation_id"), recorded.get("project_id")):
        raise UserFacingValueError("Open the chat that owns this domain operation.")


def history(account_id=None, context=None):
    owner = _owner(context)
    with accounts.LOCK:
        values = accounts.read()["operations"].values()
        values = [op for op in values if (not account_id or op["plan"]["account_id"] == account_id)
                  and ((owner["surface"] == "sites_ui" and not owner.get("conversation_id")) or (op["plan"]["owner"].get("conversation_id"), op["plan"]["owner"].get("project_id")) == (owner.get("conversation_id"), owner.get("project_id")))]
        result = [_public(op) for op in sorted(values, key=lambda op: op["plan"]["created_at"], reverse=True)[:100]]
        sites_privacy.require_generation(owner["privacy_generation"])
        return result


def _validate_plan(plan):
    owner = _owner(plan["owner"], check_recording=False)
    rec = accounts.account(plan["account_id"])
    if rec["revision"] != plan["account_revision"] or (plan.get("account_identity") and rec.get("identity_hash") != plan["account_identity"]):
        raise UserFacingValueError("The account changed after this review. Prepare a new review.")
    site = plan.get("site")
    if site:
        current = _site({"site_id": site["site_id"], "site_revision": site["revision"], "account_id": plan["account_id"], "domain": plan["domain"]}, owner)
        if current != site:
            raise UserFacingValueError("The site's domain binding changed after this review.")
    return rec


def _validate_reconciliation(plan, generation):
    """Fresh reads can use a rotated credential, never a changed destination."""
    owner = _owner(dict(plan["owner"], privacy_generation=generation), check_recording=False)
    rec = accounts.account(plan["account_id"])
    identity = plan.get("account_identity")
    if rec["revision"] != plan["account_revision"]:
        if not isinstance(identity, str) or not identity or rec.get("identity_hash") != identity:
            raise UserFacingValueError("This operation's original account identity cannot be verified after reconnecting.")
    elif identity and rec.get("identity_hash") != identity:
        raise UserFacingValueError("This operation's account identity changed.")
    saved = plan.get("site")
    if saved:
        current = _site({"site_id": saved["site_id"], "account_id": plan["account_id"], "domain": plan["domain"]}, owner)
        same_owner = all(current.get(key) == saved.get(key) for key in ("conversation_id", "project_id"))
        same_binding = all(current["domain"].get(key) == saved["domain"].get(key) for key in ("account_id", "domain", "hostname"))
        if not same_owner or not same_binding:
            raise UserFacingValueError("The site's owner or effective domain binding changed. This operation cannot read a different destination.")
    return rec


def _save_op(op, *, generation=None):
    sites_privacy.require_generation(op["plan"]["owner"]["privacy_generation"] if generation is None else generation)
    state = accounts.read()
    state["operations"][op["operation_id"]] = op
    accounts.write(state)


def _prepare(action, args, owner, site):
    account_id, domain = str(args.get("account_id") or ""), namecom.domain_name(args.get("domain"))
    rec = accounts.account(account_id)
    if site and site["domain"].get("account_revision") != rec["revision"]:
        raise UserFacingValueError("Reconnect this site's domain binding after the account changed.")
    if action != "prepare_renewal" and any(
            item["plan"]["account_id"] == account_id and item["plan"]["domain"] == domain
            and item["status"] in {"applying", "unknown", "provider_accepted"}
            for item in accounts.read()["operations"].values()):
        raise UserFacingValueError("Another change for this domain is unresolved. Inspect and reconcile it before preparing another change; nothing was replayed.")
    client = accounts.client(rec, generation=owner["privacy_generation"])
    info = _domain(client, domain, generation=owner["privacy_generation"])
    sites_privacy.require_generation(owner["privacy_generation"])
    now = time.time()
    plan = {"action": action, "account_id": account_id, "account_revision": rec["revision"], "account_label": rec["label"],
            "account_identity": rec.get("identity_hash"),
            "domain": domain, "owner": owner, "site": site, "created_at": now, "expires_at": now + 900,
            "privacy_boot": sites_privacy.boot_id(),
            "nameservers": info["nameservers"]}
    if action == "prepare_dns":
        if info["dns_authority"] != "namecom":
            raise UserFacingValueError("This domain uses another DNS provider. No Name.com DNS change was prepared.")
        records = _records(client, domain, generation=owner["privacy_generation"])
        sites_privacy.require_generation(owner["privacy_generation"])
        plan["zone_digest"] = _digest(records)
        rid = namecom.record_id(args["record_id"]) if args.get("record_id") is not None else None
        before = next((r for r in records if r["id"] == rid), None)
        if rid and not before:
            raise UserFacingValueError("That DNS record no longer exists. Refresh the records.")
        _check_record_scope(before, site, domain)
        if type(args.get("delete", False)) is not bool:
            raise UserFacingValueError("Choose whether this is a record deletion.")
        if args.get("delete"):
            if "record" in args:
                raise UserFacingValueError("Deletion uses an exact record ID, not a replacement record.")
            if not before:
                raise UserFacingValueError("Deletion needs an exact DNS record ID.")
            namecom.record_payload(_record_body(before))
            after = None
        else:
            after = namecom.record_payload(args.get("record"))
            _check_record_scope(after, site, domain)
            matches = [r for r in records if r["host"] in {after["host"], "@" if not after["host"] else after["host"]} and r["type"] == after["type"]]
            if rid is None and any(_same_record(r, after) for r in matches):
                return {"status": "ok", "message": "This exact DNS record already exists. Nothing changed."}
            if any(r["id"] != rid and r["host"] == after["host"] and (r["type"] == "CNAME" or after["type"] == "CNAME") for r in records):
                raise UserFacingValueError("A CNAME would conflict with an existing record at that hostname.")
            if before and _same_record(before, after):
                return {"status": "ok", "message": "The DNS record already matches. Nothing changed."}
        plan.update(before=before, after=after, record_id=rid)
        description = "Delete this exact DNS record." if after is None else ("Update this exact DNS record; other records stay as they are." if rid else "Add this DNS record; existing records stay as they are.")
    elif action == "prepare_autorenew":
        enabled = args.get("enabled")
        if type(enabled) is not bool or info["autorenew_enabled"] is None:
            raise UserFacingValueError("Choose on or off after Name.com returns the current auto-renew setting.")
        if info["autorenew_enabled"] == enabled:
            return {"status": "ok", "message": "Auto-renew already has that setting. Nothing changed."}
        plan.update(before={"autorenew_enabled": info["autorenew_enabled"]}, after={"autorenew_enabled": enabled})
        description = "Enabling auto-renew permits future registrar charges on this account." if enabled else "Disabling auto-renew can let this domain expire unless it is renewed separately."
    else:
        years = args.get("years", 1)
        if type(years) is not int or not 1 <= years <= 10:
            raise UserFacingValueError("Choose a renewal term from 1 to 10 years.")
        sites_privacy.require_generation(owner["privacy_generation"])
        pricing = client.pricing(domain, years)
        sites_privacy.require_generation(owner["privacy_generation"])
        if pricing.get("renewalPrice") is None:
            raise UserFacingValueError("Name.com does not currently offer renewal for this domain and term.")
        plan.update(years=years, before={"expire_date": info["expire_date"]}, quote={"subtotal": namecom.amount(pricing["renewalPrice"]),
            "currency": "USD", "tax": "unknown", "total": None, "quoted_at": now, "source": "namecom_api"})
        op = {"operation_id": "dom_" + uuid.uuid4().hex, "plan": plan, "plan_digest": _digest(plan), "status": "awaiting_provider_checkout",
              "checkout_url": "https://www.name.com/account/renewalcenter",
              "message": "Review this domain and term in the named account at Name.com. The subtotal excludes VAT; final payment happens there. Friday has made no charge. After paying, check again here."}
        _save_op(op)
        return _public(op)
    op = {"operation_id": "dom_" + uuid.uuid4().hex, "plan": plan, "plan_digest": _digest(plan), "status": "awaiting_approval"}
    _save_op(op)
    from agent_friday.services import approvals
    register()
    sites_privacy.require_generation(owner["privacy_generation"])
    card = approvals.create_approval(kind=KIND, subject_type="domain_operation", subject_id=op["operation_id"],
        title=("Change DNS for " if action == "prepare_dns" else "Change auto-renew for ") + domain,
        description=description, action_description=description + " Account: " + rec["label"] + "; domain: " + domain,
        payload={"operation_id": op["operation_id"], "plan_digest": op["plan_digest"], "account_id": account_id,
                 "account_label": rec["label"], "domain": domain, "before": plan["before"], "after": plan["after"],
                 "spoken": description + " Review the exact account and change on this card."},
        requested_by=owner["surface"], action_class="outward", force_gate=True,
        _before_publish=lambda: sites_privacy.require_operation(owner["privacy_generation"], plan["privacy_boot"]))
    op["approval_id"] = card["approval_id"]
    if card["status"] != "pending":
        op["status"] = card["status"]
    _save_op(op)
    return _public(op)


def _observe(op, client, generation):
    plan = op["plan"]
    info = _domain(client, plan["domain"], generation=generation)
    sites_privacy.require_generation(generation)
    if plan["action"] == "prepare_dns":
        records = _records(client, plan["domain"], generation=generation)
        sites_privacy.require_generation(generation)
        current = next((r for r in records if r["id"] == plan["record_id"]), None)
        if plan["after"] is None:
            matches = current is None
        elif plan["record_id"]:
            matches = current is not None and _same_record(current, plan["after"])
        else:
            matches = sum(_same_record(r, plan["after"]) for r in records) == 1
        return matches, {"provider_records_match": matches, "dns_authority": info["dns_authority"],
                         "public_dns": "not_checked", "https": "not_checked"}
    if plan["action"] == "prepare_autorenew":
        matches = info["autorenew_enabled"] == plan["after"]["autorenew_enabled"]
        return matches, {"autorenew_enabled": info["autorenew_enabled"]}
    before, after = plan["before"]["expire_date"], info["expire_date"]
    try:
        changed = datetime.fromisoformat(after.replace("Z", "+00:00")) > datetime.fromisoformat(before.replace("Z", "+00:00"))
    except (AttributeError, ValueError, TypeError):
        changed = False
    return changed, {"expire_date": after, "prior_expire_date": before, "expiration_extended": changed, "payment": "not_verified"}


def _reconcile(op, *, generation=None):
    # A fresh explicit read can reconcile an older public plan, but never
    # changes its immutable origin/digest or authorizes another provider write.
    generation = op["plan"]["owner"]["privacy_generation"] if generation is None else generation
    rec = _validate_reconciliation(op["plan"], generation)
    matches, evidence = _observe(op, accounts.client(rec, generation=generation), generation)
    _validate_reconciliation(op["plan"], generation)
    op.update(checked_at=time.time(), evidence=evidence)
    if matches:
        op["status"] = "renewal_observed" if op["plan"]["action"] == "prepare_renewal" else "provider_verified"
        op["message"] = "The account's expiration date has advanced. Payment was made outside Friday and is not independently verified." if op["plan"]["action"] == "prepare_renewal" else "Name.com confirms the reviewed state. Public DNS propagation and HTTPS have not been checked."
    elif op["status"] in {"applying", "unknown", "provider_accepted", "provider_verified"}:
        op["status"] = "unknown"
        op["message"] = "The reviewed state is not confirmed. Inspect the current provider state before preparing another change; nothing was replayed."
    _save_op(op, generation=generation)
    return _public(op)


def _execution_locks(plan):
    stack = ExitStack()
    # Lock order is Sites -> registrar -> conversations across all paths.
    try:
        if plan.get("site"):
            from agent_friday.services import sites_operations as sites
            lock = getattr(sites, "LOCK", None)
            if lock is None:
                raise UserFacingValueError("Site ownership cannot be held while applying this change.")
            stack.enter_context(lock)
        stack.enter_context(accounts.LOCK)
        if plan["owner"].get("conversation_id") or plan.get("site"):
            from agent_friday.services import conversations
            stack.enter_context(conversations._LOCK)
    except BaseException:
        stack.close()
        raise
    return stack


def _matches_card(record, op):
    payload, plan = record.get("payload") or {}, op["plan"]
    return (record.get("approval_id") == op.get("approval_id")
            and record.get("subject_type") == "domain_operation"
            and record.get("subject_id") == op["operation_id"]
            and payload.get("operation_id") == op["operation_id"]
            and payload.get("plan_digest") == op["plan_digest"] == _digest(plan)
            and all(payload.get(key) == plan.get(key) for key in ("account_id", "account_label", "domain", "before", "after")))


def _on_decision(record):
    if record.get("kind") != KIND:
        return
    payload = record.get("payload") or {}
    with accounts.LOCK:
        op = _operation(payload.get("operation_id"))
    if not _matches_card(record, op):
        return
    if record.get("status") != "approved":
        if record.get("status") in {"denied", "expired", "blocked"}:
            with accounts.LOCK:
                op = _operation(op["operation_id"])
                if _matches_card(record, op) and op["status"] == "awaiting_approval":
                    try:
                        sites_privacy.require_generation(op["plan"]["owner"]["privacy_generation"])
                    except ValueError:
                        return
                    op["status"] = record["status"]
                    _save_op(op)
        return
    from agent_friday.services import approvals
    try:
        sites_privacy.require_operation(op["plan"]["owner"]["privacy_generation"], op["plan"].get("privacy_boot"))
    except ValueError:
        return
    if not approvals.claim_for_execution(record["approval_id"]):
        return
    attempted = False
    try:
        accounts.require_recording()
        with _execution_locks(op["plan"]):
            op = _operation(op["operation_id"])
            plan = op["plan"]
            if not _matches_card(record, op) or op["status"] != "awaiting_approval":
                return
            if plan["expires_at"] < time.time():
                raise UserFacingValueError("This review expired. Prepare a fresh review.")
            sites_privacy.require_operation(plan["owner"]["privacy_generation"], plan.get("privacy_boot"))
            rec = _validate_plan(plan)
            client = accounts.client(rec, generation=plan["owner"]["privacy_generation"])
            info = _domain(client, plan["domain"], generation=plan["owner"]["privacy_generation"])
            if info["nameservers"] != plan["nameservers"]:
                raise UserFacingValueError("The domain's DNS authority changed. Prepare a fresh review.")
            if plan["action"] == "prepare_dns":
                if info["dns_authority"] != "namecom" or _digest(_records(client, plan["domain"], generation=plan["owner"]["privacy_generation"])) != plan["zone_digest"]:
                    raise UserFacingValueError("DNS changed after this review. Prepare a fresh review.")
            elif plan["action"] == "prepare_autorenew":
                if info["autorenew_enabled"] != plan["before"]["autorenew_enabled"]:
                    raise UserFacingValueError("Auto-renew changed after this review. Prepare a fresh review.")
            else:
                raise UserFacingValueError("Paid renewal must finish at Name.com checkout.")
            if plan["expires_at"] < time.time():
                raise UserFacingValueError("This review expired during verification. Prepare a fresh review.")
            sites_privacy.require_operation(plan["owner"]["privacy_generation"], plan.get("privacy_boot"))
            if any(item["operation_id"] != op["operation_id"]
                   and item["plan"]["account_id"] == plan["account_id"]
                   and item["plan"]["domain"] == plan["domain"]
                   and item["status"] in {"applying", "unknown", "provider_accepted"}
                   for item in accounts.read()["operations"].values()):
                raise UserFacingValueError("Another change for this domain is unresolved. Inspect and reconcile it before approving another change; nothing was replayed.")
            from agent_friday.governance import action_gate
            action_gate.record_external(KIND, surface="approval_card", approval_id=record["approval_id"], target=plan["domain"])
            op.update(status="applying", attempted_at=time.time())
            _save_op(op)
            sites_privacy.require_operation(plan["owner"]["privacy_generation"], plan.get("privacy_boot"))
            attempted = True
            if plan["action"] == "prepare_autorenew":
                client.autorenew(plan["domain"], plan["after"]["autorenew_enabled"])
            elif plan["after"] is None:
                client.delete_record(plan["domain"], plan["record_id"])
            else:
                client.put_record(plan["domain"], plan["after"], plan["record_id"])
            op["status"] = "provider_accepted"
            _save_op(op)
            _reconcile(op)
    except Exception as exc:
        with accounts.LOCK:
            op = _operation(op["operation_id"])
            op["status"] = "unknown" if attempted else "refused"
            op["message"] = str(exc) if isinstance(exc, (ValueError, namecom.ProviderError)) else "The operation could not be confirmed. Inspect its state before trying again."
            try:
                sites_privacy.require_generation(op["plan"]["owner"]["privacy_generation"])
            except ValueError:
                # A privacy transition cannot turn a late provider result into
                # a new public record. The durable pre-write state survives.
                pass
            else:
                _save_op(op)
    finally:
        with accounts.LOCK:
            result = _operation(op["operation_id"])
        try:
            sites_privacy.require_generation(result["plan"]["owner"]["privacy_generation"])
        except ValueError:
            return
        approvals.mark_used(record["approval_id"], KIND, detail={"operation_id": op["operation_id"], "status": result["status"]})


def register():
    global _REGISTERED
    if not _REGISTERED:
        from agent_friday.services import approvals
        approvals.register_decision_hook(KIND, _on_decision)
        _REGISTERED = True


def execute(action, args=None, context=None):
    if not isinstance(action, str) or action not in SERVICE_ACTIONS or (args is not None and not isinstance(args, dict)):
        raise UserFacingValueError("Choose a supported domain action.")
    args = args or {}
    if set(args) - SERVICE_ARGUMENTS[action]:
        raise UserFacingValueError("This domain action contains unsupported fields.")
    owner = _owner(context)
    def checked(result):
        _owner(owner, check_recording=False)
        return result
    if action in {"operation", "reconcile"}:
        with accounts.LOCK:
            op = _operation(args.get("operation_id"))
        with _execution_locks(op["plan"]):
            _owner(owner, check_recording=False)
            op = _operation(op["operation_id"])
            _access_operation(op, owner)
            if action == "operation":
                return checked(_public(op))
            return checked(_reconcile(op, generation=owner["privacy_generation"]))
    access = {"owner": owner, "site": {"site_id": args["site_id"]} if args.get("site_id") else None}
    with _execution_locks(access):
        _owner(owner, check_recording=False)
        site = _site(args, owner)
        if site and action in {"accounts", "inventory", "sync"}:
            raise UserFacingValueError("Inspect this site's bound domain; manage account inventory directly in Sites.")
        if action == "accounts":
            return checked({"status": "ok", "accounts": accounts.list_accounts()})
        if action == "inventory":
            return checked({"status": "ok", "inventory": accounts.inventory(args.get("account_id"))})
        if action == "sync":
            return checked({"status": "ok", "inventory": accounts.sync(str(args.get("account_id") or ""), _generation=owner["privacy_generation"])})
        if action.startswith("prepare_"):
            return checked(_prepare(action, args, owner, site))
        account_id, domain = str(args.get("account_id") or ""), namecom.domain_name(args.get("domain"))
        rec = accounts.account(account_id)
        if site and site["domain"].get("account_revision") != rec["revision"]:
            raise UserFacingValueError("Reconnect this site's domain binding after the account changed.")
        client = accounts.client(rec, generation=owner["privacy_generation"])
        info = _domain(client, domain, generation=owner["privacy_generation"])
        sites_privacy.require_generation(owner["privacy_generation"])
        info.update(account_id=account_id, account_revision=rec["revision"], verified_at=time.time())
        if action == "verify":
            from agent_friday.services import domain_dns
            if site:
                if not isinstance(args.get("records"), list):
                    raise UserFacingValueError("Supply the explicit DNS records to check.")
                for record in args["records"]:
                    _check_record_scope(namecom.record_payload(record), site, domain)
            return checked(domain_dns.verify(domain, args.get("records"), generation=owner["privacy_generation"]))
        if action == "records":
            host = _site_host(site, domain)
            records = _records(client, domain, generation=owner["privacy_generation"])
            return checked({"status": "ok", "domain": info, "records": [r for r in records if host is None or r["host"] == host]})
        return checked({"status": "ok", "domain": info})


def verify_records(account_id, domain, required, context, *, site_id=None, site_revision=None):
    return execute("verify", {"account_id": account_id, "domain": domain, "records": required,
        **({"site_id": site_id, "site_revision": site_revision} if site_id else {})}, context)
