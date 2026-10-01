"""The genomes the photosensitivity spec draws (tests/app/specs/
photosensitivity.spec.ts) are genomes the genome code really produces: v1,
and an evolved one at the bright, busy corner of every range. Every shared
gene is at its maximum, every structure gene is at its maximum (Giga Earth
at its final form), and the palette turns as far as validation allows.

If the genome code changes what these draw, this test fails. Regenerate the
fixture with FRIDAY_REGEN_FIXTURES=1 and re-run the spec.
"""
import json
import os
import pathlib

from agent_friday.services import avatar_genome as g

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "app" / "photosensitivity" / "genomes.json"
SIGIL = {"arms": 6, "tilt": -12.5, "accent_slot": 3, "phase": 0.42}


def _view(gen, name):
    return {"genome": gen, "expression": g.express(gen), "palette": g.palette(gen),
            "sigil": SIGIL, "v1": name is None,
            "step": None if name is None else {"content_hash": "sha256:flash-fixture", "name": name}}


def evolved_genome():
    gen = g.defaults()
    for sec, genes in g.SHARED_GENES.items():
        for k, spec in genes.items():
            if spec["kind"] in ("f", "i"):
                gen[sec][k] = spec["max"]
    gen["palette"]["scheme"] = [s for s in g.SCHEMES if s != "v1"][-1]
    gen["facets"] = g.FACETS_MAX
    for sid, genes in g.STRUCTURE_GENES.items():
        for k, spec in genes.items():
            gen["structures"][sid][k] = spec["max"]
    # As far round the colour wheel as the palette's guardrails allow.
    for off in range(30, -31, -2):
        gen["palette"]["base_offset"] = off
        if not g.validate(g.clamp_absolute(gen)):
            break
    return g.clamp_absolute(gen)


def views():
    return {"v1": _view(g.defaults(), None), "evolved": _view(evolved_genome(), "The brightest corner")}


def test_the_evolved_genome_is_one_the_server_could_draw():
    gen = evolved_genome()
    assert g.validate(gen) == []
    assert gen["structures"]["EDEN"]["stage"] == 6
    assert gen["luma"]["bloom"] == g.SHARED_GENES["luma"]["bloom"]["max"]


def test_the_fixture_is_what_the_genome_code_draws():
    want = json.loads(json.dumps(views()))
    if os.environ.get("FRIDAY_REGEN_FIXTURES") == "1":
        FIXTURE.write_text(json.dumps(want, indent=1) + "\n", encoding="utf-8")
    assert FIXTURE.exists(), "run with FRIDAY_REGEN_FIXTURES=1 to write the fixture"
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == want
