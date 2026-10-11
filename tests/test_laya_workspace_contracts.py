"""Laya in every workspace: the guard that holds each workspace's manifest to the code.

Standing rule: when a workspace, a workspace object or a UI action is added or changed, its Laya
manifest (src/agent_friday/resources/laya_manifests/<id>.json) changes in the same commit. This file
fails when the two drift apart. It reads fixtures and JSON, the pure tables of the modules it checks and
the source text of the tree; it starts no server, loads no model and makes no network call.

  * the UI action list is a fixed fixture (tests/fixtures/laya_ws_ui_actions.json), never a scan at
    test time: a new UI action is added there and to its manifest;
  * the gate class in a manifest is descriptive; governance/action_gate decides, and a manifest may
    never be looser than it;
  * coverage is printed per workspace and may only go up (tests/fixtures/laya_coverage_floor.json);
  * a verb at a Laya tier must name an eval case that exists. A model-only verb has no such requirement until
    it is promoted to a Laya tier, and gains it then;
  * the `jobs` a manifest declares are held to the visible_job calls in the source
    (services/visible_jobs.visible_job; scripts/check_visible_jobs.py holds the rest): every declared job
    is a real call with that kind and label, and every kind in the source belongs to a workspace.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import time
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import laya_manifests as lm
from agent_friday.services import laya_resolver as lres
from agent_friday.services import laya_router as lr
from agent_friday.services import screen_stage as ss
from agent_friday.services import workspace_registry as wr

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIX = ROOT / "tests" / "fixtures"
MANIFESTS = lm.all_manifests()
REGISTRY = {w["id"]: w for w in wr.all_workspaces()}
UI = json.loads((FIX / "laya_ws_ui_actions.json").read_text(encoding="utf-8"))["workspaces"]
FLOOR = json.loads((FIX / "laya_coverage_floor.json").read_text(encoding="utf-8"))
VERBS = [(ws, v) for ws, m in sorted(MANIFESTS.items()) for v in m.get("verbs") or []]
OBJECTS = lm.all_objects()
JOBS = [(ws, j) for ws, m in sorted(MANIFESTS.items()) for j in m.get("jobs") or []]
NOW = time.time()


# ── the manifests exist and say who they are ───────────────────────────────────

def test_every_registry_workspace_has_a_manifest_and_nothing_else_does():
    assert set(MANIFESTS) == set(REGISTRY), set(MANIFESTS) ^ set(REGISTRY)
    assert {p.stem for p in lm.MANIFEST_DIR.glob("*.json")} == set(REGISTRY)


@pytest.mark.parametrize("ws", sorted(REGISTRY))
def test_a_manifest_takes_its_identity_from_the_registry(ws):
    m, w = MANIFESTS[ws], REGISTRY[ws]
    assert (m["id"], m["label"], m["group"], m.get("held")) == (w["id"], w["label"], w["group"], w.get("held"))


# ── every UI action is a verb or a justified not-for-Laya entry ────────────────

def test_the_ui_action_fixture_covers_every_registry_workspace():
    assert set(UI) == set(REGISTRY)
    # A new UI action is added with audit_status "new" (and its verb or not-for-Laya entry). The total is whatever
    # the fixture holds: it is derived, never a number to edit.
    assert sum(len(v) for v in UI.values()) > 0
    assert all(a["audit_status"] in ("L", "LD", "M", "N", "new") for v in UI.values() for a in v)
    ids = [a["id"] for v in UI.values() for a in v]
    assert len(ids) == len(set(ids))
    for ws, acts in UI.items():
        assert all(a["id"].startswith(ws + ".") and a["group"] in lm.GROUPS and a["label"] for a in acts), ws


@pytest.mark.parametrize("ws", sorted(REGISTRY))
def test_every_ui_action_is_a_manifest_verb_or_a_justified_not_for_laya_entry(ws):
    acts = {a["id"] for a in UI[ws]}
    verbs = [v["action"] for v in lm.verbs(ws)]
    nfl = [n["action"] for n in lm.not_for_laya(ws)]
    assert len(verbs) == len(set(verbs)) and len(nfl) == len(set(nfl))
    assert not set(verbs) & set(nfl), "an action is a verb or not-for-Laya, not both"
    assert set(verbs) | set(nfl) == acts, (sorted(acts - set(verbs) - set(nfl)), sorted((set(verbs) | set(nfl)) - acts))


def test_a_not_for_laya_entry_has_a_one_line_reason_and_says_whether_it_is_by_design_or_a_gap():
    for ws in MANIFESTS:
        for n in lm.not_for_laya(ws):
            assert n["kind"] in ("by_design", "gap"), n
            assert n["reason"].strip() and "\n" not in n["reason"] and len(n["reason"]) <= 160, n
            if n["kind"] == "gap":
                ids = lm.wave_ids(n.get("wave"))
                assert ids and set(ids) <= lm.PACKAGE_IDS, n   # any declared package id, or several joined by "/"


# ── verbs: the tool exists, the gate class is the gate's, the evidence exists ───

@pytest.fixture(scope="module")
def agent_mod():
    from agent_friday.services import agent
    return agent


@pytest.fixture(scope="module")
def known_tools(agent_mod):
    names = set(agent_mod.TOOL_RINGS) | set(agent_mod.CLAUDE_TOOL_HANDLERS) | set(lr.TOOLS)
    for tools in agent_mod.WORKSPACE_TOOLS.values():
        names |= {t["name"] for t in tools}
    return names


@pytest.mark.parametrize("ws,verb", VERBS, ids=[v["id"] for _, v in VERBS])
def test_a_verbs_tools_exist_and_its_shape_is_valid(ws, verb, known_tools):
    assert verb["id"] == verb["action"] and verb["id"].startswith(ws + ".")
    assert verb["group"] in lm.GROUPS and verb["laya_tier"] in lm.LAYA_TIERS + lm.OTHER_TIERS
    assert verb["tool"] in known_tools, verb["tool"]
    for t in verb["also_tools"]:
        assert t in known_tools, t
    assert verb["ui"]["control"]


@pytest.mark.parametrize("ws,verb", VERBS, ids=[v["id"] for _, v in VERBS])
def test_a_verbs_gate_class_is_the_one_action_gate_gives_and_never_looser(ws, verb, agent_mod):
    tool, g = verb["tool"], verb["gate"]
    assert g["class"] in lm.GATE_CLASSES
    assert g["class"] == lm.gate_class(tool), "the manifest describes the gate; action_gate decides"
    if g["class"] == "internal":
        assert ag.classify(tool, {})[0] == ag.INTERNAL, "a manifest never calls internal what the gate would hold"
    assert g["ring"] == agent_mod.TOOL_RINGS.get(tool)


@pytest.mark.parametrize("ws,verb", VERBS, ids=[v["id"] for _, v in VERBS])
def test_a_verbs_voice_exposure_is_the_voice_engines(ws, verb):
    from agent_friday.services import voice_engine as ve
    live = verb["tool"] in {t[0] for t in ve._VOICE_LIVE_TOOLS}
    shared = verb["tool"] in set(ve._VOICE_SHARED_TOOLS)
    assert verb["voice"] == {"live": live, "shared": shared}
    if live or shared:
        assert verb["phrasings"], "a verb the owner can reach by voice needs at least one phrasing"


def _eval_ref_exists(ref: str) -> bool:
    if "::" in ref:
        path, _, name = ref.partition("::")
        f = ROOT / path
        return f.is_file() and re.search(r"^\s*(?:async )?def %s\(" % re.escape(name), f.read_text(encoding="utf-8"), re.M) is not None
    name, _, case = ref.partition("#")
    f = FIX / name
    if not f.is_file():
        return False
    data = json.loads(f.read_text(encoding="utf-8"))
    return any(c.get("text") == case or c.get("id") == case for c in data["cases"])


@pytest.mark.parametrize("ws,verb", VERBS, ids=[v["id"] for _, v in VERBS])
def test_a_verb_Laya_runs_has_an_eval_case_that_exists(ws, verb):
    for ref in verb["eval"]:
        assert _eval_ref_exists(ref), ref
    if verb["laya_tier"] in lm.LAYA_TIERS:
        assert verb["eval"], "a verb at a Laya tier names at least one eval case"


@pytest.mark.parametrize("ws,verb", VERBS, ids=[v["id"] for _, v in VERBS])
def test_a_verbs_tier_names_a_path_that_exists(ws, verb):
    tool, tier = verb["tool"], verb["laya_tier"]
    rules = {t for t, _ in lr._RULES} | {t for t, _ in lr._ACTION_RULES}
    if tier == "router_rule":
        assert tool in lr.TOOLS and tool in rules or tool == "answer_card"
    elif tier == "router_seed":
        assert tool in lr.TOOLS and tool in lr.SEEDS
    else:
        assert tier == "model_only"


@pytest.mark.parametrize("ws,verb", [(w, v) for w, v in VERBS if v.get("undo")], ids=[v["id"] for _, v in VERBS if v.get("undo")])
def test_a_verbs_undo_names_a_function_or_a_tool_that_exists(ws, verb, known_tools):
    fn = verb["undo"]["fn"]
    if "." in fn:
        mod, _, name = fn.rpartition(".")
        module = __import__("agent_friday.services." + mod, fromlist=[name])
        assert callable(getattr(module, name, None)), fn
        assert verb["undo"]["via"] == "undo_action"
    else:
        assert fn in known_tools, fn


def test_router_phrasings_route_to_their_tool():
    checked = 0
    for ws, v in VERBS:
        if v["laya_tier"] not in ("router_rule", "router_seed"):
            continue
        for p in v["phrasings"]:
            if v["tool"] == "answer_card":
                assert lr._YES.match(p) or lr._NO.match(p), (v["id"], p)
            else:
                r = lr._tier0(" ".join(p.split()))
                assert r is not None and r.tool == v["tool"], (v["id"], p, r and (r.decision, r.tool))
            checked += 1
    assert checked >= 20


# ── rows: what each stage publishes and how one opens ──────────────────────────

def test_every_row_kind_a_stage_may_publish_belongs_to_one_manifest_object():
    kinds = [o["kind"] for _, o in OBJECTS]
    assert len(kinds) == len(set(kinds))
    assert set(kinds) == set(ss.ROW_KINDS), set(kinds) ^ set(ss.ROW_KINDS)


@pytest.mark.parametrize("ws,o", OBJECTS, ids=[o["kind"] for _, o in OBJECTS])
def test_an_objects_stage_facts_are_screen_stages(ws, o):
    sw, st = o["stage_workspace"], o["see_touch"]
    assert o["ref_template"].startswith(o["kind"] + ":")
    assert st["facets"] == sorted(ss.STAGE_FACETS.get(sw, ())) and sw in ss.STAGE_FACETS
    assert o["noun"] == ss.NOUNS.get(sw)
    assert st["privacy"] == ("counts_only" if sw in ss.PRIVATE_WORKSPACES else "titles")
    want = {k: (sorted(v) if v else None) for k, v in (ss.FILTER_KEYS.get(sw) or {}).items()}
    assert st["filters"] == want


def test_every_stage_workspace_screen_stage_knows_has_an_object_and_the_other_way_round():
    assert {o["stage_workspace"] for _, o in OBJECTS} == set(ss.STAGE_FACETS)


def test_the_workspaces_with_no_stage_of_their_own():
    """Code carries only the chat list (convo:); it has no stage of its own, which the audit counts."""
    staged = {w for w, _ in OBJECTS}
    assert set(REGISTRY) - staged == {"crew", "knowledge", "marketplace", "settings"}
    assert [o["kind"] for o in lm.objects("code")] == ["convo"]


@pytest.mark.parametrize("ws,o", OBJECTS, ids=[o["kind"] for _, o in OBJECTS])
def test_every_row_kind_opens_where_its_manifest_says(ws, o):
    """A row opens with navigate_to at a kind the desktop has, or the manifest says why it does not and names
    the package that closes the gap. A row with no row-level target is never recorded as opening its workspace
    (it looks like success and does nothing)."""
    from agent_friday.services import desktop_targets as dt
    op = o["open"]
    if op["tool"] is None:
        assert op["none_because"].strip(), o["kind"]
        if op.get("gap"):
            assert set(lm.wave_ids(op.get("wave"))) <= lm.PACKAGE_IDS and lm.wave_ids(op.get("wave")), o["kind"]
    else:
        assert op["tool"] == "navigate_to" and op["kind"] in dt.KINDS and op["row_level"] is True, o["kind"]
        assert op["kind"] != "workspace", "opening the workspace is not opening the row"


def test_the_rows_with_no_row_level_target_are_the_manifests_gaps():
    gaps = {o["kind"] for _, o in OBJECTS if o["open"]["tool"] is None and o["open"].get("gap")}
    assert gaps == {"lib", "wf", "job", "trust", "site", "health", "fin", "fam"}
    assert next(o for _, o in OBJECTS if o["kind"] == "card")["open"]["tool"] is None   # an approval card is answered
    assert next(o for _, o in OBJECTS if o["kind"] == "media")["open"]["kind"] == "card"   # a Media card opens


# ── what can be ticked ──────────────────────────────────────────────────────────

def test_the_lists_that_can_be_ticked_are_the_manifests_selectable_objects(agent_mod):
    selectable = {o["stage_workspace"] for _, o in OBJECTS if o["see_touch"]["selectable"]}
    assert set(agent_mod._TICKABLE) == selectable, set(agent_mod._TICKABLE) ^ selectable


def test_news_stories_can_be_ticked(agent_mod):
    """News published a stage but was not in _TICKABLE, so 'select these stories' failed."""
    assert "news" in agent_mod._TICKABLE
    assert not agent_mod._tool_screen_select({"op": "select", "workspace": "news", "scope": "screen"}).startswith(
        "SELECT_FAIL: I can show ticks")


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_news_adapter_reads_and_writes_the_ticks_and_draws_them(page):
    """Both copies of the page: the adapter needs getSel/setSel or the page cannot apply a tick."""
    text = (ROOT / page).read_text(encoding="utf-8")
    a = text.index("newsStageCfg.current = {")
    b = text.index("stageFS.makeAdapter('news'", a)
    cfg = text[a:b]
    assert "getSel: () => newsSelRef.current" in cfg and "setSel: next =>" in cfg
    assert "stageFS.emptySelection()" in text[text.index("const newsStageCfg = useRef(null);"):a]
    assert re.search(r"data-fr-sel['\"]?[:=]\s*\{?\s*newsSel\.refs\.indexOf\('news:' ?\+ ?\(it\.id ?\|\| ?i\)\) ?>= ?0", text), page


def test_the_page_scripts_that_mark_rows_agree_with_the_manifests():
    js = (ROOT / "static" / "workspace_stages.js").read_text(encoding="utf-8")
    found = re.findall(r"(\w+): \{ prefix: '(\w+):'((?:[^}]*))\}", js)
    assert len(found) == 11
    by_kind = {o["kind"]: o for _, o in OBJECTS}
    for sw, prefix, rest in found:
        o = by_kind[prefix]
        assert o["stage_workspace"] == sw, (prefix, sw)
        assert o["see_touch"]["selectable"] == ("selectable: true" in rest), prefix
        assert (o["see_touch"]["privacy"] == "counts_only") == ("counts_only: true" in rest), prefix


# ── the resolver ─────────────────────────────────────────────────────────────────

#: The tools a resolver command may name. The resolver decides and the caller runs the command
#: through the governed tool path.
_RESOLVER_TOOLS = ("navigate_to", "library_show")


def test_the_resolvers_sources_are_the_manifests_and_each_command_is_a_tool_the_desktop_runs():
    claimed = {o["resolver"]["source"] for _, o in OBJECTS if o.get("resolver")}
    claimed |= {s for m in MANIFESTS.values() for s in m.get("resolver_sources") or []}
    assert claimed | {"workspace"} == set(lres.CANDIDATE_SOURCES), claimed ^ set(lres.CANDIDATE_SOURCES)
    from agent_friday.services import desktop_targets as dt
    for kind in ("email", "wiki_page", "file", "library", "news", "workspace", "calendar", "contact"):
        cmd = lres.command_for(lres.Candidate(kind, "id1", "A title", extra={"url": "https://x.example/a"}))
        assert cmd["tool"] in _RESOLVER_TOOLS, (kind, cmd)
        if cmd["tool"] == "navigate_to":
            assert cmd["input"]["kind"] in dt.KINDS, (kind, cmd)


def test_the_resolver_opens_a_news_story_with_navigate_to_news_article():
    """The resolver's open_url for a story was not a tool the desktop confirms, so it was thrown away."""
    cmd = lres.command_for(lres.Candidate("news", "ab12", "School board votes", extra={"url": "https://example.org/a"}))
    assert cmd == {"tool": "navigate_to", "input": {"kind": "news_article", "id": "ab12"}}


# ── the router's workspace words ────────────────────────────────────────────────

def _router_words():
    """Every word the navigate rule's alternation accepts, with the optional letters written out."""
    out = []
    for alt in lr._WORKSPACES.split("|"):
        alt = alt.replace("\\-", "-")
        m = re.fullmatch(r"(.*?)(.)\?(.*)", alt)
        if m:
            out += [m.group(1) + m.group(3), m.group(1) + m.group(2) + m.group(3)]
        else:
            out.append(alt)
    return out


def _word_id(word):
    q = lr._nav_args("open " + word, re.search(r"(?P<q>.+)", word))["query"]
    return wr.aliases(include_held=True).get(q.lower()) or (q if q in REGISTRY else None)


def test_every_word_the_router_opens_a_workspace_by_names_one():
    words = _router_words()
    assert len(words) >= 19
    assert [w for w in words if _word_id(w) is None] == [], "a spoken workspace word that resolves to nothing"


def test_every_registry_workspace_can_be_opened_by_voice_through_the_router():
    """Family, Sites and Marketplace had no word in the router's list."""
    reachable = {_word_id(w) for w in _router_words()}
    assert set(REGISTRY) - reachable == set()


def test_the_router_asks_navigate_to_for_a_workspace_id_not_an_alias():
    """'inbox' went out as 'email' and 'notes' as 'wiki', aliases the desktop resolved only by luck."""
    for text, ws in [("open my inbox", "messages"), ("open e-mail", "messages"), ("go to the wiki", "knowledge"),
                     ("take me to people", "contacts"), ("show me the family", "family"), ("open sites", "futurespeak"),
                     ("open podcasts", "media"), ("pull up marketplace", "marketplace")]:
        r = lr._tier0(text)
        assert r is not None and r.tool == "navigate_to", text
        assert r.args == {"kind": "workspace", "query": ws}, (text, r.args)


def test_words_that_name_no_workspace_are_not_routed_as_one():
    for text in ("open files", "open home", "show me chat", "open projects", "go to activity"):
        r = lr._tier0(text)
        assert r is None or r.tool != "navigate_to", text


@pytest.mark.parametrize("ws", sorted(REGISTRY))
def test_a_manifests_spoken_words_are_the_words_the_router_takes_for_it(ws):
    mine = {w for w in _router_words() if _word_id(w) == ws}
    base = {ws, REGISTRY[ws]["label"].lower()}
    assert mine == base | set(MANIFESTS[ws]["spoken_words"]["router_extra"])


# ── the tools a workspace brings ─────────────────────────────────────────────────

def test_every_workspace_tools_key_is_a_workspace_a_manifest_names_or_a_pool_one_draws_on(agent_mod):
    assert lm.check_workspace_tools(agent_mod.WORKSPACE_TOOLS.keys(), set(REGISTRY)) == []


def test_the_workspace_tools_check_can_fail():
    keys = {"news", "media", "settings", "hub", "on_demand"}
    assert lm.check_workspace_tools(keys, set(REGISTRY)) == []
    assert any("'bogus'" in p for p in lm.check_workspace_tools(keys | {"bogus"}, set(REGISTRY)))
    assert any("'media'" in p for p in lm.check_workspace_tools(keys - {"media"}, set(REGISTRY)))
    assert any("'hub'" in p for p in lm.check_workspace_tools(keys - {"hub"}, set(REGISTRY)))


# ── the jobs a workspace launches ────────────────────────────────────────────────

def _site_value(node):
    return node.value if isinstance(node, ast.Constant) else None


def _is_visible_job(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "visible_job") or (isinstance(f, ast.Attribute) and f.attr == "visible_job")


def visible_job_sites(source: str, module: str) -> list:
    """[(module, qualified function, kind, name)] for every visible_job in the source, decorator or `with`."""
    out = []

    def visit(node, stack):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not isinstance(ch, ast.ClassDef):
                    for d in ch.decorator_list:
                        if _is_visible_job(d):
                            kw = {k.arg: _site_value(k.value) for k in d.keywords}
                            out.append((module, ".".join(stack + [ch.name]), kw.get("kind"), _site_value(d.args[0]) if d.args else None))
                visit(ch, stack + [ch.name])
            else:
                if isinstance(ch, (ast.With, ast.AsyncWith)):
                    for it in ch.items:
                        if _is_visible_job(it.context_expr):
                            d = it.context_expr
                            kw = {k.arg: _site_value(k.value) for k in d.keywords}
                            out.append((module, ".".join(stack), kw.get("kind"), _site_value(d.args[0]) if d.args else None))
                visit(ch, stack)

    visit(ast.parse(source), [])
    return out


def builtin_labels(source: str) -> dict:
    """{ref: label} for every register_builtin_task("ref", fn, label="...") in the scheduler's source."""
    out = {}
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "register_builtin_task" and n.args and _site_value(n.args[0]):
            out[n.args[0].value] = next((_site_value(k.value) for k in n.keywords if k.arg == "label"), None)
    return out


@pytest.fixture(scope="module")
def tree_sites():
    sites = []
    for p in sorted((SRC / "agent_friday").rglob("*.py")):
        text = p.read_text(encoding="utf-8-sig")
        if "visible_job" not in text:
            continue
        mod = ".".join(p.relative_to(SRC).with_suffix("").parts)
        sites += visible_job_sites(text, mod[:-9] if mod.endswith(".__init__") else mod)
    return sites


@pytest.fixture(scope="module")
def schedule_labels():
    return builtin_labels((SRC / "agent_friday" / "services" / "scheduler.py").read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def task_launchers():
    """check_visible_jobs.ALLOW: the task machinery whose TASKS record IS the visible task."""
    spec = importlib.util.spec_from_file_location("check_visible_jobs", ROOT / "scripts" / "check_visible_jobs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.ALLOW)


#: Kinds in the source that are not a registry workspace's job, and why. Everything else is a manifest's.
UNOWNED_KINDS = {
    "analyze_file": "A file attached to a chat (/api/analyze); the chat is its surface, not a workspace.",
    "cloud_once": "The one cloud turn the owner asked for in a chat (local_offer); the chat is its surface, not a workspace.",
    "voice_live_reduce": "A tool result trimmed inside a Gemini Live call; the call is its surface, not a workspace.",
}


def _undeclared_kinds(sites, jobs, unowned) -> set:
    declared = {j["task_kind"] for j in jobs if j.get("task_kind")}
    return {s[2] for s in sites if s[2]} - declared - set(unowned)


def test_a_job_has_a_shape_the_activity_lane_can_show():
    assert JOBS, "no workspace declares a job"
    ids = [j["id"] for _, j in JOBS]
    assert len(ids) == len(set(ids))
    for ws, j in JOBS:
        assert j["id"].startswith(ws + "."), j
        assert j["trigger"] in lm.JOB_TRIGGERS and set(j.get("also") or []) <= set(lm.JOB_TRIGGERS), j
        assert j["model_class"] in lm.JOB_MODEL_CLASSES and j["helper"] in lm.JOB_HELPERS, j
        assert j["activity_label"].strip() and len(j["activity_label"]) <= 60, j
        assert re.fullmatch(r"agent_friday\.[a-z_.]+:[A-Za-z_][\w.]*", j["entry"]), j
        if j.get("schedule_ref"):
            assert "schedule" in [j["trigger"]] + list(j.get("also") or []), j


@pytest.mark.parametrize("ws,job", [(w, j) for w, j in JOBS if j["helper"] == "visible_job"], ids=[j["id"] for w, j in JOBS if j["helper"] == "visible_job"])
def test_a_declared_job_is_a_visible_job_call_with_that_kind_and_label(ws, job, tree_sites, schedule_labels):
    mod, _, qual = job["entry"].partition(":")
    hits = [s for s in tree_sites if s[0] == mod and s[1] == qual and s[2] == job["task_kind"]]
    assert hits, "no visible_job(kind=%r) in %s" % (job["task_kind"], job["entry"])
    if job.get("schedule_ref"):
        assert job["task_kind"] == "scheduled" or job.get("schedule_ref") in schedule_labels
        assert job["schedule_ref"] in schedule_labels, "no register_builtin_task(%r) in the scheduler" % job["schedule_ref"]
        if job["task_kind"] == "scheduled":
            assert job["activity_label"] == schedule_labels[job["schedule_ref"]]
            return
    # a name taken from a variable (the queued item's title) is declared with the label the code falls back to
    assert any(s[3] == job["activity_label"] for s in hits) or any(s[3] is None for s in hits), (job["activity_label"], hits)


@pytest.mark.parametrize("ws,job", [(w, j) for w, j in JOBS if j["helper"] == "task"], ids=[j["id"] for w, j in JOBS if j["helper"] == "task"])
def test_a_task_record_job_is_the_task_machinery_check_visible_jobs_allows(ws, job, task_launchers):
    mod, _, qual = job["entry"].partition(":")
    assert "%s:%s" % (mod, qual) in task_launchers, "not allow-listed as a TASKS record's launcher: " + job["entry"]
    assert not job.get("task_kind"), "a TASKS record's kind is the record's own"


def test_every_job_kind_in_the_source_belongs_to_a_workspace(tree_sites):
    """A new visible_job kind is a job some workspace can launch: it is declared in that workspace's manifest
    (or listed here as not a workspace's job, with the reason) in the same change."""
    kinds = {s[2] for s in tree_sites if s[2]}
    assert len(kinds) >= 40, "the scan found %d kinds; it should find the tree's visible_job calls" % len(kinds)
    assert _undeclared_kinds(tree_sites, [j for _, j in JOBS], UNOWNED_KINDS) == set()
    for kind, why in UNOWNED_KINDS.items():
        assert kind in kinds and why.strip(), kind


def test_a_workspace_with_a_button_that_starts_model_work_declares_it():
    """The workspaces the source ties a visible_job to by name must declare one."""
    declared = {ws for ws, _ in JOBS}
    assert {"news", "calendar", "messages", "career", "code", "media", "library", "knowledge", "workflows", "system",
            "settings"} <= declared


def test_the_job_checks_can_fail(tree_sites):
    # a kind in the source that no manifest declares is reported
    extra = tree_sites + [("agent_friday.services.new_thing", "run", "brand_new_kind", "Something new")]
    assert _undeclared_kinds(extra, [j for _, j in JOBS], UNOWNED_KINDS) == {"brand_new_kind"}
    # taking a kind out of a manifest's jobs is reported
    pruned = [j for _, j in JOBS if j.get("task_kind") != "front_page"]
    assert "front_page" in _undeclared_kinds(tree_sites, pruned, UNOWNED_KINDS)
    # the scan finds a decorator and a with block, and ignores a call that is not the door
    src = ("from x import visible_job\n"
           "@visible_job('One', kind='k1', workspace='w')\ndef a():\n    pass\n"
           "def b():\n    with visible_job('Two', kind='k2'):\n        pass\n"
           "def c():\n    other('Three', kind='k3')\n")
    assert visible_job_sites(src, "m") == [("m", "a", "k1", "One"), ("m", "b", "k2", "Two")]
    assert builtin_labels("register_builtin_task('r1', f, label='Lab', default_trigger='daily')\n") == {"r1": "Lab"}


# ── the drift checks can fail ────────────────────────────────────────────────────

def test_the_tickable_check_can_fail(agent_mod):
    selectable = {o["stage_workspace"] for _, o in OBJECTS if o["see_touch"]["selectable"]}
    assert set(agent_mod._TICKABLE) == selectable
    assert set(agent_mod._TICKABLE[:-1]) != selectable


def test_the_router_word_check_can_fail(monkeypatch):
    monkeypatch.setattr(lr, "_WORKSPACES", lr._WORKSPACES + "|files|home")
    assert any(_word_id(w) is None for w in _router_words())
    # A workspace is reached by its id AND its label ("futurespeak" and "sites"); take away every word for these two.
    monkeypatch.setattr(lr, "_WORKSPACES", "|".join(
        w for w in lr._WORKSPACES.split("|") if w not in ("family", "sites", "futurespeak")))
    assert set(REGISTRY) - {_word_id(w) for w in _router_words()} == {"family", "futurespeak"}


def test_the_open_check_can_fail():
    """A manifest row that claims to open its workspace as if that opened the row is not accepted."""
    o = next(o for _, o in OBJECTS if o["kind"] == "lib")
    assert o["open"]["tool"] is None, "the Library row has no row-level target"
    bad = {"tool": "navigate_to", "kind": "workspace", "args": {"workspace": "library"}, "row_level": False}
    assert not (bad["row_level"] is True and bad["kind"] != "workspace")


# ── coverage: reported, and it may only go up ───────────────────────────────────

def _coverage():
    return {ws: lm.coverage(ws, [a["id"] for a in UI[ws]]) for ws in sorted(REGISTRY)}


def test_laya_coverage_per_workspace_never_drops_below_its_floor(capsys):
    """The ratchet is on the covered COUNT and on the % over the Laya-eligible actions (every action except
    the by_design not-for-Laya ones). Adding an owner-only control, listed by_design with its reason, moves
    neither; a new action that is a gap lowers the % and fails, so it is a verb, a by_design entry or a
    declared gap in the same commit as the control. The fixture's total is derived."""
    cur = _coverage()
    total_a = sum(c["actions"] for c in cur.values())
    total_e = sum(c["eligible"] for c in cur.values())
    total_l = sum(c["laya"] for c in cur.values())
    total_pct = round(100.0 * total_l / total_e, 1)
    with capsys.disabled():
        print("\nLaya coverage (verbs at a Laya tier / Laya-eligible UI actions):")
        for ws, c in cur.items():
            f = FLOOR["workspaces"][ws]
            print("  %-12s %2d/%-2d  %5.1f%%  (floor %2d, %5.1f%%)" % (ws, c["laya"], c["eligible"], c["pct"], f["laya"], f["pct"]))
        print("  %-12s %2d/%-3d %5.1f%%  (floor %2d, %5.1f%%; %d actions in all)" % (
            "overall", total_l, total_e, total_pct, FLOOR["overall"]["laya"], FLOOR["overall"]["pct"], total_a))
    assert set(FLOOR["workspaces"]) == set(REGISTRY)
    for ws, c in cur.items():
        f = FLOOR["workspaces"][ws]
        assert c["laya"] >= f["laya"], "Laya coverage fell in %s (%d verbs, floor %d)" % (ws, c["laya"], f["laya"])
        assert c["pct"] >= f["pct"], "Laya coverage fell in %s (%.1f%% of eligible, floor %.1f%%)" % (ws, c["pct"], f["pct"])
    assert total_l >= FLOOR["overall"]["laya"]
    assert total_pct >= FLOOR["overall"]["pct"], (total_pct, FLOOR["overall"]["pct"])


def test_the_coverage_ratchet_can_fail():
    cur = _coverage()
    assert cur["messages"]["laya"] == FLOOR["workspaces"]["messages"]["laya"] > 0
    assert lm.coverage("messages", [a["id"] for a in UI["messages"]] + ["messages.not_a_verb"])["pct"] < cur["messages"]["pct"]


def test_a_by_design_entry_takes_an_action_out_of_the_eligible_count_and_a_gap_does_not():
    ids = [a["id"] for a in UI["marketplace"]]
    base = lm.coverage("marketplace", ids)
    assert base["eligible"] < base["actions"], "marketplace holds by_design owner-only controls"
    by_design = {n["action"] for n in lm.not_for_laya("marketplace") if n["kind"] == "by_design"}
    assert base["eligible"] == base["actions"] - len(by_design & set(ids))
    more = lm.coverage("marketplace", ids + ["marketplace.not_listed"])       # an unlisted action is eligible
    assert more["eligible"] == base["eligible"] + 1 and more["pct"] <= base["pct"]
