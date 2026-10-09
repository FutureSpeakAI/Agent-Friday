"""The avatar genome: one set of visual traits every holographic structure
expresses, kept as signed, versioned steps in a tree.

docs/design/active/avatar-visual-genome.md §3 (the genome), §6 (guardrails),
§7 (signed steps and rollback). The weekly step that proposes changes lives in
services/avatar_growth.py; this module owns what a genome may be, how each of
the 15 structures expresses it, and the history.

Rules this module enforces, so no author (a model, the seeded engine, a
hand-edited file) can get round them:

- **One genome.** Palette, luminance, form and speech genes are shared by
  every structure; each structure section holds form genes only, never a
  colour. Switching structures never changes her colours.
- **The empty genome is v1.** `express(defaults())` returns the literals
  index.html draws today, so a missing or invalid genome changes nothing.
- **Bounds.** Every gene has a range and a per-step maximum; a step changes
  at most three genes within one shared budget, and only the running
  structure's section. The anchor hue (v1 cyan) never moves.
- **Status colours stay status.** A palette within ΔE2000 20 of a reserved
  status colour (the approval amber, approve green, deny pink, error reds) is
  rejected, so an idle Friday never looks like a pending approval.
- **The drawing budget.** No structure expresses more than 110% of its v1
  element count.
- **Signed and verifiable.** A step is hashed with the provenance
  conventions and signed with the Ed25519 attestation key. A step whose hash
  no longer matches is never expressed.
- **Nothing is removed but what the user deletes.** Rollback, undo and reset
  move a pointer; delete moves a step to trash for 30 days; the purge is the
  only code that removes a step.

The seed is 32 random bytes made once per install. It never leaves this
directory: steps carry the sigil derived from it, never the seed.
"""
from __future__ import annotations

import colorsys
import copy
import hashlib
import json
import math
import os
import shutil
import threading
import time
from pathlib import Path

from agent_friday.core import FRIDAY_DIR
from agent_friday.user_errors import UserFacingValueError

AVATAR_DIR = FRIDAY_DIR / "avatar"
_LOCK = threading.RLock()

STEP_BUDGET = 2.5
MAX_GENES_PER_STEP = 3
TRASH_DAYS = 30

STRUCTURE_IDS = ("CUBES", "ICOSAHEDRON", "NETWORK", "DOME", "ASTROLABE", "TESSERACT",
                 "QUANTUM", "MANDELBROT", "MOBIUS", "GRID", "CABLES", "NONE", "EDEN",
                 "WORMHOLE", "BLACKHOLE")

#: Accent schemes: how far each accent turns from where v1 put it, in
#: degrees. All stay in the cool family; a complementary scheme would put an
#: accent on amber or pink, which mean status (§6.2). "v1" is today's accents.
SCHEMES = {"v1": 0, "cool": 20, "violet": 40}
EASES = ("spring", "snap", "glide")

#: The identity moods the genome recolours, with their v1 (base, accent). At
#: rest the scene is IDLE; its base colour is the anchor. The state moods
#: (LISTENING, REASONING, SPEAKING, EXECUTING) mean status and keep their hue;
#: the warm and magenta ambient moods keep their v1 colours (§6.2).
IDENTITY_MOODS = {
    "IDLE":       ("#1e54c7", "#5fa8ff"),
    "CALM":       ("#001133", "#0055aa"),
    "CURIOUS":    ("#00b3b3", "#00ffff"),
    "FOCUSED":    ("#0a1e6e", "#1e90ff"),
    "REFLECTIVE": ("#8888aa", "#ddeeff"),
}
#: Hue bands a recoloured identity colour may not enter: amber..green and
#: pink. A v1 colour already in a band keeps its hue.
FORBIDDEN_HUE_BANDS = ((45, 160), (300, 345))
#: v1 IDLE base hue (#1e54c7), the anchor base_offset is measured from.
ANCHOR_HUE = 221

# ── shared genes (§3.2): section -> gene -> spec ─────────────────────────────
#  kind: f (float), i (int), e (enum); step: most it moves in one step.
SHARED_GENES = {
    "palette": {
        "base_offset":  {"kind": "f", "min": -30, "max": 30, "step": 4, "default": 0},
        "scheme":       {"kind": "e", "values": tuple(SCHEMES), "default": "v1",
                         "every": 8},
        "accent_share": {"kind": "f", "min": 0.20, "max": 0.35, "step": 0.03, "default": 0.20},
        "saturation":   {"kind": "f", "min": 0.85, "max": 1.10, "step": 0.03, "default": 1.0},
    },
    "luma": {
        "bloom": {"kind": "f", "min": 0.85, "max": 1.10, "step": 0.03, "default": 1.0},
        "grain": {"kind": "f", "min": 0.6, "max": 1.1, "step": 0.05, "default": 1.0},
    },
    "form": {
        "density":   {"kind": "f", "min": 0.85, "max": 1.10, "step": 0.04, "default": 1.0},
        "coherence": {"kind": "f", "min": 0.0, "max": 1.0, "step": 0.08, "default": 0.5},
        "symmetry":  {"kind": "i", "min": 5, "max": 8, "step": 1, "default": 8, "every": 4},
    },
    "speech": {
        "tempo":     {"kind": "f", "min": 0.85, "max": 1.15, "step": 0.05, "default": 1.0},
        "amplitude": {"kind": "f", "min": 0.85, "max": 1.10, "step": 0.05, "default": 1.0},
    },
    # Style of the processing-state gestures (§13.7): never their meaning.
    "gesture": {
        "tempo": {"kind": "f", "min": 0.85, "max": 1.15, "step": 0.05, "default": 1.0},
        "ease":  {"kind": "e", "values": EASES, "default": "spring", "every": 4},
        "trail": {"kind": "f", "min": 0.0, "max": 0.5, "step": 0.05, "default": 0.0},
    },
}
FACETS_MAX = 12

# ── per-structure genes (§3.3): form only; `count` genes scale with density ──
STRUCTURE_GENES = {
    "CUBES":       {"spacing":  {"kind": "f", "min": 1.4, "max": 1.9, "step": 0.05, "default": 1.6},
                    "sparsity": {"kind": "f", "min": 0.05, "max": 0.30, "step": 0.03, "default": 0.15}},
    "ICOSAHEDRON": {"shells":   {"kind": "i", "min": 2, "max": 4, "step": 1, "default": 3},
                    "detail_delta": {"kind": "i", "min": -1, "max": 0, "step": 1, "default": 0}},
    "NETWORK":     {"nodes":    {"kind": "i", "min": 100, "max": 132, "step": 4, "default": 120,
                                 "count": True},
                    "link_distance": {"kind": "f", "min": 5.0, "max": 7.0, "step": 0.25, "default": 6}},
    "DOME":        {"pillars":  {"kind": "i", "min": 6, "max": 10, "step": 1, "default": 8},
                    "crystals": {"kind": "i", "min": 4, "max": 7, "step": 1, "default": 6}},
    "ASTROLABE":   {"rings":    {"kind": "i", "min": 6, "max": 9, "step": 1, "default": 8},
                    "tilt_spread": {"kind": "f", "min": 0.3, "max": 1.0, "step": 0.1, "default": 1.0}},
    "TESSERACT":   {"w_ratio":  {"kind": "f", "min": 0.6, "max": 1.4, "step": 0.1, "default": 1.0}},
    "QUANTUM":     {"wave":     {"kind": "f", "min": 8.0, "max": 12.0, "step": 0.5, "default": 10}},
    "MANDELBROT":  {"max_iter": {"kind": "i", "min": 32, "max": 40, "step": 2, "default": 40},
                    "step":     {"kind": "f", "min": 0.012, "max": 0.015, "step": 0.0005,
                                 "default": 0.012}},
    "MOBIUS":      {"twists":   {"kind": "i", "min": 1, "max": 3, "step": 2, "default": 1},
                    "width":    {"kind": "f", "min": 1.35, "max": 1.65, "step": 0.05, "default": 1.5}},
    "GRID":        {"wave_scale": {"kind": "f", "min": 0.8, "max": 1.2, "step": 0.05, "default": 1.0}},
    "CABLES":      {"tubes":    {"kind": "i", "min": 64, "max": 88, "step": 4, "default": 80,
                                 "count": True}},
    "NONE":        {"lines":    {"kind": "i", "min": 80, "max": 110, "step": 5, "default": 100,
                                 "count": True}},
    # Giga Earth never evolves by model: its one gene is the form on its set
    # track (TRACKS), moved only by track_step.
    "EDEN":        {"stage":    {"kind": "i", "min": 0, "max": 6, "step": 1, "default": 0,
                                 "track": True}},
    # The Einstein-Rosen bridge: the rings of its grid. Hawking radiation: the
    # dust that orbits the hole.
    "WORMHOLE":    {"rings":    {"kind": "i", "min": 16, "max": 24, "step": 2, "default": 20,
                                 "count": True}},
    "BLACKHOLE":   {"dust":     {"kind": "i", "min": 240, "max": 360, "step": 20, "default": 320,
                                 "count": True}},
}

#: Structures that evolve on a set track instead of by model (§15). While one
#: of them is on screen, a step moves it one form along its track and nothing
#: else; no model is asked and nothing leaves the machine.
TRACKS = {
    "EDEN": {"gene": "stage", "label": "Giga Earth",
             "forms": ("Sealed", "Cracked", "Lock-on", "Unveiled", "Arms", "Rings",
                       "Final form")},
}

#: v1 literals that are not genes, carried into the expression unchanged.
FIXED_LITERALS = {
    "CUBES": {"grid": 3, "cube_size": 1.4, "scale": 1.5, "lattice_spread": 0.65},
}

#: Reserved status colours (§1.3): the palette keeps ΔE2000 >= 20 from each.
RESERVED_COLOURS = {
    "approval amber": "#f59e0b", "approve green": "#00ff80", "deny pink": "#ff0080",
    "error red": "#ff0033", "error red 2": "#ef4444", "status green": "#00ff66",
    "status yellow": "#ffcc00",
}
#: Each check is relative to v1: a colour must clear the threshold, or be no
#: worse than today's colour already is (several v1 moods sit closer).
RESERVED_MIN_DE = 20.0
CONTRAST_MIN = 3.0          # against the scene's clear colour
CVD_MIN_DE = 10.0           # base vs accent under simulated colour-vision deficiency
SCENE_BACKGROUND = "#000103"


# ═══════════════════════════════════════════════════════════════════════════
#  The genome's shape
# ═══════════════════════════════════════════════════════════════════════════

def defaults() -> dict:
    """The empty genome: v1, exactly."""
    gen = {sec: {k: s["default"] for k, s in genes.items()}
           for sec, genes in SHARED_GENES.items()}
    gen["palette"]["anchor_hue"] = ANCHOR_HUE
    gen["palette"]["scheme_changed_at"] = 0
    gen["form"]["symmetry_changed_at"] = 0
    gen["gesture"]["ease_changed_at"] = 0
    gen["facets"] = 0
    gen["structures"] = {sid: {k: s["default"] for k, s in genes.items()}
                         for sid, genes in STRUCTURE_GENES.items()}
    return gen


def is_v1(gen: dict) -> bool:
    return _genes_only(clamp_absolute(gen)) == _genes_only(defaults())


def _genes_only(gen):
    g = copy.deepcopy(gen)
    for sec, key in (("palette", "scheme_changed_at"), ("form", "symmetry_changed_at"),
                     ("gesture", "ease_changed_at")):
        g.get(sec, {}).pop(key, None)
    return g


def _clamp_value(spec, v, fallback):
    kind = spec["kind"]
    if kind == "e":
        return v if v in spec["values"] else fallback
    try:
        x = float(v)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(x) or math.isinf(x):
        return fallback
    x = min(spec["max"], max(spec["min"], x))
    if kind == "i":
        return int(round(x))
    return round(x, 6)


def clamp_absolute(gen: dict) -> dict:
    """Every gene into its range; unknown keys and sections dropped; the
    anchor restored. The result is always a well-formed genome."""
    base = defaults()
    src = gen if isinstance(gen, dict) else {}
    out = defaults()
    for sec, genes in SHARED_GENES.items():
        given = src.get(sec) if isinstance(src.get(sec), dict) else {}
        for k, spec in genes.items():
            out[sec][k] = _clamp_value(spec, given.get(k, base[sec][k]), base[sec][k])
    for sec, key in (("palette", "scheme_changed_at"), ("form", "symmetry_changed_at"),
                     ("gesture", "ease_changed_at")):
        v = (src.get(sec) or {}).get(key, 0) if isinstance(src.get(sec), dict) else 0
        out[sec][key] = int(v) if isinstance(v, int) and v >= 0 else 0
    f = src.get("facets", 0)
    out["facets"] = int(min(FACETS_MAX, max(0, f))) if isinstance(f, (int, float)) else 0
    structs = src.get("structures") if isinstance(src.get("structures"), dict) else {}
    for sid, genes in STRUCTURE_GENES.items():
        given = structs.get(sid) if isinstance(structs.get(sid), dict) else {}
        for k, spec in genes.items():
            out["structures"][sid][k] = _clamp_value(spec, given.get(k, spec["default"]),
                                                     spec["default"])
    return out


def _iter_genes(target=None):
    """(path, spec) for every shared gene, and for the target structure's."""
    for sec, genes in SHARED_GENES.items():
        for k, spec in genes.items():
            yield (sec, k), spec
    if target in STRUCTURE_GENES and target not in TRACKS:
        for k, spec in STRUCTURE_GENES[target].items():
            yield ("structures", target, k), spec


def _get(gen, path):
    cur = gen
    for p in path:
        cur = cur[p]
    return cur


def _set(gen, path, value):
    cur = gen
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


def _cost(spec, a, b):
    if spec["kind"] == "e":
        return 0.0 if a == b else 1.0
    return abs(float(b) - float(a)) / float(spec["step"])


def step_cost(parent: dict, child: dict, target=None) -> float:
    return sum(_cost(spec, _get(parent, p), _get(child, p)) for p, spec in _iter_genes(target))


def clamp_step(parent: dict, proposed: dict, *, target: str, step_number: int):
    """Clamp a proposed genome to one step from `parent`.

    Each gene moves at most its step; enum genes change only as often as
    their cadence allows; facets grow by at most one; only the `target`
    structure's section may change; at most MAX_GENES_PER_STEP genes move,
    and their normalised moves sum to at most STEP_BUDGET. Returns
    (genome, [moved gene paths])."""
    parent = clamp_absolute(parent)
    prop = clamp_absolute(proposed)
    out = copy.deepcopy(parent)
    if target in TRACKS:
        # A structure on a set track is never a model's to change.
        return out, []
    moves = []
    cadence = {("palette", "scheme"): ("palette", "scheme_changed_at"),
               ("form", "symmetry"): ("form", "symmetry_changed_at"),
               ("gesture", "ease"): ("gesture", "ease_changed_at")}
    for path, spec in _iter_genes(target):
        a, b = _get(parent, path), _get(prop, path)
        if a == b:
            continue
        if path in cadence:
            last = _get(parent, cadence[path])
            if last and step_number - last < spec["every"]:
                continue
        if spec["kind"] == "e":
            moves.append((1.0, path, b))
            continue
        delta = max(-spec["step"], min(spec["step"], float(b) - float(a)))
        nv = float(a) + delta
        nv = int(round(nv)) if spec["kind"] == "i" else round(nv, 6)
        if nv != a:
            moves.append((abs(nv - a) / spec["step"], path, nv))
    moves.sort(key=lambda m: -m[0])
    moves = moves[:MAX_GENES_PER_STEP]
    total = sum(m[0] for m in moves)
    scale = min(1.0, STEP_BUDGET / total) if total else 1.0
    moved = []
    for cost, path, nv in moves:
        spec = dict(_iter_genes(target))[path]
        a = _get(parent, path)
        if spec["kind"] == "e":
            _set(out, path, nv)
        elif spec["kind"] == "i":
            v = int(round(a + (nv - a) * scale))
            if v == a:
                continue
            _set(out, path, v)
        else:
            _set(out, path, round(a + (nv - a) * scale, 6))
        moved.append(path)
        if path in cadence:
            _set(out, cadence[path], int(step_number))
    while step_cost(parent, out, target) > STEP_BUDGET + 1e-9 and moved:
        path = moved.pop()
        _set(out, path, _get(parent, path))
    f_parent, f_prop = parent["facets"], prop["facets"]
    out["facets"] = min(FACETS_MAX, f_parent + (1 if f_prop > f_parent else 0))
    return out, ["/".join(p) for p in moved]


def track_step(parent: dict, target: str):
    """The next form on `target`'s set track: (genome, [moved gene paths]),
    with nothing moved once the track has reached its last form."""
    parent = clamp_absolute(parent)
    out = copy.deepcopy(parent)
    tr = TRACKS.get(target)
    if not tr:
        return out, []
    spec = STRUCTURE_GENES[target][tr["gene"]]
    cur = out["structures"][target][tr["gene"]]
    if cur >= spec["max"]:
        return out, []
    out["structures"][target][tr["gene"]] = cur + 1
    return out, ["structures/%s/%s" % (target, tr["gene"])]


def track_form(target: str, gen: dict):
    """The name of the form `gen` shows on `target`'s track, or None."""
    tr = TRACKS.get(target)
    if not tr:
        return None
    return tr["forms"][clamp_absolute(gen)["structures"][target][tr["gene"]]]


# ═══════════════════════════════════════════════════════════════════════════
#  Expression: what each structure draws
# ═══════════════════════════════════════════════════════════════════════════

_BACKGROUND_VERTS = 800 + 20 * 50 + 15


def _struct_verts(sid, e):
    if sid == "CUBES":
        return 27 * (1 - e["sparsity"]) * 60
    if sid == "ICOSAHEDRON":
        # The Dyson sphere: a fixed allotment of hexagonal panels shared by its
        # shells (fewer, bigger ones a detail step down), each drawn twice
        # (collector and rim), the star and the prominences' points.
        panels = 315 if e["detail_delta"] < 0 else 420
        return panels * 2 * 14 + 2562 + 8 * 240
    if sid == "NETWORK":
        return e["nodes"] * 9
    if sid == "DOME":
        return 2000 + 600 + e["pillars"] * 24 + e["crystals"] * 18
    if sid == "ASTROLABE":
        return e["rings"] * 600
    if sid == "TESSERACT":
        return 80
    if sid == "QUANTUM":
        return 6000                     # the probability cloud's points
    if sid == "MANDELBROT":
        return (3.0 / e["step"]) ** 2
    if sid == "MOBIUS":
        return (2 * math.pi / 0.04) * (e["width"] * 2 / 0.15)
    if sid == "GRID":
        return 81 * 81
    if sid == "CABLES":
        return e["tubes"] * 576
    if sid == "NONE":
        return e["lines"] * 2 * 64 * 2  # two fibres a line, 64 segments a fibre
    if sid == "EDEN":
        # Tunnel, 200 tiles, the robot, two rings and their shards, the rail
        # halos: the same parts at every form (a form shows or places them),
        # so the same count.
        return 40 * 40 * 2 + 200 * 24 + 400 + 12 * 96 + 15 * 20 + 60 * 2
    if sid == "WORMHOLE":
        # Rings of the near funnel and the shorter far one, the spokes of both,
        # the throat's ring, the far side and the stream through it.
        return e["rings"] * 144 * 1.45 + 36 * 40 * 4 + 160
    if sid == "BLACKHOLE":
        # The sphere the light is traced on, the pairs and motes, the dust.
        return 49 * 33 + 168 + e["dust"]
    return 0


def _express_one(sid, gen):
    genes = STRUCTURE_GENES[sid]
    e = dict(FIXED_LITERALS.get(sid, {}))
    e.update(gen["structures"][sid])
    density = gen["form"]["density"]
    for k, spec in genes.items():
        if spec.get("count"):
            e[k] = int(round(e[k] * density))
    v1 = defaults()
    cap = 1.10 * (_struct_verts(sid, {**FIXED_LITERALS.get(sid, {}), **v1["structures"][sid]})
                  + _BACKGROUND_VERTS) - _BACKGROUND_VERTS
    for _ in range(8):
        if _struct_verts(sid, e) <= cap:
            break
        for k, spec in genes.items():
            if spec.get("count"):
                e[k] = max(1, int(e[k] * 0.97))
        if not any(s.get("count") for s in genes.values()):
            e.update(v1["structures"][sid])
            break
    return e


def express(gen: dict) -> dict:
    """Structure id -> the literals it draws with. `express(defaults())` is v1."""
    gen = clamp_absolute(gen)
    return {sid: _express_one(sid, gen) for sid in STRUCTURE_IDS}


def element_count(sid: str, gen: dict) -> float:
    """What the structure plus the shared background draws, in vertices."""
    return _struct_verts(sid, express(gen)[sid]) + _BACKGROUND_VERTS


def _rotate(hexs, degrees, sat_mult=1.0):
    """Turn a colour's hue, keeping its perceived brightness: blue at a given
    HSL lightness looks far darker than cyan, so the lightness is re-solved
    until the relative luminance matches the original (within gamut)."""
    if not degrees and sat_mult == 1.0:
        return hexs.lower()            # unchanged means exactly v1
    r, g, b = _rgb(hexs)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.05:                       # a grey has no hue to turn
        return _hex((r, g, b))
    target = _luminance(hexs)
    h = (h + degrees / 360.0) % 1.0
    s = min(1.0, s * sat_mult)
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if _luminance(_hex(colorsys.hls_to_rgb(h, mid, s))) < target:
            lo = mid
        else:
            hi = mid
    return _hex(colorsys.hls_to_rgb(h, (lo + hi) / 2, s))


def _hue(hexs):
    r, g, b = _rgb(hexs)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return None if s < 0.3 else round(h * 360, 1)


def palette(gen: dict) -> dict:
    """The one palette every structure draws from (§3.2, §6.2): the identity
    moods, turned by base_offset from how they look today, accents turned a
    further `scheme` degrees, saturation scaled."""
    gen = clamp_absolute(gen)
    p = gen["palette"]
    off, extra, sat = p["base_offset"], SCHEMES[p["scheme"]], p["saturation"]
    moods = {m: {"base": _rotate(b, off, sat), "accent": _rotate(a, off + extra, sat)}
             for m, (b, a) in IDENTITY_MOODS.items()}
    idle = moods["IDLE"]
    hues = [h for m in moods.values() for h in (_hue(m["base"]), _hue(m["accent"]))
            if h is not None]
    return {"base_hue": round((ANCHOR_HUE + off) % 360, 1), "hues": hues,
            "moods": moods, "colours": [idle["base"], idle["accent"]],
            "saturation": sat, "accent_share": p["accent_share"],
            "bloom": gen["luma"]["bloom"], "grain": gen["luma"]["grain"]}


# ── colour science for the validators ────────────────────────────────────────

def _hex(rgb):
    return "#" + "".join("%02x" % int(round(max(0, min(1, c)) * 255)) for c in rgb)


def _rgb(hexs):
    h = hexs.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_lab(rgb_lin):
    r, g, b = rgb_lin
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(c1, c2) -> float:
    """CIEDE2000 between two colours given as hex or linear-RGB tuples."""
    lab1 = _to_lab(tuple(_lin(c) for c in _rgb(c1)) if isinstance(c1, str) else c1)
    lab2 = _to_lab(tuple(_lin(c) for c in _rgb(c2)) if isinstance(c2, str) else c2)
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    if C1p * C2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-((hbp - 275) / 25) ** 2)
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def _luminance(hexs):
    r, g, b = (_lin(c) for c in _rgb(hexs))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


#: Machado, Oliveira and Fernandes (2009), severity 1.0, on linear RGB.
_CVD = {
    "protanopia":   ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
                     (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia":   ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
                     (0.004733, 0.691367, 0.303900)),
}


def _simulate(hexs, m):
    lin = [_lin(c) for c in _rgb(hexs)]
    return tuple(max(0.0, min(1.0, sum(m[i][j] * lin[j] for j in range(3)))) for i in range(3))


def _in_band(h):
    return h is not None and any(lo <= h % 360 <= hi for lo, hi in FORBIDDEN_HUE_BANDS)


def _colour_checks(base, accent):
    """The numbers the validators compare: reserved distance, contrast and
    colour-blind separation, for one mood's pair."""
    out = {}
    for which, c in (("base", base), ("accent", accent)):
        out[which + "_reserved"] = min(delta_e(c, r) for r in RESERVED_COLOURS.values())
        out[which + "_contrast"] = _contrast(c, SCENE_BACKGROUND)
    out["cvd"] = min(delta_e(_simulate(base, m), _simulate(accent, m)) for m in _CVD.values())
    return out


_V1_CHECKS = {}


def _v1_checks(mood):
    if mood not in _V1_CHECKS:
        _V1_CHECKS[mood] = _colour_checks(*IDENTITY_MOODS[mood])
    return _V1_CHECKS[mood]


def validate(gen: dict) -> list:
    """Problems with a genome, in plain words; [] when it may be drawn.

    Colour rules are measured against v1: a recoloured identity colour must
    clear each threshold, or be no worse than today's colour already is."""
    gen = clamp_absolute(gen)
    problems = []
    pal = palette(gen)
    eps = 0.5
    for mood, pair in pal["moods"].items():
        now, v1 = _colour_checks(pair["base"], pair["accent"]), _v1_checks(mood)
        v1b, v1a = IDENTITY_MOODS[mood]
        for which, colour, v1c in (("base", pair["base"], v1b), ("accent", pair["accent"], v1a)):
            need = min(RESERVED_MIN_DE, v1[which + "_reserved"]) - eps
            if now[which + "_reserved"] < need:
                problems.append("%s %s %s is too close to a reserved status colour"
                                % (mood, which, colour))
            # The accent draws the lines: if it clears 3:1 today it must keep
            # clearing it. A base colour (the cube faces, drawn at 15%) and a
            # colour dim by design (CALM, FOCUSED) may lose at most a quarter.
            v1c_contrast = v1[which + "_contrast"]
            need = (CONTRAST_MIN if which == "accent" and v1c_contrast >= CONTRAST_MIN
                    else min(CONTRAST_MIN, v1c_contrast) * 0.75)
            if now[which + "_contrast"] < need:
                problems.append("%s %s %s is too dark against the scene" % (mood, which, colour))
            if _in_band(_hue(colour)) and not _in_band(_hue(v1c)):
                problems.append("%s %s %s turns into green, amber or pink"
                                % (mood, which, colour))
        if now["cvd"] < min(CVD_MIN_DE, v1["cvd"] * 0.8):
            problems.append("%s base and accent cannot be told apart with colour-blindness" % mood)
    v1g = defaults()
    for sid in STRUCTURE_IDS:
        if element_count(sid, gen) > element_count(sid, v1g) * 1.10 + 1:
            problems.append("%s is over its drawing budget" % sid)
    return problems


# ═══════════════════════════════════════════════════════════════════════════
#  The seed and the sigil
# ═══════════════════════════════════════════════════════════════════════════

def seed() -> bytes:
    """32 random bytes, made once per install, never sent anywhere."""
    with _LOCK:
        p = AVATAR_DIR / "seed"
        try:
            b = p.read_bytes()
            if len(b) == 32:
                return b
        except OSError:
            pass
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        b = os.urandom(32)
        p.write_bytes(b)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return b


def sigil() -> dict:
    """The small mark every structure draws the same way. Derived from the
    seed and fixed for the life of the install."""
    s = hashlib.sha256(b"friday-sigil/1" + seed()).digest()
    return {"arms": 3 + s[0] % 6, "tilt": round(s[1] / 255 * 60 - 30, 2),
            "accent_slot": s[2] % 8, "phase": round(s[3] / 255, 3)}


# ═══════════════════════════════════════════════════════════════════════════
#  Signed steps and the tree (§7)
# ═══════════════════════════════════════════════════════════════════════════

def _engine():
    from agent_friday.services import provenance
    return provenance._integrity()


def _deterministic(body):
    from agent_friday.services import provenance
    return provenance._deterministic(body)


def _body(step):
    return {k: v for k, v in step.items() if k not in ("content_hash", "signature")}


def _steps_dir():
    return AVATAR_DIR / "steps"


def _trash_dir():
    return AVATAR_DIR / "trash"


def _path_for(content_hash, trash=False):
    name = str(content_hash).split(":", 1)[-1]
    if not all(c in "0123456789abcdef" for c in name) or len(name) != 64:
        raise KeyError(content_hash)
    return (_trash_dir() if trash else _steps_dir()) / (name + ".json")


def state() -> dict:
    with _LOCK:
        try:
            d = json.loads((AVATAR_DIR / "state.json").read_text("utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}


def save_state(d: dict) -> None:
    with _LOCK:
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        tmp = AVATAR_DIR / "state.json.tmp"
        tmp.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, AVATAR_DIR / "state.json")


def update_state(**changes) -> dict:
    with _LOCK:
        d = state()
        d.update(changes)
        save_state(d)
        return d


def load_step(content_hash, *, include_trash=False):
    for trash in ((False, True) if include_trash else (False,)):
        try:
            return json.loads(_path_for(content_hash, trash).read_text("utf-8"))
        except (OSError, ValueError):
            continue
    return None


def verify_step(step) -> str:
    """"verified", "unsigned" (hash good, no key was available to sign) or
    "tampered" (the hash does not match, or the signature fails)."""
    try:
        body = _body(step)
        digest = "sha256:" + hashlib.sha256(_deterministic(body)).hexdigest()
        if digest != step.get("content_hash"):
            return "tampered"
        sig = step.get("signature") or {}
        if sig.get("value") in (None, "", "ed25519_unavailable"):
            return "unsigned"
        from agent_friday.governance.proof_of_integrity import IntegrityEngine
        signed = dict(body, content_hash=step["content_hash"])
        ok = IntegrityEngine.verify_payload(_deterministic(signed), sig.get("value"),
                                            sig.get("pubkey"))
        return "verified" if ok else "tampered"
    except Exception:
        return "tampered"


def commit_step(genome, *, parent, kind, target, reason, author, input_digest,
                name=None, diff=None, activate=True, now=None, sent=None) -> dict:
    """Sign and store one step; make it active unless `activate` is False
    (a pending step in ask-first mode). `sent` is the exact payload a cloud
    author was sent (numbers and enums only), for the history's "What was
    sent"; None when nothing left the machine."""
    with _LOCK:
        par = load_step(parent) if parent else None
        number = (par or {}).get("step", 0) + 1
        gen = clamp_absolute(genome)
        body = {
            "format": "friday.avatar.step/1",
            "step": number,
            "parent": parent,
            "created_at": float(now if now is not None else time.time()),
            "kind": kind,
            "target_structure": target,
            "name": name,
            "genome": gen,
            "sigil": sigil(),
            "diff": diff if diff is not None else _diff(par["genome"] if par else defaults(), gen),
            "reason": str(reason or "")[:280],
            "author": author,
            "input_digest": input_digest,
            "sent": sent,
            "generator": _generator(),
        }
        body["content_hash"] = "sha256:" + hashlib.sha256(_deterministic(body)).hexdigest()
        eng = _engine()
        sig = None
        pub = None
        if eng is not None:
            try:
                sig = eng.sign_payload(_deterministic(body))
                pub = eng.get_public_key_hex()
            except Exception:
                sig = None
        body["signature"] = {"alg": "ed25519", "pubkey": pub,
                             "value": sig or "ed25519_unavailable"}
        _steps_dir().mkdir(parents=True, exist_ok=True)
        _path_for(body["content_hash"]).write_text(json.dumps(body, indent=1),
                                                   encoding="utf-8")
        if activate:
            update_state(active=body["content_hash"])
        return body


def _generator():
    try:
        from agent_friday import __version__
        return "agent-friday/%s" % __version__
    except Exception:
        return "agent-friday"


def _diff(a, b):
    out = []
    for path, _spec in _iter_genes(None):
        if _get(a, path) != _get(b, path):
            out.append({"gene": "/".join(path), "from": _get(a, path), "to": _get(b, path)})
    for sid in STRUCTURE_IDS:
        for k in STRUCTURE_GENES[sid]:
            if a["structures"][sid][k] != b["structures"][sid][k]:
                out.append({"gene": "structures/%s/%s" % (sid, k),
                            "from": a["structures"][sid][k], "to": b["structures"][sid][k]})
    if a.get("facets") != b.get("facets"):
        out.append({"gene": "facets", "from": a.get("facets"), "to": b.get("facets")})
    return out


def active_step():
    """The newest verified step on the active branch, or None for v1."""
    h = state().get("active")
    seen = set()
    while h and h not in seen:
        seen.add(h)
        step = load_step(h)
        if step is None:
            return None
        if verify_step(step) in ("verified", "unsigned"):
            return step
        h = step.get("parent")
    return None


def active_genome() -> dict:
    step = active_step()
    return clamp_absolute(step["genome"]) if step else defaults()


def history(*, include_hidden=True) -> list:
    """Every stored step, oldest first, with its verification and flags."""
    st = state()
    hidden = set(st.get("hidden", []))
    out = []
    d = _steps_dir()
    if not d.exists():
        return out
    for p in d.glob("*.json"):
        try:
            step = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not include_hidden and step.get("content_hash") in hidden:
            continue
        step["verification"] = verify_step(step)
        step["hidden"] = step.get("content_hash") in hidden
        out.append(step)
    out.sort(key=lambda s: (s.get("created_at", 0), s.get("step", 0)))
    return out


def rollback(content_hash) -> dict:
    step = load_step(content_hash)
    if step is None:
        raise KeyError(content_hash)
    if verify_step(step) == "tampered":
        raise UserFacingValueError("that step failed verification")
    update_state(active=content_hash)
    return step


def undo():
    """Back to the active step's parent (v1 when it has none). The undone step
    stays in history, labelled undone."""
    with _LOCK:
        cur = active_step()
        if cur is None:
            return None
        st = state()
        undone = list(st.get("undone", []))
        if cur["content_hash"] not in undone:
            undone.append(cur["content_hash"])
        update_state(active=cur.get("parent"), undone=undone)
        return load_step(cur["parent"]) if cur.get("parent") else None


def reset() -> None:
    update_state(active=None)


def set_hidden(content_hash, hidden=True) -> None:
    with _LOCK:
        st = state()
        hs = [h for h in st.get("hidden", []) if h != content_hash]
        if hidden:
            hs.append(content_hash)
        update_state(hidden=hs)


def delete(content_hash, *, now=None) -> None:
    """Move a step to trash; restorable for TRASH_DAYS. The active step must
    be switched away from first."""
    with _LOCK:
        if state().get("active") == content_hash:
            raise UserFacingValueError("switch to another look before deleting this one")
        src = _path_for(content_hash)
        if not src.exists():
            raise KeyError(content_hash)
        _trash_dir().mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(_path_for(content_hash, trash=True)))
        st = state()
        deleted = dict(st.get("deleted", {}))
        deleted[content_hash] = float(now if now is not None else time.time())
        update_state(deleted=deleted)


def restore(content_hash) -> None:
    with _LOCK:
        src = _path_for(content_hash, trash=True)
        if not src.exists():
            raise KeyError(content_hash)
        shutil.move(str(src), str(_path_for(content_hash)))
        st = state()
        deleted = dict(st.get("deleted", {}))
        deleted.pop(content_hash, None)
        update_state(deleted=deleted)


def purge(*, now=None) -> int:
    """Remove steps the user deleted more than TRASH_DAYS ago. The only code
    that removes history, and only what the user deleted."""
    now = float(now if now is not None else time.time())
    removed = 0
    with _LOCK:
        st = state()
        deleted = dict(st.get("deleted", {}))
        for h, at in list(deleted.items()):
            if now - float(at) >= TRASH_DAYS * 86400:
                try:
                    _path_for(h, trash=True).unlink()
                except (OSError, KeyError):
                    pass
                deleted.pop(h, None)
                removed += 1
        update_state(deleted=deleted)
    return removed


def public_view() -> dict:
    """What the page gets: the active genome as drawn, never the seed."""
    step = active_step()
    gen = active_genome()
    return {
        "genome": gen,
        "expression": express(gen),
        "palette": palette(gen),
        "sigil": sigil(),
        "v1": step is None,
        "step": None if step is None else {
            k: step.get(k) for k in ("content_hash", "step", "name", "kind", "created_at",
                                     "target_structure", "reason", "author", "diff", "parent")
        },
    }
