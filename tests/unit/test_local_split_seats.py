"""Flipping the owner's split re-seats the card: on, FridayWeaver 2 is the pinned Quick reflexes seat and Bonsai
leaves (it loads on demand for private turns); off, the plan goes back to the owner's own bindings."""
import threading

from agent_friday.services import local_split as ls
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import seat_binding as sb

BONSAI_LOCAL = {"capability_routing": {"local": {"model": "bonsai2:27b", "provider": "llama-cpp-local"}}}


def test_split_pins_fridayweaver_as_the_quick_seat(monkeypatch):
    monkeypatch.setattr(ls, "_installed", lambda m: True)
    out = sb.overrides_from_settings(dict(BONSAI_LOCAL, local_turn_split=ls.MODE_SPLIT))
    assert out["sidekick"] == ls.QUICK_MODEL


def test_split_off_keeps_the_owners_binding(monkeypatch):
    monkeypatch.setattr(ls, "_installed", lambda m: True)
    assert sb.overrides_from_settings(dict(BONSAI_LOCAL, local_turn_split="off"))["sidekick"] == "bonsai2:27b"
    assert sb.overrides_from_settings(BONSAI_LOCAL)["sidekick"] == "bonsai2:27b"


def test_split_without_fridayweaver_installed_changes_nothing(monkeypatch):
    monkeypatch.setattr(ls, "_installed", lambda m: False)
    out = sb.overrides_from_settings(dict(BONSAI_LOCAL, local_turn_split=ls.MODE_SPLIT))
    assert out["sidekick"] == "bonsai2:27b"


class _Llama:
    def __init__(self, live):
        self.procs = {m: ("p", 8090) for m in live}
        self.evicted = []

    def evict(self, m):
        self.evicted.append(m)
        self.procs.pop(m, None)


def _arb(live, seats, lease=None):
    arb = object.__new__(ra.Arbiter)
    arb._lock = threading.Lock()
    arb.plan = {"seats": seats, "refusals": []}
    arb.llama = _Llama(live)
    arb.ollama = type("O", (), {"resident": lambda s: [], "evict": lambda s, m: None})()
    arb.lease = lease
    arb.state = ra.STATE_DEFAULT
    arb.seat_problems = {}
    arb.entries = []
    arb.records = []
    arb._record = lambda *a: arb.records.append(a)
    arb.gpu_not_ours = lambda: None
    arb._build_hours_active = lambda: False
    arb.loaded = []

    def load(seat, role):
        arb.loaded.append(seat["model_id"])
        arb.llama.procs[seat["model_id"]] = ("p", 8091)
    arb._load_pinned = load
    return arb


def test_reseat_swaps_bonsai_out_and_fridayweaver_in():
    arb = _arb({"bonsai2:27b"}, {"sidekick": {"model_id": ls.QUICK_MODEL, "status": "pinned", "device": "gpu"}})
    out = arb.reseat_to_plan("split on")
    assert out["ok"] and arb.llama.evicted == ["bonsai2:27b"] and arb.loaded == [ls.QUICK_MODEL]


def test_reseat_waits_for_a_held_lease():
    arb = _arb({"bonsai2:27b"}, {"sidekick": {"model_id": ls.QUICK_MODEL, "status": "pinned", "device": "gpu"}},
               lease={"kind": "voice_call"})
    out = arb.reseat_to_plan("split on")
    assert arb.llama.evicted == [] and "lease" in out["note"]


def test_reseat_leaves_the_card_alone_when_it_is_not_fridays():
    arb = _arb({"bonsai2:27b"}, {"sidekick": {"model_id": ls.QUICK_MODEL, "status": "pinned", "device": "gpu"}})
    arb.gpu_not_ours = lambda: "Friday is stood down"
    out = arb.reseat_to_plan("split on")
    assert arb.llama.evicted == [] and not out["ok"]
