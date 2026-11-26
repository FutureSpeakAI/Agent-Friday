"""The `private_turn` lease (the owner's split, services/local_split): Bonsai on demand for a private turn.

Under the split the pinned Quick reflexes seat is FridayWeaver 2. A private turn needs Bonsai, and the two do not fit
the card together, so this lease is the one R10 exception besides the voice leases: it displaces the sidekick too.
Friday is not mute meanwhile, because Bonsai itself answers. Release (or the idle expiry) brings FridayWeaver back.
"""
import threading

from agent_friday.services import local_scheduler as lsched
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import residency_policy as rp

FW2 = "gemma4:e2b-fridayweaver-2.0-r1"
BONSAI = "bonsai2:27b"
SPLIT_PLAN = {"sidekick": {"model_id": FW2, "status": "pinned", "device": "gpu", "num_ctx": 65536},
              "memory_manager": {"model_id": BONSAI, "status": "on_demand", "device": "gpu", "num_ctx": 65536}}


class _Llama:
    def __init__(self, live):
        self.procs = {m: ("proc", 8090) for m in live}
        self.evicted = []

    def evict(self, model_id):
        self.evicted.append(model_id)
        self.procs.pop(model_id, None)


class _Ollama:
    def resident(self):
        return []

    def evict(self, model_id):
        pass


def _arbiter(monkeypatch, load_raises=False):
    arb = object.__new__(ra.Arbiter)
    arb._lock = threading.Lock()
    arb.plan = {"seats": {r: dict(s) for r, s in SPLIT_PLAN.items()}, "refusals": []}
    arb.llama = _Llama({FW2})
    arb.ollama = _Ollama()
    arb.lease = None
    arb.state = ra.STATE_DEFAULT
    arb.seat_problems = {}
    arb.profile = {}
    arb.entries = [{"model_id": BONSAI}, {"model_id": FW2}]
    arb.records = []
    arb._record = lambda *a: arb.records.append(a)
    arb.gpu_not_ours = lambda: None
    arb._gpu_embedder = lambda: None
    arb.admit = lambda entry, **k: {"ok": True}
    arb.leased = []

    def load_leased(seat, role):
        if load_raises:
            raise RuntimeError("llama-server exited: out of memory")
        arb.leased.append((seat["model_id"], role, seat.get("num_ctx")))
        arb.llama.procs[seat["model_id"]] = ("proc", 8110)
    arb._load_leased = load_leased
    arb.restored = []
    arb._rollback = lambda: arb.records.append(("rollback",))
    arb.sidekick_loads = []

    def load_pinned(seat, role):
        arb.sidekick_loads.append((seat["model_id"], role))
        arb.llama.procs[seat["model_id"]] = ("proc", 8090)
    arb._load_pinned = load_pinned
    monkeypatch.setattr(ra.Arbiter, "_build_hours_active", staticmethod(lambda: False))
    from agent_friday.services import hardware_profile, machine_monitor
    monkeypatch.setattr(hardware_profile, "vram_headroom",
                        lambda reserve_mib=None, **k: {"ok": True, "total_mib": 12288})
    monkeypatch.setattr(machine_monitor, "last_sample", lambda: {})
    monkeypatch.setattr(machine_monitor, "disk_system_verdict", lambda s: {"status": "ok"})
    return arb


def test_private_turn_swaps_fridayweaver_for_bonsai(monkeypatch):
    arb = _arbiter(monkeypatch)
    got = arb._grant_now("private_turn", role=BONSAI, ttl_s=600)
    assert got["ok"], got
    assert FW2 in arb.llama.evicted and FW2 not in arb.llama.procs
    assert arb.leased == [(BONSAI, "private", 65536)]
    assert got["lease"]["kind"] == "private_turn" and got["lease"]["model_id"] == BONSAI
    assert got["lease"]["displaced"] == ["sidekick"]


def test_private_turn_needs_a_model(monkeypatch):
    arb = _arbiter(monkeypatch)
    assert not arb._grant_now("private_turn", role=None)["ok"]
    assert arb.llama.evicted == []


def test_a_failed_bonsai_load_rolls_back_and_fridayweaver_returns(monkeypatch):
    arb = _arbiter(monkeypatch, load_raises=True)
    got = arb._grant_now("private_turn", role=BONSAI, ttl_s=600)
    assert not got["ok"] and got.get("rolled_back")
    assert ("rollback",) in arb.records
    assert FW2 in arb.llama.procs and (FW2, "sidekick") in arb.sidekick_loads


def test_release_evicts_bonsai_and_brings_fridayweaver_back(monkeypatch):
    arb = _arbiter(monkeypatch)
    monkeypatch.setattr(ra, "_restore_sleep", lambda s: None, raising=False)
    assert arb._grant_now("private_turn", role=BONSAI, ttl_s=600)["ok"]
    out = arb.release("private_turn")
    assert out["ok"], out
    assert BONSAI not in arb.llama.procs and FW2 in arb.llama.procs
    assert arb.lease is None


def test_expiry_releases_the_private_lease(monkeypatch):
    arb = _arbiter(monkeypatch)
    monkeypatch.setattr(ra, "_restore_sleep", lambda s: None, raising=False)
    arb._grant_now("private_turn", role=BONSAI, ttl_s=600)
    arb.lease["expires_at"] = 0
    arb.expire_if_due()
    assert arb.lease is None and FW2 in arb.llama.procs and BONSAI not in arb.llama.procs


def test_private_turn_renews_while_the_conversation_continues(monkeypatch):
    arb = _arbiter(monkeypatch)
    arb._grant_now("private_turn", role=BONSAI, ttl_s=1)
    assert arb.renew("private_turn", 600)["ok"]
    assert arb.lease["expires_at"] > __import__("time").time() + 500


def test_every_other_lease_still_keeps_the_sidekick(monkeypatch):
    arb = _arbiter(monkeypatch)
    arb.comfy = type("C", (), {"reserve_vram_mib": 0, "start": lambda s: 0.1, "stop": lambda s: None})()
    arb._evict_all_but_retained = lambda: None
    assert arb._grant_now("bench_job", ttl_s=60)["ok"]
    assert FW2 in arb.llama.procs


def test_policy_names_the_exception():
    assert "private_turn" in rp.LEASES_DISPLACING_SIDEKICK


def test_scheduler_knows_the_kind():
    assert lsched.KIND_CLASS["private_turn"] == "user_job"
    assert "private_turn" in lsched.LOADING_KINDS
    assert "private" in lsched._lease_word("private_turn")
