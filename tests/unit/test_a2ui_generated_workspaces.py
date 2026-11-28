"""Every served workspace gets a view: one generic broker query and one generated A2UI document (MP-3a).

Spec: ftv/program/specs/MOBILE_FULL_PARITY_STORM_2026-11-28.md, section 3.4 (the generic document). These tests hold:
* a workspace the PC serves to a device, with no designed document, gets one generated from its device block, so it
  is never left as the verb list; a designed document (Knowledge, Sites) always wins;
* a workspace with no device block still gets no view;
* `ws.rows` hands a device only title, detail, when and state, as capped plain text, never more than its limit, and
  drops a row holding a URL;
* `ws.rows` reads only a section an adapter serves, and a view reads only its own workspace's sections;
* the generated view is served to an owner device with its data, and a reset brings it back.
Synthetic data only.
"""
import copy

import pytest

from agent_friday.services import a2ui_broker as broker
from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend
from agent_friday.services import a2ui_store as store
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.services import workspace_registry as reg
from agent_friday.tv_gateway import ws_code, ws_content, ws_knowledge
from tests.unit.test_tv_pc_approve_decide import Enrolled, _clean, client  # noqa: F401  (fixtures)

GENERATED = ("calendar", "code", "crew", "news")
NO_DEVICE_BLOCK = ("health", "finance", "messages", "family", "settings")


def repo(name, detail="main · clean", **extra):
    return {"ref": "repo:" + name, "kind": "repo", "title": name, "line": detail, "when": None, "state": "clean",
            "thumb": None, "lives_on": "pc", "sensitivity": "personal", "taint": None, **extra}


@pytest.fixture(autouse=True)
def _views(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIEWS_DIR", tmp_path / "liquid" / "views")
    monkeypatch.setattr(store, "_admit_write", lambda: 0)
    monkeypatch.setattr(store, "_check_write", lambda origin: None)
    monkeypatch.setattr(store, "_announce", lambda view_id: None)
    monkeypatch.setattr(ws_knowledge, "pages", lambda sensitivity: [])


@pytest.fixture
def repos(monkeypatch):
    """ws_code.pages answering `rows`, counting its reads."""
    state = {"rows": [repo("garden"), repo("kite")], "reads": 0}

    def fake(sensitivity):
        state["reads"] += 1
        return copy.deepcopy(state["rows"])
    monkeypatch.setattr(ws_code, "pages", fake)
    return state


def components(doc) -> list:
    return doc[0]["createSurface"]["components"]


def rows_binding(ws, section, limit=20, path=None):
    """A document reading one section through ws.rows."""
    path = path or "/%s/%s" % (ws, section)
    return aws._surface([{"id": "root", "component": "record_table", "rows": {"@path": path}, "columns": ["title"]}],
                        {"b": {"path": path, "query": "ws.rows", "params": {"ws": ws, "section": section,
                                                                            "limit": limit}}})


# ── which workspaces get a view ────────────────────────────────────────────

def test_calendar_code_crew_and_news_each_get_a_view_built_from_their_device_block():
    for ws in GENERATED:
        view = aws.current(ws)
        assert view is not None, ws
        assert not view["stored"] and view["rev"].startswith(aws.BUILTIN_PREFIX), ws
        assert view["label"] == reg.label(ws), ws
        served = set(ws_content.serving(ws)[0])
        assert broker.sections_read(view["doc"]) == served, "%s reads every section it serves, and only those" % ws
        assert ws_content.view_block(ws) is not None, "%s lists as an A2UI workspace" % ws


def test_a_generated_document_passes_the_profile_and_the_device_document_rules():
    for ws in GENERATED:
        doc = aws.current(ws)["doc"]
        store._validated(doc, aws.ctx(ws))
        aws.check_document(ws, doc)            # data only from bindings, no reserved words


def test_a_generated_view_is_the_same_every_time():
    for ws in GENERATED:
        assert aws.current(ws)["rev"] == aws.current(ws)["rev"] == aws.builtin_rev(ws), ws


def test_a_designed_document_wins_over_a_generated_one():
    for ws in ("knowledge", "futurespeak"):
        assert aws.current(ws)["doc"] == aws.BUILTINS[ws](), ws
    assert {q for _p, q, _params in broker.check_bindings(aws.current("knowledge")["doc"])} == {
        "knowledge.sections", "knowledge.recent"}


def test_a_workspace_with_no_device_block_still_has_no_view():
    for ws in NO_DEVICE_BLOCK + ("no-such", "..", "KNOWLEDGE"):
        assert aws.current(ws) is None, ws
        assert ws_content.view_block(ws) is None, ws


def test_the_calendar_reads_as_a_timeline_and_the_lists_as_tables():
    kinds = {ws: [c["component"] for c in components(aws.current(ws)["doc"])] for ws in GENERATED}
    assert "timeline" in kinds["calendar"] and "record_table" not in kinds["calendar"]
    assert kinds["code"].count("record_table") == 1
    assert kinds["news"].count("record_table") == 2, "Latest and Read later"


def test_each_section_opens_the_shells_own_browser_by_internal_id():
    links = [c for c in components(aws.current("news")["doc"]) if c["component"] == "nav_link"]
    assert sorted(c["destination"] for c in links) == ["news.later", "news.latest"]


def test_a_column_the_manifest_gives_no_role_is_not_drawn():
    # Code's repo kind has no "when" role, so its table has no empty WHEN column
    table = next(c for c in components(aws.current("code")["doc"]) if c["component"] == "record_table")
    assert table["columns"] == ["title", "detail", "state"]


# ── ws.rows: what a device gets ────────────────────────────────────────────

def test_ws_rows_hands_over_only_four_plain_capped_fields(repos):
    repos["rows"] = [repo("x" * 5000, detail="y" * 5000, path="/srv/sample/hidden-plan", token="opaque-sample",
                          state="s" * 500)]
    got = broker.resolve(rows_binding("code", "repos"), sensitivity="personal")
    (row,) = got["data"]["code"]["repos"]
    assert set(row) == {"title", "detail", "when", "state"}
    assert len(row["title"]) <= broker.TITLE_MAX and len(row["detail"]) <= broker.DETAIL_MAX
    assert len(row["state"]) <= broker.STATE_MAX
    assert "hidden-plan" not in repr(got) and "opaque-sample" not in repr(got)


def test_ws_rows_never_passes_a_non_text_value(repos):
    repos["rows"] = [repo("garden", state={"nested": "C:/x"}, when=12345)]
    (row,) = broker.resolve(rows_binding("code", "repos"), sensitivity="personal")["data"]["code"]["repos"]
    assert row["state"] == "" and row["when"] == ""


def test_ws_rows_drops_a_row_holding_a_url_and_counts_it(repos):
    repos["rows"] = [repo("garden"), repo("kite", detail="see https://example.test/x")]
    got = broker.resolve(rows_binding("code", "repos"), sensitivity="personal")
    assert [r["title"] for r in got["data"]["code"]["repos"]] == ["garden"]
    assert got["dropped"] == 1


def test_ws_rows_caps_ten_thousand_rows_at_its_limit(repos):
    repos["rows"] = [repo("r%05d" % i) for i in range(10_000)]
    got = broker.resolve(rows_binding("code", "repos", limit=broker.ROWS_MAX), sensitivity="personal")
    assert len(got["data"]["code"]["repos"]) == broker.ROWS_MAX


@pytest.mark.parametrize("params", [
    {"ws": "health", "section": "metrics", "limit": 5},       # no adapter serves it
    {"ws": "code", "section": "agenda", "limit": 5},          # a section of another workspace
    {"ws": "../code", "section": "repos", "limit": 5},
    {"ws": 5, "section": "repos", "limit": 5},
    {"ws": "code", "limit": 5},                               # no section
    {"ws": "code", "section": "repos"},                       # no limit
    {"ws": "code", "section": "repos", "limit": 41},          # over the cap
    {"ws": "code", "section": "repos", "limit": True},
    {"ws": "code", "section": "repos", "limit": 5, "route": "/api/repos/scan"},
])
def test_ws_rows_refuses_anything_but_a_served_section_before_reading(repos, params):
    doc = aws._surface([{"id": "root", "component": "record_table", "rows": {"@path": "/x/y"}, "columns": ["title"]}],
                       {"b": {"path": "/x/y", "query": "ws.rows", "params": params}})
    with pytest.raises(broker.BrokerRefused) as e:
        broker.resolve(doc, sensitivity="personal")
    assert e.value.rule == "V7"
    assert repos["reads"] == 0, "nothing is read for a refused document"


def test_a_view_reads_only_its_own_workspaces_sections():
    doc = rows_binding("code", "repos")
    assert broker.reads(doc) == {("code", "repos")}
    assert aws.serves("code", {"repos"}) is True
    # news declaring a section called "repos" still may not read Code's repositories
    assert aws._reads_only(doc, "news", {"latest", "later", "repos"}) is False
    assert aws._reads_only(doc, "code", {"repos"}) is True


def test_the_designed_queries_name_their_workspace():
    assert broker.reads(aws.builtin("knowledge")) == {("knowledge", "pages")}
    assert broker.reads(aws.builtin("futurespeak")) == {("futurespeak", "projects")}


def test_a_device_document_reading_another_workspace_is_refused():
    with pytest.raises(store.ViewRefused):
        aws._check_reads(rows_binding("code", "repos"), "news", {"latest", "later", "repos"})


# ── served to a device ─────────────────────────────────────────────────────

def test_an_owner_device_gets_the_generated_view_with_its_data(client, repos):
    r = Enrolled(client).get("/tv/v1/workspaces/code/view")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert [row["title"] for row in body["data"]["code"]["repos"]] == ["garden", "kite"]
    shown = rend.render(body["doc"], prof.Context(subject="workspace:code", authorship="builtin"), data=body["data"])
    assert shown.ok, shown.receipt


def test_reset_brings_a_generated_view_back_to_its_generated_document():
    first = aws.current("code")
    got = aws.apply_change("code", {"customization": {"note": "sample note"}}, base_rev=first["rev"], by="test")
    assert aws.current("code")["stored"]
    aws.apply_change("code", {"reset": True}, base_rev=got["rev"], by="test")
    assert aws.current("code")["doc"] == first["doc"]
