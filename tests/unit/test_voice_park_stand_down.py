"""The voice-call lease stands the brain down for real.

`_evict_pinned` reads the plan. A brain the plan does not name as a pinned
GPU seat (adopted after a restart, planned while the card was not Friday's,
or the model the retained sidekick also serves) was left serving: the grant
returned with nothing displaced and the call loaded its front into a full
card. `stand_down_for_voice` stops the live seat by model id, says why the
plan missed it, and release brings it back.
"""
import threading

from agent_friday.services import residency_arbiter as ra


class _Llama:
    def __init__(self, live):
        self.procs = {m: ("proc", 8090) for m in live}
        self.adopted = 0
        self.evicted = []

    def adopt_live(self):
        self.adopted += 1

    def evict(self, model_id):
        self.evicted.append(model_id)
        self.procs.pop(model_id, None)


def _arbiter(seats, live=("bonsai2:27b",), lease=True):
    arb = object.__new__(ra.Arbiter)
    arb._lock = threading.Lock()
    arb.plan = {"seats": seats}
    arb.llama = _Llama(live)
    arb.lease = {"kind": "voice_call", "displaced": []} if lease else None
    arb.records = []
    arb._record = lambda *a: arb.records.append(a)
    return arb


BRAIN = {"model_id": "bonsai2:27b", "status": "pinned", "device": "gpu"}


def test_an_adopted_brain_the_plan_missed_is_stood_down_and_recorded():
    arb = _arbiter({"interactive_brain": dict(BRAIN, status="on-demand")})
    res = arb.stand_down_for_voice("bonsai2:27b")
    assert res["ok"] and arb.llama.adopted == 1 and arb.llama.evicted == ["bonsai2:27b"]
    assert "interactive_brain is on-demand, not pinned" in res["why_missed"]
    assert arb.lease["stood_down"] == ["bonsai2:27b"]


def test_why_the_plan_missed_it_is_said_plainly():
    assert "retained" in _arbiter({"interactive_brain": BRAIN, "sidekick": BRAIN}) \
        .why_not_displaced("bonsai2:27b")
    assert "planned on cpu" in _arbiter({"interactive_brain": dict(BRAIN, device="cpu")}) \
        .why_not_displaced("bonsai2:27b")
    assert "no seat in the plan" in _arbiter({}).why_not_displaced("bonsai2:27b")


def test_no_stand_down_without_a_voice_lease():
    arb = _arbiter({"interactive_brain": BRAIN}, lease=False)
    assert arb.stand_down_for_voice("bonsai2:27b")["ok"] is False and arb.llama.evicted == []


def test_release_brings_a_stood_down_brain_back():
    arb = _arbiter({"interactive_brain": dict(BRAIN, status="on-demand")})
    arb.stand_down_for_voice("bonsai2:27b")
    calls = []
    arb.compute_plan = lambda: calls.append("plan")
    arb._load_default_seats = lambda: calls.append("seats") or arb.llama.procs.update(
        {"bonsai2:27b": ("proc", 8090)})
    arb._restore_stood_down(arb.lease["stood_down"])
    assert calls == ["plan", "seats"] and "bonsai2:27b" in arb.llama.procs
    arb2 = _arbiter({"interactive_brain": BRAIN})
    arb2.compute_plan = lambda: calls.append("again")
    arb2._restore_stood_down(["bonsai2:27b"])        # already back: nothing to do
    assert "again" not in calls
