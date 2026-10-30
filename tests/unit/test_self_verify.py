"""Friday re-checks her own safeguards at start and every few hours (north
star §26.20-1): the receipt chain, the signed rules, the egress gate, the
action gate, the credential store, the vault's settings and the authority
switch. A problem is said through the data-integrity path, each sweep is in
the action ledger, and what is not covered yet is named."""
from __future__ import annotations

import base64
import importlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _sv():
    assert importlib.util.find_spec("agent_friday.services.self_verify") is not None, \
        "Friday never re-checks her own safeguards"
    return importlib.import_module("agent_friday.services.self_verify")


@pytest.fixture
def healthy(monkeypatch):
    sv = _sv()
    fns = {cid: (lambda: ("healthy", "ok")) for cid, _f, _s in sv.CHECKS}
    monkeypatch.setattr(sv, "CHECKS", tuple((cid, fns[cid], said) for cid, _f, said in sv.CHECKS))
    from agent_friday.services import action_ledger
    got = []
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: got.append((kind, rec)) or {})
    monkeypatch.setattr(sv, "_LAST", {})
    monkeypatch.setattr(sv, "_key_present", lambda: True)
    return sv, got


def test_health_never_runs_a_sweep_and_a_sweep_never_mints_a_key(healthy, monkeypatch):
    sv, got = healthy
    ran = []
    monkeypatch.setattr(sv, "_sweep", lambda record: ran.append(record) or {})
    assert sv.probe() == [] and ran == []                    # /api/health reads the last sweep only
    monkeypatch.undo()
    sv2 = _sv()
    monkeypatch.setattr(sv2, "_key_present", lambda: False)
    calls = []
    monkeypatch.setattr(sv2, "CHECKS", tuple((cid, (lambda c=cid: calls.append(c) or ("healthy", "ok")), said)
                                              for cid, _f, said in sv2.CHECKS))
    from agent_friday.services import action_ledger
    recorded = []
    monkeypatch.setattr(action_ledger, "try_append", lambda *a, **k: recorded.append(a) or {})
    sv2.sweep()
    assert "authority" not in calls and recorded == []        # nothing that reaches the ledger's key


def test_a_locked_key_store_is_not_a_fault(monkeypatch):
    sv = _sv()
    from agent_friday.services import health_check
    monkeypatch.setattr(health_check, "check_credential_store", lambda: (False, "KeystoreLocked: waiting"))
    assert sv._credential_store() == ("unverified", "keystore_locked")
    monkeypatch.setattr(health_check, "check_credential_store", lambda: (False, "OSError: disk"))
    assert sv._credential_store()[0] == "broken"


def test_the_sweep_covers_each_safeguard_and_names_what_it_does_not():
    sv = _sv()
    ids = {cid for cid, _f, _s in sv.CHECKS}
    assert ids >= {"receipt_chain", "rules", "egress_gate", "action_gate", "credential_store", "authority",
                   "update_signatures", "sandbox"}
    assert set(sv.NOT_COVERED) == {"connector_domains", "principal_invariants"}
    assert not ids & set(sv.NOT_COVERED)


def test_a_clean_sweep_is_recorded_and_says_nothing(healthy):
    sv, got = healthy
    out = sv.sweep()
    assert out["failed"] == [] and out["checked"] == len(sv.CHECKS)
    assert got == [("self_check", {"checked": len(sv.CHECKS), "failed": [], "codes": []})]
    assert sv.probe() == []


def test_a_broken_safeguard_or_a_check_that_cannot_run_is_said(healthy, monkeypatch):
    sv, got = healthy

    def boom():
        raise OSError("disk")
    checks = list(sv.CHECKS)
    checks[0] = ("receipt_chain", lambda: ("broken", "breaks_to_review"), checks[0][2])
    checks[1] = ("rules", lambda: ("blocked_by_policy", "rules_signature_mismatch"), checks[1][2])
    checks[2] = ("egress_gate", boom, checks[2][2])
    checks[3] = ("action_gate", lambda: ("unverified", "self_test_not_run_in_this_process"), checks[3][2])
    monkeypatch.setattr(sv, "CHECKS", tuple(checks))
    out = sv.sweep()
    assert [f["id"] for f in out["failed"]] == ["receipt_chain", "rules", "egress_gate"]   # not merely unverified
    assert got[-1][1]["failed"] == ["receipt_chain", "rules", "egress_gate"]
    probs = sv.probe()
    assert {p["store"] for p in probs} == {"safeguard:receipt_chain", "safeguard:rules", "safeguard:egress_gate"}
    assert all(p["message"] and "disk" not in p["message"] for p in probs)    # Friday's words, not the error's


def test_a_fixed_problem_that_comes_back_raises_a_new_card(monkeypatch):
    from agent_friday.services import integrity_report as ir
    cards, state = [], {"bad": True}
    monkeypatch.setattr(ir, "_card", lambda store, detail: cards.append(store))
    monkeypatch.setattr(ir, "_PROBES", {"s": lambda: [{"store": "s", "state": "x", "message": "m"}] if state["bad"] else []})
    monkeypatch.setattr(ir, "_ANNOUNCED", set())
    ir.announce()
    ir.announce()
    state["bad"] = False
    ir.announce()
    state["bad"] = True
    ir.announce()
    assert cards == ["s", "s"]


def test_the_server_starts_the_sweep_and_activity_names_it():
    _sv()
    src = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "self_verify" in src and "start_periodic()" in src
    led = (ROOT / "src" / "agent_friday" / "routes" / "ledger.py").read_text(encoding="utf-8")
    assert '"self_check":' in led


def test_start_periodic_waits_then_sweeps(monkeypatch):
    sv = _sv()
    import threading
    done, slept = threading.Event(), []
    monkeypatch.setattr(sv, "sweep", lambda record=True: done.set() or {"failed": []})

    def sleep(s):
        slept.append(s)
        if len(slept) > 1:
            raise SystemExit   # stop the loop after one sweep
    from agent_friday.services import integrity_report as ir
    monkeypatch.setattr(ir, "announce", lambda: [])
    registered = []
    monkeypatch.setattr(ir, "register", lambda store, fn: registered.append(store))   # no probe left behind
    sv.start_periodic(interval_s=99, first_delay_s=7, sleep=sleep)
    assert done.wait(10) and slept[0] == 7 and registered == ["safeguards"]


def _check(sv, name):
    assert hasattr(sv, name), "the sweep has no %s check" % name
    return getattr(sv, name)


def test_the_update_signature_check_tells_good_from_bad(monkeypatch):
    sv = _sv()
    check = _check(sv, "_update_signatures")
    assert check() == ("healthy", "verifier_ok")
    from agent_friday.services import minisign, trust_keys
    real = minisign.verify
    monkeypatch.setattr(minisign, "verify", lambda *a, **k: {"meta": {}})          # accepts anything
    assert check() == ("broken", "bad_signature_accepted")

    def refuses(*a, **k):
        raise minisign.SignatureError("no")
    monkeypatch.setattr(minisign, "verify", refuses)
    assert check() == ("broken", "good_signature_refused")

    def ignores_comment(data, sig, keys):                                         # forgets the trusted comment
        lines = sig.split("\n")
        good = lines[:2] + ["trusted comment: name=self-check"] + lines[3:]
        return real(data, "\n".join(good), keys) if "agent-friday-release" in sig else real(data, sig, keys)
    monkeypatch.setattr(minisign, "verify", ignores_comment)
    assert check() == ("broken", "bad_signature_accepted")

    def plain_only(data, sig, keys):                                              # the prehashed branch broke
        if base64.b64decode(sig.split("\n")[1])[:2] == b"ED":
            return {"meta": {}}
        return real(data, sig, keys)
    monkeypatch.setattr(minisign, "verify", plain_only)
    assert check() == ("broken", "bad_signature_accepted")
    monkeypatch.setattr(minisign, "verify", real)
    monkeypatch.setattr(trust_keys, "PUBLIC_LIST_KEYS", ("not a key",))
    assert check() == ("broken", "trusted_key_unreadable")


def test_once_a_release_key_exists_an_unsigned_release_must_be_refused(monkeypatch):
    sv = _sv()
    check = _check(sv, "_update_signatures")
    pub, _sign = sv._minisign_pair()
    from agent_friday.services import trust_keys, update_check
    monkeypatch.setattr(trust_keys, "RELEASE_KEYS", (pub,))
    assert check() == ("healthy", "verifier_ok")
    monkeypatch.setattr(update_check, "refused_states", lambda: update_check.REFUSED_STATES)
    assert check() == ("broken", "unsigned_release_not_refused")


def test_the_sandbox_check_sees_its_rules_go(monkeypatch):
    sv = _sv()
    check = _check(sv, "_sandbox")
    assert check() == ("healthy", "sandbox_rules_ok")
    from agent_friday.services import code_sandbox as cs
    with monkeypatch.context() as m:
        m.setattr(cs, "_BOOT", cs._BOOT.replace('"getaddrinfo", ', ""))
        assert check() == ("broken", "network_block_missing")
    with monkeypatch.context() as m:
        m.setattr(cs, "classify", lambda args: (cs.INTERNAL, "x"))
        assert check() == ("broken", "host_backend_not_outward")
    with monkeypatch.context() as m:
        m.setattr(cs, "classify", lambda args: (cs.OUTWARD, "x"))
        assert check() == ("broken", "unknown_backend_allowed")
    with monkeypatch.context() as m:
        m.setattr(cs, "_BOOT", cs._BOOT.replace("setattr(_m, _n, _no_network)", "pass"))
        assert check() == ("broken", "network_block_missing")
    with monkeypatch.context() as m:
        m.setattr(cs, "_BOOT", cs._BOOT + "\nif (:\n")
        with pytest.raises(SyntaxError):                 # the sweep records this as check_failed
            check()
    with monkeypatch.context() as m:
        real_env = cs._clean_env
        m.setattr(cs, "_clean_env", lambda w: dict(real_env(w), OPENAI_API_KEY="sk-x"))  # pragma: allowlist secret
        assert check() == ("broken", "environment_inherited")
    with monkeypatch.context() as m:
        real_env = cs._clean_env
        m.setattr(cs, "_clean_env", lambda w: dict(real_env(w), HTTPS_PROXY=""))
        assert check() == ("broken", "proxy_not_dead")
    with monkeypatch.context() as m:
        m.setattr(cs, "_clean_env", lambda w: dict(real_env(w), no_proxy="*"))
        assert check() == ("broken", "proxy_not_dead")
