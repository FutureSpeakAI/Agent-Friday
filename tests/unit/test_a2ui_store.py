"""The A2UI document store: one stored, versioned, reversible document per workspace view (step 1 of A2UI as
the single workspace format).

Rules held here:
* every save runs the whole document through the V1-V14 profile first; a document that breaks a rule is refused
  with that rule and NOTHING is written (the 75 shared hostile cases are run through the store itself);
* a save carries the revision it was made from; a stale one is refused with the current revision, never merged;
* the previous state is snapshotted before every change, at most 40 versions are kept, and every state can be
  restored; nothing a person did is deleted by an undo;
* his customizations are properties of the document (accent, density, note, summary, hidden component ids): typed
  at the edge, checked against the document, never CSS;
* a write is atomic (a failure leaves the old file whole), a damaged file is an error and never an empty view,
  a view id is one plain name, and a privacy context that changed refuses the write.
"""
import json
import os
from pathlib import Path

import pytest

from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_store as store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "a2ui" / "profile_cases_v1.json"
FX = json.loads(FIXTURE.read_text(encoding="utf-8"))
SUBJECT = "knowledge:home"


def ctx(**kw):
    base = dict(subject=SUBJECT, verbs=lambda name: None, authorship="composer", model_authored=False)
    base.update(kw)
    return prof.Context(**base)


def page(*texts):
    comps = [{"id": "root", "component": "column", "children": ["t%d" % i for i in range(len(texts))]}]
    comps += [{"id": "t%d" % i, "component": "text_block", "text": t, "style": "body"} for i, t in enumerate(texts)]
    return [{"version": "1.0", "createSurface": {"surfaceId": "view", "catalogId": prof.CATALOG_ID,
                                                 "sendDataModel": False, "components": comps}}]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIEWS_DIR", tmp_path / "liquid" / "views")
    monkeypatch.setattr(store, "_admit_write", lambda: 0)
    monkeypatch.setattr(store, "_check_write", lambda origin: None)
    monkeypatch.setattr(store, "_announce", lambda view_id: None)
    return tmp_path


def files(tmp):
    d = tmp / "liquid" / "views"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def save(view_id="knowledge", doc=None, base="new", **kw):
    return store.save(view_id, doc if doc is not None else page("Hello"), ctx=kw.pop("ctx", ctx()),
                      base_rev=base, label=kw.pop("label", "a change"), by=kw.pop("by", "pc"), **kw)


# ── saving ──────────────────────────────────────────────────────────────────

def test_a_first_save_stores_the_document_and_hands_back_its_revision(_home):
    got = save()
    assert got["rev"] and got["rev"] != "new" and got["versions"] == 0
    view = store.load("knowledge")
    assert view["doc"] == page("Hello") and view["rev"] == got["rev"] and view["label"] == "a change"
    assert files(_home) == ["knowledge.json"]


def test_a_second_save_snapshots_the_state_it_replaces(_home):
    first = save(doc=page("One"))
    second = save(doc=page("Two"), base=first["rev"], label="two")
    view = store.load("knowledge")
    assert view["doc"] == page("Two") and second["rev"] != first["rev"] and second["versions"] == 1
    assert [v["kind"] for v in view["versions"]] == ["change"]
    assert view["versions"][0]["doc"] == page("One")


def test_a_save_from_an_old_revision_is_refused_with_the_current_one_and_changes_nothing(_home):
    first = save(doc=page("One"))
    save(doc=page("Two"), base=first["rev"])
    with pytest.raises(store.ViewStale) as e:
        save(doc=page("Mine"), base=first["rev"])
    assert e.value.current_rev == store.load("knowledge")["rev"]
    assert store.load("knowledge")["doc"] == page("Two")


def test_creating_over_an_existing_view_with_a_new_base_is_stale_not_an_overwrite(_home):
    save(doc=page("One"))
    with pytest.raises(store.ViewStale):
        save(doc=page("Two"), base="new")
    assert store.load("knowledge")["doc"] == page("One")


def test_a_save_against_a_view_that_is_not_there_is_stale(_home):
    with pytest.raises(store.ViewStale):
        save(base="0" * 64)


# ── the profile gates every save ──────────────────────────────────────────────

def test_a_good_document_passes_the_shared_fixture_s_good_case(_home):
    fx_ctx = ctx(subject=FX["subject"], verbs=lambda name: FX["verbs"].get(name), authorship="friday_liquid",
                 model_authored=True)
    assert store.save("fixture", FX["good"][0], ctx=fx_ctx, base_rev="new", label="x", by="pc")["rev"]


@pytest.mark.parametrize("case", FX["hostile"], ids=lambda c: c["name"][:60])
def test_every_shared_hostile_document_is_refused_with_its_rule_and_nothing_is_written(_home, case):
    fx_ctx = ctx(subject=FX["subject"], verbs=lambda name: FX["verbs"].get(name), authorship="friday_liquid",
                 model_authored=FX["model_authored"])
    with pytest.raises(store.ViewRefused) as e:
        store.save("fixture", case["document"], ctx=fx_ctx, base_rev="new", label="x", by="pc")
    assert e.value.rule == case["rule"]
    assert files(_home) == []


def test_a_document_from_an_author_the_profile_does_not_know_is_refused(_home):
    with pytest.raises(store.ViewRefused) as e:
        save(ctx=ctx(authorship="a stranger"))
    assert e.value.rule == "V14" and files(_home) == []


def test_a_refused_save_leaves_the_stored_view_and_its_revision_untouched(_home):
    first = save(doc=page("One"))
    bad = page("One")
    bad[0]["createSurface"]["sendDataModel"] = True
    with pytest.raises(store.ViewRefused):
        save(doc=bad, base=first["rev"])
    view = store.load("knowledge")
    assert view["rev"] == first["rev"] and view["doc"] == page("One") and view["versions"] == []


def test_a_document_over_the_size_limit_is_refused_before_it_is_checked(_home):
    huge = page("x" * 10)
    huge[0]["createSurface"]["metadata"] = {"pad": "y" * (store.MAX_DOC_BYTES + 1)}
    with pytest.raises(store.ViewRefused) as e:
        save(doc=huge)
    assert e.value.rule == "size" and files(_home) == []


# ── versions and reversal ─────────────────────────────────────────────────────

def test_at_most_forty_versions_are_kept_newest_last(_home):
    rev = save(doc=page("v0"))["rev"]
    for i in range(1, 46):
        rev = save(doc=page("v%d" % i), base=rev)["rev"]
    versions = store.load("knowledge")["versions"]
    assert len(versions) == store.MAX_VERSIONS
    assert versions[-1]["doc"] == page("v44")


def test_history_lists_newest_first_with_ids_and_no_documents(_home):
    rev = save(doc=page("a"), label="first")["rev"]
    save(doc=page("b"), base=rev, label="second")
    rows = store.history("knowledge")
    assert [r["label"] for r in rows] == ["first"] and set(rows[0]) == {"id", "when", "label", "kind", "by"}


def test_a_version_can_be_restored_and_what_it_replaced_is_kept(_home):
    rev = save(doc=page("one"))["rev"]
    rev = save(doc=page("two"), base=rev)["rev"]
    rev = save(doc=page("three"), base=rev)["rev"]
    target = store.load("knowledge")["versions"][0]["id"]
    got = store.restore("knowledge", target, base_rev=rev, by="pc")
    view = store.load("knowledge")
    assert view["doc"] == page("one") and got["rev"] == view["rev"]
    assert any(v["doc"] == page("three") for v in view["versions"])            # nothing was thrown away


def test_restoring_checks_the_revision_and_the_version_id(_home):
    rev = save(doc=page("one"))["rev"]
    rev2 = save(doc=page("two"), base=rev)["rev"]
    vid = store.load("knowledge")["versions"][0]["id"]
    with pytest.raises(store.ViewStale):
        store.restore("knowledge", vid, base_rev=rev, by="pc")
    with pytest.raises(store.ViewRefused):
        store.restore("knowledge", "no-such-version", base_rev=rev2, by="pc")


def test_a_restored_document_goes_through_the_profile_again(_home, monkeypatch):
    rev = save(doc=page("one"))["rev"]
    save(doc=page("two"), base=rev)
    view = store.load("knowledge")
    vid = view["versions"][0]["id"]
    monkeypatch.setattr(prof, "check", lambda *a, **k: (_ for _ in ()).throw(prof.ProfileError("V4", "now banned")))
    with pytest.raises(store.ViewRefused) as e:
        store.restore("knowledge", vid, base_rev=view["rev"], by="pc", ctx=ctx())
    assert e.value.rule == "V4" and store.load("knowledge")["doc"] == page("two")


def test_undo_walks_back_one_change_at_a_time(_home):
    rev = save(doc=page("one"))["rev"]
    rev = save(doc=page("two"), base=rev)["rev"]
    rev = save(doc=page("three"), base=rev)["rev"]
    rev = store.undo("knowledge", base_rev=rev, by="pc")["rev"]
    assert store.load("knowledge")["doc"] == page("two")
    store.undo("knowledge", base_rev=rev, by="pc")
    assert store.load("knowledge")["doc"] == page("one")


def test_undo_with_nothing_to_undo_is_refused(_home):
    rev = save()["rev"]
    with pytest.raises(store.ViewRefused):
        store.undo("knowledge", base_rev=rev, by="pc")


# ── his customizations are document properties ─────────────────────────────────────

GOOD_LOOK = {"accent": "#3ab0ff", "density": "compact", "note": "Mine, for Monday.", "summary": "Tighter",
             "hidden": ["t1"]}


def test_a_customization_is_saved_with_the_document_and_changes_its_revision(_home):
    first = save(doc=page("a", "b"))
    got = store.set_customization("knowledge", GOOD_LOOK, base_rev=first["rev"], by="pc")
    view = store.load("knowledge")
    assert view["customization"] == GOOD_LOOK and got["rev"] != first["rev"]
    assert view["doc"] == page("a", "b")


@pytest.mark.parametrize("bad", [{"accent": "red"}, {"accent": "#12"}, {"density": "tiny"}, {"note": "n" * 1501},
                                 {"css": "a{}"}, {"hidden": ["nope"]}, {"hidden": "t1"}, {"summary": 5}, "x", None])
def test_a_customization_is_typed_at_the_edge_and_checked_against_the_document(_home, bad):
    first = save(doc=page("a", "b"))
    with pytest.raises(store.ViewRefused):
        store.set_customization("knowledge", bad, base_rev=first["rev"], by="pc")
    assert store.load("knowledge")["customization"] == {} and store.load("knowledge")["rev"] == first["rev"]


def test_a_new_document_version_drops_a_hidden_id_that_is_no_longer_in_it(_home):
    first = save(doc=page("a", "b"))
    rev = store.set_customization("knowledge", {"hidden": ["t1"]}, base_rev=first["rev"], by="pc")["rev"]
    save(doc=page("a"), base=rev)
    assert store.load("knowledge")["customization"].get("hidden", []) == []


def test_a_customization_change_is_a_reversible_version_too(_home):
    first = save(doc=page("a", "b"))
    rev = store.set_customization("knowledge", GOOD_LOOK, base_rev=first["rev"], by="pc")["rev"]
    store.undo("knowledge", base_rev=rev, by="pc")
    assert store.load("knowledge")["customization"] == {}


# ── the file itself ───────────────────────────────────────────────────────────────────

def test_a_failed_write_leaves_the_old_file_whole_and_no_temp_file(_home, monkeypatch):
    first = save(doc=page("one"))
    # scoped: monkeypatch.undo() would also undo _home's VIEWS_DIR patch and read the real ~/.friday
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            save(doc=page("two"), base=first["rev"])
    assert store.load("knowledge")["doc"] == page("one") and files(_home) == ["knowledge.json"]


def test_a_damaged_file_is_an_error_not_an_empty_view(_home):
    save()
    (_home / "liquid" / "views" / "knowledge.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(store.ViewCorrupt):
        store.load("knowledge")


def test_a_file_for_a_different_view_is_refused(_home):
    save("knowledge")
    (_home / "liquid" / "views" / "people.json").write_text(
        (_home / "liquid" / "views" / "knowledge.json").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(store.ViewCorrupt):
        store.load("people")


@pytest.mark.parametrize("view_id", ["../x", "a/b", "A", "", "x" * 49, "a.b", "-a", "con%00"])
def test_a_view_id_is_one_plain_name(_home, view_id):
    with pytest.raises(ValueError):
        save(view_id)
    assert files(_home) == []


def test_a_view_that_is_not_there_loads_as_none(_home):
    assert store.load("nothing") is None


def test_the_views_are_listed_with_their_labels_and_revisions(_home):
    save("knowledge", label="Knowledge")
    save("people", label="People")
    rows = store.list_views()
    assert [r["id"] for r in rows] == ["knowledge", "people"] and all(r["rev"] and r["label"] for r in rows)


def test_a_privacy_context_that_changed_refuses_the_write(_home, monkeypatch):
    def refuse():
        raise PermissionError("off the record")
    monkeypatch.setattr(store, "_admit_write", refuse)
    with pytest.raises(PermissionError):
        save()
    assert files(_home) == []


def test_a_save_announces_the_view_to_the_desktop(_home, monkeypatch):
    seen = []
    monkeypatch.setattr(store, "_announce", lambda view_id: seen.append(view_id))
    save()
    assert seen == ["knowledge"]
