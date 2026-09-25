"""Enforcement tests for the resource arbiter of record (AE-0).

The governing rule of the spec (D13 / R7) is that a field ships only in the same
change as its enforcement, with a test that fails if the enforcement is removed.
Every test below is written to that standard: each one names, in its docstring,
the line of `services/arbiter.py` whose deletion makes it fail. If you can delete
an `if` and this file still passes, the field it guarded is a dead setting.

`TestFieldEnforcementMap` is the meta-test: it fails if `arbiter.status()` grows a
key with no named enforcement test, or names a test that does not exist.
"""
import types

import pytest

from agent_friday.services import arbiter as arb

arb._ensure_schema()


@pytest.fixture(autouse=True)
def clean():
    arb._reset_for_tests()
    yield
    arb._reset_for_tests()


def _stub_machine(monkeypatch, **overrides):
    """Pin the machine read so a test asserts about arithmetic, not about the
    box it happens to run on. Defaults are a plausible 12 GiB card."""
    base = {
        "gpu_vram": {"total": 12288, "measured_free": 12288},
        "gpu_exclusive": {"total": 1, "measured_free": None},
        "system_ram": {"total": 65536, "measured_free": 65536},
        "cpu_cores": {"total": 16, "measured_free": 16},
        "disk_headroom": {"total": 1000000, "measured_free": 500000},
    }
    for k, v in overrides.items():
        base[k] = dict(base[k], **v)
    monkeypatch.setattr(arb, "_read_machine", lambda: base)
    arb.reset_capacity_cache_for_tests()
    return base


def _res(st, name):
    return next(r for r in st["resources"] if r["resource"] == name)


class TestTheLedger:

    def test_claimed_rises_on_acquire_and_falls_on_release(self, monkeypatch):
        """Enforces `claimed_units`. Delete the `_claimed()` call in status()
        and the strip stops reflecting what has been claimed."""
        _stub_machine(monkeypatch)
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 0
        d = arb.acquire("gpu_vram", 4096, "llama-seat", purpose="qwen3")
        assert d["granted"] is True
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 4096
        arb.release(d["lease_id"])
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 0

    def test_holders_name_who_blocks(self, monkeypatch):
        """Enforces `holders` and the `blocking` list on a refusal. This is the
        difference between 'image generation failed' and 'the language seat has
        4096 MiB'."""
        _stub_machine(monkeypatch, gpu_vram={"measured_free": 5200})
        arb.acquire("gpu_vram", 4096, "llama-seat", purpose="qwen3-14b")
        st = _res(arb.status(), "gpu_vram")
        assert [h["holder"] for h in st["holders"]] == ["llama-seat"]
        d = arb.acquire("gpu_vram", 6000, "image-gen")
        assert d["granted"] is False
        assert [b["holder"] for b in d["blocking"]] == ["llama-seat"]
        assert "llama-seat" in d["detail"]

    def test_units_and_labels_come_from_the_resource_table(self, monkeypatch):
        """Enforces `unit` / `label`. A unit invented per call site is how two
        parts of a UI end up disagreeing about MiB versus GB."""
        _stub_machine(monkeypatch)
        for r in arb.status()["resources"]:
            assert r["unit"] == arb.RESOURCES[r["resource"]]["unit"]
            assert r["label"] == arb.RESOURCES[r["resource"]]["label"]

    def test_expiry_is_reconciled_on_read_without_a_daemon(self, monkeypatch):
        """Enforces `expires_at`. Delete the expire_due() call at the top of
        held() and a lease is observable long after its deadline."""
        _stub_machine(monkeypatch)
        # A controlled clock: with a real 50 ms TTL, a slow runner could spend
        # the whole lease between acquire() and the first read.
        clock = [1_758_000_000.0]
        monkeypatch.setattr(arb, "time", types.SimpleNamespace(time=lambda: clock[0]))
        d = arb.acquire("gpu_vram", 1000, "short-job", ttl_s=60)
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 1000
        clock[0] += 61
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 0
        assert any(e["kind"] == "expire" for e in arb.events())


class TestTheNumbersAreReal:

    def test_totals_come_from_the_machine_not_a_constant(self, monkeypatch):
        """Enforces `total_units`. If someone hardcodes a capacity, changing the
        machine read stops moving the number and this fails."""
        _stub_machine(monkeypatch, gpu_vram={"total": 12288})
        assert _res(arb.status(), "gpu_vram")["total_units"] == 12288
        _stub_machine(monkeypatch, gpu_vram={"total": 24576})
        assert _res(arb.status(), "gpu_vram")["total_units"] == 24576

    def test_measured_free_tracks_the_sampler(self, monkeypatch):
        """Enforces `measured_free`. This is the 400 MiB case: nothing Friday
        claimed, and yet almost nothing free."""
        _stub_machine(monkeypatch, gpu_vram={"measured_free": 400})
        r = _res(arb.status(), "gpu_vram")
        assert r["measured_free"] == 400
        assert r["claimed_units"] == 0
        assert r["available_units"] == 0  # 400 - 1024 reserve, floored

    def test_available_is_the_tighter_of_measured_and_paper(self, monkeypatch):
        """Enforces `available_units`. Both directions, because overstating
        availability is the failure that costs a training run.

        Delete the `min()` and one of these two assertions fails."""
        # Measured is the binding constraint: nothing claimed, but the card is
        # full of processes Friday knows nothing about.
        _stub_machine(monkeypatch, gpu_vram={"total": 12288,
                                             "measured_free": 2048})
        assert _res(arb.status(), "gpu_vram")["available_units"] == 1024
        # Paper is the binding constraint: the card reads free, but a claim was
        # made that has not materialised yet.
        _stub_machine(monkeypatch, gpu_vram={"total": 12288,
                                             "measured_free": 12288})
        arb.acquire("gpu_vram", 10240, "llama-seat")
        assert _res(arb.status(), "gpu_vram")["available_units"] == 1024

    def test_reserve_is_subtracted_from_availability(self, monkeypatch):
        """Enforces `reserve_units`. A starved desktop compositor is a real
        failure mode; the reserve is what stops Friday causing it."""
        _stub_machine(monkeypatch, gpu_vram={"total": 8192,
                                             "measured_free": 8192})
        r = _res(arb.status(), "gpu_vram")
        assert r["reserve_units"] == arb.RESOURCES["gpu_vram"]["reserve"]
        assert r["available_units"] == 8192 - r["reserve_units"]

    def test_unexplained_reports_undeclared_consumption(self, monkeypatch):
        """Enforces `unexplained_units` -- AR5 divergence detection.

        The literal incident: a 12 GiB card with 400 MiB free and nothing
        declared. Seven-plus gigabytes are gone and the strip must say so rather
        than let Friday guess at causes."""
        _stub_machine(monkeypatch, gpu_vram={"total": 12288,
                                             "measured_free": 400})
        assert _res(arb.status(), "gpu_vram")["unexplained_units"] == 11888
        # A declared hold explains its own share and stops being a mystery.
        # It has to be a *declared* hold: a Friday acquire() here is correctly
        # refused, since there is nothing left to claim -- which is the point.
        assert arb.acquire("gpu_vram", 4096, "llama-seat")["granted"] is False
        arb.declare_foreign_hold("gpu_vram", 4096, "llama-seat",
                                 purpose="qwen3-14b on port 8095")
        assert _res(arb.status(), "gpu_vram")["unexplained_units"] == 7792

    def test_declared_classes_are_labelled_declared(self, monkeypatch):
        """Enforces `basis`. Exclusivity cannot be probed; saying it was
        measured would be an invented number."""
        _stub_machine(monkeypatch)
        assert _res(arb.status(), "gpu_exclusive")["basis"] == "declared"
        assert _res(arb.status(), "gpu_vram")["basis"] == "measured"

    def test_all_four_scarce_classes_are_watched(self, monkeypatch):
        """Watching only VRAM would have missed two of tonight's three problems."""
        _stub_machine(monkeypatch)
        names = {r["resource"] for r in arb.status()["resources"]}
        assert {"gpu_vram", "gpu_exclusive", "system_ram", "cpu_cores",
                "disk_headroom"} <= names


class TestRefusal:

    def test_unverifiable_resource_refuses_rather_than_granting(self, monkeypatch):
        """Enforces `verifiable`. The honesty rule: None means cannot verify,
        never 'plenty free'. Delete the `available is None` branch in acquire()
        and this becomes a grant against a number nobody read."""
        _stub_machine(monkeypatch, gpu_vram={"total": None,
                                             "measured_free": None})
        r = _res(arb.status(), "gpu_vram")
        assert r["verifiable"] is False
        assert r["available_units"] is None
        d = arb.acquire("gpu_vram", 1, "image-gen")
        assert d["granted"] is False
        assert "cannot verify" in d["reason"]

    def test_unknown_resource_is_refused(self, monkeypatch):
        """Enforces `resource`. The class set is closed."""
        _stub_machine(monkeypatch)
        d = arb.acquire("quantum_foam", 1, "someone")
        assert d["granted"] is False
        assert "unknown resource" in d["reason"]

    def test_refusal_names_what_holds_it_and_offers_options(self, monkeypatch):
        """A refusal with no cause attached is the bug this phase exists to fix."""
        _stub_machine(monkeypatch, gpu_vram={"total": 12288,
                                             "measured_free": 12288})
        arb.acquire("gpu_vram", 10240, "llama-seat", purpose="qwen3-14b")
        d = arb.acquire("gpu_vram", 6000, "image-gen")
        assert d["granted"] is False
        assert d["available"] == 1024
        assert d["shortfall"] == 4976
        assert d["blocking"][0]["holder"] == "llama-seat"
        assert {o["kind"] for o in d["options"]} >= {"reduce"}

    def test_refusal_is_recorded_as_a_receipt(self, monkeypatch):
        """A refusal nobody can audit is indistinguishable from a silent failure."""
        _stub_machine(monkeypatch, gpu_vram={"measured_free": 1100})
        arb.acquire("gpu_vram", 9000, "image-gen")
        assert any(e["kind"] == "refuse" for e in arb.events())


class TestForeignHolds:
    """AR2 -- the rule that alone would have prevented the scoring-job incident."""

    def test_exclusive_hold_refuses_gpu_work_naming_the_holder(self, monkeypatch):
        """The incident, reproduced. A training run declares itself; Friday's
        next GPU request is refused within the call, with the holder named.

        Delete the gpu_exclusive branch in acquire() and Friday piles on."""
        _stub_machine(monkeypatch)
        arb.declare_preset("training", "training-run",
                           purpose="finetune llama-3")
        d = arb.acquire("gpu_vram", 512, "scoring-job")
        assert d["granted"] is False
        assert "training-run" in d["reason"]
        assert "does not preempt work it did not start" in d["detail"]
        assert {o["kind"] for o in d["options"]} >= {"wait", "cpu_only"}

    def test_the_training_preset_takes_the_gpu_and_a_core(self, monkeypatch):
        """The one-click declaration the spec asks for, verified as two claims."""
        _stub_machine(monkeypatch)
        out = arb.declare_preset("training", "training-run")
        assert out["ok"] is True
        st = arb.status()
        assert st["gpu_exclusive_held_by"] == "training-run"
        assert _res(st, "cpu_cores")["claimed_units"] == 1

    def test_a_foreign_hold_can_never_be_evicted(self, monkeypatch):
        """AR2, enforced rather than documented. Delete the holder_kind check in
        evict() and Friday can kill a training run."""
        _stub_machine(monkeypatch)
        h = arb.declare_foreign_hold("gpu_vram", 8000, "training-run")
        out = arb.evict(h["lease_id"], reason="I want the card")
        assert out["ok"] is False
        assert "never evicted" in out["reason"]
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 8000

    def test_a_foreign_hold_is_never_an_eviction_candidate(self, monkeypatch):
        """The plan must not even offer it -- offering is how a bad idea gets
        one click away."""
        _stub_machine(monkeypatch)
        arb.declare_foreign_hold("gpu_vram", 8000, "training-run")
        plan = arb.plan_eviction("gpu_vram", 4000)
        assert plan["feasible"] is False
        assert plan["leases"] == []

    def test_the_holder_of_the_exclusive_lease_may_still_use_the_gpu(self, monkeypatch):
        """A hold protects its owner's work; it does not lock the owner out."""
        _stub_machine(monkeypatch)
        arb.declare_foreign_hold("gpu_exclusive", 1, "training-run")
        d = arb.acquire("gpu_vram", 512, "training-run")
        assert d["granted"] is True


class TestEvictAndRestore:
    """A lease that can only be refused is half a design."""

    def test_refusal_offers_an_eviction_with_a_stated_cost(self, monkeypatch):
        """The interactive case: the language seat holds four gigabytes and
        the user asks for an image. The right answer is an offer, not a no."""
        _stub_machine(monkeypatch, gpu_vram={"total": 12288,
                                             "measured_free": 12288})
        arb.acquire("gpu_vram", 10240, "llama-seat", purpose="qwen3-14b",
                    evictable=True, evict_cost_s=8, restore_cost_s=50,
                    restore_token="qwen3-14b@8095")  # pragma: allowlist secret
        d = arb.acquire("gpu_vram", 6000, "image-gen")
        assert d["granted"] is False
        offer = next(o for o in d["options"] if o["kind"] == "evict_and_restore")
        assert offer["cost_round_trip_s"] == 58
        assert "llama-seat" in offer["description"]
        assert offer["plan"]["frees_units"] == 10240

    def test_a_lease_that_did_not_declare_itself_evictable_is_not_evicted(self, monkeypatch):
        """Enforces `evictable`. Delete the check and every lease becomes fair
        game, which is the same class of mistake as preempting a foreign hold."""
        _stub_machine(monkeypatch)
        d = arb.acquire("gpu_vram", 4096, "llama-seat")
        out = arb.evict(d["lease_id"])
        assert out["ok"] is False
        assert "evictable" in out["reason"]

    def test_evict_then_restore_returns_the_claim(self, monkeypatch):
        """Round trip, observed on the ledger: claimed falls, then comes back."""
        _stub_machine(monkeypatch, gpu_vram={"total": 12288,
                                             "measured_free": 12288})
        d = arb.acquire("gpu_vram", 8000, "llama-seat", evictable=True,
                        evict_cost_s=8, restore_cost_s=50,
                        restore_token="qwen3-14b@8095")  # pragma: allowlist secret
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 8000
        out = arb.evict(d["lease_id"], reason="image generation")
        assert out["ok"] is True
        assert out["restore_token"] == "qwen3-14b@8095"
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 0
        back = arb.restore(d["lease_id"])
        assert back["granted"] is True
        assert back["restored_from"] == d["lease_id"]
        assert _res(arb.status(), "gpu_vram")["claimed_units"] == 8000

    def test_allow_evict_makes_room_and_reports_what_it_unloaded(self, monkeypatch):
        """End-to-end expressibility: one call, and the caller is told exactly
        which leases it displaced so it can put them back."""
        _stub_machine(monkeypatch, gpu_vram={"total": 12288,
                                             "measured_free": 12288})
        seat = arb.acquire("gpu_vram", 10240, "llama-seat", evictable=True,
                           evict_cost_s=8, restore_cost_s=50)
        d = arb.acquire("gpu_vram", 6000, "image-gen", allow_evict=True)
        assert d["granted"] is True
        assert d["evicted"] == [seat["lease_id"]]
        assert arb.restore(seat["lease_id"])["granted"] is False  # still full


class TestFieldEnforcementMap:
    """Spec T10 generalised: grep the shipped fields against the test suite.

    A sixth dead setting cannot be added to this module without this failing."""

    def test_every_shipped_field_names_an_enforcement_test(self, monkeypatch):
        _stub_machine(monkeypatch)
        shipped = set(arb.status()["resources"][0].keys())
        mapped = set(arb.FIELD_ENFORCEMENT)
        assert shipped == mapped, (
            "fields with no named enforcement test: " + str(shipped - mapped) +
            "; mapped fields no longer shipped: " + str(mapped - shipped))

    def test_every_named_enforcement_test_exists(self):
        import inspect
        import sys
        src = inspect.getsource(sys.modules[__name__])
        missing = [t for t in set(arb.FIELD_ENFORCEMENT.values())
                   if ("def " + t + "(") not in src]
        assert not missing, "named but absent: " + str(missing)
