"""Project-owned static Sites, frozen builds and exact once-only publication plans."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import ExitStack

from agent_friday.user_errors import UserFacingValueError
from agent_friday.paths import friday_home, contained
from agent_friday.services.workflow_operations import atomic_write
from agent_friday.services import sites_privacy

LOCK = threading.RLock()
KIND = "site_operation"
_REGISTERED = False
_INSTANCE = uuid.uuid4().hex
ACTIONS = ("list", "inspect", "save", "build", "preview", "prepare_publish", "deployment_status",
           "hosting_requirements", "operation")
CONFIG = {"name", "codebase_id", "build_root", "build_command", "output_dir", "hosting", "domain"}
ACTION_FIELDS = {
    "list": set(), "inspect": {"site_id"}, "save": CONFIG | {"site_id", "revision"},
    "build": {"site_id"}, "preview": {"site_id", "build_id"},
    "prepare_publish": {"site_id", "build_id"}, "hosting_requirements": {"site_id"},
    "deployment_status": {"site_id", "operation_id"}, "operation": {"site_id", "operation_id"},
}


def _root():
    return friday_home() / "sites"


def _id(value, prefix):
    if not re.fullmatch(prefix + r"-[a-f0-9]{32}", str(value)):
        raise UserFacingValueError("Choose a saved %s." % prefix)
    return value


def _site_dir(site_id):
    return _root() / _id(site_id, "site")


def _read(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, data, *, generation=None):
    sites_privacy.require_generation(data.get("privacy_generation") if generation is None else generation)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def get_site(site_id):
    """Private read seam for exact domain-plan revalidation; never performs network I/O."""
    with LOCK:
        return _read(_site_dir(site_id) / "site.json")


def _context(context):
    context = dict(context or {})
    if any(context.get(key) for key in ("nested_execution", "is_background_task", "task_id", "schedule_id",
                                       "agent_id", "agent_profile_id", "grant_scope", "scope", "scheduled")):
        raise UserFacingValueError("Sites account and publication actions need the owning chat or Sites panel, not a background worker.")
    if not context.get("conversation_id"):
        raise UserFacingValueError("Choose the chat that owns this site.")
    return context


def validate_site_owner(site, context=None):
    from agent_friday.services import conversations, projects, codebases
    if not site:
        raise UserFacingValueError("That site no longer exists.")
    cid, pid = site.get("conversation_id"), site.get("project_id") or None
    conversation = conversations.load(cid)
    if not conversation or conversation.get("status") == "archived" or (conversation.get("project") or None) != pid:
        raise UserFacingValueError("The site's owning chat was deleted, archived, or moved. Save a new site in the intended chat.")
    project = projects.load(pid) if pid else None
    if pid and (not project or project.get("archived")):
        raise UserFacingValueError("The site's project is unavailable.")
    codebase = codebases.load(site["codebase_id"])
    if (not codebase or codebase.get("conversation_id") != cid
            or conversation.get("codebase") != site["codebase_id"]):
        raise UserFacingValueError("The site codebase is no longer bound to its owning chat.")
    if context is not None:
        caller = _context(context)
        if caller["conversation_id"] != cid or (caller.get("project_id") or None) != pid:
            raise UserFacingValueError("This site belongs to another chat or project.")
    return site


def _caller(context):
    from agent_friday.services import conversations
    context = _context(context)
    generation = sites_privacy.admit(context)
    conversation = conversations.load(context["conversation_id"])
    if not conversation or conversation.get("status") == "archived":
        raise UserFacingValueError("The owning chat is unavailable.")
    pid = conversation.get("project") or None
    if "project_id" in context and (context.get("project_id") or None) != pid:
        raise UserFacingValueError("The owning chat has moved to a different project.")
    return dict(context, project_id=pid, _sites_generation=generation)


def _durable():
    from agent_friday.services.workflow_operations import require_recording
    require_recording()


def _build(site, build_id):
    from agent_friday.services.site_builds import build_dir
    value = _read(build_dir(site["site_id"], _id(build_id, "build")) / "build.json")
    if not value or value.get("site_id") != site["site_id"]:
        raise UserFacingValueError("That build does not belong to this site.")
    return value


def _operation(site, operation_id):
    value = _read(_site_dir(site["site_id"]) / "operations" / (_id(operation_id, "op") + ".json"))
    if not value or value.get("site_id") != site["site_id"]:
        raise UserFacingValueError("That operation does not belong to this site.")
    if value.get("status") in {"awaiting_approval", "applying"}:
        applying = value["status"] == "applying"
        expired = applying and value.get("executor_instance") != _INSTANCE
        try:
            sites_privacy.require_operation(value.get("privacy_generation"), value.get("privacy_boot"))
        except ValueError:
            expired = True
        if expired:
            value.update(status="unknown" if applying else "refused", verified=False,
                error="The execution or privacy context ended during this operation. Inspect the outcome; no write was replayed." if applying else
                      "The privacy context changed or Friday restarted after this review. Prepare a fresh review; this one cannot make a change.")
    return value


def _save_operation(operation, *, generation=None):
    with LOCK:
        path = _site_dir(operation["site_id"]) / "operations" / (operation["operation_id"] + ".json")
        previous = _read(path) or {}
        operation["record_revision"] = previous.get("record_revision", 0) + 1
        _write(path, operation, generation=generation)


def _plan_hash(operation):
    fields = ("operation_id", "site_id", "site_revision", "conversation_id", "project_id", "action", "build_id",
              "source_hash", "source_revision", "output_hash", "connection", "domain", "files", "privacy_generation", "privacy_boot")
    return hashlib.sha256(json.dumps({key: operation.get(key) for key in fields}, sort_keys=True).encode()).hexdigest()


def _hostname(value):
    try:
        value = str(value or "").strip().rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise UserFacingValueError("Enter a valid domain hostname.") from exc
    if len(value) > 253 or "." not in value or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in value.split(".")):
        raise UserFacingValueError("Enter a valid domain hostname without a URL, port or wildcard.")
    return value


def _config(args, previous, caller):
    from agent_friday.services import site_builds, site_hosting
    merged = {key: copy.deepcopy(args.get(key, (previous or {}).get(key))) for key in CONFIG}
    name, command = merged["name"], merged["build_command"] or ""
    if not isinstance(name, str) or not name.strip() or len(name) > 160:
        raise UserFacingValueError("Give the site a name of at most 160 characters.")
    if not isinstance(command, str) or len(command) > 2000:
        raise UserFacingValueError("The build command must be text of at most 2000 characters.")
    from agent_friday.services import credential_paths, publish_web
    if (credential_paths.scan_command(command) or any(pattern.search(command) for pattern in publish_web._SECRET_RES)
            or re.search(r"(?i)(?:token|secret|password|api[_-]?key)\s*=", command)):
        raise UserFacingValueError("Build commands cannot contain credentials or read protected credential paths.")
    merged.update(name=name.strip(), build_command=command.strip(),
                  build_root=site_builds.relative_path(merged["build_root"] or "."),
                  output_dir=site_builds.relative_path(merged["output_dir"] or "."))
    hosting = merged["hosting"]
    if hosting is not None:
        if not isinstance(hosting, dict) or set(hosting) != {"connection_id"}:
            raise UserFacingValueError("Select a saved hosting connection.")
        connection = site_hosting.get_connection(hosting["connection_id"])
        site_hosting.require_site_target(connection)
        target = site_hosting.target_identity(connection)
        for path in _root().glob("site-*/site.json"):
            other = _read(path)
            if other and other["site_id"] != (previous or {}).get("site_id") and other.get("hosting"):
                other_connection = site_hosting.get_connection(other["hosting"]["connection_id"])
                if site_hosting.target_identity(other_connection) == target:
                    raise UserFacingValueError("That hosting target belongs to another saved site. Use a dedicated Pages project or repository.")
    binding = merged["domain"]
    if binding is not None:
        if not isinstance(binding, dict) or set(binding) - {"account_id", "account_revision", "domain", "hostname"}:
            raise UserFacingValueError("Select a verified domain account and hostname.")
        from agent_friday.services import domain_operations
        domain, hostname = _hostname(binding.get("domain")), _hostname(binding.get("hostname"))
        if hostname != domain and not hostname.endswith("." + domain):
            raise UserFacingValueError("The hostname must be inside the selected domain.")
        verified = domain_operations.validate_binding(binding.get("account_id"), domain, caller)
        if type(binding.get("account_revision")) is not int or binding["account_revision"] != verified["account_revision"]:
            raise UserFacingValueError("The selected domain account changed. Refresh and review its binding before saving.")
        merged["domain"] = {key: verified[key] for key in ("account_id", "account_revision", "domain")}
        merged["domain"]["hostname"] = hostname
        if hosting:
            from agent_friday.services.publish_adapters import site_dns_requirements
            site_dns_requirements(connection, hostname, domain)
    return merged


def _save(args, caller):
    _durable()
    from agent_friday.services import conversations
    with LOCK:
        previous = get_site(args["site_id"]) if args.get("site_id") else None
        if args.get("site_id") and not previous:
            raise UserFacingValueError("That site no longer exists.")
        if previous:
            validate_site_owner(previous, caller)
            if type(args.get("revision")) is not int or args["revision"] != previous["revision"]:
                raise UserFacingValueError("The site changed. Reload it before saving.")
        config = _config(args, previous, caller)
        record = dict(config, site_id=(previous or {}).get("site_id") or "site-" + uuid.uuid4().hex,
                      revision=int((previous or {}).get("revision", 0)) + 1,
                      conversation_id=caller["conversation_id"], project_id=caller["project_id"],
                      privacy_generation=caller["_sites_generation"],
                      created_at=(previous or {}).get("created_at") or time.time(), updated_at=time.time())
        if record.get("hosting"):
            from agent_friday.services import site_hosting
            record["hosting_target"] = site_hosting.target_identity(site_hosting.get_connection(record["hosting"]["connection_id"]))
        with conversations._LOCK:
            validate_site_owner(record, caller)
            _write(_site_dir(record["site_id"]) / "revisions" / (str(record["revision"]) + ".json"), record)
            _write(_site_dir(record["site_id"]) / "site.json", record)
        return record


def _records(site, folder, pattern):
    rows = [_operation(site, path.stem) if folder == "operations" else _read(path)
            for path in (_site_dir(site["site_id"]) / folder).glob(pattern)]
    return sorted(rows, key=lambda row: row.get("created_at", 0), reverse=True)


def _overview(site):
    builds = _records(site, "builds", "build-*/build.json")
    operations = _records(site, "operations", "op-*.json")
    deployments = [row for row in operations if row["action"] == "publish"]
    last_verified = next((row for row in deployments if row.get("verified_at")), None)
    current = copy.deepcopy(last_verified)
    if current:
        newer = [row for row in deployments if row["created_at"] > current["created_at"]
                 and row["status"] not in ("awaiting_approval", "denied", "expired", "blocked", "failed", "refused")]
        if newer or not current.get("verified"):
            current.update(status="previously_verified", verified=False,
                           note="This version passed an earlier live check. Its current content is not established after a later publication or check.")
    else:
        current = next((row for row in deployments if row.get("status") in ("provider_ready", "provider_accepted", "partial",
                                                                          "content_pending", "certificate_pending", "verification_pending", "provider_pending")), None)
    return dict(site, latest_build=builds[0] if builds else None, builds=builds,
                current_deployment=current, last_verified_deployment=last_verified,
                latest_deployment=deployments[0] if deployments else None,
                history=operations, allowed_actions=list(ACTIONS),
                note="Git and build status do not prove that a published version is live.")


def _connection(site, expected=None, *, allow_reconnected=False):
    from agent_friday.services import site_hosting
    if not site.get("hosting"):
        raise UserFacingValueError("Select a saved hosting connection before publishing.")
    connection = site_hosting.get_connection(site["hosting"]["connection_id"], secret=True)
    site_hosting.require_site_target(connection)
    target = site_hosting.target_identity(connection)
    if target != site.get("hosting_target"):
        raise UserFacingValueError("The hosting target changed. Review and save the site binding again.")
    for path in _root().glob("site-*/site.json"):
        other = _read(path)
        if other and other["site_id"] != site["site_id"] and other.get("hosting_target") == target:
            raise UserFacingValueError("This hosting target is already reserved by another saved site.")
    identity_fields = ("connection_id", "adapter", "repo", "branch", "account_id", "project")
    if expected and (any(connection.get(key) != expected.get(key) for key in identity_fields)
                     or (not allow_reconnected and connection.get("revision") != expected.get("revision"))):
        raise UserFacingValueError("The hosting connection changed. Prepare a new approval.")
    return connection


def _binding(site, generation):
    if site.get("domain"):
        from agent_friday.services import domain_operations
        binding = site["domain"]
        current = domain_operations.validate_binding(binding["account_id"], binding["domain"],
            {"conversation_id": site["conversation_id"], "project_id": site["project_id"],
             "_sites_origin": sites_privacy.require_generation(generation)})
        if current["account_revision"] != binding["account_revision"]:
            raise UserFacingValueError("The domain connection changed. Save the binding again before publishing.")


def _bundle(site, build, operation_id=None):
    from agent_friday.services import site_builds, publish_web
    if build.get("status") != "built":
        raise UserFacingValueError("Choose a successful static build before publishing.")
    files = site_builds.collect(site_builds.build_dir(site["site_id"], build["build_id"]) / "output", output=True)
    if site_builds.manifest(files) != build.get("files") or site_builds.digest(files) != build.get("output_hash"):
        raise UserFacingValueError("Build output changed after preview. Prepare a new build.")
    if operation_id:
        files[".well-known/friday-deployment.json"] = json.dumps({"site_id": site["site_id"], "operation_id": operation_id,
            "build_id": build["build_id"], "output_hash": build["output_hash"]}, sort_keys=True).encode()
    bundle = publish_web.Bundle(files, site["name"], site["site_id"], "site", site["site_id"], site["conversation_id"], site["revision"])
    scan = publish_web.scan(bundle)
    if not scan["ok"]:
        raise UserFacingValueError("The output no longer passes the publication scan.")
    return bundle, scan


def _preparation_current(site, generation, privacy_boot):
    from agent_friday.services import conversations
    with LOCK, conversations._LOCK:
        sites_privacy.require_operation(generation, privacy_boot)
        current = validate_site_owner(get_site(site["site_id"]))
        if current["revision"] != site["revision"]:
            raise UserFacingValueError("The site changed while preparing the review. Prepare it again.")


def _prepare(site, action, args, generation):
    from agent_friday.services import approvals, site_builds
    _durable()
    sites_privacy.require_generation(generation)
    register()
    for pending in _records(site, "operations", "op-*.json"):
        if (pending["action"] == action and pending["site_revision"] == site["revision"]
                and pending.get("privacy_generation") == generation
                and pending.get("privacy_boot") == sites_privacy.boot_id()
                and pending["status"] == "awaiting_approval"
                and (action == "build" or pending.get("build_id") == args.get("build_id"))):
            card = approvals.get_approval(pending.get("approval_id"))
            if card and card["status"] == "pending":
                return {"status": "ok", "site": site, "operation": pending, "operation_id": pending["operation_id"],
                        "approval_id": card["approval_id"], "approval": card}
    operation = {"operation_id": "op-" + uuid.uuid4().hex, "site_id": site["site_id"], "site_revision": site["revision"],
                 "conversation_id": site["conversation_id"], "project_id": site["project_id"], "action": action,
                 "privacy_generation": generation, "privacy_boot": sites_privacy.boot_id(),
                 "created_at": time.time(), "status": "awaiting_approval", "verified": False}
    if action == "build":
        build_id = "build-" + uuid.uuid4().hex
        source = site_builds.freeze(site, build_id, generation=generation)
        build = dict(source, build_id=build_id, site_id=site["site_id"], site_revision=site["revision"],
                     operation_id=operation["operation_id"],
                     privacy_generation=generation, privacy_boot=operation["privacy_boot"],
                     status="awaiting_approval", created_at=time.time(), command=site["build_command"],
                     build_root=site["build_root"], output_dir=site["output_dir"])
        _write(site_builds.build_dir(site["site_id"], build_id) / "build.json", build)
        operation["build_id"] = build_id
        operation["source_hash"] = source["source_hash"]
        description = ("Build captured source %s with %s from %s. Output: %s. " %
                       (source["source_hash"][:12], site["build_command"] or "no command (static files)", site["build_root"], site["output_dir"]))
        description += "Commands run on this PC in a separate copy, with no inherited provider credentials. This is not an operating-system sandbox."
    else:
        unresolved = [row for row in _records(site, "operations", "op-*.json")
                      if row["action"] == "publish" and row["status"] in ("applying", "unknown")]
        if unresolved:
            raise UserFacingValueError("A publication outcome is unresolved. Check its deployment status before preparing another write.")
        build = _build(site, args.get("build_id"))
        connection = _connection(site)
        _binding(site, generation)
        bundle, scan = _bundle(site, build, operation["operation_id"])
        operation.update(build_id=build["build_id"], source_revision=build["source_revision"], output_hash=build["output_hash"],
                         connection={key: value for key, value in connection.items() if key != "token"},
                         domain=copy.deepcopy(site.get("domain")), files=bundle.manifest(), scan=scan)
        description = "%d files (build %s, output %s) become public at %s. Existing target contents are replaced. %s" % (
            len(bundle.files), build["build_id"], build["output_hash"][:12], site["hosting_target"], "Associate " + site["domain"]["hostname"] + "; DNS needs its separate exact approval."
            if site.get("domain") else "No custom domain is attached.")
    # Persist the intent before approval exists, so a process interruption has a recovery identity.
    operation["plan_hash"] = _plan_hash(operation)
    _preparation_current(site, generation, operation["privacy_boot"])
    _save_operation(operation)
    sites_privacy.require_generation(generation)
    card = approvals.create_approval(kind=KIND, subject_type="site", subject_id=operation["operation_id"],
        title=("Build " if action == "build" else "Publish ") + site["name"], description=description,
        action_description=("run the exact site build command" if action == "build" else "publish the exact site build and host association"),
        payload={"site_id": site["site_id"], "operation_id": operation["operation_id"], "plan_hash": operation["plan_hash"],
                 "conversation_id": site["conversation_id"], "project_id": site["project_id"], "description": description,
                 "build_id": operation["build_id"], "hosting_target": site.get("hosting_target"),
                 "source_revision": build["source_revision"], "output_hash": build.get("output_hash"),
                 "hostname": (site.get("domain") or {}).get("hostname")},
        requested_by="sites", action_class="outward", force_gate=True,
        _before_publish=lambda: _preparation_current(site, generation, operation["privacy_boot"]))
    operation["approval_id"] = card["approval_id"]
    operation["status"] = "awaiting_approval" if card["status"] == "pending" else card["status"]
    _save_operation(operation)
    return {"status": "ok", "site": site, "operation": operation, "operation_id": operation["operation_id"],
            "approval_id": card["approval_id"], "approval": card}


def register():
    global _REGISTERED
    if _REGISTERED:
        return
    from agent_friday.services import approvals
    approvals.register_decision_hook(KIND, _on_decision)
    _REGISTERED = True


def _matches_card(record, operation):
    payload = record.get("payload") or {}
    return (record.get("approval_id") == operation.get("approval_id")
            and record.get("subject_type") == "site"
            and record.get("subject_id") == operation["operation_id"]
            and payload.get("plan_hash") == operation.get("plan_hash") == _plan_hash(operation)
            and all(payload.get(key) == operation.get(key)
                    for key in ("site_id", "operation_id", "conversation_id", "project_id", "build_id")))


def _approved_operation(record):
    payload = record.get("payload") or {}
    site = get_site(payload.get("site_id"))
    validate_site_owner(site)
    operation = _operation(site, payload.get("operation_id"))
    sites_privacy.require_operation(operation.get("privacy_generation"), operation.get("privacy_boot"))
    if (not _matches_card(record, operation) or operation["site_revision"] != site["revision"]
            or operation["conversation_id"] != site["conversation_id"]
            or operation.get("project_id") != site.get("project_id")):
        raise UserFacingValueError("The site or approval changed. Prepare a new operation.")
    from agent_friday.services import approvals
    stored = approvals.get_approval(record["approval_id"])
    if not stored or stored.get("status") != "approved" or not stored.get("executing_at"):
        raise UserFacingValueError("This operation has no current, claimed approval.")
    return site, operation


def _on_decision(record):
    from agent_friday.services import approvals
    if not isinstance(record, dict) or record.get("kind") != KIND:
        return
    payload = record.get("payload") or {}
    with LOCK:
        try:
            site = get_site(payload.get("site_id"))
            operation = _operation(site, payload.get("operation_id"))
            sites_privacy.require_operation(operation.get("privacy_generation"), operation.get("privacy_boot"))
            if not _matches_card(record, operation) or operation["status"] != "awaiting_approval":
                return
        except (ValueError, TypeError, KeyError):
            return
        if record.get("status") in ("denied", "expired", "blocked"):
            operation["status"] = record["status"]
            _save_operation(operation)
            if operation["action"] == "build":
                from agent_friday.services.site_builds import build_dir
                build = _build(site, operation["build_id"])
                if build["status"] == "awaiting_approval":
                    build["status"] = record["status"]
                    _write(build_dir(site["site_id"], build["build_id"]) / "build.json", build)
            return
        if record.get("status") != "approved" or not approvals.claim_for_execution(record["approval_id"]):
            return
        # Claim before dispatch. Interrupted operations are inspected, never replayed.
        operation.update(status="applying", started_at=time.time(), executor_instance=_INSTANCE)
        _save_operation(operation)
    threading.Thread(target=_perform, args=(copy.deepcopy(record),), daemon=True, name="site-operation").start()


def _perform(record):
    from agent_friday.services import site_builds, publish_adapters
    from agent_friday.governance import action_gate
    payload = record["payload"]
    site = get_site(payload["site_id"])
    operation = _operation(site, payload["operation_id"])
    remote_started = False
    try:
        sites_privacy.require_operation(operation.get("privacy_generation"), operation.get("privacy_boot"))
        _durable()
        site, operation = _approved_operation(record)
        build = _build(site, operation["build_id"])
        if operation["action"] == "build":
            action_gate.record_external(KIND, surface="approval_card", approval_id=record["approval_id"], target=site["codebase_id"])
            result = site_builds.execute_build(site, build)
            from agent_friday.services import conversations
            with LOCK, conversations._LOCK:
                _approved_operation(record)
                build.update(result, finished_at=time.time())
                _write(site_builds.build_dir(site["site_id"], build["build_id"]) / "build.json", build)
                operation.update(status=build["status"], error=build.get("error"), build_id=build["build_id"], finished_at=time.time())
                _save_operation(operation)
                return
        else:
            from agent_friday.services import site_hosting, conversations
            with ExitStack() as stack:
                stack.enter_context(LOCK)
                stack.enter_context(site_hosting.LOCK)
                if site.get("domain"):
                    from agent_friday.services import domain_accounts
                    stack.enter_context(domain_accounts.LOCK)
                if any(other["operation_id"] != operation["operation_id"] and other["action"] == "publish"
                       and other["status"] in ("applying", "unknown")
                       for other in _records(site, "operations", "op-*.json")):
                    raise UserFacingValueError("Another publication is unresolved; no additional write was started.")
                connection = _connection(site, operation["connection"])
                _binding(site, operation["privacy_generation"])
                bundle, _scan = _bundle(site, build, operation["operation_id"])
                if bundle.manifest() != operation["files"]:
                    raise UserFacingValueError("The approved output changed. Prepare a new publication.")
                # The owner cannot move while a bounded external mutation is in flight.
                stack.enter_context(conversations._LOCK)
                _approved_operation(record)
                action_gate.record_external(KIND, surface="approval_card", approval_id=record["approval_id"], target=operation["connection"]["connection_id"])
                remote_started = True
                receipt = publish_adapters.publish_site(bundle, connection, hostname=(site.get("domain") or {}).get("hostname", ""),
                                                        privacy_generation=operation["privacy_generation"])
                operation.update(receipt)
                operation["finished_at"] = time.time()
                _save_operation(operation)
                return
    except Exception:
        # Provider errors may contain credential echoes. No exception text is persisted or returned.
        operation.update(status="unknown" if remote_started else "failed", verified=False,
                         error="The provider outcome is uncertain. Inspect this operation before any new publish." if remote_started else
                               "The operation could not complete. Check ownership, source, connection and the build output before preparing it again.")
    operation["finished_at"] = time.time()
    try:
        with LOCK:
            _save_operation(operation)
            if operation["action"] == "build":
                sites_privacy.require_operation(operation.get("privacy_generation"), operation.get("privacy_boot"))
                failed_build = _build(site, operation["build_id"])
                if (failed_build.get("status") == "awaiting_approval"
                        and failed_build.get("operation_id") == operation["operation_id"]
                        and all(failed_build.get(key) == operation.get(key)
                                for key in ("site_id", "build_id", "site_revision", "privacy_generation", "privacy_boot"))):
                    failed_build.update(status="failed", error=operation["error"], finished_at=operation["finished_at"])
                    _write(site_builds.build_dir(site["site_id"], operation["build_id"]) / "build.json", failed_build)
    except ValueError:
        # A privacy transition invalidates delayed public receipts too.
        return


def preview_file(site_id, build_id, rel, *, generation):
    sites_privacy.require_generation(generation)
    from agent_friday.services import site_builds
    site = validate_site_owner(get_site(site_id))
    build = _build(site, build_id)
    if build.get("status") != "built":
        raise UserFacingValueError("This build has no successful preview.")
    path = contained(site_builds.build_dir(site_id, build_id) / "output", rel)
    site_builds._regular(path)
    file = next((item for item in build.get("files", []) if item["path"] == rel), None)
    if not file or path.stat().st_size > site_builds.MAX_FILE_BYTES:
        raise UserFacingValueError("That preview file is unavailable.")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != file["sha256"]:
        raise UserFacingValueError("Build output changed. Prepare a new build.")
    sites_privacy.require_generation(generation)
    return data


def _execute(action, args, caller):
    """One owner-scoped contract for UI, chat and voice; never executes approval tokens from input."""
    if action not in ACTIONS or (args is not None and not isinstance(args, dict)):
        raise UserFacingValueError("Choose a supported Sites action and object arguments.")
    args = dict(args or {})
    if set(args) - ACTION_FIELDS[action]:
        raise UserFacingValueError("Unsupported Sites fields. Ownership comes from the current chat.")
    if action == "save":
        return {"status": "ok", "site": _overview(_save(args, caller))}
    if action == "list":
        sites = []
        for path in _root().glob("site-*/site.json"):
            site = _read(path)
            if site and site["conversation_id"] == caller["conversation_id"]:
                try:
                    validate_site_owner(site, caller)
                except ValueError:
                    continue
                sites.append(_overview(site))
        return {"status": "ok", "sites": sites}
    site = validate_site_owner(get_site(args.get("site_id")), caller)
    if action == "inspect":
        return {"status": "ok", "site": _overview(site)}
    if action in ("build", "prepare_publish"):
        return _prepare(site, "build" if action == "build" else "publish", args, caller["_sites_generation"])
    if action == "preview":
        build = _build(site, args.get("build_id"))
        _bundle(site, build)
        return {"status": "ok", "site_id": site["site_id"], "site_revision": site["revision"],
                "build_id": build["build_id"]}
    if action == "hosting_requirements":
        from agent_friday.services import publish_adapters
        binding = site.get("domain")
        if not binding:
            raise UserFacingValueError("Select the site's domain first.")
        return {"status": "ok", "requirements": publish_adapters.site_dns_requirements(_connection(site), binding["hostname"], binding["domain"])}
    operation = _operation(site, args.get("operation_id"))
    if action == "deployment_status":
        from agent_friday.services import publish_adapters, site_verification
        if operation["action"] != "publish":
            raise UserFacingValueError("Choose a publication operation.")
        if operation["status"] in {"awaiting_approval", "denied", "expired", "blocked", "refused", "failed"}:
            raise UserFacingValueError("This publication has not started. Inspect its review before checking the provider.")
        if operation["status"] == "applying":
            return {"status": "ok", "operation": operation, "operation_id": operation["operation_id"]}
        observed_revision = operation.get("record_revision", 0)
        connection = _connection(site, operation["connection"], allow_reconnected=True)
        # Read-back belongs to this fresh call; it never renews mutation authority.
        generation = caller["_sites_generation"]
        prior_status = operation["status"]
        try:
            receipt = publish_adapters.site_deployment_status(connection, operation, privacy_generation=generation)
            sites_privacy.require_generation(generation)
            validate_site_owner(site, caller)
            operation.update(provider_evidence=receipt, checked_at=time.time())
            evidence = site_verification.verify(site, operation, generation=generation)
            operation["verification"] = evidence
            if evidence["verified"]:
                operation.update(status="verified_live", verified=True, verified_at=evidence["verified_at"])
            elif prior_status in ("applying", "unknown"):
                operation.update(status="unknown", verified=False)
            else:
                operation.update(status=evidence["status"] if receipt["status"] == "provider_ready" else receipt["status"], verified=False)
        except Exception:
            operation.update(checked_at=time.time(), status="unknown", verified=False, error="Provider status is unavailable. No mutation was retried.")
        sites_privacy.require_generation(generation)
        # Fresh read-back may add evidence to an old public plan, while its
        # mutation authority and original generation remain unchanged.
        from agent_friday.services import conversations, site_hosting
        with LOCK, site_hosting.LOCK, conversations._LOCK:
            current = validate_site_owner(get_site(site["site_id"]), caller)
            if current["revision"] != site["revision"]:
                raise UserFacingValueError("The site changed during verification. Check its current deployment again.")
            _connection(current, operation["connection"], allow_reconnected=True)
            latest = _operation(current, operation["operation_id"])
            if latest.get("record_revision", 0) != observed_revision:
                # A publication receipt or newer observation won this race.
                # Discard the stale whole observation, never its newer receipt.
                operation = latest
            else:
                _save_operation(operation, generation=generation)
    return {"status": "ok", "operation": operation, "operation_id": operation["operation_id"]}


def execute(action, args=None, context=None):
    caller = _caller(context)
    result = _execute(action, args, caller)
    sites_privacy.require_generation(caller["_sites_generation"])
    _caller(caller)
    if isinstance(args, dict) and args.get("site_id"):
        validate_site_owner(get_site(args["site_id"]), caller)
    return result
