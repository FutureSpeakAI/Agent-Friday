"""The owner's list of accepted issuers (NS-40.4-1, design A-5 and A-6, PR 4). Test credits only."""
import ast
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_friday.services import accepted_issuers as acc
from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.issuer_registry import IssuerRef

OWNER = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC = OWNER.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
REF = IssuerRef("test.one", "d" * 64, "TESTP")
SOURCE = Path(acc.__file__).resolve().parents[2]


def store(tmp_path, name="a.json"):
    return acc.AcceptedIssuers(tmp_path / name, lambda data: OWNER.sign(data).hex(), PUBLIC)


def test_by_default_no_issuer_is_accepted(tmp_path):
    s = store(tmp_path)
    assert s.refs() == [] and not s.is_accepted(REF) and not s.is_accepted_id("test.one")


def test_accepting_needs_a_press_and_nothing_else_counts(tmp_path):
    s = store(tmp_path)
    for not_a_press in (None, "owner", "voice", "owner:text", True, {"press": True}, object()):
        assert s.accept(REF, not_a_press, 1) is acc.AcceptOutcome.PRESS_REQUIRED
    assert s.refs() == [] and not (tmp_path / "a.json").exists()
    assert s.accept(REF, acc.Press(), 5) is acc.AcceptOutcome.ACCEPTED
    assert s.is_accepted(REF) and s.is_accepted_id("test.one")
    assert s.accept(REF, acc.Press(), 6) is acc.AcceptOutcome.ALREADY


def test_who_accepted_is_recorded_as_the_press_never_as_a_caller_supplied_label(tmp_path):
    s = store(tmp_path)
    s.accept(REF, acc.Press(), 5)
    entry = json.loads((tmp_path / "a.json").read_text())["entries"][0]
    assert entry["accepted_by"] == "owner:press" and entry["accepted_at"] == 5


def test_withdrawing_needs_no_press_and_works_from_anywhere(tmp_path):
    s = store(tmp_path)
    s.accept(REF, acc.Press(), 5)
    assert s.withdraw("test.one") is True and s.refs() == []
    assert s.withdraw("test.one") is False


def test_the_same_id_with_another_charter_is_not_accepted(tmp_path):
    s = store(tmp_path)
    s.accept(REF, acc.Press(), 5)
    other = IssuerRef("test.one", "e" * 64, "TESTP")
    assert not s.is_accepted(other) and s.is_accepted(REF)
    assert s.accept(other, acc.Press(), 6) is acc.AcceptOutcome.OTHER_CHARTER
    assert s.refs() == [REF]


def test_a_hand_edited_list_reads_as_empty(tmp_path):
    s = store(tmp_path)
    s.accept(REF, acc.Press(), 5)
    path = tmp_path / "a.json"
    doc = json.loads(path.read_text())
    doc["entries"].append({"issuer_id": "rogue", "charter_digest": "f" * 64, "unit_code": "BAD",
                           "accepted_by": "owner:press", "accepted_at": 1})
    path.write_text(json.dumps(doc))
    assert s.refs() == [] and not s.is_accepted_id("rogue") and not s.is_accepted(REF)
    path.write_text("not json")
    assert s.refs() == []
    other_key = acc.AcceptedIssuers(path, lambda data: "00" * 64, PUBLIC)
    assert other_key.refs() == []


def test_a_list_signed_by_another_key_is_not_trusted(tmp_path):
    mallory = Ed25519PrivateKey.from_private_bytes(bytes([9] * 32))
    forged = acc.AcceptedIssuers(tmp_path / "a.json", lambda data: mallory.sign(data).hex(), PUBLIC)
    forged.accept(REF, acc.Press(), 1)
    assert store(tmp_path).refs() == []


def test_a_list_that_cannot_be_signed_is_not_written(tmp_path):
    s = acc.AcceptedIssuers(tmp_path / "a.json", lambda data: "", PUBLIC)
    assert s.accept(REF, acc.Press(), 1) is acc.AcceptOutcome.CANNOT_SIGN
    assert not (tmp_path / "a.json").exists()


SECOND = IssuerRef("test.two", "c" * 64, "TWOP")


def test_an_older_signed_list_put_back_is_not_trusted(tmp_path):
    """Owed from the #915 review: a withdrawn issuer must not come back because someone restored an older list that
    was validly signed. Each write carries a version; the highest version this PC has written is kept beside the list."""
    s = store(tmp_path)
    path = tmp_path / "a.json"
    s.accept(REF, acc.Press(), 1)
    old = path.read_bytes()
    assert s.withdraw("test.one") is True and s.refs() == []
    path.write_bytes(old)                                   # the owner's own earlier, validly signed list
    assert s.refs() == [] and not s.is_accepted(REF), "a rolled-back list reads as empty"
    assert store(tmp_path).refs() == [], "and so does a fresh object over the same files"
    s2 = store(tmp_path, "b.json")
    s2.accept(REF, acc.Press(), 1)
    older = (tmp_path / "b.json").read_bytes()
    s2.accept(SECOND, acc.Press(), 2)
    (tmp_path / "b.json").write_bytes(older)
    assert s2.refs() == [] and not s2.is_accepted(REF)


def test_a_mark_that_does_not_verify_fails_closed_and_the_list_keeps_counting_up(tmp_path):
    s = store(tmp_path)
    s.accept(REF, acc.Press(), 1)
    s.accept(SECOND, acc.Press(), 2)
    versions = []
    for _ in range(2):
        versions.append(json.loads((tmp_path / "a.json").read_text())["version"])
        s.withdraw("test.two") if len(versions) == 1 else s.accept(SECOND, acc.Press(), 3)
    versions.append(json.loads((tmp_path / "a.json").read_text())["version"])
    assert versions == sorted(set(versions)) and len(versions) == 3, versions
    mark = tmp_path / "a.json.mark"
    mark.write_text(json.dumps({"version": 1, "signature": "00" * 64}))
    assert s.refs() == [], "a mark that does not verify cannot be trusted, so nothing is"


def test_two_threads_accepting_different_issuers_lose_neither(tmp_path):
    """Owed from the #915 review: accept and withdraw are read-modify-write and need a lock."""
    import threading
    lost = []
    for round_ in range(15):
        s = store(tmp_path, f"r{round_}.json")

        def one(ref):
            acc.AcceptedIssuers(tmp_path / f"r{round_}.json", lambda data: OWNER.sign(data).hex(), PUBLIC).accept(
                ref, acc.Press(), 1)
        threads = [threading.Thread(target=one, args=(r,)) for r in (REF, SECOND)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if {r.issuer_id for r in s.refs()} != {"test.one", "test.two"}:
            lost.append(round_)
    assert lost == []


def test_two_processes_accepting_different_issuers_lose_neither(tmp_path):
    """The lock works across processes, not only threads: each process accepts its own issuer into 40 lists at once."""
    import os
    import subprocess
    import sys
    worker = Path(__file__).with_name("accepted_issuers_worker.py")
    env = {**os.environ, "PYTHONPATH": str(SOURCE)}
    procs = [subprocess.Popen([sys.executable, str(worker), who, str(tmp_path)], env=env) for who in ("a", "b")]
    assert [p.wait(timeout=180) for p in procs] == [0, 0]
    lost = [n for n in range(40) if {r.issuer_id for r in store(tmp_path, f"p{n}.json").refs()} != {"test.one", "test.two"}]
    assert lost == []


def offer(issuer):
    return wire.canonical({"body": {"payload": {"terms": {"issuer": issuer}}}, "signature": "x"})


def test_named_issuer_reads_only_the_issuer_field():
    assert acc.named_issuer(offer("test-issuer")) == "test-issuer"
    for raw in (b"", b"{}", b"[1]", b"not json", wire.canonical({"body": {"payload": {}}}),
                wire.canonical({"body": {"payload": {"terms": {"issuer": 5}}}}),
                wire.canonical({"body": {"payload": {"terms": {}}}}), b'{"a":1,"a":2}'):
        assert acc.named_issuer(raw) is None


def test_only_a_request_on_this_pcs_screen_makes_a_press_and_only_this_module_builds_one():
    class Stub:
        pass
    assert acc.Press.from_request(Stub()) is None
    for path in (SOURCE / "agent_friday").rglob("*.py"):
        if path.name == "accepted_issuers.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "Press" \
                    and (isinstance(node.func, ast.Name) or getattr(node.func.value, "id", "") == "accepted_issuers"):
                pytest.fail(f"{path} builds a Press itself")


# ── second-review notes (PR #915), written red first ──────────────────────────────────────────────

def test_a_list_that_cannot_be_signed_is_a_named_refusal_not_an_exception(tmp_path):
    s = acc.AcceptedIssuers(tmp_path / "a.json", lambda data: "", PUBLIC)
    assert s.accept(REF, acc.Press(), 1) is acc.AcceptOutcome.CANNOT_SIGN
    assert not (tmp_path / "a.json").exists() and s.refs() == []
    broken = acc.AcceptedIssuers(tmp_path / "b.json", lambda data: (_ for _ in ()).throw(OSError("no key")), PUBLIC)
    assert broken.accept(REF, acc.Press(), 1) is acc.AcceptOutcome.CANNOT_SIGN


def test_press_is_built_only_by_from_request_inside_its_own_module():
    tree = ast.parse(Path(acc.__file__).read_text(encoding="utf-8"))
    builders = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for call in [c for c in ast.walk(fn) if isinstance(c, ast.Call)]:
            if getattr(call.func, "id", "") in ("cls", "Press") and fn.name != "from_request":
                builders.append(fn.name)
    assert builders == [], builders


def test_the_ingest_check_says_it_matches_the_issuer_id_only():
    assert "issuer id only" in acc.__doc__ and "charter digest" in acc.__doc__
