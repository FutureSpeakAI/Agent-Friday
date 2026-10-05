"""One 3D file browser across Friday: every workspace that shows files in 3D
mounts FridayFiles3D on its own lens (Library, Media, Files), the file lenses
light a search's path with the Shelves' own pacer, and voice reaches it with
show_files_3d, which only moves the owner's screen."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel,needle", [
    ("static/media_ws.js", "h(window.FridayFiles3D, { lens: 'media'"),
    ("static/library_ws.js", "h(window.FridayFiles3D, { lens: 'files', root: 'documents'"),
    ("static/library_ws.js", "h(window.FridayFiles3D || window.LibraryShelves3D, { lens: 'library'"),
    ("static/friday3d_records.js", "h(window.FridayFiles3D || window.Files3DPanel, { lens: 'files', root: 'projects'"),
    ("index.html", "React.createElement(window.FridayFiles3D, { lens: 'files' })"),
    ("ui_parts/app.html", "<window.FridayFiles3D lens=\"files\"/>"),
])
def test_every_workspace_s_3d_view_is_the_one_browser(rel, needle):
    assert needle in src(rel)


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/styles_and_scene.html"))
def test_the_one_browser_loads_after_the_engine_and_the_shelves(rel):
    s = src(rel)
    eng, shelves, one = (s.index('/static/studio_files3d.js"'), s.index('/static/library_shelves.js"'),
                         s.index('/static/friday_files3d.js"'))
    assert eng < shelves < one


def test_the_lens_bar_offers_library_media_and_files_over_the_one_engine():
    s = src("static/friday_files3d.js")
    assert re.search(r"id: 'library'.*id: 'media'.*id: 'files'", s, re.S)
    assert "window.LibraryShelves3D" in s and "window.Files3DPanel" in s
    assert "createEngine" not in s, "no second engine: the lenses draw through the one engine"
    assert "friday-files3d" in s and "__fridayFiles3dPending" in s


def test_the_file_lenses_light_a_search_with_the_shelves_own_pacer():
    shelves, engine = src("static/library_shelves.js"), src("static/studio_files3d.js")
    assert "window.Friday3D.createPathLights = createPathLights" in shelves
    body = engine[engine.index("const lightPath = mask =>"):engine.index("// filter")]
    assert "F.createPathLights(" in body and "eng.setGlow(" in body
    assert "path.onDecision(" in body and "shown < 12" in body
    # Nothing is lit for an empty search.
    assert "lightPath(null)" in engine and "lightPath(q ? mask : null)" in engine


def test_show_files_3d_is_a_ring_0_screen_tool_everywhere_library_show_is():
    from agent_friday.governance import action_gate
    from agent_friday.services import agent, voice_engine
    from agent_friday.services.library import tools
    assert tools.RINGS["show_files_3d"] == 0 and "show_files_3d" in tools.HANDLERS
    decl = next(t for t in tools.TOOLS if t["name"] == "show_files_3d")
    assert decl["input_schema"]["properties"]["lens"]["enum"] == ["library", "media", "files"]
    assert "show_files_3d" in action_gate.INTERNAL_TOOLS
    assert "show_files_3d" in agent.ON_DEMAND_TOOLS
    assert "show_files_3d" in src("src/agent_friday/services/voice_engine.py")


def test_show_files_3d_opens_the_lens_s_workspace_and_lights_the_search(monkeypatch):
    from agent_friday.services import desktop_bus
    from agent_friday.services.library import ui
    sent = []
    monkeypatch.setattr(desktop_bus, "send", lambda acts: sent.append(acts) or {"delivered": True, "acked": True})
    out = ui.files3d_show({"lens": "media", "query": "poster"})
    assert out.startswith("FILES3D_OK")
    nav, act = sent[0]
    assert nav["type"] == "navigate" and nav["workspace"] == "media" and nav["view"] == "3d"
    assert act == {"type": "files3d", "lens": "media", "query": "poster"}
    assert ui.files3d_show({"lens": "everything"}).startswith("FILES3D_FAIL")
    assert len(sent) == 1, "an unknown lens moves nothing"


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/app.html"))
def test_the_bus_hands_a_files3d_action_to_the_open_browser(rel):
    s = src(rel)
    i = s.index("a.type === 'files3d'" if rel == "index.html" else "a.type==='files3d'")
    block = s[i:i + 500]
    assert "__fridayFiles3dPending" in block and "friday-files3d" in block
