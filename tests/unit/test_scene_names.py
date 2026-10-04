"""The holographic scene's structure names agree everywhere they are kept, and
a structure answers to its name and to the names it used to have.

- The server's SCENE_NAMES (/api/evolution), the setup wizard's table and
  both scene files' EVOLUTION_PATH list the same names in the same order
  (the wizard's may carry accents).
- The Dyson Sphere (id ICOSAHEDRON, index 1) was the Sacred Sphere; its id
  and index are what is stored, so nobody's choice moves, and "sacred
  sphere" still finds it.
- fridayStructureIndex (the page's name resolver for voice and chat, in
  index.html and its mirror app.html) matches a name or an alias, with or
  without "the", and says -1 for anything else; the server's scene_index_for
  (voice's avatar_evolution show) answers exactly the same, from the same
  aliases.
"""
import json
import pathlib
import re
import shutil
import subprocess
import unicodedata

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]
node = shutil.which("node")
PATH = re.compile(r"const EVOLUTION_PATH = \[(.*?)\];", re.S)
ENTRY = re.compile(r"\{ id: '([A-Z]+)', name: '([^']+)'(?:, aliases: \[([^\]]*)\])? \}")
RESOLVER = re.compile(r"(function fridayStructureIndex\(text\) \{.*?\n\})", re.S)
# What someone might say, and the structure each means (-1: none).
SAID = ["sacred sphere", "Dyson Sphere", "the dyson sphere", "SACRED SPHERE!", "giga earth",
        "the ocean of light", "the earth", "transcendence", "nonsense", "",
        "the wormhole", "Einstein-Rosen bridge", "einstein rosen", "Hawking Radiation", "a black hole", "black hole"]
MEANT = [1, 1, 1, 1, 12, 9, 12, 11, -1, -1, 13, 13, 13, 14, 14, 14]


def _path(file):
    m = PATH.search(file.read_text(encoding="utf-8"))
    assert m, f"{file.name}: no EVOLUTION_PATH"
    return [(i, n, [a.strip().strip("'") for a in (al or "").split(",") if a.strip()]) for i, n, al in ENTRY.findall(m.group(1))]


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_every_list_of_names_agrees(scene):
    from agent_friday.routes.insights import SCENE_NAMES
    from agent_friday.setup_wizard import EVOLUTION_STRUCTURES
    page = _path(scene)
    assert [n for _, n, _ in page] == list(SCENE_NAMES)
    assert [ident for ident, _, _ in page] == [row[1] for row in EVOLUTION_STRUCTURES]
    # The wizard keeps the accent ("Turing Möbius"); the scene's labels are plain capitals.
    plain = lambda s: unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    assert [plain(row[2]) for row in EVOLUTION_STRUCTURES] == list(SCENE_NAMES)


def test_the_page_and_the_server_keep_the_same_aliases():
    from agent_friday.routes.insights import SCENE_ALIASES
    page = {a: n for _, n, al in _path(SCENES[0]) for a in al}
    assert page == SCENE_ALIASES


def test_the_dyson_sphere_was_the_sacred_sphere():
    page = _path(SCENES[0])
    assert page[1] == ("ICOSAHEDRON", "DYSON SPHERE", ["SACRED SPHERE"])
    assert not any("SACRED" in n for _, n, _ in page)


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_a_structure_answers_to_its_name_and_its_old_one(path):
    m = RESOLVER.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no fridayStructureIndex"
    structures = [{"id": i, "name": n, "aliases": a} for i, n, a in _path(SCENES[0])]
    src = ("global.window = { fridayVibe: { getStructures: () => " + json.dumps(structures) + " } };\n" + m.group(1) + "\n"
           "console.log(JSON.stringify(" + json.dumps(SAID) + ".map(fridayStructureIndex)));")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == MEANT


def test_the_server_resolves_a_name_as_the_page_does():
    from agent_friday.routes.insights import scene_index_for
    assert [scene_index_for(s) for s in SAID] == MEANT
