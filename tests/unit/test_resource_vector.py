"""Resource readings: types, exact N1 and the guards that keep them apart from money (NS-40.4-15)."""
import ast
import dataclasses
import itertools
import json
from pathlib import Path

import pytest

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services import resource_vector as rv

SERVICES = Path(rv.__file__).parent
VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "economy-vectors.json").read_text(encoding="utf-8"))
HASH = "ab" * 32


def calibration(**over):
    base = dict(profile="N1:testGPU-a", benchmark_id="bench-1", config_digest="cfg-1", runs=5,
                median_device_active_ms=4000, variation_permille=20, meter="process/psutil",
                calibrated_at="2026-11-16T00:00:00Z")
    return rv.Calibration(**{**base, **over})


def quote(**over):
    base = dict(operation="transcribe", operation_version="1", model_digest="m" * 8, quantization="q4",
                benchmark_id="bench-1", hardware="testGPU-a", driver="1.0", power_settings="balanced",
                workload_size=100, concurrency=1, profile="N1:testGPU-a",
                expected=rv.ResourceVector(duration_ms=rv.Estimated(5000, "last run")))
    return rv.ResourceQuote(**{**base, **over})


def test_the_bundle_negatron_vector_is_exactly_five_halves():
    case = VECTORS["negatron"][0]
    cal = calibration(profile=case["profile"], median_device_active_ms=case["reference_device_ms"])
    n1 = rv.n1_for(cal, "cfg-1", rv.Measured(case["job_device_ms"], 0, "m", "c"))
    assert (n1.numerator, n1.denominator) == (case["result_numerator"], case["result_denominator"]) == (5, 2)
    assert n1.shown(1) == "2.5 N1 (to 1 place)"
    assert case["energy_wh"] is None
    energy = rv.ResourceVector(energy_mwh=rv.Unavailable("no meter")).to_wire()["energy_mwh"]
    assert energy == {"unavailable": "no meter"}


def test_an_unmeasured_field_is_unavailable_and_the_record_has_no_zero_there():
    blank = rv.ResourceVector().to_wire()
    assert all(list(v) == ["unavailable"] for v in blank.values())
    assert b":0" not in wire.canonical(blank).replace(b'"unavailable":"not measured"', b"")


def test_a_zero_is_not_a_reading_without_a_meter():
    for bad in (0, None, 5, "0"):
        with pytest.raises(ValueError):
            rv.ResourceVector(energy_mwh=bad)


def test_readings_are_whole_numbers():
    for bad in (-1, 1.5, True, "3"):
        with pytest.raises(ValueError):
            rv.Measured(bad, 0, "m", "c")
        with pytest.raises(ValueError):
            rv.Estimated(bad, "basis")
    assert rv.Measured(0, 0, "m", "c").to_wire()["v"] == 0


def test_n1_quantities_combine_only_within_one_profile():
    profiles = ["N1:a", "N1:b", "N1:cpu"]
    for p, q in itertools.product(profiles, repeat=2):
        left, right = rv.N1Quantity(p, 2, 4), rv.N1Quantity(q, 1, 3)
        for op in (lambda: left + right, lambda: left < right, lambda: left <= right,
                   lambda: left > right, lambda: left >= right):
            if p == q:
                op()
            else:
                with pytest.raises(rv.MixedProfiles):
                    op()
    total = rv.N1Quantity("N1:a", 1, 2) + rv.N1Quantity("N1:a", 1, 3)
    assert (total.numerator, total.denominator) == (5, 6)


def test_an_n1_quantity_is_kept_exact_and_reduced():
    q = rv.N1Quantity("N1:a", 10000, 4000)
    assert (q.numerator, q.denominator) == (5, 2)
    with pytest.raises(ValueError):
        rv.N1Quantity("N1:a", 1, 0)
    with pytest.raises(TypeError):
        q + 1


def test_another_configuration_gets_no_n1():
    cal = calibration()
    reading = rv.Measured(10000, 0, "m", "c")
    assert rv.n1_for(cal, "cfg-1", reading) is not None
    assert rv.n1_for(cal, "cfg-2", reading) is None


def _good_receipt_fields():
    reading = rv.Measured(10000, 15, "m", "c")
    cal = calibration(median_device_active_ms=4000)
    return dict(task_id="t1", profile="N1:testGPU-a", vector=rv.ResourceVector(gpu_active_ms=reading),
                n1=rv.n1_for(cal, "cfg-1", reading), status=rv.ReceiptStatus.MEASURED, calibration=cal)


def test_a_receipt_cannot_be_built_without_a_quote_hash():
    ok = _good_receipt_fields()
    assert rv.MeasuredReceipt(quote_hash=rv.quote_hash(quote()), **ok).to_wire()["quote_hash"]
    for bad in (None, "", "short", "g" * 64, "A" * 64, 5, b"x" * 64):
        with pytest.raises(ValueError):
            rv.MeasuredReceipt(quote_hash=bad, **ok)
    with pytest.raises(TypeError):
        rv.MeasuredReceipt(**ok)


def test_a_receipt_states_its_calibration_honestly():
    base = dict(quote_hash=HASH, task_id="t1", profile="N1:testGPU-a", vector=rv.ResourceVector())
    with pytest.raises(ValueError):
        rv.MeasuredReceipt(**base, n1=None, status=rv.ReceiptStatus.MEASURED)
    assert rv.MeasuredReceipt(**base, n1=None, status=rv.ReceiptStatus.UNCALIBRATED).to_wire()["n1"] is None
    with pytest.raises(ValueError):
        rv.MeasuredReceipt(**base, n1=rv.N1Quantity("N1:other", 1, 1), status=rv.ReceiptStatus.MEASURED)
    with pytest.raises(ValueError):
        rv.MeasuredReceipt(**base, n1=None, status="paid")


def test_a_quote_holds_estimates_only_and_every_field_changes_its_hash():
    base = quote()
    first = rv.quote_hash(base)
    assert first == rv.quote_hash(quote())
    changes = dict(operation="other", operation_version="2", model_digest="n" * 8, quantization="q8",
                   benchmark_id="bench-2", hardware="testGPU-b", driver="2.0", power_settings="max",
                   workload_size=101, concurrency=2, profile="N1:other",
                   expected=rv.ResourceVector(duration_ms=rv.Estimated(5001, "last run")))
    assert set(changes) == {f.name for f in dataclasses.fields(rv.ResourceQuote)}, "a field has no change case"
    for name, value in changes.items():
        assert rv.quote_hash(quote(**{name: value})) != first, name
    with pytest.raises(ValueError):
        quote(expected=rv.ResourceVector(duration_ms=rv.Measured(1, 0, "m", "c")))


def test_a_vector_has_no_total():
    assert not [n for n in dir(rv.ResourceVector) if n.startswith(("total", "sum", "__add__"))]


def _tree(name):
    return ast.parse((SERVICES / name).read_text(encoding="utf-8"))


def adds_gpu_to_cpu(source):
    """True when one addition or subtraction has gpu_active_ms on one side and cpu_active_ms on the other."""
    def names(node):
        return {n.attr if isinstance(n, ast.Attribute) else n.id
                for n in ast.walk(node) if isinstance(n, (ast.Attribute, ast.Name))}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            left, right = names(node.left), names(node.right)
            if ({"gpu_active_ms"} <= left and {"cpu_active_ms"} <= right) or (
                    {"cpu_active_ms"} <= left and {"gpu_active_ms"} <= right):
                return True
    return False


def test_nothing_adds_gpu_time_to_cpu_time():
    for module in ("resource_vector.py", "resource_measure.py"):
        assert not adds_gpu_to_cpu((SERVICES / module).read_text(encoding="utf-8")), module


def test_the_gpu_plus_cpu_guard_can_fail():
    assert adds_gpu_to_cpu("x = v.gpu_active_ms + v.cpu_active_ms")
    assert adds_gpu_to_cpu("x = a.cpu_active_ms - a.gpu_active_ms")
    assert not adds_gpu_to_cpu("x = v.gpu_active_ms + v.gpu_active_ms")


MONEY = ("issuer_journal", "issuer_registry", "stage_escrow", "exchange", "economy", "journal_commitment")
RESOURCE = ("resource_vector", "resource_measure")


def imports_of(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return {i.rsplit(".", 1)[-1] for i in found} | {i.split(".")[-1] for i in found}


def test_resource_and_money_modules_import_nothing_of_each_other():
    for name in RESOURCE:
        assert not imports_of(SERVICES / f"{name}.py") & set(MONEY), name
    for name in MONEY:
        for path in ([SERVICES / f"{name}.py"] if (SERVICES / f"{name}.py").exists() else
                     list((SERVICES / name).glob("*.py")) if (SERVICES / name).is_dir() else []):
            assert not imports_of(path) & set(RESOURCE), path.name


def test_the_import_guard_can_fail(tmp_path):
    bad = tmp_path / "m.py"
    bad.write_text("from agent_friday.services import issuer_journal\n", encoding="utf-8")
    assert imports_of(bad) & set(MONEY)
    bad.write_text("import agent_friday.services.resource_vector as r\n", encoding="utf-8")
    assert imports_of(bad) & set(RESOURCE)


def test_no_resource_type_carries_an_amount_field():
    for cls in (rv.ResourceVector, rv.ResourceQuote, rv.MeasuredReceipt, rv.Calibration, rv.N1Quantity):
        for f in dataclasses.fields(cls):
            assert "amount" not in f.name and "price" not in f.name and "balance" not in f.name, f.name


# ── second-review findings (PR #892, finding 2), written red first ────────────────────────────────

def _cal():
    return calibration(median_device_active_ms=4000)


def test_n1_for_takes_the_reading_not_an_integer():
    cal = _cal()
    with pytest.raises(TypeError):
        rv.n1_for(cal, "cfg-1", 10000)
    measured = rv.n1_for(cal, "cfg-1", rv.Measured(10000, 15, "m", "c"))
    estimated = rv.n1_for(cal, "cfg-1", rv.Estimated(10000, "sampled"))
    assert (measured.numerator, measured.denominator, measured.estimated) == (5, 2, False)
    assert (estimated.numerator, estimated.denominator, estimated.estimated) == (5, 2, True)
    assert rv.n1_for(cal, "cfg-1", rv.Unavailable("no meter")) is None
    assert rv.n1_for(cal, "cfg-2", rv.Measured(10000, 0, "m", "c")) is None


def _receipt(reading, n1, status, **kw):
    vector = rv.ResourceVector(gpu_active_ms=reading)
    return rv.MeasuredReceipt(quote_hash="ab" * 32, task_id="t1", profile="N1:testGPU-a", vector=vector, n1=n1,
                              status=status, calibration=_cal(), **kw)


def test_a_receipt_is_measured_only_when_its_device_reading_was_measured():
    S = rv.ReceiptStatus
    exact = rv.Measured(10000, 15, "m", "c")
    n1_exact = rv.n1_for(_cal(), "cfg-1", exact)
    assert _receipt(exact, n1_exact, S.MEASURED).to_wire()["status"] == "measured"
    guess = rv.Estimated(10000, "sampled")
    n1_guess = rv.n1_for(_cal(), "cfg-1", guess)
    with pytest.raises(ValueError):
        _receipt(guess, n1_guess, S.MEASURED)
    assert _receipt(guess, n1_guess, S.ESTIMATED).to_wire()["n1"]["estimated"] is True
    with pytest.raises(ValueError):
        _receipt(exact, n1_exact, S.ESTIMATED)
    with pytest.raises(ValueError):
        _receipt(rv.Unavailable("none"), rv.N1Quantity("N1:testGPU-a", 999, 1), S.MEASURED)
    with pytest.raises(ValueError):
        _receipt(rv.Unavailable("none"), rv.N1Quantity("N1:testGPU-a", 999, 1), S.UNCALIBRATED)
    assert _receipt(rv.Unavailable("none"), None, S.UNCALIBRATED).n1 is None
    with pytest.raises(ValueError):
        _receipt(exact, None, S.MEASURED), "a measured receipt carries its N1"


def test_a_receipts_n1_must_be_its_reading_over_the_calibrations_median_and_its_status_a_closed_enum():
    S = rv.ReceiptStatus
    assert {s.value for s in S} == {"measured", "estimated", "uncalibrated", "failed"}
    exact = rv.Measured(10000, 15, "m", "c")
    for wrong in (rv.N1Quantity("N1:testGPU-a", 3, 1), rv.N1Quantity("N1:testGPU-b", 5, 2)):
        with pytest.raises(ValueError):
            _receipt(exact, wrong, S.MEASURED)
    with pytest.raises(ValueError):
        _receipt(exact, rv.n1_for(_cal(), "cfg-1", exact), "measured")
    with pytest.raises(ValueError):
        _receipt(exact, rv.n1_for(_cal(), "cfg-1", exact), "paid")
    assert _receipt(rv.Unavailable("x"), None, S.FAILED).status is S.FAILED


def test_an_n1_from_an_estimate_stays_marked_when_quantities_are_added():
    a = rv.N1Quantity("N1:a", 1, 2, estimated=True)
    b = rv.N1Quantity("N1:a", 1, 3, estimated=False)
    assert (a + b).estimated is True and (b + b).estimated is False
