"""Import staging (services/import_staging): nothing imported is durable before the owner answers.

North Star NS-8.10-2 and NS-34.9-7 ("memory tests: import staging"), first-run spec §6.4 and
§7.2. The commit hook here is a recorder, so each test can prove exactly when anything became
durable. On a tree without the module, each test fails at its fixture ("there is no import
staging").
"""
from __future__ import annotations

import importlib
import json

import pytest


def _module():
    try:
        return importlib.import_module("agent_friday.services.import_staging")
    except ImportError:
        pytest.fail("there is no import staging: services/import_staging does not exist")


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    mod = _module()
    committed = []

    def commit(payload):
        if payload.get("explode"):
            raise RuntimeError("store down")
        committed.append(dict(payload))
        return {"created": True}
    monkeypatch.setitem(mod._KINDS, "thing", {"commit": commit, "fields": ("name",)})
    from agent_friday.services import forget_person as fp
    monkeypatch.setattr(fp, "forgotten_names", lambda: set())
    monkeypatch.setattr(fp, "forgotten_emails", lambda: set())
    mod.committed = committed
    mod.home = tmp_path
    return mod


def _rows(*names):
    return [{"key": n.lower(), "label": n, "payload": {"name": n}, "names": [n]} for n in names]


def test_staging_writes_nothing_durable(st):
    out = st.stage("testsrc", "thing", _rows("Ada", "Bo"))
    assert out["ok"] and out["staged"] == 2
    assert st.committed == []
    items = st.list_items(source="testsrc", status="open")
    assert [i["label"] for i in items] == ["Ada", "Bo"]
    for it in items:
        assert it["authority"] == "import" and it["status"] == "proposed"
        assert it["source"] == "testsrc" and it["sensitivity"]
    files = sorted(p.name for p in (st.home / "wiki" / "_staging").glob("imp-*.json"))
    assert len(files) == 2


def test_accept_commits_exactly_once_and_reject_never(st):
    st.stage("testsrc", "thing", _rows("Ada", "Bo", "Cy"))
    ada, bo, cy = st.list_items(source="testsrc", status="open")
    assert st.review(ada["id"], "accept")["ok"]
    assert st.review(ada["id"], "accept")["ok"] is False          # already answered: not committed twice
    assert st.review(bo["id"], "reject")["ok"]
    assert st.review(cy["id"], "defer")["ok"]
    assert st.committed == [{"name": "Ada"}]
    assert [i["label"] for i in st.list_items(source="testsrc", status="open")] == ["Cy"]


def test_a_rejected_item_keeps_no_content_and_is_not_proposed_again(st):
    st.stage("testsrc", "thing", _rows("Ada"))
    ada = st.list_items(source="testsrc")[0]
    st.review(ada["id"], "reject")
    raw = json.loads((st.home / "wiki" / "_staging" / (ada["id"] + ".json")).read_text(encoding="utf-8"))
    assert "payload" not in raw and "label" not in raw and raw["status"] == "rejected"
    out = st.stage("testsrc", "thing", _rows("Ada"))
    assert out["staged"] == 0 and out["already_decided"] == 1
    assert st.list_items(source="testsrc", status="open") == []


def test_edit_accepts_only_the_editable_fields(st):
    st.stage("testsrc", "thing", [{"key": "a", "label": "Ada", "payload": {"name": "Ada", "role": "x"}}])
    it = st.list_items(source="testsrc")[0]
    assert st.review(it["id"], "edit", edits={"name": "Ada L.", "role": "admin"})["ok"]
    assert st.committed == [{"name": "Ada L.", "role": "x"}]


def test_a_failed_commit_leaves_the_item_waiting_with_the_reason(st):
    st.stage("testsrc", "thing", [{"key": "a", "label": "Ada", "payload": {"name": "Ada", "explode": "1"}}])
    it = st.list_items(source="testsrc")[0]
    out = st.review(it["id"], "accept")
    assert out["ok"] is False
    again = st.list_items(source="testsrc", status="open")[0]
    assert again["status"] == "proposed" and "not saved" in again["error"]


def test_a_whole_class_can_be_answered(st):
    st.stage("testsrc", "thing", _rows("Ada", "Bo"))
    out = st.review_class("testsrc", "accept")
    assert out == {"ok": True, "done": 2, "failed": 0}
    assert [c["name"] for c in st.committed] == ["Ada", "Bo"]


def test_every_answer_is_recorded_without_content(st):
    st.stage("testsrc", "thing", _rows("Ada", "Bo"))
    a, b = st.list_items(source="testsrc")
    st.review(a["id"], "accept")
    st.review(b["id"], "reject")
    lines = [json.loads(x) for x in (st.home / "wiki" / "_staging" / "imp-answers.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    assert [(x["answer"], x["result"]) for x in lines] == [("accept", "accepted"), ("reject", "rejected")]
    assert "Ada" not in json.dumps(lines) and "Bo" not in json.dumps(lines)


def test_ids_from_outside_never_reach_another_file(st):
    for bad in ("../x", "imp-testsrc-../../a", "lib-1-abc-fact", "", None, "imp-testsrc-zzzz"):
        assert st.review(bad, "accept")["ok"] is False
    assert st.review("imp-testsrc-0123456789abcdef", "launch")["ok"] is False


def test_an_unknown_kind_or_source_is_refused(st):
    assert st.stage("bad source!", "thing", _rows("Ada"))["ok"] is False
    assert st.stage("testsrc", "nope", _rows("Ada"))["ok"] is False


def test_a_forgotten_person_is_never_staged_and_forgetting_removes_waiting_items(st, monkeypatch):
    from agent_friday.services import forget_person as fp
    st.stage("testsrc", "thing", _rows("Ada Lane", "Bo Kim"))
    assert st.forget_find({"ada lane"}, set()) == 1
    assert st.forget_purge({"ada lane"}, set()) == 1
    assert [i["label"] for i in st.list_items(source="testsrc")] == ["Bo Kim"]
    monkeypatch.setattr(fp, "forgotten_names", lambda: {"ada lane"})
    out = st.stage("testsrc", "thing", _rows("Ada Lane"))
    assert out["staged"] == 0 and out["forgotten"] == 1


def test_a_person_forgotten_after_staging_is_not_saved_on_accept(st, monkeypatch):
    from agent_friday.services import forget_person as fp
    st.stage("testsrc", "thing", _rows("Ada Lane"))
    it = st.list_items(source="testsrc")[0]
    monkeypatch.setattr(fp, "forgotten_names", lambda: {"ada lane"})
    assert st.review(it["id"], "accept")["ok"] is False
    assert st.committed == []


def test_the_forget_registry_asks_import_staging(st):
    from agent_friday.services import forget_registry as fr
    assert "import_staging" in fr.registered()


def test_a_saved_item_keeps_no_content_in_staging(st):
    """Once saved, the person lives in the store it was saved to, where forgetting reaches it."""
    st.stage("testsrc", "thing", [{"key": "a", "label": "Ada Lane", "payload": {"name": "Ada Lane"},
                                   "names": ["Ada Lane"], "emails": ["ada@example.test"]}])
    it = st.list_items(source="testsrc")[0]
    st.review(it["id"], "accept")
    raw = (st.home / "wiki" / "_staging" / (it["id"] + ".json")).read_text(encoding="utf-8")
    assert "Ada" not in raw and "ada@example.test" not in raw
    assert json.loads(raw)["status"] == "accepted"


def test_a_whole_list_is_answered_in_the_files_order(st):
    rows = _rows("Zed", "Amy", "Kit", "Bea")
    st.stage("testsrc", "thing", rows)
    st.review_class("testsrc", "accept")
    assert [c["name"] for c in st.committed] == ["Zed", "Amy", "Kit", "Bea"]


def test_an_edit_cannot_save_a_forgotten_person(st, monkeypatch):
    from agent_friday.services import forget_person as fp
    st.stage("testsrc", "thing", _rows("Ada Lane"))
    it = st.list_items(source="testsrc")[0]
    monkeypatch.setattr(fp, "forgotten_names", lambda: {"bo kim"})
    assert st.review(it["id"], "edit", edits={"name": "Bo Kim"})["ok"] is False
    assert st.committed == []


def test_library_drafts_are_not_mixed_in(st):
    d = st.home / "wiki" / "_staging"
    d.mkdir(parents=True, exist_ok=True)
    (d / "lib-1-abcdef12-fact-1.json").write_text(json.dumps({"id": "lib-1-abcdef12-fact-1",
                                                             "status": "proposed"}), encoding="utf-8")
    assert st.list_items() == []
