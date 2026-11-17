"""Issuer charters, refs and exact amounts (NS-40.4-1, design Part A). Test credits only."""
import ast
import dataclasses
import itertools
from pathlib import Path

import pytest

from agent_friday.services import issuer_registry as reg

KEY = "ab" * 32
SERVICES = Path(reg.__file__).parent


def pilot(**over):
    base = dict(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=1_000_000,
                treasury_account="treasury", cash_claim=False, genesis_authority=KEY,
                created="2026-11-16T00:00:00Z")
    return reg.IssuerCharter(**{**base, **over})


def ref(issuer_id="test.one", digest="d" * 64, unit="TESTP"):
    return reg.IssuerRef(issuer_id, digest, unit)


def test_the_pilot_digest_is_stable_and_every_field_changes_it():
    first = reg.charter_digest(pilot())
    assert first == reg.charter_digest(pilot())
    assert len(first) == 64
    changes = dict(issuer_id="test.two", unit_code="OTHERP", precision=1, genesis_supply=999,
                   treasury_account="vault", cash_claim=True, genesis_authority="cd" * 32,
                   created="2026-11-17T00:00:00Z")
    covered = set(changes) | {"mint_after_genesis", "recovery", "protocol"}
    assert covered == {f.name for f in dataclasses.fields(reg.IssuerCharter)}, "a field has no change case"
    for name, value in changes.items():
        assert reg.charter_digest(pilot(**{name: value})) != first, name


def test_the_pilot_digest_is_a_fixed_value():
    import hashlib
    written_by_hand = (
        b'{"cash_claim":false,"created":"2026-11-16T00:00:00Z","genesis_authority":"' + KEY.encode()
        + b'","genesis_supply":1000000,"issuer_id":"test.one","mint_after_genesis":"never",'
        b'"precision":0,"protocol":"asimov-issuer-charter/v1","recovery":"single_authority_log",'
        b'"treasury_account":"treasury","unit_code":"TESTP"}')
    assert pilot().canonical_bytes() == written_by_hand
    expected = hashlib.sha256(b"asimov-issuer-charter/v1" + bytes([0]) + written_by_hand).hexdigest()
    assert reg.charter_digest(pilot()) == expected


@pytest.mark.parametrize("bad", [dict(issuer_id="Test.One"), dict(issuer_id="../x"), dict(issuer_id=""),
                                 dict(unit_code="t"), dict(precision=True), dict(precision=19),
                                 dict(genesis_supply=-1), dict(genesis_supply=2 ** 53),
                                 dict(genesis_supply=True), dict(genesis_supply=1.5),
                                 dict(cash_claim=0), dict(genesis_authority="zz"),
                                 dict(treasury_account="A b"), dict(created="yesterday"),
                                 dict(protocol="asimov-issuer-charter/v2")])
def test_a_malformed_charter_is_refused(bad):
    with pytest.raises(reg.CharterError):
        pilot(**bad)


def test_a_charter_round_trips_through_its_bytes():
    charter = pilot()
    assert reg.IssuerCharter.from_bytes(charter.canonical_bytes()) == charter
    for raw in (b"", b"{}", b"[1]", charter.canonical_bytes() + b" ", b'{"issuer_id":"x"}'):
        with pytest.raises(reg.CharterError):
            reg.IssuerCharter.from_bytes(raw)


def test_the_same_id_with_another_charter_is_refused(tmp_path):
    registry = reg.IssuerRegistry(tmp_path)
    assert registry.register(pilot()) is reg.IssuerOutcome.REGISTERED
    assert registry.register(pilot(genesis_supply=5)) is reg.IssuerOutcome.CHARTER_MISMATCH
    assert registry.get("test.one") == pilot()


def test_registering_identical_bytes_again_is_a_no_op_and_nothing_can_update_it(tmp_path):
    registry = reg.IssuerRegistry(tmp_path)
    registry.register(pilot())
    path = tmp_path / "issuers" / "test.one" / "charter.json"
    before = path.read_bytes()
    assert registry.register(pilot()) is reg.IssuerOutcome.ALREADY_REGISTERED
    assert path.read_bytes() == before
    assert not [n for n in dir(registry) if n.startswith(("update", "set", "replace", "delete", "remove"))]


def test_ref_is_none_for_an_unknown_issuer_and_pins_the_digest_for_a_known_one(tmp_path):
    registry = reg.IssuerRegistry(tmp_path)
    assert registry.get("test.one") is None and registry.ref("test.one") is None
    registry.register(pilot())
    assert registry.ref("test.one") == reg.IssuerRef("test.one", reg.charter_digest(pilot()), "TESTP")


@pytest.mark.parametrize("bad", ["../x", "a/b", "A", "", None, 5])
def test_an_issuer_id_cannot_leave_the_registry_folder(tmp_path, bad):
    with pytest.raises(reg.CharterError):
        reg.IssuerRegistry(tmp_path).get(bad)


def test_a_damaged_charter_file_is_refused(tmp_path):
    registry = reg.IssuerRegistry(tmp_path)
    registry.register(pilot())
    path = tmp_path / "issuers" / "test.one" / "charter.json"
    path.write_bytes(b"{not json")
    with pytest.raises(reg.CharterError):
        registry.get("test.one")
    path.write_bytes(pilot(issuer_id="test.two").canonical_bytes())
    with pytest.raises(reg.CharterError):
        registry.get("test.one")


def test_an_amount_is_a_checked_whole_number():
    r = ref()
    assert reg.Amount(r, 0).units == 0 and reg.Amount(r, 2 ** 53 - 1).units == 2 ** 53 - 1
    for bad in (-1, 2 ** 53, 1.0, True, "1", None):
        with pytest.raises(ValueError):
            reg.Amount(r, bad)
    with pytest.raises(ValueError):
        reg.Amount("test.one", 1)
    with pytest.raises(ValueError):
        reg.Amount(r, 1) - reg.Amount(r, 2)
    with pytest.raises(ValueError):
        reg.Amount(r, 2 ** 53 - 1) + reg.Amount(r, 1)


def test_amount_arithmetic_succeeds_only_between_equal_refs_for_every_small_case():
    refs = [ref(), ref(digest="e" * 64), ref(issuer_id="test.two"), ref(unit="OTHERP")]
    for a, b in itertools.product(refs, repeat=2):
        for x, y in itertools.product((0, 1, 5), repeat=2):
            left, right = reg.Amount(a, x), reg.Amount(b, y)
            operations = [lambda: left + right, lambda: left < right, lambda: left <= right,
                          lambda: left > right, lambda: left >= right]
            if y <= x:
                operations.append(lambda: left - right)
            for op in operations:
                if a == b:
                    op()
                else:
                    with pytest.raises(reg.MixedIssuers):
                        op()
    assert (reg.Amount(refs[0], 3) + reg.Amount(refs[0], 4)).units == 7
    assert reg.Amount(refs[0], 3) < reg.Amount(refs[0], 4)


def test_an_amount_does_not_combine_with_a_bare_integer():
    amount = reg.Amount(ref(), 3)
    with pytest.raises(TypeError):
        amount + 1
    with pytest.raises(TypeError):
        amount < 1


def _imports(path):
    found = set()
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def test_neither_the_registry_nor_legacy_economy_imports_the_other():
    assert not any(i.endswith("economy") for i in _imports(SERVICES / "issuer_registry.py"))
    assert not any(i.endswith("issuer_registry") for i in _imports(SERVICES / "economy.py"))


def bare_amount_params(source):
    banned = {"amount", "units", "quantity"}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.arg in banned and isinstance(arg.annotation, ast.Name) and arg.annotation.id == "int":
                    found.append(f"{node.name}({arg.arg}: int)")
    return found


def test_no_public_function_takes_a_bare_amount_integer():
    for module in ("issuer_registry.py",):
        assert bare_amount_params((SERVICES / module).read_text(encoding="utf-8")) == [], module


def test_the_bare_integer_guard_can_fail():
    assert bare_amount_params("def pay(units: int): pass") == ["pay(units: int)"]
    assert bare_amount_params("def pay(units): pass" + chr(10) + "def _p(amount: int): pass") == []


# ── second-review findings (PR #889), written red first ───────────────────────────────────────────

def test_two_racing_registrations_of_one_id_answer_registered_exactly_once(tmp_path, monkeypatch):
    """Finding 1 / T7: both callers passed the exists() check and both were told REGISTERED; the second write won."""
    import threading
    registry = reg.IssuerRegistry(tmp_path)
    target = tmp_path / "issuers" / "race.one" / "charter.json"
    gate = threading.Barrier(2, timeout=5)
    real_exists = Path.exists

    def exists(self):
        found = real_exists(self)
        if self == target and not found:
            try:
                gate.wait()                      # hold both callers at the check, as the reviewer did
            except threading.BrokenBarrierError:
                pass
        return found
    monkeypatch.setattr(Path, "exists", exists)
    answers = {}

    def register(name, supply):
        answers[name] = registry.register(pilot(issuer_id="race.one", genesis_supply=supply))
    threads = [threading.Thread(target=register, args=("a", 1000)), threading.Thread(target=register, args=("b", 2000))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    monkeypatch.undo()
    assert sorted(a.value for a in answers.values()).count("registered") == 1, answers
    stored = registry.get("race.one")
    winner = "a" if answers["a"] is reg.IssuerOutcome.REGISTERED else "b"
    assert stored.genesis_supply == {"a": 1000, "b": 2000}[winner], "the stored charter is the winner's"
    assert answers["b" if winner == "a" else "a"] in (reg.IssuerOutcome.CHARTER_MISMATCH, reg.IssuerOutcome.ALREADY_REGISTERED)


def test_many_rounds_of_racing_registrations_never_report_two_winners(tmp_path):
    import threading
    for round_ in range(25):
        registry = reg.IssuerRegistry(tmp_path / f"r{round_}")
        gate = threading.Barrier(4)
        answers = []

        def go(n):
            gate.wait()
            answers.append(registry.register(pilot(issuer_id="x.one", genesis_supply=100 + n)))
        threads = [threading.Thread(target=go, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert answers.count(reg.IssuerOutcome.REGISTERED) == 1, answers


def test_a_registration_leaves_no_temporary_file_behind(tmp_path):
    registry = reg.IssuerRegistry(tmp_path)
    registry.register(pilot())
    registry.register(pilot(genesis_supply=5))
    assert sorted(p.name for p in (tmp_path / "issuers" / "test.one").iterdir()) == ["charter.json"]


def test_an_issuer_id_cannot_end_in_a_dot_where_windows_folds_it_away(tmp_path):
    for bad in ("a.", "test.", "a..", "x-."):
        with pytest.raises(reg.CharterError):
            pilot(issuer_id=bad)
        with pytest.raises(reg.CharterError):
            reg.IssuerRegistry(tmp_path).get(bad)
    assert pilot(issuer_id="a.b").issuer_id == "a.b" and pilot(issuer_id="a-").issuer_id == "a-"
