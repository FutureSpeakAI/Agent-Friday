"""Decision heads: thresholds as versioned files, directions in code, and no way
for Laya to make herself bolder.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md section 6 and
acceptance tests A1 to A5 and A9 (owner decisions S1 to S9, 2026-10-24).

On main before this change there is no services/decision_heads.py and no
resources/decision_heads/: the tests that use the module fail there on an
assertion (the fixture turns the missing module into a failure that says so).
``test_the_threshold_card_kind_is_screen_only`` is a guard on an existing
rule this change relies on, and passes on main.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import importlib
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agent_friday"
SHIPPED = SRC / "resources" / "decision_heads"
KEY = b"k" * 32
HEAD = "triage.mail"
#: The module's own _key, captured before any test replaces it.
REAL_KEY: list = []


@pytest.fixture
def dh():
    try:
        mod = importlib.import_module("agent_friday.services.decision_heads")
    except ImportError as e:
        pytest.fail("services/decision_heads does not exist (%s): thresholds are not versioned files" % e)
    if not REAL_KEY:
        REAL_KEY.append(mod._key)
    mod._reset_caches_for_tests()
    yield mod
    mod._reset_caches_for_tests()


@pytest.fixture
def home(tmp_path, monkeypatch, dh):
    friday = tmp_path / "friday"
    friday.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(friday))
    monkeypatch.delenv("FRIDAY_TRIAL", raising=False)
    monkeypatch.setattr(dh, "_key", lambda: KEY)
    return friday


class Cards:
    """A stand-in approvals store holding the cards a test pressed."""

    def __init__(self, monkeypatch):
        from agent_friday.services import approvals
        self.recs = {}
        monkeypatch.setattr(approvals, "get_approval", lambda i: dict(self.recs[i]) if i in self.recs else None)

    def press(self, dh, hid, *, change=None, undo=False, card_id="appr_dmcard01", **override):
        rec = {"id": card_id, "status": "approved", "kind": "laya_threshold", "decided_by": "owner:ui",
               "payload": dh.card_payload(hid, change=change, undo=undo),
               "title": dh.card_text(hid, change=change, undo=undo)[0]}
        rec.update(override)
        self.recs[card_id] = rec
        return card_id


@pytest.fixture
def cards(monkeypatch):
    return Cards(monkeypatch)


def _copy_shipped(tmp_path, monkeypatch, dh) -> Path:
    copy = tmp_path / "decision_heads"
    shutil.copytree(SHIPPED, copy)
    monkeypatch.setattr(dh, "SHIPPED_DIR", copy)
    dh._reset_caches_for_tests()
    return copy


def _rehash(copy: Path, hid: str, mutate) -> None:
    """Edit a shipped head file and update its manifest row, the way a release would."""
    f = copy / ("%s.json" % hid)
    spec = json.loads(f.read_text(encoding="utf-8"))
    mutate(spec)
    text = json.dumps(spec, indent=2) + "\n"
    f.write_bytes(text.encode("utf-8"))
    m = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    for r in m["heads"]:
        if r["id"] == hid:
            r["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            r["version"] = spec.get("version", r["version"])
    (copy / "manifest.json").write_bytes((json.dumps(m, indent=2) + "\n").encode("utf-8"))


def _bolder(dh, act=0.45, mode=None):
    cur = dh.effective(HEAD)
    return {"thresholds": dict(cur["thresholds"], act=act), "mode": mode or cur["mode"]}


# --- A1: the shipped heads exist, check out, and each is a Laya preset ---------

def test_every_shipped_head_validates_and_is_a_laya_preset(dh):
    assert (SHIPPED / "manifest.json").exists(), "no resources/decision_heads/manifest.json"
    ids = dh.head_ids()
    assert ids, "the manifest names no heads"
    from agent_friday.services import laya
    for hid in ids:
        spec = dh.shipped(hid)
        assert spec is not None, "%s refused: %s" % (hid, dh.problems().get(hid))
        assert spec["preset"] in laya.PRESETS, "%s names preset %r, which laya.ask does not have" % (hid, spec["preset"])
        assert spec["mode_default"] in ("off", "shadow")
        assert spec["direction"] == dh.DIRECTIONS[hid]


def test_every_manifest_pin_matches_what_the_loader_reads(dh):
    # A Windows checkout may turn LF into CRLF; the loader reads LF text, and the pins must match it.
    m = json.loads((SHIPPED / "manifest.json").read_text(encoding="utf-8"))
    for r in m["heads"]:
        assert dh.shipped_sha(r["id"]) == r["sha256"], r["file"]


# --- direction and scorer are code, not data ------------------------------------

def test_a_file_that_changes_a_heads_direction_is_refused(tmp_path, monkeypatch, dh):
    copy = _copy_shipped(tmp_path, monkeypatch, dh)
    _rehash(copy, "triage.notify", lambda s: s.update(direction="label_only"))
    assert dh.shipped("triage.notify") is None
    assert "direction" in dh.problems()["triage.notify"]
    assert dh.effective("triage.notify") is None


def test_a_hand_edited_shipped_file_is_refused(tmp_path, monkeypatch, dh):
    copy = _copy_shipped(tmp_path, monkeypatch, dh)
    f = copy / ("%s.json" % HEAD)
    f.write_bytes(f.read_bytes().replace(b'"act": 0.55', b'"act": 0.05'))
    assert dh.shipped(HEAD) is None
    assert "manifest hash" in dh.problems()[HEAD]


@pytest.mark.parametrize("scorer", ["ternary-bonsai:1.7b", "bonsai2:27b", "some-unknown-model"])
def test_a_generative_or_unknown_scorer_is_never_a_judge(dh, scorer):
    spec = json.loads((SHIPPED / ("%s.json" % HEAD)).read_text(encoding="utf-8"))
    spec["scorer"] = scorer
    assert any("not an allowed judge" in p for p in dh.validate(spec))


def test_the_scorer_table_holds_no_generative_entry(dh):
    assert all(not e.get("generative", True) for e in dh.SCORERS.values())


@pytest.mark.parametrize("field,value,words", [("mode_default", "suggest", "never further"),
                                               ("mode_default", "act", "never further"),
                                               ("version", True, "whole number"),
                                               ("thresholds", {"act": 0.5, "abstain": 0.6}, "abstain")])
def test_a_shipped_head_with_a_bad_field_is_refused(dh, field, value, words):
    spec = json.loads((SHIPPED / ("%s.json" % HEAD)).read_text(encoding="utf-8"))
    spec[field] = value
    assert any(words in p for p in dh.validate(spec)), dh.validate(spec)


# --- A9: a choice head always has a way to say "none of these" --------------------

def test_a_choice_head_without_an_abstain_label_is_refused(dh):
    spec = json.loads((SHIPPED / ("%s.json" % HEAD)).read_text(encoding="utf-8"))
    spec["labels"] = [l for l in spec["labels"] if l not in dh.ABSTAIN_LABELS]
    assert any("'other', 'unsure' or 'none'" in p for p in dh.validate(spec))
    for hid in dh.head_ids():
        s = dh.shipped(hid)
        if s["question_type"] == "choice":
            assert set(s["labels"]) & dh.ABSTAIN_LABELS, hid


# --- S7: learning tightens on its own; bolder needs Stephen's press -------------

def test_tighten_raises_a_threshold_and_records_it(home, dh):
    before = dh.effective(HEAD)
    row = dh.tighten(HEAD, {"thresholds": {"act": before["thresholds"]["act"] + 0.1}}, actor="learning:test")
    after = dh.effective(HEAD)
    assert after["thresholds"]["act"] == pytest.approx(before["thresholds"]["act"] + 0.1)
    assert after["source"] == "override" and row["actor"] == "learning:test"
    assert dh.history(HEAD)[-1]["after"]["thresholds"]["act"] == pytest.approx(after["thresholds"]["act"])
    assert "+o" in dh.versions()[HEAD] and dh.versions()[HEAD] == after["tag"]


@pytest.mark.parametrize("change", [{"thresholds": {"act": 0.10}}, {"thresholds": {"abstain": 0.0}},
                                    {"mode": "act"}, {"mode": "suggest"}])
def test_learning_can_never_make_a_head_bolder(home, dh, change):
    with pytest.raises(dh.HeadChangeRefused):
        dh.tighten(HEAD, change, actor="learning:test")
    assert not (home / "decision_heads" / "active" / ("%s.json" % HEAD)).exists()
    assert dh.effective(HEAD)["source"] == "shipped"


@pytest.mark.parametrize("change", [{"thresholds": {"act": float("nan")}}, {"thresholds": {"act": 1e999}},
                                    {"thresholds": {"act": True}}, {"thresholds": {"novel": 0.9}},
                                    {"mode": "act", "extra": 1}, {"thresholds": {"act": 0.2, "abstain": 0.5}}])
def test_a_malformed_change_is_refused_before_anything_is_written(home, dh, change):
    with pytest.raises(dh.HeadChangeRefused):
        dh.tighten(HEAD, change, actor="learning:test")
    assert not (home / "decision_heads" / "active").exists()


def test_bolder_needs_an_approved_press_on_a_card_naming_exactly_this_change(home, cards, dh):
    values = _bolder(dh, mode="act")
    with pytest.raises(dh.HeadChangeRefused):
        dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}, "mode": "act"}, card_id="appr_nothing")
    card = cards.press(dh, HEAD, change=values)
    dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}, "mode": "act"}, card_id=card)
    eff = dh.effective(HEAD)
    assert eff["mode"] == "act" and eff["thresholds"]["act"] == pytest.approx(0.45)


@pytest.mark.parametrize("bad", [dict(decided_by="owner:/voice/"), dict(decided_by="owner"),
                                 dict(decided_by="owner:chat"), dict(decided_by="grant:g1"),
                                 dict(status="pending"), dict(status="auto_approved"),
                                 dict(kind="instruction_trial"), dict(title="Something else"),
                                 dict(payload={"decision_head": HEAD, "change": {}})])
def test_a_spoken_typed_pending_retitled_or_mismatched_card_is_not_a_press(home, cards, dh, bad):
    values = _bolder(dh)
    card = cards.press(dh, HEAD, change=values, **bad)
    with pytest.raises(dh.HeadChangeRefused):
        dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}}, card_id=card)
    assert dh.effective(HEAD)["thresholds"]["act"] == pytest.approx(0.55)


def test_a_card_for_another_head_does_not_count(home, cards, dh):
    card = cards.press(dh, "triage.news", change=_bolder(dh))
    with pytest.raises(dh.HeadChangeRefused):
        dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}}, card_id=card)


def test_one_card_authorises_one_application(home, cards, dh):
    values = _bolder(dh)
    card = cards.press(dh, HEAD, change=values)
    dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}}, card_id=card)
    dh.undo(HEAD, actor="owner:ui")                 # back to shipped is stricter here: no card needed
    with pytest.raises(dh.HeadChangeRefused):
        dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}}, card_id=card)
    assert dh.effective(HEAD)["source"] == "shipped"


def test_a_bolder_override_stops_counting_when_its_card_no_longer_checks_out(home, cards, dh):
    values = _bolder(dh)
    card = cards.press(dh, HEAD, change=values)
    dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}}, card_id=card)
    cards.recs[card]["status"] = "expired"
    eff = dh.effective(HEAD)
    assert eff["thresholds"]["act"] == pytest.approx(0.55) and eff["source"] == "shipped"
    assert "approval card" in dh.problems()[HEAD]
    assert "+o" not in dh.versions()[HEAD]


def test_learning_leaves_the_owners_bolder_setting_alone(home, cards, dh):
    card = cards.press(dh, HEAD, change=_bolder(dh))
    dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}}, card_id=card)
    with pytest.raises(dh.HeadChangeRefused):
        dh.tighten(HEAD, {"thresholds": {"act": 0.50}}, actor="learning:test")
    assert dh.effective(HEAD)["thresholds"]["act"] == pytest.approx(0.45)


def test_a_change_built_on_stale_values_is_refused(home, dh):
    cur = dh.effective(HEAD)
    dh.tighten(HEAD, {"thresholds": {"act": 0.9}}, actor="learning:a")
    with pytest.raises(dh.HeadChangeRefused):
        dh._write(HEAD, {"thresholds": dict(cur["thresholds"], act=0.6), "mode": cur["mode"]},
                  actor="learning:b", card="", reason="", built_on=cur)
    assert dh.effective(HEAD)["thresholds"]["act"] == pytest.approx(0.9)


# --- undo: stricter is an owner request; bolder is a press ----------------------

def test_undo_that_would_drop_learnings_tightening_needs_a_press(home, cards, dh):
    dh.tighten(HEAD, {"thresholds": {"act": 0.9}}, actor="learning:test")
    with pytest.raises(dh.HeadChangeRefused):
        dh.undo(HEAD, actor="owner")
    with pytest.raises(dh.HeadChangeRefused):
        dh.undo(HEAD, actor="learning:test")
    assert dh.effective(HEAD)["thresholds"]["act"] == pytest.approx(0.9)
    card = cards.press(dh, HEAD, undo=True)
    dh.undo(HEAD, actor="owner:ui", card_id=card)
    assert dh.effective(HEAD)["source"] == "shipped"
    assert [r["action"] for r in dh.history(HEAD)] == ["write", "undo"]


# --- A3: overrides are signed, bound to the shipped file, and fail closed -------

def test_an_override_edited_outside_friday_is_ignored(home, dh):
    dh.tighten(HEAD, {"thresholds": {"act": 0.9}}, actor="learning:test")
    p = home / "decision_heads" / "active" / ("%s.json" % HEAD)
    p.write_text(p.read_text(encoding="utf-8").replace("0.9", "0.1"), encoding="utf-8")
    eff = dh.effective(HEAD)
    assert eff["source"] == "shipped" and eff["thresholds"]["act"] == pytest.approx(0.55)
    assert "signature" in dh.problems()[HEAD]


def test_an_override_signed_with_another_key_is_ignored(home, monkeypatch, dh):
    dh.tighten(HEAD, {"thresholds": {"act": 0.9}}, actor="learning:test")
    monkeypatch.setattr(dh, "_key", lambda: b"x" * 32)
    assert dh.effective(HEAD)["source"] == "shipped"


def test_with_no_governance_key_nothing_is_written_or_loaded(home, monkeypatch, dh):
    def nokey():
        raise RuntimeError("no key")
    monkeypatch.setattr(dh, "_key", nokey)
    with pytest.raises(dh.HeadChangeRefused):
        dh.tighten(HEAD, {"thresholds": {"act": 0.9}}, actor="learning:test")
    assert not (home / "decision_heads" / "active").exists()


def test_inside_a_practice_task_trial_no_override_is_signed(home, monkeypatch, dh):
    monkeypatch.setattr(dh, "_key", REAL_KEY[0])     # the module's own key function, not the test key
    monkeypatch.setenv("FRIDAY_TRIAL", "1")
    with pytest.raises(dh.HeadChangeRefused):
        dh.tighten(HEAD, {"thresholds": {"act": 0.9}}, actor="learning:test")
    assert not (home / "decision_heads" / "active").exists()


def test_an_update_to_the_shipped_head_pauses_an_older_override(home, tmp_path, monkeypatch, dh):
    copy = _copy_shipped(tmp_path, monkeypatch, dh)
    dh.tighten(HEAD, {"thresholds": {"act": 0.9}}, actor="learning:test")
    _rehash(copy, HEAD, lambda s: s.update(version=2, thresholds=dict(s["thresholds"], act=0.6)))
    dh._reset_caches_for_tests()
    eff = dh.effective(HEAD)
    assert eff["source"] == "shipped" and eff["thresholds"]["act"] == pytest.approx(0.6)
    assert "paused" in dh.problems()[HEAD]


def test_a_card_pressed_before_an_update_does_not_apply_after_it(home, tmp_path, monkeypatch, cards, dh):
    copy = _copy_shipped(tmp_path, monkeypatch, dh)
    values = _bolder(dh)
    card = cards.press(dh, HEAD, change=values)
    _rehash(copy, HEAD, lambda s: s.update(version=2))
    dh._reset_caches_for_tests()
    with pytest.raises(dh.HeadChangeRefused):
        dh.apply_owner_change(HEAD, {"thresholds": {"act": 0.45}}, card_id=card)


# --- versions ride every Laya receipt -------------------------------------------

def test_versions_are_empty_when_cold_and_name_every_head_when_warm(home, dh):
    with dh._LOCK:                                   # a stray laya.warm thread cannot interleave here
        dh._reset_caches_for_tests()
        assert dh.versions() == {}
    dh.warm()
    v = dh.versions()
    assert set(v) == set(dh.head_ids())
    for hid, s in v.items():
        assert s == "v%d-%s" % (dh.shipped(hid)["version"], dh.shipped_sha(hid)[:12])


def test_laya_versions_carry_the_heads_once_warm(home, dh):
    from agent_friday.services import laya
    dh.warm()
    assert laya.versions().get("heads") == dh.versions()


# --- A4: Laya never promotes herself ------------------------------------------

def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                 or (isinstance(n.func, ast.Name) and n.func.id == name))]


def _laya_files():
    services = SRC / "services"
    files = sorted(services.glob("laya*.py")) + [services / "decision_heads.py"]
    for extra in ("triage_heads.py", "reflex_turn.py"):
        if (services / extra).exists():
            files.append(services / extra)
    return files


def test_no_laya_module_can_make_a_head_bolder_or_raise_its_own_card(dh):
    for f in _laya_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for bad in ("apply_owner_change", "undo", "card_payload", "card_text", "_write", "sign", "_write_atomic"):
            if f.name == "decision_heads.py":
                continue
            assert not [c for c in _calls(tree, bad)
                        if isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
                        and c.func.value.id in ("decision_heads", "dh")], "%s calls decision_heads.%s" % (f.name, bad)
        if f.name != "decision_heads.py":
            assert '"decision_head"' not in f.read_text(encoding="utf-8"), "%s builds a decision-head card" % f.name
    tree = ast.parse((SRC / "services" / "decision_heads.py").read_text(encoding="utf-8"))
    for bad in ("create_approval", "gate_action", "record_pre_approved", "decide", "decide_with_outcome",
                "mark_used", "claim_for_execution"):
        assert not _calls(tree, bad), "decision_heads calls approvals.%s" % bad
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and isinstance(n.value, ast.Name) and n.value.id == "approvals"}
    assert attrs == {"get_approval"}, attrs


def test_the_threshold_card_kind_is_screen_only():
    from agent_friday.services import approvals
    assert approvals.SCREEN_ONLY_KINDS.get("laya_threshold") == frozenset({"owner:ui"})


def test_a_signature_binds_the_head_the_values_the_card_and_the_shipped_file(dh):
    vals = {"mode": "shadow", "thresholds": {"act": 0.6}}
    ref = dh.sign(HEAD, vals, "c", "s", KEY)
    assert dh.sign("triage.news", vals, "c", "s", KEY) != ref
    assert dh.sign(HEAD, {"mode": "act", "thresholds": {"act": 0.6}}, "c", "s", KEY) != ref
    assert dh.sign(HEAD, vals, "d", "s", KEY) != ref
    assert dh.sign(HEAD, vals, "c", "t", KEY) != ref
    assert hmac.compare_digest(ref, dh.sign(HEAD, vals, "c", "s", KEY))
