import copy
import threading

import pytest

from agent_friday.services import domain_accounts as accounts, domain_operations as ops, namecom


UI = {"surface": "sites_ui"}
ACCOUNT = "acct_synthetic"
DOMAIN = "example.com"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from agent_friday.services import off_record, sites_privacy
    monkeypatch.setattr(off_record, "generation", lambda: 3)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    monkeypatch.setitem(UI, "_sites_origin", sites_privacy.capture())
    monkeypatch.setattr(accounts, "_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(accounts, "require_recording", lambda: None)
    monkeypatch.setattr(ops, "_REGISTERED", False)
    state = accounts.read()
    state["accounts"][ACCOUNT] = {"account_id": ACCOUNT, "revision": 1, "label": "Synthetic account", "environment": "sandbox", "connection_status": "verified"}
    accounts.write(state)
    class Provider:
        def __init__(self):
            self.info = {"domainName": DOMAIN, "nameservers": ["ns1.name.com", "ns2.name.com"], "expireDate": "2030-01-01T00:00:00Z", "autorenewEnabled": False}
            self.rows = [{"id": 1, "host": "www", "type": "CNAME", "answer": "old.example.net.", "ttl": 300},
                         {"id": 2, "host": "", "type": "MX", "answer": "mail.example.net.", "ttl": 300, "priority": 10}]
            self.writes = []
            self.quote = {"renewalPrice": 15.25, "premium": False}
        def domain(self, domain):
            return copy.deepcopy(self.info)
        def records(self, domain):
            return copy.deepcopy(self.rows)
        def pricing(self, domain, years):
            return copy.deepcopy(self.quote)
        def put_record(self, domain, record, rid=None):
            self.writes.append(("put", domain, copy.deepcopy(record), rid))
            self.rows = [r for r in self.rows if r["id"] != rid]
            self.rows.append(dict(record, id=rid or 3))
            return {"id": rid or 3}
        def delete_record(self, domain, rid):
            self.writes.append(("delete", domain, rid))
            self.rows = [r for r in self.rows if r["id"] != rid]
        def autorenew(self, domain, enabled):
            self.writes.append(("autorenew", domain, enabled))
            self.info["autorenewEnabled"] = enabled
    provider = Provider()
    monkeypatch.setattr(accounts, "client", lambda rec, **kw: provider)
    from agent_friday.services import approvals
    from agent_friday.governance import action_gate
    cards, claimed, used = {}, set(), []
    def create(**kwargs):
        card = dict(kwargs, approval_id="approval-" + str(len(cards)), status="pending")
        cards[card["approval_id"]] = card
        return copy.deepcopy(card)
    def claim(aid):
        if aid in claimed:
            return False
        claimed.add(aid)
        return True
    monkeypatch.setattr(approvals, "create_approval", create)
    monkeypatch.setattr(approvals, "register_decision_hook", lambda *a: None)
    monkeypatch.setattr(approvals, "claim_for_execution", claim)
    monkeypatch.setattr(approvals, "mark_used", lambda *a, **kw: used.append((a, kw)))
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **kw: None)
    def decide(result, status="approved"):
        card = copy.deepcopy(cards[result["approval_id"]])
        card["status"] = status
        ops._on_decision(card)
        return ops.execute("operation", {"operation_id": result["operation_id"]}, UI)
    return provider, cards, decide, used


def dns_args(**extra):
    return dict(account_id=ACCOUNT, domain=DOMAIN, record_id=1,
                record={"host": "www", "type": "CNAME", "answer": "new.example.net", "ttl": 600}, **extra)


def test_prepare_does_not_mutate_and_card_contains_exact_change(world):
    provider, cards, decide, used = world
    result = ops.execute("prepare_dns", dns_args(), UI)
    assert result["status"] == "awaiting_approval"
    assert provider.writes == []
    assert result["before"]["id"] == 1
    assert result["after"]["answer"] == "new.example.net."
    assert cards[result["approval_id"]]["force_gate"] is True


def test_approved_update_once_preserves_unrelated_mail_and_has_readback(world):
    provider, cards, decide, used = world
    mail_before = copy.deepcopy(provider.rows[1])
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    result = decide(prepared)
    decide(prepared)
    assert len(provider.writes) == 1
    assert mail_before in provider.rows
    assert result["status"] == "provider_verified"
    assert result["evidence"]["public_dns"] == "not_checked"
    assert len(used) == 1


@pytest.mark.parametrize("decision", ["denied", "expired", "blocked"])
def test_nonapproval_causes_zero_writes(world, decision):
    provider, cards, decide, used = world
    result = decide(ops.execute("prepare_dns", dns_args(), UI), decision)
    assert result["status"] == decision
    assert provider.writes == []


def test_intervening_zone_change_invalidates_review(world):
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    provider.rows[1]["priority"] = 20
    result = decide(prepared)
    assert result["status"] == "refused"
    assert provider.writes == []
    assert "DNS changed" in result["message"]


def test_account_revision_change_invalidates_review(world):
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    state = accounts.read()
    state["accounts"][ACCOUNT]["revision"] = 2
    accounts.write(state)
    assert decide(prepared)["status"] == "refused"
    assert provider.writes == []


def test_plan_expiry_prevents_a_write_even_if_card_is_still_approved(world, monkeypatch):
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    monkeypatch.setattr(ops.time, "time", lambda: prepared["expires_at"] + 1)
    result = decide(prepared)
    assert result["status"] == "refused" and "expired" in result["message"]
    assert provider.writes == []


def test_external_nameservers_do_not_produce_dns_mutation_plan(world):
    provider, cards, decide, used = world
    provider.info["nameservers"] = ["ns1.other.example"]
    with pytest.raises(ValueError, match="another DNS provider"):
        ops.execute("prepare_dns", dns_args(), UI)
    assert not cards and not provider.writes


def test_delete_requires_exact_id_and_is_verified(world):
    provider, cards, decide, used = world
    with pytest.raises(ValueError, match="exact DNS record ID"):
        ops.execute("prepare_dns", {"account_id": ACCOUNT, "domain": DOMAIN, "delete": True}, UI)
    result = decide(ops.execute("prepare_dns", {"account_id": ACCOUNT, "domain": DOMAIN, "record_id": 1, "delete": True}, UI))
    assert result["status"] == "provider_verified"
    assert provider.writes == [("delete", DOMAIN, 1)]


def test_multiple_address_records_can_be_added_without_guessing_an_update(world):
    provider, cards, decide, used = world
    provider.rows.append({"id": 9, "host": "", "type": "A", "answer": "192.0.2.1", "ttl": 300})
    args = {"account_id": ACCOUNT, "domain": DOMAIN, "record": {"host": "", "type": "A", "answer": "192.0.2.2", "ttl": 300}}
    prepared = ops.execute("prepare_dns", args, UI)
    assert prepared["before"] is None
    assert "Add" in cards[prepared["approval_id"]]["description"]
    decide(prepared)
    assert any(r["id"] == 9 for r in provider.rows)
    again = ops.execute("prepare_dns", args, UI)
    assert again["status"] == "ok" and len(provider.writes) == 1


def test_unknown_create_is_reconciled_without_replay(world, monkeypatch):
    provider, cards, decide, used = world
    original = provider.put_record
    def apply_then_timeout(*args):
        original(*args)
        raise namecom.ProviderError("Request unconfirmed.", ambiguous=True)
    monkeypatch.setattr(provider, "put_record", apply_then_timeout)
    args = {"account_id": ACCOUNT, "domain": DOMAIN, "record": {"host": "new", "type": "A", "answer": "192.0.2.12", "ttl": 300}}
    prepared = ops.execute("prepare_dns", args, UI)
    assert decide(prepared)["status"] == "unknown"
    checked = ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, UI)
    assert checked["status"] == "provider_verified"
    decide(prepared)
    assert len(provider.writes) == 1


def test_autorenew_card_warns_of_future_charge_and_verifies_exact_boolean(world):
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_autorenew", {"account_id": ACCOUNT, "domain": DOMAIN, "enabled": True}, UI)
    assert "future registrar charges" in cards[prepared["approval_id"]]["description"]
    result = decide(prepared)
    assert result["status"] == "provider_verified"
    assert provider.writes == [("autorenew", DOMAIN, True)]


def test_renewal_is_checkout_review_not_a_paid_approval(world):
    provider, cards, decide, used = world
    review = ops.execute("prepare_renewal", {"account_id": ACCOUNT, "domain": DOMAIN, "years": 2}, UI)
    assert review["status"] == "awaiting_provider_checkout"
    assert review["quote"]["subtotal"] == "15.25" and review["quote"]["total"] is None
    assert review["quote"]["tax"] == "unknown" and review["years"] == 2
    assert review["checkout_url"].startswith("https://www.name.com/")
    assert not cards and not provider.writes
    provider.info["expireDate"] = "2032-01-01T00:00:00Z"
    checked = ops.execute("reconcile", {"operation_id": review["operation_id"]}, UI)
    assert checked["status"] == "renewal_observed"
    assert checked["evidence"]["payment"] == "not_verified"
    assert not provider.writes


@pytest.mark.parametrize("context", [{"task_id": "task"}, {"nested_execution": True}, {"schedule_id": "schedule"}, {"grant_scope": "codebase:synthetic"}])
def test_background_scope_cannot_list_or_mutate_registrar(world, context):
    for action in ("accounts", "prepare_dns"):
        with pytest.raises(ValueError, match="background"):
            ops.execute(action, dns_args() if action == "prepare_dns" else {}, dict(UI, **context))


@pytest.mark.parametrize("action", ops.SERVICE_ACTIONS)
def test_unknown_arguments_fail_before_any_provider_or_store_access(world, monkeypatch, action):
    monkeypatch.setattr(accounts, "read", lambda: pytest.fail("Store read before argument rejection"))
    monkeypatch.setattr(accounts, "client", lambda rec, **kw: pytest.fail("Provider access before argument rejection"))
    with pytest.raises(ValueError, match="unsupported fields"):
        ops.execute(action, {"unexpected_authority": True}, UI)


@pytest.mark.parametrize("args", [[], "", False, 0])
def test_nonobject_arguments_are_not_treated_as_empty_requests(world, args):
    with pytest.raises(ValueError, match="supported domain action"):
        ops.execute("accounts", args, UI)


def test_off_record_refuses_even_account_inventory(world, monkeypatch):
    def refuse():
        raise ValueError("Leave off-the-record mode first.")
    monkeypatch.setattr(accounts, "require_recording", refuse)
    with pytest.raises(ValueError, match="off-the-record"):
        ops.execute("inventory", {}, UI)


def test_moved_unfiled_chat_cannot_apply_old_plan(world, monkeypatch):
    provider, cards, decide, used = world
    from agent_friday.services import conversations, projects
    conv = {"id": "chat", "project": None, "status": "active"}
    monkeypatch.setattr(conversations, "load", lambda cid: copy.deepcopy(conv))
    monkeypatch.setattr(projects, "load", lambda pid: {"id": pid})
    prepared = ops.execute("prepare_dns", dns_args(), dict(UI, conversation_id="chat", project_id=None))
    conv["project"] = "another-project"
    assert decide(prepared)["status"] == "refused"
    assert provider.writes == []


def test_payload_tampering_cannot_expand_approved_change(world):
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    state = accounts.read()
    state["operations"][prepared["operation_id"]]["plan"]["after"]["answer"] = "unreviewed.example.net."
    accounts.write(state)
    decide(prepared)
    assert provider.writes == []


def test_project_chat_must_select_its_bound_site(world, monkeypatch):
    from agent_friday.services import conversations, projects
    monkeypatch.setattr(conversations, "load", lambda cid: {"project": "project", "status": "active"})
    monkeypatch.setattr(projects, "load", lambda pid: {"id": pid})
    with pytest.raises(ValueError, match="saved site"):
        ops.execute("inventory", {"account_id": ACCOUNT}, dict(UI, conversation_id="chat", project_id="project"))


def test_site_binding_limits_dns_reads_and_changes_to_saved_hostname(world, monkeypatch):
    provider, cards, decide, used = world
    from agent_friday.services import conversations, projects, sites_operations
    monkeypatch.setattr(conversations, "load", lambda cid: {"project": "project", "status": "active"})
    monkeypatch.setattr(projects, "load", lambda pid: {"id": pid})
    site = {"site_id": "site_test", "revision": 1, "conversation_id": "chat", "project_id": "project",
            "domain": {"account_id": ACCOUNT, "account_revision": 1, "domain": DOMAIN, "hostname": "www." + DOMAIN}}
    monkeypatch.setattr(sites_operations, "get_site", lambda sid: copy.deepcopy(site))
    monkeypatch.setattr(sites_operations, "validate_site_owner", lambda site, context: None)
    context = dict(UI, conversation_id="chat", project_id="project")
    args = {"account_id": ACCOUNT, "domain": DOMAIN, "site_id": "site_test", "site_revision": 1}
    assert [r["id"] for r in ops.execute("records", args, context)["records"]] == [1]
    with pytest.raises(ValueError, match="saved hostname"):
        ops.execute("prepare_dns", dict(args, record_id=2, delete=True), context)
    with pytest.raises(ValueError, match="saved hostname"):
        ops.execute("prepare_dns", dict(args, record={"host": "", "type": "A", "answer": "192.0.2.12", "ttl": 300}), context)
    with pytest.raises(ValueError, match="saved hostname"):
        ops.execute("verify", dict(args, records=[{"host": "", "type": "A", "answer": "192.0.2.12", "ttl": 300}]), context)
    # An explicit ID cannot move a different hostname into the allowed one.
    with pytest.raises(ValueError, match="saved hostname"):
        ops.execute("prepare_dns", dict(args, record_id=2, record=dns_args()["record"]), context)
    assert not provider.writes and not cards
    result = ops.execute("prepare_dns", dict(dns_args(), site_id="site_test", site_revision=1), context)
    assert decide(result)["status"] == "provider_verified"


def test_ownership_is_held_until_provider_mutation_finishes(world, monkeypatch):
    provider, cards, decide, used = world
    from agent_friday.services import conversations
    monkeypatch.setattr(conversations, "load", lambda cid: {"project": None, "status": "active"})
    entered, release, moved = threading.Event(), threading.Event(), threading.Event()
    original = provider.put_record
    def paused(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(provider, "put_record", paused)
    prepared = ops.execute("prepare_dns", dns_args(), dict(UI, conversation_id="chat", project_id=None))
    worker = threading.Thread(target=lambda: decide(prepared))
    def move():
        with conversations._LOCK:
            moved.set()
    mover = threading.Thread(target=move)
    try:
        worker.start()
        assert entered.wait(3)
        mover.start()
        assert not moved.wait(.05)
    finally:
        release.set()
        if worker.ident:
            worker.join(3)
        if mover.ident:
            mover.join(3)
    assert moved.is_set() and not worker.is_alive()
    assert len(provider.writes) == 1


def test_privacy_cycle_during_provider_read_creates_no_plan_or_card(world, monkeypatch):
    from agent_friday.services import off_record
    provider, cards, decide, used = world
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    original = provider.domain
    def response(domain):
        epoch["value"] = 5
        return original(domain)
    monkeypatch.setattr(provider, "domain", response)
    before = accounts._path().read_bytes()
    with pytest.raises(ValueError, match="privacy context"):
        ops.execute("prepare_dns", dns_args(), UI)
    assert accounts._path().read_bytes() == before
    assert not cards and not provider.writes


def test_delayed_approval_cannot_reacquire_authority_after_privacy_cycle(world, monkeypatch):
    from agent_friday.services import off_record
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    before = accounts._path().read_bytes()
    monkeypatch.setattr(off_record, "generation", lambda: 5)
    card = dict(cards[prepared["approval_id"]], status="approved")
    ops._on_decision(card)
    assert accounts._path().read_bytes() == before
    assert not provider.writes and not used


def test_late_write_result_stays_unknown_until_new_explicit_read_only_reconciliation(world, monkeypatch):
    from agent_friday.services import off_record, sites_privacy
    provider, cards, decide, used = world
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    original = provider.put_record
    def response(*args):
        result = original(*args)
        epoch["value"] = 5
        return result
    monkeypatch.setattr(provider, "put_record", response)
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    plan = copy.deepcopy(accounts.read()["operations"][prepared["operation_id"]]["plan"])
    ops._on_decision(dict(cards[prepared["approval_id"]], status="approved"))
    assert accounts.read()["operations"][prepared["operation_id"]]["status"] == "applying"
    assert len(provider.writes) == 1 and not used
    fresh = dict(UI, _sites_origin=sites_privacy.capture())
    assert ops.execute("operation", {"operation_id": prepared["operation_id"]}, fresh)["status"] == "unknown"
    result = ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, fresh)
    assert result["status"] == "provider_verified" and len(provider.writes) == 1
    assert accounts.read()["operations"][prepared["operation_id"]]["plan"] == plan


@pytest.mark.parametrize("action,args,method", [
    ("prepare_dns", {"account_id": ACCOUNT, "domain": DOMAIN, "record_id": 1, "delete": True}, "delete_record"),
    ("prepare_autorenew", {"account_id": ACCOUNT, "domain": DOMAIN, "enabled": True}, "autorenew"),
])
def test_late_mutation_results_never_replace_prewrite_intent(world, monkeypatch, action, args, method):
    from agent_friday.services import off_record, sites_privacy
    provider, cards, decide, used = world
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    original = getattr(provider, method)
    snapshots = []
    def response(*values):
        snapshots.append(accounts._path().read_bytes())
        original(*values)
        epoch["value"] = 5
        raise namecom.ProviderError("Late response details must not persist.", ambiguous=True)
    monkeypatch.setattr(provider, method, response)
    prepared = ops.execute(action, args, UI)
    approved = dict(cards[prepared["approval_id"]], status="approved")
    ops._on_decision(approved)
    assert accounts._path().read_bytes() == snapshots[0]
    assert len(provider.writes) == 1 and not used
    fresh = dict(UI, _sites_origin=sites_privacy.capture())
    assert ops.execute("operation", {"operation_id": prepared["operation_id"]}, fresh)["status"] == "unknown"
    with pytest.raises(ValueError, match="unresolved"):
        ops.execute(action, args, fresh)
    ops._on_decision(approved)
    assert len(provider.writes) == 1
    assert ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, fresh)["status"] == "provider_verified"
    assert len(provider.writes) == 1


def test_privacy_change_after_applying_checkpoint_stops_dispatch(world, monkeypatch):
    from agent_friday.services import off_record, sites_privacy
    provider, cards, decide, used = world
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    original = accounts.write
    checkpoints = []
    def transition(state):
        original(state)
        if state["operations"][prepared["operation_id"]]["status"] == "applying":
            checkpoints.append(accounts._path().read_bytes())
            epoch["value"] = 5
    monkeypatch.setattr(accounts, "write", transition)
    ops._on_decision(dict(cards[prepared["approval_id"]], status="approved"))
    assert accounts._path().read_bytes() == checkpoints[0]
    assert not provider.writes and not used
    fresh = dict(UI, _sites_origin=sites_privacy.capture())
    assert ops.execute("operation", {"operation_id": prepared["operation_id"]}, fresh)["status"] == "unknown"


def test_pending_plan_cannot_execute_after_process_restart_even_with_same_generation(world, monkeypatch):
    from agent_friday.services import sites_privacy
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    before = accounts._path().read_bytes()
    monkeypatch.setattr(sites_privacy, "_BOOT_ID", "another-process")
    result = decide(prepared)
    assert result["status"] == "refused" and "restarted" in result["message"]
    assert accounts._path().read_bytes() == before
    assert not provider.writes and not used
    fresh = ops.execute("prepare_dns", dns_args(), UI)
    assert decide(fresh)["status"] == "provider_verified"
    assert len(provider.writes) == 1


def test_applying_plan_after_restart_can_only_be_read_back(world, monkeypatch):
    from agent_friday.services import sites_privacy
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    state = accounts.read()
    op = state["operations"][prepared["operation_id"]]
    op["status"] = "applying"
    original_plan = copy.deepcopy(op["plan"])
    accounts.write(state)
    # The provider received the request before the process ended.
    provider.put_record(DOMAIN, op["plan"]["after"], 1)
    monkeypatch.setattr(sites_privacy, "_BOOT_ID", "replacement-process")
    assert decide(prepared)["status"] == "unknown"
    with pytest.raises(ValueError, match="unresolved"):
        ops.execute("prepare_dns", dns_args(), UI)
    checked = ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, UI)
    assert checked["status"] == "provider_verified"
    assert accounts.read()["operations"][prepared["operation_id"]]["plan"] == original_plan
    assert len(provider.writes) == 1 and not used


@pytest.mark.parametrize("field,replacement", [("account_id", "another-account"), ("domain", "other.example"), ("before", None), ("after", None)])
def test_card_display_must_match_the_immutable_plan(world, field, replacement):
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    cards[prepared["approval_id"]]["payload"][field] = replacement
    assert decide(prepared)["status"] == "awaiting_approval"
    assert not provider.writes and not used


def test_older_denial_cannot_replace_a_completed_operation(world):
    provider, cards, decide, used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    assert decide(prepared)["status"] == "provider_verified"
    assert decide(prepared, "denied")["status"] == "provider_verified"
    assert len(provider.writes) == 1


@pytest.mark.parametrize("action", ["inspect", "records", "verify", "prepare_dns"])
def test_stale_saved_account_binding_refuses_before_provider_access(world, monkeypatch, action):
    from agent_friday.services import sites_operations
    site = {"site_id": "site_test", "revision": 2, "conversation_id": "chat", "project_id": None,
            "domain": {"account_id": ACCOUNT, "account_revision": 1, "domain": DOMAIN, "hostname": "www." + DOMAIN}}
    monkeypatch.setattr(sites_operations, "get_site", lambda sid: copy.deepcopy(site))
    monkeypatch.setattr(sites_operations, "validate_site_owner", lambda site, context: None)
    state = accounts.read()
    state["accounts"][ACCOUNT]["revision"] = 2
    accounts.write(state)
    monkeypatch.setattr(accounts, "client", lambda rec, **kw: pytest.fail("Stale binding reached provider"))
    args = dict(account_id=ACCOUNT, domain=DOMAIN, site_id="site_test", site_revision=2)
    if action == "prepare_dns":
        args.update(record=dns_args()["record"], record_id=1)
    if action == "verify":
        args["records"] = [dns_args()["record"]]
    with pytest.raises(ValueError, match="account changed"):
        ops.execute(action, args, UI)


def test_chat_bound_ui_does_not_gain_global_operation_access(world, monkeypatch):
    from agent_friday.services import conversations
    monkeypatch.setattr(conversations, "load", lambda cid: {"project": None, "status": "active"})
    prepared = ops.execute("prepare_dns", dns_args(), dict(UI, conversation_id="first-chat"))
    with pytest.raises(ValueError, match="chat that owns"):
        ops.execute("operation", {"operation_id": prepared["operation_id"]}, dict(UI, conversation_id="second-chat"))
    assert ops.history(context=dict(UI, conversation_id="second-chat")) == []
    assert ops.execute("operation", {"operation_id": prepared["operation_id"]}, UI)["operation_id"] == prepared["operation_id"]


def _set_account_identity(identity="synthetic-account-identity", revision=1):
    state = accounts.read()
    state["accounts"][ACCOUNT].update(identity_hash=identity, revision=revision)
    accounts.write(state)


def _mark_unconfirmed_applied(provider, prepared):
    state = accounts.read()
    op = state["operations"][prepared["operation_id"]]
    provider.put_record(DOMAIN, op["plan"]["after"], op["plan"]["record_id"])
    op["status"] = "unknown"
    accounts.write(state)
    return copy.deepcopy(op)


def test_reconnected_same_identity_can_reconcile_without_replaying_or_rewriting_plan(world):
    provider, cards, decide, used = world
    _set_account_identity()
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    original = _mark_unconfirmed_applied(provider, prepared)
    _set_account_identity(revision=2)
    checked = ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, UI)
    assert checked["status"] == "provider_verified"
    saved = accounts.read()["operations"][prepared["operation_id"]]
    assert saved["plan"] == original["plan"] and saved["plan_digest"] == original["plan_digest"]
    assert len(provider.writes) == 1
    decide(prepared)
    assert len(provider.writes) == 1


def test_reconnect_never_relaxes_a_pending_approval_execution(world):
    provider, cards, decide, used = world
    _set_account_identity()
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    _set_account_identity(revision=2)
    assert decide(prepared)["status"] == "refused"
    assert provider.writes == []


@pytest.mark.parametrize("original_identity,new_identity", [
    ("first-identity", "different-identity"), (None, "new-identity"), ("", "new-identity"),
])
def test_reconnect_recovery_needs_the_original_nonempty_account_identity(world, monkeypatch, original_identity, new_identity):
    provider, cards, decide, used = world
    _set_account_identity(original_identity)
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    _mark_unconfirmed_applied(provider, prepared)
    _set_account_identity(new_identity, revision=2)
    before = accounts._path().read_bytes()
    monkeypatch.setattr(accounts, "client", lambda rec, **kw: pytest.fail("Different identity reached the provider"))
    with pytest.raises(ValueError, match="account identity"):
        ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, UI)
    assert accounts._path().read_bytes() == before
    assert len(provider.writes) == 1


@pytest.mark.parametrize("change,allowed", [("revision", True), ("hostname", False), ("owner", False)])
def test_reconnect_site_recovery_preserves_effective_destination_and_owner(world, monkeypatch, change, allowed):
    from agent_friday.services import sites_operations
    provider, cards, decide, used = world
    _set_account_identity()
    site = {"site_id": "site_test", "revision": 1, "conversation_id": "chat", "project_id": None,
            "domain": {"account_id": ACCOUNT, "account_revision": 1, "domain": DOMAIN, "hostname": "www." + DOMAIN}}
    monkeypatch.setattr(sites_operations, "get_site", lambda sid: copy.deepcopy(site))
    monkeypatch.setattr(sites_operations, "validate_site_owner", lambda site, context: None)
    prepared = ops.execute("prepare_dns", dict(dns_args(), site_id=site["site_id"], site_revision=1), UI)
    original = _mark_unconfirmed_applied(provider, prepared)
    _set_account_identity(revision=2)
    site["revision"] = 2
    site["domain"]["account_revision"] = 2
    if change == "hostname":
        site["domain"]["hostname"] = "different." + DOMAIN
    if change == "owner":
        site["conversation_id"] = "another-chat"
    if allowed:
        assert ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, UI)["status"] == "provider_verified"
    else:
        monkeypatch.setattr(accounts, "client", lambda rec, **kw: pytest.fail("Changed destination reached the provider"))
        with pytest.raises(ValueError, match="owner or effective domain binding changed"):
            ops.execute("reconcile", {"operation_id": prepared["operation_id"]}, UI)
    saved = accounts.read()["operations"][prepared["operation_id"]]
    assert saved["plan"] == original["plan"] and saved["plan_digest"] == original["plan_digest"]
    assert len(provider.writes) == 1


def test_two_prepared_creates_cannot_dispatch_after_an_unconfirmed_sibling(world, monkeypatch):
    provider, _cards, decide, _used = world
    args = {"account_id": ACCOUNT, "domain": DOMAIN,
            "record": {"host": "new", "type": "A", "answer": "192.0.2.20", "ttl": 300}}
    first = ops.execute("prepare_dns", args, UI)
    second = ops.execute("prepare_dns", args, UI)
    assert first["operation_id"] != second["operation_id"]
    unchanged = copy.deepcopy(provider.rows)

    def timeout_without_visible_change(domain, record, rid=None):
        provider.writes.append(("unconfirmed_put", domain, copy.deepcopy(record), rid))
        raise namecom.ProviderError("Request unconfirmed.", ambiguous=True)

    monkeypatch.setattr(provider, "put_record", timeout_without_visible_change)
    assert decide(first)["status"] == "unknown"
    assert provider.rows == unchanged
    result = decide(second)
    assert result["status"] == "refused"
    assert "unresolved" in result["message"]
    assert len(provider.writes) == 1
    assert provider.rows == unchanged


@pytest.mark.parametrize("action", ["records", "prepare_dns"])
def test_domain_read_transition_prevents_the_following_record_request(world, monkeypatch, action):
    from agent_friday.services import off_record
    provider, _cards, _decide, _used = world
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    original = provider.domain
    def delayed(domain):
        result = original(domain)
        epoch["value"] = 5
        return result
    monkeypatch.setattr(provider, "domain", delayed)
    monkeypatch.setattr(provider, "records", lambda *_: pytest.fail("No record request after the domain read loses its origin"))
    args = dns_args() if action == "prepare_dns" else {"account_id": ACCOUNT, "domain": DOMAIN}
    before = accounts._path().read_bytes()
    with pytest.raises(ValueError, match="privacy context"):
        ops.execute(action, args, UI)
    assert accounts._path().read_bytes() == before
    assert provider.writes == []


def test_approved_domain_read_transition_prevents_record_verification_and_mutation(world, monkeypatch):
    from agent_friday.services import off_record
    provider, cards, _decide, _used = world
    prepared = ops.execute("prepare_dns", dns_args(), UI)
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    original = provider.domain
    def delayed(domain):
        result = original(domain)
        epoch["value"] = 5
        return result
    monkeypatch.setattr(provider, "domain", delayed)
    monkeypatch.setattr(provider, "records", lambda *_: pytest.fail("The expired approval cannot continue provider verification"))
    before = accounts._path().read_bytes()
    ops._on_decision(dict(cards[prepared["approval_id"]], status="approved"))
    assert accounts._path().read_bytes() == before
    assert provider.writes == []
