"""The A2UI data broker (D2): the only code that turns a document's declared bindings into data.

Contract draft 3, section 3. These tests hold:
* a query or a parameter outside the closed vocabulary refuses the WHOLE document (rule V7), before anything is read;
* the two Knowledge queries return what the contract says, from the same adapter the content routes use;
* a row holding a URL is dropped (and counted), so one odd page title can't blank the view, and nothing else leaks;
* the answer carries as_of and ttl_s and is keyed by the binding path, exactly what render(doc, data) takes.
Synthetic data only.
"""
import copy

import pytest

from agent_friday.services import a2ui_broker as broker
from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.tv_gateway import ws_knowledge

ROWS = [
    {"ref": "page:entities/sample-one.md", "title": "sample one", "line": "entities", "when": "2026-11-21T10:00:00Z"},
    {"ref": "page:projects/garden.md", "title": "garden", "line": "projects", "when": "2026-11-20T10:00:00Z"},
    {"ref": "page:entities/sample-two.md", "title": "sample two", "line": "entities", "when": "2026-11-19T10:00:00Z"},
    {"ref": "page:notes/odd.md", "title": "see www.example.test/x", "line": "notes", "when": "2026-11-18T10:00:00Z"},
]


@pytest.fixture
def pages(monkeypatch):
    seen = []

    def fake(sensitivity):
        seen.append(sensitivity)
        return copy.deepcopy(ROWS)
    monkeypatch.setattr(ws_knowledge, "pages", fake)
    return seen


def with_binding(query, params, path="/knowledge/sections"):
    doc = copy.deepcopy(aws.builtin("knowledge"))
    doc[0]["createSurface"]["metadata"]["friday"]["bindings"] = {"b": {"path": path, "query": query, "params": params}}
    doc[0]["createSurface"]["components"] = [{"id": "root", "component": "record_table", "rows": {"@path": path},
                                             "columns": ["name"]}]
    return doc


@pytest.mark.parametrize("query,params", [
    ("files.read", {}),                         # not in the vocabulary
    ("knowledge.recent", {"limit": 500}),       # over the cap
    ("knowledge.recent", {"limit": "12"}),      # not an int
    ("knowledge.recent", {"limit": True}),      # a bool is not an int here
    ("knowledge.sections", {"path": "C:/"}),    # a parameter the query does not take
])
def test_a_query_or_parameter_outside_the_vocabulary_refuses_the_whole_document(pages, query, params):
    with pytest.raises(broker.BrokerRefused) as e:
        broker.resolve(with_binding(query, params), sensitivity="personal")
    assert e.value.rule == "V7"
    assert pages == [], "nothing is read for a refused document"


def test_a_binding_without_a_query_is_refused(pages):
    doc = with_binding("knowledge.sections", {})
    del doc[0]["createSurface"]["metadata"]["friday"]["bindings"]["b"]["query"]
    with pytest.raises(broker.BrokerRefused):
        broker.check_bindings(doc)


def test_sections_and_recent_come_from_the_content_adapter_keyed_by_path(pages):
    got = broker.resolve(aws.builtin("knowledge"), sensitivity="personal")
    assert pages == ["personal"]
    kn = got["data"]["knowledge"]
    assert kn["sections"] == [{"name": "entities", "count": 2}, {"name": "projects", "count": 1}]
    assert [r["title"] for r in kn["recent"]] == ["sample one", "garden", "sample two"]
    assert kn["recent"][0] == {"title": "sample one", "section": "entities", "when": "2026-11-21T10:00:00Z"}
    assert got["dropped"] == 2, "the odd row is dropped from both queries, and the notes section with it"
    assert got["ttl_s"] == broker.TTL_S and got["as_of"].endswith("Z")


def test_recent_honours_its_limit(pages):
    doc = with_binding("knowledge.recent", {"limit": 1}, path="/knowledge/recent")
    got = broker.resolve(doc, sensitivity="personal")
    assert [r["title"] for r in got["data"]["knowledge"]["recent"]] == ["sample one"]


def test_the_resolved_data_renders_the_builtin_document(pages):
    got = broker.resolve(aws.builtin("knowledge"), sensitivity="personal")
    r = rend.render(aws.builtin("knowledge"), prof.Context(subject="workspace:knowledge", authorship="builtin"),
                    data=got["data"])
    assert r.ok, r.receipt
    assert "example" not in r.html


def test_a_document_with_no_bindings_resolves_to_no_data(pages):
    doc = copy.deepcopy(aws.builtin("knowledge"))
    doc[0]["createSurface"]["metadata"] = {}
    doc[0]["createSurface"]["components"] = [{"id": "root", "component": "text_block", "text": "Hi"}]
    assert broker.resolve(doc, sensitivity="personal")["data"] == {}
    assert pages == []


def test_every_query_names_the_section_it_reads():
    assert set(broker.READS) == set(broker.VOCABULARY)
    assert broker.sections_read(aws.builtin("knowledge")) == {"pages"}
