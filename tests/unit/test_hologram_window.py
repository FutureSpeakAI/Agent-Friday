"""The hologram window: with face tracking on, the screen is a window and the
avatar holds its place in the world behind it. The user's head moves only the
eye: move aside and the avatar is seen from there, and what was past the far
edge comes into view; lean in and the avatar comes closer while the view
through the glass widens. The glass is the screen, so what sits on it stays
put on screen. It is done at the camera and projection level, so every
structure answers to it at once and a structure that evolves cannot lose it,
and nothing in an avatar reads the head.

Three parts. The engine's maths runs under node, lifted verbatim from
index.html with the vendored three.js, through the same placeWindow the render
loop calls: the avatar's world position is the same at every head position
while the projection changes, the glass is fixed to the screen, the sides and
the lean behave like a window, a head movement in centimetres is the same
movement behind the glass, the clamps hold, reduced motion keeps only a gentle
lean, and a lost face eases back in about 300 ms. A static check holds every
reader of the head state to the camera, the HUD and the dock. Then a real
browser loads the served page and, for each of the fifteen structures,
measures how close its centre is to the eye at rest and leaned in, before and
again after a genome step has rebuilt every structure, and that the structure
stays where it is.
"""
import functools
import http.server
import json
import pathlib
import re
import shutil
import socketserver
import subprocess
import threading
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
MIRROR = REPO / "ui_parts" / "styles_and_scene.html"
node = shutil.which("node")
BLOCK = re.compile(r"// <tracking-engine>\n(.*?)// </tracking-engine>", re.S)

# What the browser half is calibrated to: a face twice the neutral width is
# one octave nearer, so at depth 1 the eye would come twice as near the glass,
# capped by zoom_in_max.
NEUTRAL_W = 0.18
STRUCTURE_COUNT = 15
GLASS_AT = 0.62          # the page's share of the way from the eye to the look target


# ── the engine under node ─────────────────────────────────────────────────────

NODE_HARNESS = r"""
const THREE = require(process.argv[2]);   // node - <three.js path>
globalThis.window = globalThis;
globalThis.innerWidth = 1600; globalThis.innerHeight = 900;
globalThis.screen = { width: 1600 };
globalThis.document = { body: { classList: { toggle() {} } },
                        documentElement: { style: { setProperty() {} } } };
let reduced = false;
globalThis.matchMedia = () => ({ matches: reduced });
globalThis.dockDepthK = () => 1; globalThis.resetDockVars = () => {};
BLOCK
const TK = window.FridayTracking;
const cam = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
// The default structure's resting pose: the eye at (0, 5, 18) looking at the
// origin, the glass GLASS_AT of the way there.
const base = new THREE.Vector3(0, 5, 18), target = new THREE.Vector3(0, 0, 0);
const look = target.clone().sub(base).normalize();
const D = base.distanceTo(target), S = GLASS_AT * D;
const avatar = new THREE.Group(); avatar.position.copy(target);
const scene = new THREE.Scene(); scene.add(avatar);
function at(hx, hy, hz) {
  const r = TK.placeWindow(cam, base, look, S, hx, hy, hz);
  scene.updateMatrixWorld(true);
  return r;
}
// The glass's own axes, from the resting eye.
at(0, 0, 0);
const right = new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld, 0);
const up = new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld, 1);
const g = TK.glassHalf(cam, S);
const onGlass = (a, b) => base.clone().addScaledVector(look, S).addScaledVector(right, a * g.w).addScaledVector(up, b * g.h);
const glassPts = [onGlass(0, 0), onGlass(0.8, -0.6), onGlass(-0.95, 0.9)];
// Far behind the glass and just past its right edge at rest (x = 1.1 on screen).
const FAR = 40;
const pastRight = onGlass(1.1 * (S + FAR) / S, 0).addScaledVector(look, FAR);
const front = base.clone().addScaledVector(look, 0.6 * S);      // between the eye and the glass
const ndc = v => { const p = v.clone().project(cam); return [p.x, p.y]; };
const snap = (hx, hy, hz) => {
  const r = at(hx, hy, hz);
  return { r, avatar: avatar.getWorldPosition(new THREE.Vector3()).toArray(),
           proj: Array.from(cam.projectionMatrix.elements), quat: cam.quaternion.toArray(),
           eye: cam.position.toArray(), glass: glassPts.map(ndc), centre: ndc(target),
           pastRight: ndc(pastRight), front: ndc(front), dist: cam.position.distanceTo(target) };
};
const out = {};
TK.apply({ depth_strength: 1, zoom_in_max: 1.8, zoom_out_max: 1.5, parallax_strength: 1,
           viewing_distance_cm: 60, screen_width_cm: 0 });
out.poses = { left: snap(-0.5, 0, 0), centre: snap(0, 0, 0), right: snap(0.5, 0, 0),
              near: snap(0, 0, 1), far: snap(0, 0, -1), up: snap(0, -0.5, 0) };
out.plain_when_neutral = (() => {
  at(0, 0, 0);
  const plain = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
  return cam.projectionMatrix.elements.every((v, i) => Math.abs(v - plain.projectionMatrix.elements[i]) < 1e-9);
})();
// True to the head: a screen 50 cm wide filling the page, a head 10 cm right.
TK.apply({ screen_width_cm: 50 });
const gw = TK.glassHalf(cam, S).w, hx10 = 10 / (TK.WEBCAM_TAN_X * 60);
out.cm_true = { want: 10 * 2 * gw / 50, got: at(hx10, 0, 0).ex,
                near_got: at(hx10, 0, 1).ex, near_zoom: TK.head.zoom };
TK.apply({ parallax_strength: 2 }); out.cm_double = at(hx10, 0, 0).ex;
TK.apply({ parallax_strength: 0 }); out.cm_off = at(hx10, 0, 0).ex;
TK.apply({ parallax_strength: 1, screen_width_cm: 0 });
out.edge_clamp = at(1, 1, 0).ex / TK.glassHalf(cam, S).w;
TK.apply({ depth_strength: 1, zoom_in_max: 1.8, zoom_out_max: 1.5 });
out.zoom_octave = TK.headZoom(1); out.zoom_back = TK.headZoom(-1); out.zoom_half = TK.headZoom(0.5);
TK.apply({ depth_strength: 0 }); out.zoom_off = TK.headZoom(1);
TK.apply({ depth_strength: 1, zoom_in_max: 9, zoom_out_max: 9 });
out.zoom_hard_cap = TK.headZoom(5); out.zoom_nan = TK.headZoom(NaN);
out.dolly_floor = (S - TK.eyeDolly(S, TK.headZoom(5))) / S;
reduced = true;
out.reduced_in = TK.headZoom(3); out.reduced_out = TK.headZoom(-3); out.reduced_lateral = TK.lateralGain();
out.reduced_eye = at(0.5, 0.3, 0).ex;
reduced = false;
out.lateral = TK.lateralGain();
TK.apply({ head_smoothing: 0.35, head_response: 0.5, neutral_face_width: 0.18, zoom_in_max: 1.8, zoom_out_max: 1.5 });
let t = 0; for (let i = 0; i < 60; i++) { t += 1 / 60; TK.pushFace(0, 0, 0.36, t); }
const zLean = TK.head.z, zs = [];
for (let i = 0; i < 30; i++) { t += 1 / 60; TK.relaxToNeutral(t); zs.push(TK.head.z); }
out.lean_z = zLean; out.relax_first = zs[0]; out.relax_300ms = zs[17]; out.relax_last = zs[zs.length - 1];
out.relax_monotone = zs.every((v, i) => i === 0 || v <= zs[i - 1] + 1e-12);
// Lost leaning in and to the side, eased back, found again sitting normally:
// followed from where it eased to, not from where it was lost.
for (let i = 0; i < 60; i++) { t += 1 / 60; TK.pushFace(0.8, 0, 0.36, t); }
for (let i = 0; i < 60; i++) { t += 1 / 60; TK.relaxToNeutral(t); }
const back = [];
for (let i = 0; i < 6; i++) { t += 1 / 30; TK.pushFace(0, 0, 0.18, t); back.push([TK.head.x, TK.head.z]); }
out.reacquired_max = Math.max(...back.map(([x, z]) => Math.max(Math.abs(x), Math.abs(z))));
out.calibrated = TK.calibrate(0.25); out.cfg_neutral = TK.get().neutral_face_width;
TK.apply({ neutral_face_width: 0 }); out.uncalibrated_baseline = TK.head.baseline;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def engine():
    if not node:
        pytest.skip("node is not installed")
    m = BLOCK.search(INDEX.read_text(encoding="utf-8"))
    assert m, "index.html has no <tracking-engine> block"
    three = str(REPO / "static" / "vendor" / "three-r128.min.js")
    src = NODE_HARNESS.replace("BLOCK", m.group(1)).replace("GLASS_AT", repr(GLASS_AT))
    r = subprocess.run([node, "-", three], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_the_page_puts_the_glass_where_this_test_does():
    src = INDEX.read_text(encoding="utf-8")
    assert "const GLASS_AT = %s;" % GLASS_AT in src
    assert "TK.placeWindow(camera, baseCamPos, currentLook, screenDist," in src


def test_the_avatar_holds_its_place_while_the_projection_changes(engine):
    """Left, centre, right, near and far: the avatar is where it was, the
    camera faces the same way, and only the eye and the projection move."""
    poses = engine["poses"]
    names = ["left", "centre", "right", "near", "far"]
    for n in names:
        assert _close(poses[n]["avatar"], poses["centre"]["avatar"], 0), n
        assert _close(poses[n]["quat"], poses["centre"]["quat"], 1e-12), n
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            diff = max(abs(x - y) for x, y in zip(poses[a]["proj"], poses[b]["proj"]))
            assert diff > 1e-3, "%s and %s draw with the same projection" % (a, b)


def test_the_glass_is_the_screen(engine):
    """A point on the glass draws at the same place on screen wherever the
    head is: the window is anchored to the physical screen."""
    poses = engine["poses"]
    for n, p in poses.items():
        for got, want in zip(p["glass"], poses["centre"]["glass"]):
            assert _close(got, want, 1e-9), n


def test_moving_aside_sees_the_avatar_from_there(engine):
    """Behind the glass, the avatar slides toward the side the head went (it
    is seen past the frame from a new place); what is in front of the glass
    moves the other way; and moving left brings into view what was past the
    right edge."""
    p = engine["poses"]
    assert p["right"]["centre"][0] > 0.05 and p["left"]["centre"][0] < -0.05
    assert p["right"]["front"][0] < p["centre"]["front"][0] < p["left"]["front"][0]
    assert p["centre"]["pastRight"][0] == pytest.approx(1.1, abs=1e-6)
    assert p["left"]["pastRight"][0] < 1, "moving left should show what was past the right edge"
    assert p["right"]["pastRight"][0] > 1.1
    assert p["up"]["centre"][1] > 0.05, "the head up, the avatar slides up past the frame"


def test_leaning_in_brings_it_closer_and_widens_the_view(engine):
    p = engine["poses"]
    assert p["near"]["dist"] < 0.8 * p["centre"]["dist"], "leaning in should bring the avatar closer"
    assert p["far"]["dist"] > 1.2 * p["centre"]["dist"]
    # The glass shows more: something far behind it draws nearer the middle.
    assert abs(p["near"]["pastRight"][0]) < 0.8 * abs(p["centre"]["pastRight"][0])
    assert abs(p["far"]["pastRight"][0]) > abs(p["centre"]["pastRight"][0])


def test_a_head_movement_is_the_same_movement_behind_the_glass(engine):
    """With the screen's width known, a head 10 cm to the right puts the eye
    10 screen-centimetres to the right of the glass's middle; leaning in, the
    same place in the camera's view is fewer centimetres off; the parallax
    dial scales it and 0 turns it off; it never goes past 1.5 glass widths."""
    c = engine["cm_true"]
    assert c["got"] == pytest.approx(c["want"], rel=1e-9)
    assert c["near_got"] == pytest.approx(c["want"] / c["near_zoom"], rel=1e-9)
    assert engine["cm_double"] == pytest.approx(2 * c["want"], rel=1e-9)
    assert engine["cm_off"] == 0
    assert engine["edge_clamp"] == pytest.approx(1.5, abs=1e-9)


def test_with_the_head_at_rest_the_projection_is_the_plain_camera(engine):
    assert engine["plain_when_neutral"]


def test_the_lean_follows_the_head_and_is_bounded(engine):
    assert engine["zoom_octave"] == pytest.approx(1.8)          # 2, capped by zoom_in_max
    assert engine["zoom_back"] == pytest.approx(1 / 1.5)        # 0.5, capped by zoom_out_max
    assert engine["zoom_half"] == pytest.approx(2 ** 0.5)
    assert engine["zoom_off"] == 1
    assert engine["zoom_nan"] == 1


def test_the_eye_can_never_reach_the_avatar_whatever_the_dials_say(engine):
    assert engine["zoom_hard_cap"] == pytest.approx(2.5)
    assert engine["dolly_floor"] == pytest.approx(0.4)


def test_reduced_motion_keeps_only_a_gentle_lean(engine):
    assert engine["reduced_in"] == pytest.approx(1.12)
    assert engine["reduced_out"] == pytest.approx(1 / 1.12)
    assert engine["reduced_lateral"] == 0 and engine["reduced_eye"] == 0
    assert engine["lateral"] == 1


def test_a_lost_face_eases_back_in_about_300_ms(engine):
    assert engine["lean_z"] == pytest.approx(1.0, abs=0.02)
    assert engine["relax_monotone"]
    assert engine["relax_first"] > 0.8 * engine["lean_z"], "the first relaxed frame jumped"
    assert engine["relax_300ms"] < 0.06 * engine["lean_z"], "300 ms later it is not back at the middle"


def test_a_face_found_again_is_followed_from_where_it_eased_to(engine):
    assert engine["reacquired_max"] < 0.05, "the view went back toward where the face was lost"


def test_calibration_is_a_setting(engine):
    assert engine["calibrated"] == 0.25 and engine["cfg_neutral"] == 0.25
    assert engine["uncalibrated_baseline"] is None


# ── nothing in an avatar reads the head ──────────────────────────────────────

HEAD_READ = re.compile(r"currFace[XYZ]|TKh\.[xyz]\b|TK\.head\.[xyz]\b|FridayTracking\.head\.[xyz]\b")
# Every place the page may read the head: the head state itself and its
# easing, the camera at the window, the HUD and the dock in front of the
# glass, and the tracking debug overlay.
ALLOWED = [re.compile(p) for p in (
    r"^\s*let currFaceX = 0, currFaceY = 0, currFaceZ = 0;",
    r"^\s*const dz = TKh\.z - headZHeld;",
    r"^\s*currFace[XYZ] \+= \((TKh\.[xyz]|headZHeld) - currFace[XYZ]\) \* hk;",
    r"^\s*updateDock3D\(currFaceX, currFaceY, currFaceZ\);",
    r"^\s*hudEl\.style\.transform = `perspective\(1000px\) rotateY\(\$\{currFaceX \* -2\}deg\)",
    r"^\s*currFaceX, currFaceY, currFaceZ\)\.zoom;",
    r"^\s*rootStyle\.setProperty\('--holo-",
    r"^\s*const h[xy] = \(0\.5 \+ TK\.head\.[xy] \* 0\.5\) \* [WH];",
    r"^\s*ctx\.arc\(hx, hy, 10 \+ TK\.head\.z \* 8,",
    r"^\s*'head  x ' \+ TK\.head\.x\.toFixed\(3\)",
    r"^\s*'  z ' \+ TK\.head\.z\.toFixed\(3\)",
)]


def test_nothing_in_an_avatar_reads_the_head():
    """The avatar holds its place: only the camera at the window, the HUD and
    the dock in front of the glass, and the debug overlay read where the head
    is. An avatar that leaned with the head would be following it."""
    stray = []
    for n, line in enumerate(INDEX.read_text(encoding="utf-8").splitlines(), 1):
        if HEAD_READ.search(line) and not any(a.search(line) for a in ALLOWED):
            stray.append("index.html:%d: %s" % (n, line.strip()[:140]))
    assert not stray, "these read the head outside the window:\n  " + "\n  ".join(stray)


def test_the_mirror_has_no_avatar_that_follows_the_head():
    """The mirror's camera code is older; its avatars still must not lean."""
    text = MIRROR.read_text(encoding="utf-8")
    assert "interactionForce" not in text
    assert not [line for line in text.splitlines() if "edenPlayer" in line and HEAD_READ.search(line)]


# ── the page in a browser ─────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the repo, with the page under test at /index.html."""

    def do_GET(self):                                          # noqa: N802
        if self.path.split("?")[0] == "/index.html":
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


MEASURE = r"""
window.__holo = {
  // How near the active structure's centre is to the eye, as 1 / distance:
  // the size a unit there subtends, which is what "closer" means through a
  // window. The bounding sphere's centre, so a structure's own breathing and
  // transition scale cancel out; only the eye is left. `at` is where the
  // structure itself stands in the world.
  measure() {
    const s = fridayDebugScene(); const g = s.structures[s.targetStructure]; const cam = s.camera;
    if (!g || !cam) return null;
    g.updateMatrixWorld(true);
    // three.js caches a geometry's bounding box from the first frame that
    // asked for it; a structure whose points are still flowing into place
    // on that frame would keep that stale box for good. Measure what is
    // drawn now, and let a transient (non-finite) frame be retried.
    g.traverse(o => { if (o.geometry && o.geometry.computeBoundingBox) o.geometry.computeBoundingBox(); });
    const box = new THREE.Box3().setFromObject(g);
    if (box.isEmpty()) return null;
    const sph = box.getBoundingSphere(new THREE.Sphere());
    if (!(sph.radius > 0.05) || !isFinite(sph.radius)) return null;
    return { unit: 1 / cam.position.distanceTo(sph.center), structure: s.targetStructure,
             at: g.getWorldPosition(new THREE.Vector3()).toArray(),
             zoom: FridayTracking.head.zoom, z: FridayTracking.head.z };
  },
  // A synthetic head, held until released: no webcam in a test.
  lean(octaves) { FridayTracking.debugHead(0, 0, 0.18 * Math.pow(2, octaves)); },
  aside(x) { FridayTracking.debugHead(x, 0, 0.18); },
  release() { FridayTracking.debugHead(0, 0, 0); },
  z() { return FridayTracking.head.z; },
  x() { return FridayTracking.head.x; },
  projection() { return Array.from(fridayDebugScene().camera.projectionMatrix.elements); },
  eye() { return fridayDebugScene().camera.position.toArray(); },
  plain() {
    const cam = fridayDebugScene().camera;
    return Array.from(new THREE.PerspectiveCamera(cam.fov, cam.aspect, cam.near, cam.far).projectionMatrix.elements);
  },
  structures() { return window.fridayVibe.getStructures().map(s => s.id); },
  show(i) { window.fridayVibe.setStructure(i); },
  genomeStep(hash) {
    // A step that changes the expression of several structures, so a
    // rebuild really happens (avatar-visual-genome.md §3.4).
    window.FridayGenome._receive({ v1: false, step: { content_hash: hash },
      expression: { CUBES: { spacing: 1.9, sparsity: 0.2 }, ASTROLABE: { rings: 6 },
                    NETWORK: { nodes: 40 }, DOME: { pillars: 10 }, CABLES: { tubes: 5 } },
      palette: {}, sigil: { arms: 5, tilt: 3, accent_slot: 1, phase: 0.2 } }, false);
    window.FridayGenome._rebuild();
    return Object.keys(fridayDebugScene().structures).length;
  }
};
"""


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % httpd.server_address[1]
    try:
        with sync_api.sync_playwright() as pw:
            try:
                # Without a real GL backend headless Chromium has no WebGL and
                # the scene never starts; the app suite uses the same flags.
                browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                   "--enable-unsafe-swiftshader"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            pg = browser.new_page(viewport={"width": 1280, "height": 800})
            # The page's API calls 404 against the static server; the scene
            # does not need any of them. Settings must not arrive at all,
            # so the engine runs on its shipped defaults.
            pg.route("**/api/settings**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                              body='{"status":"ok","settings":{}}'))
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera"
                                 " && !!window.FridayTracking && !!window.FridayGenome", timeout=60000)
            pg.evaluate(MEASURE)
            _until(pg, "() => __holo.measure() !== null", "the scene never drew a structure")
            yield pg
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _until(pg, js, what, secs=15):
    try:
        pg.wait_for_function(js, timeout=secs * 1000)
    except Exception as exc:
        state = pg.evaluate("() => { try { return JSON.stringify({ target: fridayDebugScene().targetStructure,"
                            " head: FridayTracking.head, m: __holo.measure() }); } catch (e) { return String(e); } }")
        raise AssertionError("%s\n  %s\n  page: %s" % (what, str(exc).splitlines()[0][:200], state[:400]))


def _settled(pg, octaves):
    """Hold a synthetic lean and wait for the filtered head to arrive."""
    pg.evaluate("__holo.lean(%r)" % octaves)
    _until(pg, "() => Math.abs(__holo.z() - (%r)) < 0.03" % octaves,
           "the head never settled at %g octaves" % octaves)
    pg.wait_for_timeout(120)


def _eye_still(pg, what):
    """With the head held, the cinematic drift stops at its resting pose; a
    structure just shown glides there first. Wait until the eye is still."""
    deadline = time.time() + 20
    prev = pg.evaluate("__holo.eye()")
    while time.time() < deadline:
        pg.wait_for_timeout(250)
        cur = pg.evaluate("__holo.eye()")
        if max(abs(a - b) for a, b in zip(cur, prev)) < 2e-3:
            return
        prev = cur
    raise AssertionError("the eye never came to rest for %s" % what)


def _unit_at(pg, octaves, samples=3):
    _settled(pg, octaves)
    vals, at = [], None
    for _ in range(samples):
        m = pg.evaluate("__holo.measure()")
        assert m, "nothing to measure"
        vals.append(m["unit"])
        at = m["at"]
        pg.wait_for_timeout(60)
    return sum(vals) / len(vals), at


def _ratio(pg, octaves):
    """Leaned by `octaves`, how much nearer the eye the structure's centre is
    than at rest, and where the structure stood each time. Rest is sampled on
    both sides of the lean."""
    _settled(pg, 0)
    _eye_still(pg, "a held head at rest")
    rest, at0 = _unit_at(pg, 0)
    leaned, at1 = _unit_at(pg, octaves)
    rest2, at2 = _unit_at(pg, 0)
    return leaned / ((rest + rest2) / 2), [at0, at1, at2]


def _each_structure(pg, label):
    """For every structure: how much nearer its centre comes leaned in by one
    octave, and that the structure itself stays where it is, leaned or moved
    aside. Returns what is wrong."""
    ids = pg.evaluate("__holo.structures()")
    assert len(ids) == STRUCTURE_COUNT, ids
    wrong = []
    for i, sid in enumerate(ids):
        pg.evaluate("__holo.show(%d)" % i)
        _until(pg, "() => fridayDebugScene().targetStructure === %r && __holo.measure() !== null" % sid,
               "%s never became the active structure" % sid)
        ratio, ats = _ratio(pg, 1)
        pg.evaluate("__holo.aside(0.6)")
        _until(pg, "() => __holo.x() > 0.5", "the head never moved sideways")
        ats.append(pg.evaluate("__holo.measure()")["at"])
        # zoom_in_max is 1.8: the eye comes from the glass's distance to 1/1.8
        # of it, and the glass stands GLASS_AT of the way to the look target,
        # so a centre at the look target comes 1 / (1 - 0.62 * (1 - 1/1.8)),
        # about 1.38x, nearer. A centre in front of its look target comes
        # nearer still, one behind it less. A structure that ignored the lean
        # would sit at 1.0 and the gentle reduced-motion lean at about 1.07.
        if not 1.2 <= ratio <= 3:
            wrong.append("%s: %.2fx nearer (%s)" % (sid, ratio, label))
        if any(max(abs(a - b) for a, b in zip(at, ats[0])) > 1e-9 for at in ats):
            wrong.append("%s moved with the head: %s (%s)" % (sid, ats, label))
    pg.evaluate("__holo.release()")
    return wrong


def test_every_structure_comes_closer_when_you_lean_in_and_stays_put(page):
    wrong = _each_structure(page, "v1 look")
    assert not wrong, "leaning in one octave should bring each structure about 1.4x nearer, and none may move:\n  " \
        + "\n  ".join(wrong)


def test_every_structure_still_answers_after_a_genome_step(page):
    n = page.evaluate("__holo.genomeStep('sha256:window-test')")
    assert n == STRUCTURE_COUNT
    wrong = _each_structure(page, "after a genome step")
    assert not wrong, "after the rebuild these no longer answer to the window:\n  " + "\n  ".join(wrong)


def test_leaning_back_makes_it_recede(page):
    page.evaluate("__holo.show(1)")                              # the sphere, centred on its look target
    ratio, _ = _ratio(page, -1)
    page.evaluate("__holo.release()")
    assert 0.68 <= ratio <= 0.84, ratio                          # 1 / (1 + 0.62 * 0.5) = 0.76


def test_with_no_face_the_scene_is_the_plain_camera(page):
    page.evaluate("__holo.release()")
    _until(page, "() => Math.abs(__holo.z()) < 0.01 && Math.abs(__holo.x()) < 0.01",
           "the head never relaxed to neutral")
    page.wait_for_timeout(150)
    proj, plain = page.evaluate("__holo.projection()"), page.evaluate("__holo.plain()")
    assert all(abs(a - b) < 1e-6 for a, b in zip(proj, plain)), "the projection is not the plain camera at rest"


def test_moving_sideways_shears_the_window(page):
    page.evaluate("__holo.aside(0.8)")
    _until(page, "() => __holo.x() > 0.7", "the head never moved sideways")
    page.wait_for_timeout(100)
    proj = page.evaluate("__holo.projection()")
    page.evaluate("__holo.release()")
    assert abs(proj[8]) > 0.05, "no off-axis shear with the head to one side"


def test_reduced_motion_keeps_a_gentle_lean_and_no_sideways_shear(page):
    page.emulate_media(reduced_motion="reduce")
    try:
        page.evaluate("__holo.show(1)")
        ratio, _ = _ratio(page, 1)
        assert 1.03 <= ratio <= 1.12, ratio                      # GENTLE_ZOOM 1.12: about 1.07
        page.evaluate("__holo.aside(0.8)")
        _until(page, "() => __holo.x() > 0.7", "the head never moved sideways")
        page.wait_for_timeout(100)
        proj = page.evaluate("__holo.projection()")
        assert abs(proj[8]) < 1e-6, "reduced motion still shears the window sideways"
    finally:
        page.evaluate("__holo.release()")
        page.emulate_media(reduced_motion="no-preference")


def test_the_action_bus_applies_a_spoken_change_live(page):
    res = page.evaluate("""() => {
        const a = { type: 'tracking', tracking: { zoom_in_max: 1.25 } };
        fridayRunActions([a]);
        return { cfg: FridayTracking.cfg.zoom_in_max, result: a.result };
    }""")
    try:
        assert res["cfg"] == 1.25 and res["result"]["tracking"]["zoom_in_max"] == 1.25
        page.evaluate("__holo.show(1)")
        ratio, _ = _ratio(page, 1)
        assert 1.08 <= ratio <= 1.22, ratio                      # zoom_in_max 1.25: about 1.14
        # No face on the camera: a calibrate says so rather than storing a stale width.
        cal = page.evaluate("""() => { const a = { type: 'tracking', op: 'calibrate' };
                                     fridayRunActions([a]); return a.result; }""")
        assert cal == {"calibrated": None}
    finally:
        page.evaluate("__holo.release()")
        page.evaluate("fridayRunActions([{ type: 'tracking', tracking: { zoom_in_max: 1.8 } }])")
