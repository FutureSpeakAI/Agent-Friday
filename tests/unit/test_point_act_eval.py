"""DA-2 gate: the point-and-act eval (60 synthetic stage snapshots x utterances, 20 spoken with
speech-recognition noise), scored by tools/point_act_eval.py.

The default run uses the stand-in encoder, so it needs no model; its job is the SAFETY floor and the
rule paths (ordinals, deixis, senders, kinds, writes deferred), which do not depend on the encoder.
The real-encoder run is the certification of the choice-head constants: set FRIDAY_POINT_ACT_REAL=1 and
FRIDAY_REAL_HOME (a >= 4 GB free machine); it asserts the same zero-wrong-item bar.
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

from agent_friday.services import point_act as pa

ROOT = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("point_act_eval", ROOT / "tools/point_act_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def stand_in_scale(monkeypatch):
    monkeypatch.setattr(pa, "CHOICE_MIN_SIM", 0.40)
    monkeypatch.setattr(pa, "CHOICE_MARGIN", 0.08)
    monkeypatch.setattr(pa, "CHOICE_ASK_MIN", 0.30)
    pa.reset()


def test_the_fixture_has_sixty_snapshots_and_a_twenty_case_spoken_subset():
    ev = _tool()
    cases, snaps = ev.load()
    assert len(snaps) == 60 and len({c["snap"] for c in cases}) == 60
    assert sum(1 for c in cases if c.get("spoken")) == 20
    assert {c["want"] for c in cases} == {"act", "ask", "none"}
    assert all(c["refs"] for c in cases if c["want"] == "act") and not any(c["refs"] for c in cases if c["want"] != "act")


def test_the_fixture_is_the_builders_output_so_labels_cannot_drift_from_their_oracle():
    ev = _tool()
    built = ev.build_fixture()
    on_disk = json.loads(ev.FIXTURE.read_text(encoding="utf-8"))
    assert built["cases"] == on_disk["cases"] and built["snapshots"] == on_disk["snapshots"]


def test_no_act_on_the_wrong_item_and_none_where_it_should_have_asked(stand_in_scale):
    ev = _tool()
    cases, snaps = ev.load()
    res = ev.score(cases, snaps, ev.lexical_embed)
    assert res["bad"] == [], res["bad"][:5]
    assert res["all"]["wrong_item_acts"] == 0 and res["all"]["over_acts"] == 0


def test_exact_ref_accuracy_where_acted_and_the_ask_rate_are_reported(stand_in_scale):
    ev = _tool()
    cases, snaps = ev.load()
    res = ev.score(cases, snaps, ev.lexical_embed)
    assert res["plain"]["exact_ref_accuracy"] >= 0.95
    assert res["spoken"]["cases"] == 20 and res["spoken"]["exact_ref_accuracy"] >= 0.95
    assert 0.0 < res["all"]["ask_rate"] < 0.5
    # the rule paths do not need an encoder at all
    for kind in ("first-n", "last-n", "ordinal", "from-sender", "deictic", "deictic-write", "write-ordinal"):
        assert res["by_kind"][kind]["exact_ref_accuracy"] == 1.0, kind
        assert res["by_kind"][kind]["under_act_rate"] == 0.0, kind


def test_the_rule_paths_never_touch_the_encoder(stand_in_scale):
    ev = _tool()
    cases, snaps = ev.load()
    rule = [c for c in cases if c["note"].split(":")[0] in ("first-n", "last-n", "ordinal", "from-sender", "deictic",
                                                              "deictic-write", "write-ordinal", "unread", "out of range")]

    def boom(texts):
        raise AssertionError("the encoder was used for a rule-resolved phrase")
    res = ev.score(rule, snaps, boom)
    assert res["all"]["wrong_item_acts"] == 0 and res["all"]["over_acts"] == 0
    assert res["all"]["exact_ref_accuracy"] == 1.0


def test_no_change_in_the_eval_is_ever_a_tool_route(stand_in_scale):
    ev = _tool()
    cases, snaps = ev.load()
    writes = [c for c in cases if c["class"] == "write" and c["want"] == "act"]
    assert len(writes) > 100
    acted = 0
    for c in writes:
        p = ev.run_case(c, snaps[c["snap"]], ev.lexical_embed)
        assert p is None or p.decision in ("defer", "ask"), c["id"]
        assert p is None or p.tool is None, c["id"]
        acted += p is not None and p.decision == "defer"
    assert acted > 100


@pytest.mark.skipif(not os.environ.get("FRIDAY_POINT_ACT_REAL"), reason="certification run: set FRIDAY_POINT_ACT_REAL=1")
def test_certification_with_the_real_encoder(monkeypatch):
    from agent_friday.services import laya2_encoder
    real = Path(os.environ.get("FRIDAY_REAL_HOME") or Path.home()) / ".friday" / "models" / "laya2-encoder"
    if not all((real / n).is_file() for n in laya2_encoder.REQUIRED_FILES):
        pytest.skip("the Laya 2 encoder is not on this machine")
    monkeypatch.setattr(laya2_encoder, "artifacts_dir", lambda: real)
    ev = _tool()
    cases, snaps = ev.load()
    res = ev.score(cases, snaps, None)
    assert res["bad"] == [], res["bad"][:5]
    assert res["plain"]["exact_ref_accuracy"] >= 0.95
