"""The avatar genome: one set of visual traits every structure expresses,
stored as signed, versioned steps in a tree (avatar-visual-genome.md §3, §6,
§7). These pin the rules the renderer and the weekly step rely on."""
import json

import pytest

from agent_friday.services import avatar_genome as g

STRUCTURES = ["CUBES", "ICOSAHEDRON", "NETWORK", "DOME", "ASTROLABE", "TESSERACT",
              "QUANTUM", "MANDELBROT", "MOBIUS", "GRID", "CABLES", "NONE", "EDEN"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    # A throwaway Ed25519 key: tests never touch the install's real one.
    from agent_friday.governance.proof_of_integrity import IntegrityEngine
    eng = IntegrityEngine(friday_dir=tmp_path / "identity")
    monkeypatch.setattr(g, "_engine", lambda: eng)
    monkeypatch.setattr(g, "AVATAR_DIR", tmp_path / "avatar")
    return tmp_path / "avatar"


# ── the empty genome is v1 ───────────────────────────────────────────────────

def test_every_structure_has_an_expression_section():
    assert sorted(g.STRUCTURE_GENES) == sorted(STRUCTURES)


def test_the_empty_genome_expresses_exactly_the_v1_literals():
    ex = g.express(g.defaults())
    assert ex["CUBES"] == {"grid": 3, "spacing": 1.6, "cube_size": 1.4, "sparsity": 0.15,
                           "scale": 1.5, "lattice_spread": 0.65}
    assert ex["NETWORK"]["nodes"] == 120 and ex["NETWORK"]["link_distance"] == 6
    assert ex["ASTROLABE"]["rings"] == 8
    assert ex["MANDELBROT"] == {"max_iter": 40, "step": 0.012}
    assert ex["EDEN"]["stage"] == 0          # the sealed ball (§15)
    assert g.is_v1(g.defaults())


def test_the_empty_genome_draws_todays_colours_exactly():
    pal = g.palette(g.defaults())
    for mood, (base, accent) in g.IDENTITY_MOODS.items():
        assert pal["moods"][mood] == {"base": base, "accent": accent}


def test_a_turned_colour_keeps_its_brightness():
    gen = g.defaults()
    gen["palette"]["base_offset"] = 20
    turned = g.palette(gen)["moods"]["IDLE"]["base"]
    assert abs(g._luminance(turned) - g._luminance("#1e54c7")) < 0.01


def test_one_palette_for_every_structure():
    gen = g.defaults()
    gen["palette"]["base_offset"] = 12
    pal = g.palette(gen)
    # the palette is structure-free: no structure section may carry colour
    for sid, genes in g.STRUCTURE_GENES.items():
        assert not any(k in genes for k in ("hue", "color", "colour", "accent", "accent_bias"))
    assert pal["base_hue"] == g.ANCHOR_HUE + 12


# ── bounds, per-step limits and the step budget ──────────────────────────────

def test_every_gene_is_clamped_to_its_range():
    wild = g.defaults()
    wild["palette"]["base_offset"] = 400
    wild["form"]["density"] = 9
    wild["structures"]["CUBES"]["spacing"] = 9
    wild["structures"]["MANDELBROT"]["max_iter"] = 400
    out = g.clamp_absolute(wild)
    assert out["palette"]["base_offset"] == 30
    assert out["form"]["density"] == 1.10
    assert out["structures"]["CUBES"]["spacing"] == 1.9
    assert out["structures"]["MANDELBROT"]["max_iter"] == 40   # down only


def test_unknown_keys_are_dropped_and_the_anchor_never_moves():
    wild = g.defaults()
    wild["palette"]["anchor_hue"] = 30
    wild["palette"]["exec"] = "rm -rf"
    wild["structures"]["CUBES"]["colour"] = "#ff0000"
    wild["structures"]["WORMHOLE"] = {"size": 3}
    out = g.clamp_absolute(wild)
    assert out["palette"]["anchor_hue"] == g.ANCHOR_HUE
    assert "exec" not in out["palette"]
    assert "colour" not in out["structures"]["CUBES"]
    assert "WORMHOLE" not in out["structures"]


def test_a_step_moves_each_gene_at_most_its_per_step_maximum():
    parent = g.defaults()
    child = g.defaults()
    child["palette"]["base_offset"] = 25
    child["form"]["coherence"] = 1.0
    out, moved = g.clamp_step(parent, child, target="CUBES", step_number=1)
    assert out["palette"]["base_offset"] == 4
    assert abs(out["form"]["coherence"] - (0.5 + 0.08)) < 1e-9


def test_a_step_changes_at_most_three_genes_within_the_shared_budget():
    parent = g.defaults()
    child = g.defaults()
    child["palette"]["base_offset"] = 4
    child["palette"]["saturation"] = 1.03
    child["luma"]["bloom"] = 1.03
    child["luma"]["grain"] = 1.05
    child["form"]["density"] = 1.04
    child["speech"]["tempo"] = 1.05
    out, moved = g.clamp_step(parent, child, target="CUBES", step_number=1)
    assert len(moved) <= 3
    assert g.step_cost(parent, out, target="CUBES") <= g.STEP_BUDGET + 1e-9


def test_only_the_running_structure_section_changes_in_a_step():
    parent = g.defaults()
    child = g.defaults()
    child["structures"]["CUBES"]["spacing"] = 1.7
    child["structures"]["MOBIUS"]["width"] = 1.7
    out, _ = g.clamp_step(parent, child, target="MOBIUS", step_number=1)
    assert out["structures"]["CUBES"] == parent["structures"]["CUBES"]
    assert out["structures"]["MOBIUS"]["width"] != parent["structures"]["MOBIUS"]["width"]


def test_the_scheme_changes_at_most_once_in_eight_steps():
    parent = g.defaults()
    parent["palette"]["scheme_changed_at"] = 5
    child = g.defaults()
    child["palette"]["scheme"] = "violet"
    out, _ = g.clamp_step(parent, child, target="CUBES", step_number=9)
    assert out["palette"]["scheme"] == parent["palette"]["scheme"]
    out, _ = g.clamp_step(parent, child, target="CUBES", step_number=13)
    assert out["palette"]["scheme"] == "violet"


def test_facets_only_ever_grow_by_one_per_step():
    parent = g.defaults()
    child = g.defaults()
    child["facets"] = 9
    out, _ = g.clamp_step(parent, child, target="CUBES", step_number=1)
    assert out["facets"] == 1


# ── validators ───────────────────────────────────────────────────────────────

def test_a_palette_near_a_reserved_status_colour_is_rejected():
    gen = g.defaults()
    gen["palette"]["base_offset"] = -30          # CURIOUS's cyan turns to the approve green
    problems = g.validate(gen)
    assert any("reserved" in p or "green" in p for p in problems)


def test_the_default_palette_passes_every_validator():
    assert g.validate(g.defaults()) == []


def test_no_structure_can_exceed_its_element_budget():
    gen = g.defaults()
    gen["form"]["density"] = 1.10
    for sid in STRUCTURES:
        gen["structures"][sid] = {k: spec["max"] for k, spec in g.STRUCTURE_GENES[sid].items()}
    assert not [p for p in g.validate(gen) if "budget" in p]
    for sid in STRUCTURES:
        assert g.element_count(sid, gen) <= g.element_count(sid, g.defaults()) * 1.10 + 1


def test_every_palette_that_passes_stays_out_of_green_amber_and_pink():
    """At -30° CURIOUS's cyan accent reaches hue 150, the approve green
    itself; the validators are what keep that end out."""
    passed = 0
    for scheme in g.SCHEMES:
        for offset in range(-30, 31, 2):
            gen = g.defaults()
            gen["palette"]["scheme"] = scheme
            gen["palette"]["base_offset"] = offset
            if g.validate(gen):
                continue
            passed += 1
            for hue in g.palette(gen)["hues"]:
                assert not (45 <= hue % 360 <= 160), (scheme, offset, hue)   # amber..green
                assert not (300 <= hue % 360 <= 345), (scheme, offset, hue)  # pink
    assert passed >= 30
    near_green = g.defaults()
    near_green["palette"]["base_offset"] = -30
    assert g.validate(near_green)


# ── the seed and the sigil ───────────────────────────────────────────────────

def test_the_seed_is_made_once_and_the_sigil_follows_it(home):
    s1 = g.seed()
    s2 = g.seed()
    assert s1 == s2 and len(s1) == 32
    assert g.sigil() == g.sigil()
    assert (home / "seed").read_bytes() == s1


def test_two_installs_get_different_sigils(tmp_path, monkeypatch, home):
    sig = []
    for i in range(6):
        monkeypatch.setattr(g, "AVATAR_DIR", tmp_path / f"install{i}")
        sig.append(json.dumps(g.sigil(), sort_keys=True))
    assert len(set(sig)) > 1


# ── signed steps, the tree, rollback ─────────────────────────────────────────

def _commit(parent=None, **over):
    gen = g.defaults()
    gen["palette"]["base_offset"] = over.pop("offset", 4)
    return g.commit_step(gen, parent=parent, kind="growth", target="CUBES",
                         reason="a test step", author={"path": "seeded"},
                         input_digest="sha256:x", **over)


def test_a_committed_step_is_signed_hashed_and_becomes_active(home):
    step = _commit()
    assert step["content_hash"].startswith("sha256:")
    assert step["signature"]["alg"] == "ed25519"
    assert g.verify_step(step) in ("verified", "unsigned")
    assert g.active_step()["content_hash"] == step["content_hash"]
    assert g.active_genome()["palette"]["base_offset"] == 4


def test_a_tampered_step_is_not_expressed(home):
    step = _commit()
    path = home / "steps" / (step["content_hash"].split(":")[1] + ".json")
    doc = json.loads(path.read_text("utf-8"))
    doc["genome"]["palette"]["base_offset"] = 30
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert g.verify_step(doc) == "tampered"
    assert g.is_v1(g.active_genome())            # falls back to the nearest verified ancestor


def test_rollback_to_any_step_forks_and_loses_nothing(home):
    a = _commit(offset=4)
    b = _commit(parent=a["content_hash"], offset=8)
    c = _commit(parent=b["content_hash"], offset=12)
    g.rollback(a["content_hash"])
    assert g.active_genome()["palette"]["base_offset"] == 4
    d = _commit(parent=g.active_step()["content_hash"], offset=0)
    hashes = {s["content_hash"] for s in g.history()}
    assert {a["content_hash"], b["content_hash"], c["content_hash"], d["content_hash"]} <= hashes
    assert d["parent"] == a["content_hash"]


def test_undo_returns_to_the_parent_and_reset_returns_to_v1(home):
    a = _commit(offset=4)
    b = _commit(parent=a["content_hash"], offset=8)
    assert g.undo()["content_hash"] == a["content_hash"]
    assert g.active_genome()["palette"]["base_offset"] == 4
    assert b["content_hash"] in g.state().get("undone", [])
    g.reset()
    assert g.is_v1(g.active_genome())


def test_rollback_to_an_unknown_step_is_refused(home):
    with pytest.raises(KeyError):
        g.rollback("sha256:" + "0" * 64)


def test_delete_moves_a_step_to_trash_and_restore_brings_it_back(home):
    a = _commit(offset=4)
    b = _commit(parent=a["content_hash"], offset=8)
    g.rollback(a["content_hash"])
    g.delete(b["content_hash"])
    assert b["content_hash"] not in {s["content_hash"] for s in g.history()}
    g.restore(b["content_hash"])
    assert b["content_hash"] in {s["content_hash"] for s in g.history()}


def test_deleting_the_active_step_needs_a_replacement(home):
    a = _commit(offset=4)
    with pytest.raises(ValueError):
        g.delete(a["content_hash"])


def test_the_purge_removes_only_user_deleted_steps_after_thirty_days(home):
    a = _commit(offset=4)
    b = _commit(parent=a["content_hash"], offset=8)
    g.rollback(a["content_hash"])
    g.delete(b["content_hash"], now=1_000_000)
    assert g.purge(now=1_000_000 + 29 * 86400) == 0
    assert g.purge(now=1_000_000 + 31 * 86400) == 1
    assert a["content_hash"] in {s["content_hash"] for s in g.history()}


def test_the_seed_never_appears_in_a_step(home):
    step = _commit()
    raw = json.dumps(step)
    assert g.seed().hex() not in raw
