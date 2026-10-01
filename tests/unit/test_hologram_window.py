"""The hologram window: with face tracking on, the screen is a window onto the
avatar. Leaning in brings it closer and bigger, leaning back makes it recede,
and moving sideways is a shear anchored at the glass, so what sits at the
glass stays put. It is done at the camera and projection level, so every
structure answers to it at once and a structure that evolves cannot lose it.

Two halves. The engine's maths runs under node, lifted verbatim from
index.html with the vendored three.js: the zoom ratio is exact, the glass
point is invariant, the clamps hold, reduced motion keeps only a gentle zoom,
and a lost face eases back rather than snapping. Then a real browser loads the
served page and, for each of the thirteen structures, measures how big a unit
at the structure's centre draws on screen at rest and leaned in, before and
again after a genome step has rebuilt every structure. A structure whose size
on screen does not follow the zoom fails the test.
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
node = shutil.which("node")
BLOCK = re.compile(r"// <tracking-engine>\n(.*?)// </tracking-engine>", re.S)

# What the browser half is calibrated to: a face twice the neutral width is
# one octave nearer, so at depth 1 the zoom would be 2, capped by zoom_in_max.
NEUTRAL_W = 0.18
STRUCTURE_COUNT = 13


# ── the engine under node ─────────────────────────────────────────────────────

NODE_HARNESS = r"""
const THREE = require(process.argv[2]);   // node - <three.js path>
globalThis.window = globalThis;
globalThis.innerWidth = 1600; globalThis.innerHeight = 900;
globalThis.document = { body: { classList: { toggle() {} } },
                        documentElement: { style: { setProperty() {} } } };
let reduced = false;
globalThis.matchMedia = () => ({ matches: reduced });
globalThis.dockDepthK = () => 1; globalThis.resetDockVars = () => {};
BLOCK
const TK = window.FridayTracking;
const cam = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
const S = 20;
function glass(zoom, ex, ey) {
  const ez = TK.eyeDolly(S, zoom);
  cam.position.set(ex, ey, S - ez); cam.updateMatrixWorld();
  TK.applyFrustum(cam, ex, ey, ez, S, zoom);
  const a = new THREE.Vector3(-1, 0, 0).project(cam), b = new THREE.Vector3(1, 0, 0).project(cam);
  const c = new THREE.Vector3(0, 0, 0).project(cam);
  const behind = new THREE.Vector3(0, 0, -10).project(cam), front = new THREE.Vector3(0, 0, 5).project(cam);
  return { w: b.x - a.x, cx: c.x, cy: c.y, behind: behind.x, front: front.x };
}
const out = {};
const base = glass(1, 0, 0);
out.ratio_in = glass(1.8, 0, 0).w / base.w;
out.ratio_out = glass(1 / 1.5, 0, 0).w / base.w;
const sheared = glass(1.3, 2.5, -1.5);
out.glass_point_stays = Math.abs(sheared.cx) < 1e-9 && Math.abs(sheared.cy) < 1e-9;
out.parallax = sheared.behind > 0 && sheared.front < 0;   // far follows the eye, near moves against it
out.plain_when_neutral = (() => {
  glass(1, 0, 0);
  const plain = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
  return cam.projectionMatrix.elements.every((v, i) => Math.abs(v - plain.projectionMatrix.elements[i]) < 1e-9);
})();
TK.apply({ depth_strength: 1, zoom_in_max: 1.8, zoom_out_max: 1.5 });
out.zoom_octave = TK.headZoom(1); out.zoom_back = TK.headZoom(-1); out.zoom_half = TK.headZoom(0.5);
TK.apply({ depth_strength: 0 }); out.zoom_off = TK.headZoom(1);
TK.apply({ depth_strength: 1, zoom_in_max: 9, zoom_out_max: 9 });
out.zoom_hard_cap = TK.headZoom(5); out.zoom_nan = TK.headZoom(NaN);
out.dolly_floor = (S - TK.eyeDolly(S, TK.headZoom(5))) / S;
reduced = true;
out.reduced_in = TK.headZoom(3); out.reduced_out = TK.headZoom(-3); out.reduced_lateral = TK.lateralGain();
reduced = false;
out.lateral = TK.lateralGain();
TK.apply({ head_smoothing: 0.35, head_response: 0.5, neutral_face_width: 0.18 });
let t = 0; for (let i = 0; i < 60; i++) { t += 1 / 60; TK.pushFace(0, 0, 0.36, t); }
const zLean = TK.head.z, zs = [];
for (let i = 0; i < 30; i++) { t += 1 / 60; TK.relaxToNeutral(t); zs.push(TK.head.z); }
out.lean_z = zLean; out.relax_first = zs[0]; out.relax_last = zs[zs.length - 1];
out.relax_monotone = zs.every((v, i) => i === 0 || v <= zs[i - 1] + 1e-12);
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
    r = subprocess.run([node, "-", three], input=NODE_HARNESS.replace("BLOCK", m.group(1)),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_point_at_the_glass_grows_by_exactly_the_zoom(engine):
    assert engine["ratio_in"] == pytest.approx(1.8, abs=1e-6)
    assert engine["ratio_out"] == pytest.approx(1 / 1.5, abs=1e-6)


def test_sideways_is_a_window_not_an_orbit(engine):
    """What sits at the glass stays put; what is behind follows the eye and
    what is in front moves against it, which is what a window does."""
    assert engine["glass_point_stays"]
    assert engine["parallax"]


def test_with_the_head_at_rest_the_projection_is_the_plain_camera(engine):
    assert engine["plain_when_neutral"]


def test_the_zoom_follows_the_lean_and_is_bounded(engine):
    assert engine["zoom_octave"] == pytest.approx(1.8)          # 2, capped by zoom_in_max
    assert engine["zoom_back"] == pytest.approx(1 / 1.5)        # 0.5, capped by zoom_out_max
    assert engine["zoom_half"] == pytest.approx(2 ** 0.5)
    assert engine["zoom_off"] == 1
    assert engine["zoom_nan"] == 1


def test_the_eye_can_never_reach_the_avatar_whatever_the_dials_say(engine):
    assert engine["zoom_hard_cap"] == pytest.approx(2.5)
    assert engine["dolly_floor"] == pytest.approx(0.4)


def test_reduced_motion_keeps_only_a_gentle_zoom(engine):
    assert engine["reduced_in"] == pytest.approx(1.12)
    assert engine["reduced_out"] == pytest.approx(1 / 1.12)
    assert engine["reduced_lateral"] == 0
    assert engine["lateral"] == 1


def test_a_lost_face_eases_back_instead_of_snapping(engine):
    assert engine["lean_z"] == pytest.approx(1.0, abs=0.02)
    assert engine["relax_monotone"]
    assert engine["relax_first"] > 0.6 * engine["lean_z"], "the first relaxed frame jumped"
    assert engine["relax_last"] < 0.25 * engine["lean_z"], "half a second later it is still leaned in"


def test_calibration_is_a_setting(engine):
    assert engine["calibrated"] == 0.25 and engine["cfg_neutral"] == 0.25
    assert engine["uncalibrated_baseline"] is None


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
  // How big one world unit at the active structure's centre draws on screen,
  // in NDC. Normalised by the bounding sphere's radius so a structure's own
  // breathing and transition scale cancel out; only the camera is left.
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
    const right = new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld, 0).normalize();
    const a = sph.center.clone().addScaledVector(right, -sph.radius).project(cam);
    const b = sph.center.clone().addScaledVector(right, sph.radius).project(cam);
    return { unit: Math.abs(b.x - a.x) / (2 * sph.radius), structure: s.targetStructure,
             zoom: FridayTracking.head.zoom, z: FridayTracking.head.z };
  },
  // A synthetic head, held until released: no webcam in a test.
  lean(octaves) { FridayTracking.debugHead(0, 0, 0.18 * Math.pow(2, octaves)); },
  aside(x) { FridayTracking.debugHead(x, 0, 0.18); },
  release() { FridayTracking.debugHead(0, 0, 0); },
  z() { return FridayTracking.head.z; },
  x() { return FridayTracking.head.x; },
  projection() { return Array.from(fridayDebugScene().camera.projectionMatrix.elements); },
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


def _unit_at(pg, octaves, samples=3):
    _settled(pg, octaves)
    vals = []
    for _ in range(samples):
        m = pg.evaluate("__holo.measure()")
        assert m, "nothing to measure"
        vals.append(m["unit"])
        pg.wait_for_timeout(60)
    return sum(vals) / len(vals)


def _ratio(pg, octaves):
    """Leaned by `octaves`, how much bigger a unit at the structure's centre
    draws than at rest. Rest is sampled on both sides of the lean so the
    cinematic camera's own glide between samples cancels out."""
    rest = _unit_at(pg, 0)
    leaned = _unit_at(pg, octaves)
    rest2 = _unit_at(pg, 0)
    return leaned / ((rest + rest2) / 2)


def _each_structure(pg, label):
    """For every structure: on-screen size per world unit at rest and leaned
    in by one octave. Returns the ratios that are wrong."""
    ids = pg.evaluate("__holo.structures()")
    assert len(ids) == STRUCTURE_COUNT, ids
    wrong = []
    for i, sid in enumerate(ids):
        pg.evaluate("__holo.show(%d)" % i)
        _until(pg, "() => fridayDebugScene().targetStructure === %r && __holo.measure() !== null" % sid,
               "%s never became the active structure" % sid)
        ratio = _ratio(pg, 1)
        # zoom_in_max is 1.8, and that is exact for what sits AT the glass
        # (the node half proves it). The glass is the cinematic look target,
        # and a structure whose centre sits a fraction p of the way to it
        # grows by p / (p - 1 + 1 / zoom) under the dolly: nearer grows
        # more, farther less, which is the depth the effect is for. The
        # cathedral's centre sits nearer than its look target (about 2.2x),
        # and the Mandelbrot set's camera orbits it, so its centre can be
        # well inside the glass at some phases (up to about 4x); a centre
        # halfway to the glass would reach 9x, and the eye can go no nearer.
        # A structure that ignores the camera sits at 1.0, as does the old
        # anchored window, and the gentle reduced-motion zoom at 1.12: all
        # well below this band.
        if not 1.35 <= ratio <= 9:
            wrong.append("%s: %.2fx (%s)" % (sid, ratio, label))
    pg.evaluate("__holo.release()")
    return wrong


def test_every_structure_comes_closer_when_you_lean_in(page):
    wrong = _each_structure(page, "v1 look")
    assert not wrong, "leaning in one octave should draw these 1.8x bigger:\n  " + "\n  ".join(wrong)


def test_every_structure_still_answers_after_a_genome_step(page):
    n = page.evaluate("__holo.genomeStep('sha256:window-test')")
    assert n == STRUCTURE_COUNT
    wrong = _each_structure(page, "after a genome step")
    assert not wrong, "after the rebuild these no longer follow the zoom:\n  " + "\n  ".join(wrong)


def test_leaning_back_makes_it_recede(page):
    page.evaluate("__holo.show(1)")                              # the sphere, centred on its look target
    ratio = _ratio(page, -1)
    page.evaluate("__holo.release()")
    assert 0.55 <= ratio <= 0.8, ratio                           # 1 / zoom_out_max = 0.667


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


def test_reduced_motion_keeps_a_gentle_zoom_and_no_sideways_shear(page):
    page.emulate_media(reduced_motion="reduce")
    try:
        page.evaluate("__holo.show(1)")
        ratio = _ratio(page, 1)
        assert 1.04 <= ratio <= 1.2, ratio                       # GENTLE_ZOOM 1.12
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
        ratio = _ratio(page, 1)
        assert 1.1 <= ratio <= 1.4, ratio                        # zoom_in_max now 1.25
        # No face on the camera: a calibrate says so rather than storing a stale width.
        cal = page.evaluate("""() => { const a = { type: 'tracking', op: 'calibrate' };
                                     fridayRunActions([a]); return a.result; }""")
        assert cal == {"calibrated": None}
    finally:
        page.evaluate("__holo.release()")
        page.evaluate("fridayRunActions([{ type: 'tracking', tracking: { zoom_in_max: 1.8 } }])")
