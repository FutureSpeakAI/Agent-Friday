"""Synthetic Sites ownership, immutable build and exact-approval boundaries."""
import json
from types import SimpleNamespace

import pytest

from agent_friday.services import sites_operations as sites, site_builds as builds


@pytest.fixture
def world(monkeypatch, tmp_path):
    from agent_friday.services import codebases, conversations, projects, publish_web, approvals
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(sites.sites_privacy, "admit", lambda context: 0)
    monkeypatch.setattr(sites.sites_privacy, "require_generation", lambda generation: SimpleNamespace(generation=generation))
    monkeypatch.setattr(sites, "_durable", lambda: None)
    monkeypatch.setattr(sites, "_REGISTERED", False)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "index.html").write_text("<h1>Original</h1>", encoding="utf-8")
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    conv = {"id": "chat-test", "project": "project-test", "codebase": "cb-test"}
    cb = {"id": "cb-test", "conversation_id": "chat-test", "repo": str(repo)}
    monkeypatch.setattr(conversations, "load", lambda cid: conv if cid == "chat-test" else None)
    monkeypatch.setattr(projects, "load", lambda pid: {"id": pid} if pid == "project-test" else None)
    monkeypatch.setattr(codebases, "load", lambda cid: cb if cid == "cb-test" else None)
    monkeypatch.setattr(codebases, "repo_path", lambda cid: repo)
    monkeypatch.setattr(codebases, "_git", lambda _repo, *args: SimpleNamespace(stdout="" if args[0] == "status" else "a" * 40))
    monkeypatch.setattr(publish_web, "scan", lambda bundle: {"ok": True, "refusals": [], "warnings": []})
    cards = []
    def approval(**kwargs):
        guard = kwargs.pop("_before_publish", None)
        if guard:
            guard()
        card = dict(kwargs, approval_id="approval-" + str(len(cards)), status="pending", created_at=0)
        cards.append(card)
        approvals.APPROVALS_FILE.write_text(json.dumps(cards), encoding="utf-8")
        return card
    monkeypatch.setattr(approvals, "create_approval", approval)
    caller = {"conversation_id": "chat-test", "project_id": "project-test"}
    site = sites.execute("save", {"name": "Demo", "codebase_id": "cb-test"}, caller)["site"]
    return SimpleNamespace(repo=repo, conv=conv, cb=cb, caller=caller, site=site, cards=cards, tmp=tmp_path)


def test_saved_identity_and_revision_survive_same_title(world):
    second = sites.execute("save", {"name": "Demo", "codebase_id": "cb-test"}, world.caller)["site"]
    assert second["site_id"] != world.site["site_id"]
    changed = sites.execute("save", {"site_id": world.site["site_id"], "revision": 1, "name": "Updated"}, world.caller)["site"]
    assert changed["revision"] == 2
    with pytest.raises(ValueError, match="changed"):
        sites.execute("save", {"site_id": world.site["site_id"], "revision": 1, "name": "Stale"}, world.caller)
    assert sites.get_site(world.site["site_id"])["name"] == "Updated"


@pytest.mark.parametrize("context", [{"nested_execution": True}, {"scope": "codebase:other"}, {"schedule_id": "timer"}])
def test_nested_callers_cannot_read_or_prepare_sites(world, context):
    for action in ("list", "inspect", "build"):
        with pytest.raises(ValueError, match="background"):
            sites.execute(action, {"site_id": world.site["site_id"]}, dict(world.caller, **context))
    assert world.cards == []


def test_spoofed_ownership_and_refiled_chat_are_rejected(world):
    with pytest.raises(ValueError, match="Ownership"):
        sites.execute("save", {"name": "Bad", "conversation_id": "elsewhere"}, world.caller)
    world.conv["project"] = None
    with pytest.raises(ValueError, match="moved"):
        sites.execute("inspect", {"site_id": world.site["site_id"]}, world.caller)


def test_build_preparation_freezes_source_without_running_it(world, monkeypatch):
    from agent_friday.services import codebases
    monkeypatch.setattr(codebases, "run", lambda *a, **kw: pytest.fail("Preparation cannot execute a command"))
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    assert result["approval"]["force_gate"] is True
    build = sites._build(world.site, result["operation"]["build_id"])
    (world.repo / "index.html").write_text("<h1>Unapproved change</h1>", encoding="utf-8")
    output = builds.execute_build(world.site, build)
    assert output["status"] == "built"
    assert "preview_url" not in output
    assert "preview_url" not in result["approval"]["payload"]
    data = (builds.build_dir(world.site["site_id"], build["build_id"]) / "output" / "index.html").read_text()
    assert "Original" in data and "Unapproved" not in data


def test_changed_frozen_snapshot_cannot_execute(world):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    build = sites._build(world.site, result["operation"]["build_id"])
    source = builds.build_dir(world.site["site_id"], build["build_id"]) / "source"
    (source / "index.html").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen source changed"):
        builds.execute_build(world.site, build)


def test_denied_expired_or_duplicate_approval_never_runs_twice(world, monkeypatch):
    from agent_friday.services import approvals
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    record = dict(result["approval"])
    launched = []
    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def start(self):
            launched.append(self.kwargs)
    monkeypatch.setattr(sites.threading, "Thread", Thread)
    sites._on_decision(dict(record, status="denied"))
    sites._on_decision(dict(record, status="expired"))
    assert launched == []
    assert sites._operation(world.site, result["operation_id"])["status"] == "denied"
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    record = dict(result["approval"])
    record["status"] = "approved"
    approvals.APPROVALS_FILE.write_text(json.dumps([record]), encoding="utf-8")
    sites._on_decision(record)
    sites._on_decision(record)
    assert len(launched) == 1


def test_stale_owner_and_tampered_plan_prevent_execution(world):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    operation = result["operation"]
    operation["source_hash"] = "changed"
    sites._save_operation(operation)
    with pytest.raises(ValueError, match="changed"):
        sites._approved_operation(result["approval"])
    world.conv["project"] = None
    with pytest.raises(ValueError, match="moved"):
        sites._approved_operation(result["approval"])


def test_repeated_build_request_reuses_the_pending_exact_review(world):
    first = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    second = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    assert first["operation_id"] == second["operation_id"] and len(world.cards) == 1


def test_command_cannot_store_embedded_credentials(world):
    with pytest.raises(ValueError, match="credentials"):
        sites.execute("save", {"name": "Bad", "codebase_id": "cb-test", "build_command": "$env:API_KEY=synthetic; npm run build"}, world.caller)


def test_internal_build_execution_rejects_unrelated_directory(world):
    with pytest.raises(ValueError, match="frozen build workspace"):
        builds.execution_context("cb-test", world.repo)


def test_approved_build_refuses_rebound_codebase_before_running(world, monkeypatch):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    monkeypatch.setattr(builds, "execute_build", lambda *a: pytest.fail("No build after ownership changes"))
    world.cb["conversation_id"] = "chat-other"
    sites._perform(result["approval"])
    assert sites._operation(world.site, result["operation_id"])["status"] == "failed"


def test_restart_during_execution_returns_unknown_without_replaying(world):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    operation = result["operation"]
    operation.update(status="applying", executor_instance="earlier-process")
    sites._save_operation(operation)
    restored = sites._operation(world.site, operation["operation_id"])
    assert restored["status"] == "unknown" and restored["verified"] is False


def _claim(result):
    from agent_friday.services import approvals
    record = dict(result["approval"], status="approved", executing_at=1)
    approvals.APPROVALS_FILE.write_text(json.dumps([record]), encoding="utf-8")
    return record


def test_integrity_hold_prevents_even_an_approved_build(world, monkeypatch):
    from agent_friday.governance import action_gate
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    record = _claim(result)
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **kw: (_ for _ in ()).throw(action_gate.Held("synthetic hold")))
    monkeypatch.setattr(builds, "execute_build", lambda *a: pytest.fail("Held builds cannot execute"))
    sites._perform(record)
    assert sites._operation(world.site, result["operation_id"])["status"] == "failed"


def _publication(world, monkeypatch):
    from agent_friday.services import site_hosting
    connection = {"connection_id": "host-" + "a" * 32, "revision": 1, "adapter": "github_pages", "repo": "example/site", "branch": "gh-pages", "token": "synthetic"}
    monkeypatch.setattr(site_hosting, "get_connection", lambda cid, **kw: dict(connection))
    monkeypatch.setattr(site_hosting, "require_site_target", lambda connection: None)
    saved = sites.execute("save", {"site_id": world.site["site_id"], "revision": 1, "hosting": {"connection_id": connection["connection_id"]}}, world.caller)["site"]
    built = sites.execute("build", {"site_id": saved["site_id"]}, world.caller)
    build = sites._build(saved, built["operation"]["build_id"])
    build.update(builds.execute_build(saved, build))
    sites._write(builds.build_dir(saved["site_id"], build["build_id"]) / "build.json", build)
    result = sites.execute("prepare_publish", {"site_id": saved["site_id"], "build_id": build["build_id"]}, world.caller)
    return saved, result, connection


def test_integrity_hold_happens_before_any_provider_write(world, monkeypatch):
    from agent_friday.governance import action_gate
    from agent_friday.services import publish_adapters
    site, result, _connection = _publication(world, monkeypatch)
    record = _claim(result)
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **kw: (_ for _ in ()).throw(action_gate.Held("synthetic hold")))
    monkeypatch.setattr(publish_adapters, "publish_site", lambda *a, **kw: pytest.fail("Held publication cannot contact provider"))
    sites._perform(record)
    assert sites._operation(site, result["operation_id"])["status"] == "failed"


def test_reconnected_host_prevents_old_approved_publication(world, monkeypatch):
    from agent_friday.services import publish_adapters
    site, result, connection = _publication(world, monkeypatch)
    record = _claim(result)
    connection["revision"] = 2
    monkeypatch.setattr(publish_adapters, "publish_site", lambda *a, **kw: pytest.fail("No stale connection publication"))
    sites._perform(record)
    assert sites._operation(site, result["operation_id"])["status"] == "failed"


def test_unknown_outcome_does_not_permit_a_blind_republish(world, monkeypatch):
    site, result, _connection = _publication(world, monkeypatch)
    operation = result["operation"]
    operation["status"] = "unknown"
    sites._save_operation(operation)
    with pytest.raises(ValueError, match="unresolved"):
        sites.execute("prepare_publish", {"site_id": site["site_id"], "build_id": operation["build_id"]}, world.caller)


def test_domain_binding_refuses_stale_generation(world, monkeypatch):
    from agent_friday.services import domain_operations
    monkeypatch.setattr(domain_operations, "validate_binding", lambda *args: {"account_id": "account-test", "account_revision": 2, "domain": "example.test"})
    with pytest.raises(ValueError, match="account changed"):
        sites.execute("save", {"site_id": world.site["site_id"], "revision": 1,
            "domain": {"account_id": "account-test", "account_revision": 1, "domain": "example.test", "hostname": "www.example.test"}}, world.caller)


def test_later_unknown_publish_does_not_erase_or_overstate_verified_history(world):
    for suffix, created, status in (("a", 1, "verified_live"), ("b", 2, "unknown")):
        operation = {"operation_id": "op-" + suffix * 32, "site_id": world.site["site_id"], "action": "publish", "status": status,
                     "privacy_generation": 0,
                     "created_at": created, "verified": status == "verified_live", "verified_at": 1.5 if suffix == "a" else None}
        sites._save_operation(operation)
    overview = sites._overview(world.site)
    assert overview["latest_deployment"]["status"] == "unknown"
    assert overview["last_verified_deployment"]["operation_id"] == "op-" + "a" * 32
    assert overview["current_deployment"]["status"] == "previously_verified"
    assert overview["current_deployment"]["verified"] is False


def test_privacy_generation_is_pinned_inside_the_approval_plan(world, monkeypatch):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    operation = result["operation"]
    original_hash = operation["plan_hash"]
    operation["privacy_generation"] = 1
    assert sites._plan_hash(operation) != original_hash
    monkeypatch.setattr(sites.sites_privacy, "require_generation", lambda generation: (_ for _ in ()).throw(ValueError("privacy ended")))
    monkeypatch.setattr(builds, "execute_build", lambda *a: pytest.fail("Expired origin must not execute"))
    sites._on_decision(dict(result["approval"], status="approved"))
    assert sites.get_site(world.site["site_id"])["privacy_generation"] == 0


def test_provider_wait_that_changes_privacy_writes_no_status_or_result(world, monkeypatch):
    from agent_friday.services import publish_adapters, site_verification
    site, result, _connection = _publication(world, monkeypatch)
    original = sites._operation(site, result["operation_id"])
    original["status"] = "unknown"
    sites._save_operation(original)
    active = {"valid": True}
    def require(generation):
        if not active["valid"]:
            raise ValueError("privacy ended")
        return SimpleNamespace(generation=generation)
    def provider(*args, **kwargs):
        active["valid"] = False
        return {"status": "provider_ready"}
    monkeypatch.setattr(sites.sites_privacy, "require_generation", require)
    monkeypatch.setattr(publish_adapters, "site_deployment_status", provider)
    monkeypatch.setattr(site_verification, "verify", lambda *a: pytest.fail("No new public probe after origin expires"))
    with pytest.raises(ValueError, match="privacy"):
        sites.execute("deployment_status", {"site_id": site["site_id"], "operation_id": result["operation_id"]}, world.caller)
    active["valid"] = True
    assert sites._operation(site, result["operation_id"]) == original


def test_build_launch_rechecks_owner_after_preparation(world, monkeypatch):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    record = _claim(result)
    operation = result["operation"]
    operation.update(status="applying", executor_instance=sites._INSTANCE)
    sites._save_operation(operation)
    snapshot = builds.build_dir(world.site["site_id"], operation["build_id"]) / "source"
    world.conv["project"] = None
    with pytest.raises(ValueError, match="moved"):
        with builds.launch_guard("cb-test", snapshot, ""):
            pytest.fail("Moved owner cannot launch a build process")
    assert record["executing_at"] == 1


@pytest.mark.parametrize("path", ["../output", "C:/private", "/absolute", "a\\b", "x/../../private"])
def test_build_paths_cannot_escape(world, path):
    with pytest.raises(ValueError, match="relative"):
        sites.execute("save", {"name": "Bad", "codebase_id": "cb-test", "output_dir": path}, world.caller)


def test_build_environment_has_no_inherited_credentials_or_user_configs(world, monkeypatch):
    monkeypatch.setenv("NAMECOM_API_TOKEN", "synthetic-token")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-token")
    monkeypatch.setenv("NPM_CONFIG_TOKEN", "synthetic-token")
    snapshot = world.tmp / "build" / "source"
    snapshot.mkdir(parents=True)
    env = builds.isolated_environment(snapshot)
    assert not {"NAMECOM_API_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "NPM_CONFIG_TOKEN"} & env.keys()
    assert env["HOME"] == str(snapshot.parent / "home")
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_source_secrets_and_output_server_files_are_refused(world):
    secret = world.repo / ".env.production"
    secret.write_text("SYNTHETIC=value", encoding="utf-8")
    with pytest.raises(ValueError, match="credential"):
        builds.collect(world.repo)
    secret.unlink()
    (world.repo / "server.py").write_text("print('server')", encoding="utf-8")
    with pytest.raises(ValueError, match="non-static"):
        builds.collect(world.repo, output=True)


def test_link_and_oversized_output_are_refused(world, monkeypatch):
    import stat
    monkeypatch.setattr(builds.os, "walk", lambda *a, **kw: [(str(world.repo), [], ["index.html"])])
    original = builds.Path.lstat
    def info(path):
        if path.name == "index.html":
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path)
    monkeypatch.setattr(builds.Path, "lstat", info)
    with pytest.raises(ValueError, match="symbolic"):
        builds.collect(world.repo, output=True)


@pytest.mark.parametrize("action,args", [
    ("inspect", {"build_command": "unused"}),
    ("list", {"domain": {"account_id": "unrelated"}}),
    ("build", {"build_id": "override"}),
    ("prepare_publish", {"output_dir": "other"}),
])
def test_action_specific_fields_are_rejected_before_site_access(world, monkeypatch, action, args):
    monkeypatch.setattr(sites, "get_site", lambda *a: pytest.fail("Unsupported fields must not select a site"))
    with pytest.raises(ValueError, match="Unsupported Sites fields"):
        sites.execute(action, dict(args, **({} if action == "list" else {"site_id": world.site["site_id"]})), world.caller)


def test_pending_review_from_another_boot_is_never_executed(world, monkeypatch):
    from agent_friday.services import approvals
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    operation = result["operation"]
    original_hash = operation["plan_hash"]
    operation["privacy_boot"] = "previous-process"
    assert sites._plan_hash(operation) != original_hash
    operation["plan_hash"] = sites._plan_hash(operation)
    sites._save_operation(operation)
    record = dict(result["approval"], status="approved")
    record["payload"]["plan_hash"] = operation["plan_hash"]
    monkeypatch.setattr(approvals, "claim_for_execution", lambda *_: pytest.fail("Old boot approval cannot be claimed"))
    sites._on_decision(record)
    assert sites._operation(world.site, operation["operation_id"])["status"] == "refused"


def test_stale_applying_operation_is_unknown_in_the_site_overview(world):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    operation = result["operation"]
    operation.update(status="applying", executor_instance=sites._INSTANCE, privacy_boot="previous-process")
    sites._save_operation(operation)
    overview = sites.execute("inspect", {"site_id": world.site["site_id"]}, world.caller)["site"]
    assert overview["history"][0]["status"] == "unknown"


def test_slow_approval_preparation_cannot_publish_after_owner_moves(world, monkeypatch):
    from agent_friday.services import approvals
    def slow_card(**kwargs):
        world.conv["project"] = None
        kwargs["_before_publish"]()
        pytest.fail("A card cannot appear for the moved owner")
    monkeypatch.setattr(approvals, "create_approval", slow_card)
    with pytest.raises(ValueError, match="moved"):
        sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    assert world.cards == []


def test_late_build_result_after_owner_moves_is_not_saved(world, monkeypatch):
    from agent_friday.governance import action_gate
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    record = _claim(result)
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **kw: None)
    def finished(*args):
        world.conv["project"] = None
        return {"status": "built", "run": {"output": "late private-looking output"}}
    monkeypatch.setattr(builds, "execute_build", finished)
    sites._perform(record)
    build = sites._build(world.site, result["operation"]["build_id"])
    assert "run" not in build
    assert sites._operation(world.site, result["operation_id"])["status"] == "failed"


def test_provider_write_losing_public_generation_retains_unknown_without_replay(world, monkeypatch):
    from agent_friday.services import publish_adapters
    from agent_friday.governance import action_gate
    site, result, _connection = _publication(world, monkeypatch)
    record = _claim(result)
    operation = result["operation"]
    operation.update(status="applying", executor_instance=sites._INSTANCE)
    sites._save_operation(operation)
    active = {"current": True}
    def require(generation):
        if not active["current"]:
            raise ValueError("privacy ended")
        return SimpleNamespace(generation=generation)
    calls = []
    def publish(*args, **kwargs):
        calls.append(kwargs)
        active["current"] = False
        return {"status": "provider_accepted", "provider_deployment_id": "late-provider-result"}
    monkeypatch.setattr(sites.sites_privacy, "require_generation", require)
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **kw: None)
    monkeypatch.setattr(publish_adapters, "publish_site", publish)
    sites._perform(record)
    observed = sites._operation(site, operation["operation_id"])
    assert observed["status"] == "unknown" and "provider_deployment_id" not in observed
    assert len(calls) == 1 and calls[0]["privacy_generation"] == 0
    active["current"] = True
    with pytest.raises(ValueError, match="unresolved"):
        sites.execute("prepare_publish", {"site_id": site["site_id"], "build_id": operation["build_id"]}, world.caller)
    assert len(calls) == 1


def test_fresh_readback_never_refreshes_original_mutation_authority(world, monkeypatch):
    from agent_friday.services import publish_adapters, site_verification
    site, result, _connection = _publication(world, monkeypatch)
    operation = result["operation"]
    operation.update(status="unknown", privacy_boot="previous-process")
    sites._save_operation(operation)
    original_hash = operation["plan_hash"]
    monkeypatch.setattr(sites.sites_privacy, "admit", lambda context: 1)
    generations = []
    monkeypatch.setattr(publish_adapters, "site_deployment_status",
        lambda *a, **kw: generations.append(kw["privacy_generation"]) or {"status": "provider_ready"})
    monkeypatch.setattr(site_verification, "verify",
        lambda *a, **kw: generations.append(kw["generation"]) or {"status": "verified_live", "verified": True, "verified_at": 10})
    observed = sites.execute("deployment_status", {"site_id": site["site_id"], "operation_id": operation["operation_id"]}, world.caller)["operation"]
    assert observed["status"] == "verified_live" and generations == [1, 1]
    assert observed["privacy_generation"] == 0 and observed["privacy_boot"] == "previous-process"
    assert observed["plan_hash"] == original_hash
    with pytest.raises(ValueError):
        sites._approved_operation(result["approval"])

@pytest.mark.parametrize("path", ["_worker.js", "_worker.js/index.js", "functions/api.js", "_routes.json"])
def test_static_output_refuses_server_function_bundles(world, path):
    target = world.repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("export default {}", encoding="utf-8")
    with pytest.raises(ValueError, match="server functions"):
        builds.collect(world.repo, output=True)

def test_readback_after_host_token_rotation_keeps_old_mutation_revision(world, monkeypatch):
    from agent_friday.services import publish_adapters, site_verification
    site, result, connection = _publication(world, monkeypatch)
    operation = result["operation"]
    operation.update(status="unknown")
    sites._save_operation(operation)
    connection["revision"] = 2
    seen = []
    monkeypatch.setattr(publish_adapters, "site_deployment_status",
        lambda conn, *a, **kw: seen.append(conn["revision"]) or {"status": "provider_ready"})
    monkeypatch.setattr(site_verification, "verify",
        lambda *a, **kw: {"status": "verified_live", "verified": True, "verified_at": 10})
    observed = sites.execute("deployment_status", {"site_id": site["site_id"], "operation_id": operation["operation_id"]}, world.caller)["operation"]
    assert seen == [2] and observed["status"] == "verified_live"
    assert observed["connection"]["revision"] == 1
    with pytest.raises(ValueError, match="connection changed"):
        sites._connection(site, observed["connection"])


@pytest.mark.parametrize("decision", ["denied", "expired", "blocked", "approved"])
def test_old_decision_cannot_replace_a_completed_operation_or_build(world, decision):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    operation = result["operation"]
    operation.update(status="built", finished_at=10)
    sites._save_operation(operation)
    build = sites._build(world.site, operation["build_id"])
    build["status"] = "built"
    path = builds.build_dir(world.site["site_id"], build["build_id"]) / "build.json"
    sites._write(path, build)
    before = path.read_bytes()
    sites._on_decision(dict(result["approval"], status=decision))
    assert sites._operation(world.site, operation["operation_id"]) == operation
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("approval_id", "other"), ("subject_type", "other"), ("subject_id", "other")])
def test_unrelated_denial_cannot_change_a_pending_operation(world, field, value):
    result = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    record = dict(result["approval"], status="denied", **{field: value})
    sites._on_decision(record)
    assert sites._operation(world.site, result["operation_id"]) == result["operation"]


def test_pending_publication_status_never_reads_provider_or_rewrites_review(world, monkeypatch):
    from agent_friday.services import publish_adapters
    site, result, _connection = _publication(world, monkeypatch)
    monkeypatch.setattr(publish_adapters, "site_deployment_status", lambda *a, **k: pytest.fail("No publication yet"))
    with pytest.raises(ValueError, match="not started"):
        sites.execute("deployment_status", {"site_id": site["site_id"], "operation_id": result["operation_id"]}, world.caller)
    assert sites._operation(site, result["operation_id"]) == result["operation"]


@pytest.mark.parametrize("newer_status", ["provider_accepted", "verified_live"])
def test_slow_readback_cannot_overwrite_newer_receipt_or_verification(world, monkeypatch, newer_status):
    from agent_friday.services import publish_adapters, site_verification
    site, result, _connection = _publication(world, monkeypatch)
    operation = result["operation"]
    operation["status"] = "unknown"
    sites._save_operation(operation)
    committed = {}
    def provider(*args, **kwargs):
        latest = sites._operation(site, operation["operation_id"])
        latest.update(status=newer_status, provider_deployment_id="a" * 40, finished_at=10,
                      verified=newer_status == "verified_live", checked_at=20)
        if latest["verified"]:
            latest["verification"] = {"verified": True, "verified_at": 20}
        sites._save_operation(latest)
        committed.update(latest)
        return {"status": "unknown"}
    monkeypatch.setattr(publish_adapters, "site_deployment_status", provider)
    monkeypatch.setattr(site_verification, "verify", lambda *a, **k: {"status": "content_pending", "verified": False})
    observed = sites.execute("deployment_status", {"site_id": site["site_id"], "operation_id": operation["operation_id"]}, world.caller)["operation"]
    assert observed == committed
    assert sites._operation(site, operation["operation_id"]) == committed


@pytest.mark.parametrize(("build_root", "output_dir"), [
    (".", "dist"), ("web", "."), (".", "."), ("web", "dist"),
])
def test_command_build_accepts_root_and_nested_directories(world, monkeypatch, build_root, output_dir):
    from agent_friday.services import codebases
    source = world.repo if build_root == "." else world.repo / build_root
    source.mkdir(exist_ok=True)
    (source / "entry.txt").write_text("captured input", encoding="utf-8")
    site = sites.execute("save", {"site_id": world.site["site_id"], "revision": 1,
        "build_root": build_root, "output_dir": output_dir, "build_command": "node build.mjs"}, world.caller)["site"]
    prepared = sites.execute("build", {"site_id": site["site_id"]}, world.caller)
    build = sites._build(site, prepared["operation"]["build_id"])
    frozen = builds.build_dir(site["site_id"], build["build_id"]) / "source"
    expected_cwd = frozen if build_root == "." else frozen / build_root
    (source / "entry.txt").write_text("unapproved working copy", encoding="utf-8")
    commands = []

    def command(cid, command, *, timeout_s, _site_snapshot):
        commands.append((cid, command, _site_snapshot))
        assert _site_snapshot == expected_cwd
        assert (_site_snapshot / "entry.txt").read_text(encoding="utf-8") == "captured input"
        output = _site_snapshot if output_dir == "." else _site_snapshot / output_dir
        output.mkdir(exist_ok=True)
        (output / "index.html").write_text("<h1>Built from frozen input</h1>", encoding="utf-8")
        return {"status": "ok", "exit": 0, "output": ""}

    monkeypatch.setattr(codebases, "run", command)
    result = builds.execute_build(site, build)
    assert commands == [("cb-test", "node build.mjs", expected_cwd)]
    assert result["status"] == "built"
    assert (builds.build_dir(site["site_id"], build["build_id"]) / "output" / "index.html").read_text(
        encoding="utf-8") == "<h1>Built from frozen input</h1>"


def _inline_approval_worker(monkeypatch):
    class Thread:
        def __init__(self, *, target, args, daemon, name):
            assert name == "site-operation" and daemon is True
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(sites.threading, "Thread", Thread)


@pytest.mark.parametrize("failure", ["checkpoint", "execution"])
def test_approved_early_failure_finishes_only_its_exact_build(world, monkeypatch, failure):
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals
    prepared = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    other = sites.execute("save", {"name": "Other", "codebase_id": "cb-test"}, world.caller)["site"]
    untouched = sites.execute("build", {"site_id": other["site_id"]}, world.caller)
    other_path = builds.build_dir(other["site_id"], untouched["operation"]["build_id"]) / "build.json"
    other_before = other_path.read_bytes()
    executed = []

    def checkpoint(*args, **kwargs):
        assert kwargs["approval_id"] == prepared["approval_id"]
        if failure == "checkpoint":
            raise action_gate.Held("synthetic checkpoint failure")

    def execute(*args):
        executed.append(True)
        assert failure == "execution", "A held checkpoint must prevent the build command"
        raise RuntimeError("synthetic execution failure")

    monkeypatch.setattr(action_gate, "record_external", checkpoint)
    monkeypatch.setattr(builds, "execute_build", execute)
    _inline_approval_worker(monkeypatch)
    _record, won = approvals.decide_with_outcome(prepared["approval_id"], "approve")
    assert won is True
    assert approvals.get_approval(prepared["approval_id"])["executing_at"]
    operation = sites._operation(world.site, prepared["operation_id"])
    failed = sites._build(world.site, prepared["operation"]["build_id"])
    assert operation["status"] == failed["status"] == "failed"
    assert failed["error"] == operation["error"]
    assert failed["finished_at"] == operation["finished_at"]
    assert "synthetic" not in failed["error"]
    assert executed == ([True] if failure == "execution" else [])
    assert other_path.read_bytes() == other_before
    _record, won = approvals.decide_with_outcome(prepared["approval_id"], "approve")
    assert won is False
    assert executed == ([True] if failure == "execution" else [])


def test_failed_build_does_not_write_after_original_privacy_expires(world, monkeypatch):
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals
    prepared = sites.execute("build", {"site_id": world.site["site_id"]}, world.caller)
    build_path = builds.build_dir(world.site["site_id"], prepared["operation"]["build_id"]) / "build.json"
    operation_path = sites._site_dir(world.site["site_id"]) / "operations" / (prepared["operation_id"] + ".json")
    current = {"valid": True}
    before = {}

    def require(generation):
        if not current["valid"]:
            raise ValueError("original privacy ended")
        return SimpleNamespace(generation=generation)

    def execute(*args):
        before.update(build=build_path.read_bytes(), operation=operation_path.read_bytes())
        current["valid"] = False
        raise RuntimeError("synthetic execution failure")

    monkeypatch.setattr(sites.sites_privacy, "require_generation", require)
    monkeypatch.setattr(action_gate, "record_external", lambda *args, **kwargs: None)
    monkeypatch.setattr(builds, "execute_build", execute)
    _inline_approval_worker(monkeypatch)
    _record, won = approvals.decide_with_outcome(prepared["approval_id"], "approve")
    assert won is True and before
    assert build_path.read_bytes() == before["build"]
    assert operation_path.read_bytes() == before["operation"]
