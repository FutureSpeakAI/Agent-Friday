"""Synthetic camera detections reach Simple's final avatar projection.

The MediaPipe and camera boundaries are doubles. The served detection adapter,
tracking filter, camera/frustum, stage fit and renderer are the real page.
These regressions are not evidence of a physical camera or user comfort.
"""
import functools
import http.server
import json
import math
import os
import pathlib
import socketserver
import threading
import time

import pytest


REPO = pathlib.Path(__file__).resolve().parents[2]
SEED = {"parallax_strength": .1, "depth_strength": .7, "head_smoothing": .7,
        "head_response": .2, "neutral_face_width": .24, "zoom_in_max": 1.8,
        "zoom_out_max": 1.5, "hand_gain": 3.4}


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


CAMERA = r"""
window.__FRIDAY_MEDIAPIPE_SCRIPTS__ = [];
window.__faceInput = {width:.24, present:true, calls:0, deviceRequests:0};
window.FaceDetection = class {
  setOptions() {}
  onResults(callback) { this.callback = callback; }
  async send() {
    const f = window.__faceInput;
    f.calls++;
    this.callback({detections:f.present ? [{boundingBox:{
      xCenter:.5, yCenter:.5, width:f.width, height:f.width
    }}] : []});
  }
  async close() {}
};
const canvas = document.createElement('canvas');
canvas.width = 320; canvas.height = 240;
const paint = canvas.getContext('2d');
setInterval(() => { paint.fillStyle = '#445566'; paint.fillRect(0,0,320,240); },33);
navigator.mediaDevices.getUserMedia = async () => {
  __faceInput.deviceRequests++;
  const stream = canvas.captureStream(30), track = stream.getVideoTracks()[0];
  const readSettings = track.getSettings.bind(track);
  track.getSettings = () => ({...readSettings(),deviceId:'synthetic-lean-camera'});
  return stream;
};
// Keep the media clock deterministic on software-rendered Chromium. Tracks
// are real canvas tracks; the native camera manager still owns their lifetime.
let cameraTime = 0;
Object.defineProperty(videoElement,'readyState',{configurable:true,get:()=>videoElement.srcObject ? 4 : 0});
Object.defineProperty(videoElement,'currentTime',{configurable:true,
  get:()=>videoElement.srcObject ? (cameraTime += 1/60) : 0,set:()=>{}});
videoElement.play = () => Promise.resolve();
window.__leanSample = () => {
  const scene = fridayDebugScene(), camera = scene.camera;
  const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,0);
  const a = right.clone().multiplyScalar(-.5).project(camera);
  const b = right.clone().multiplyScalar(.5).project(camera);
  const state = FridayHolographicWorkspace.state;
  return {frame:__fridayRenderer.info.render.frame,
    unit:Math.hypot((b.x-a.x)*innerWidth/2,(b.y-a.y)*innerHeight/2),
    eye:camera.position.toArray(), bounds:state.projectedAvatarBounds,
    stage:state.spatial.stage, raw:FridayTracking.head.raw.z,
    z:FridayTracking.head.z, zoom:FridayTracking.head.zoom,
    debug:FridayTracking.head.debug, seen:FridayTracking.head.seen,
    face:fridayVibe.getHologramState().faceVisible, calls:__faceInput.calls};
};
"""


@pytest.fixture(scope="module", params=[(1600, 1000), (390, 844)], ids=["wide", "portrait"])
def scene(request):
    sync_api = pytest.importorskip("playwright.sync_api")
    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    saved, writes = {"tracking": dict(SEED)}, []
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                               "--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport=dict(zip(("width", "height"), request.param)),
                                    reduced_motion="no-preference")
            page.add_init_script("localStorage.setItem('friday.display-style.v1','simple')")

            def settings(route):
                if route.request.method == "POST":
                    body = route.request.post_data_json
                    writes.append(body)
                    patch = body.get("settings", {}).get("tracking", {})
                    saved["tracking"].update(patch)
                route.fulfill(json={"status": "ok", "settings": saved})

            page.route("**/api/settings**", settings)
            page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/index.html",
                      wait_until="domcontentloaded")
            page.wait_for_function("() => window.fridayDebugScene?.().camera && window.FridayTracking"
                                   " && window.FridayHolographicWorkspace", timeout=60000)
            page.evaluate(CAMERA)
            page.evaluate("""async () => {
              FridayDisplayStyle.set('simple');
              FridayHolographicWorkspace.setArrangement('companion');
              FridayHolographicWorkspace.setMode('balanced');
              fridayVibe.setStructure(fridayVibe.getStructures().findIndex(s=>s.id==='WORMHOLE'));
              await fridayVibe.setHologram(true,'lean-regression');
            }""")
            page.wait_for_function("() => fridayDebugScene().targetStructure==='WORMHOLE'"
                                   " && fridayDebugScene().transitionProgress>=.999"
                                   " && FridayCamera.state.status==='live'"
                                   " && FridayTracking.head.seen", timeout=60000)
            # Hold the native dock open before calibration and every lean
            # comparison. Its 30-second idle hide must not resize the stage
            # between the two neutral measurements.
            page.mouse.move(1, request.param[1] - 1)
            page.wait_for_function("""() => {
              const dock = document.querySelector('.dock');
              if (!dock || dock.classList.contains('hidden')) return false;
              const style = getComputedStyle(dock);
              if (style.display === 'none') return true; // Responsive Simple chrome.
              const rect = dock.getBoundingClientRect();
              return rect.height > 0 && rect.top < innerHeight && style.transform === 'none'
                && !dock.getAnimations().some(a => a instanceof CSSTransition
                  && a.effect?.target === dock && a.transitionProperty === 'transform'
                  && (a.pending || a.playState === 'running'));
            }""", timeout=10000)
            assert page.evaluate("FridayHolographicWorkspace.cameraCalibration.calibrateCurrent().ok")
            yield page, saved, writes
            page.evaluate("() => fridayVibe.setHologram(false,'lean-regression')")
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _hold(page, width):
    before = page.evaluate("__faceInput.calls")
    page.evaluate("w => { __faceInput.width=w; __faceInput.present=true; }", width)
    base = page.evaluate("FridayTracking.head.baseline || .18")
    target = math.log2(max(.35, min(2.5, width / base)))
    page.wait_for_function("([before,z]) => __faceInput.calls>before && !FridayTracking.head.debug"
                           " && Math.abs(FridayTracking.head.raw.z-z)<.001"
                           " && Math.abs(FridayTracking.head.z-z)<.015",
                           arg=[before, target], timeout=20000)
    return _settled(page)


def _settled(page):
    """A held detector input must settle across fresh rendered frames."""
    previous = page.evaluate("__leanSample()")
    deadline, anchor, stable_since, samples = time.monotonic() + 45, previous, None, []
    while time.monotonic() < deadline:
        page.wait_for_function("f => __fridayRenderer.info.render.frame>f",
                               arg=previous["frame"], timeout=10000)
        current = page.evaluate("__leanSample()")
        relative = abs(current["unit"] / anchor["unit"] - 1)
        eye_delta = max(abs(a - b) for a, b in zip(current["eye"], anchor["eye"]))
        if relative < .002 and eye_delta < .005:
            stable_since = stable_since or time.monotonic()
            samples.append(current)
            if len(samples) >= 3 and time.monotonic() - stable_since >= .25:
                current["unit"] = sum(s["unit"] for s in samples) / len(samples)
                return current
        else:
            anchor, stable_since, samples = current, None, []
        previous = current
    raise AssertionError(f"The final projected avatar did not settle: {previous}")


def _inside(sample):
    bounds, stage = sample["bounds"], sample["stage"]
    assert bounds and stage
    assert bounds["x"] >= stage["x"] - .5
    assert bounds["y"] >= stage["y"] - .5
    assert bounds["x"] + bounds["w"] <= stage["x"] + stage["w"] + .5
    assert bounds["y"] + bounds["h"] <= stage["y"] + stage["h"] + .5


def _reset(page):
    page.emulate_media(reduced_motion="no-preference")
    page.evaluate("s => { FridayDisplayStyle.set('simple'); FridayTracking.debugHead(0,0,null);"
                  " FridayTracking.apply(s); }", SEED)


def test_calibrated_camera_lean_survives_the_final_simple_fit(scene):
    page, _, _ = scene
    proofs = None
    if output := os.environ.get("FRIDAY_SIMPLE_LEAN_PROOFS"):
        proofs = pathlib.Path(output)
        if not proofs.is_absolute():
            raise ValueError("FRIDAY_SIMPLE_LEAN_PROOFS must be an absolute private output directory")
        proofs = proofs.resolve(strict=True)
        if not proofs.is_dir() or proofs.is_relative_to(REPO):
            raise ValueError("FRIDAY_SIMPLE_LEAN_PROOFS must be an existing directory outside the checkout")
        viewport = page.viewport_size
        prefix = f"simple-lean-{viewport['width']}x{viewport['height']}"
    _reset(page)
    rest = _hold(page, .24)
    if proofs is not None:
        with (proofs / f"{prefix}-neutral.png").open("xb") as frame:
            frame.write(page.screenshot())
    moderate = _hold(page, .30)  # Twenty percent closer, with the reported comfortable dials.
    near = _hold(page, .36)
    if proofs is not None:
        with (proofs / f"{prefix}-near.png").open("xb") as frame:
            frame.write(page.screenshot())
    rest_again = _hold(page, .24)
    far = _hold(page, .16)
    if proofs is not None:
        with (proofs / f"{prefix}-far.png").open("xb") as frame:
            frame.write(page.screenshot())
    maximum = _hold(page, .60)
    if proofs is not None:
        with (proofs / f"{prefix}-maximum.png").open("xb") as frame:
            frame.write(page.screenshot())
        with (proofs / f"{prefix}.json").open("x", encoding="utf-8") as metadata:
            json.dump({"evidence": "Synthetic camera; sampled states precede assertions.",
                       "viewport": viewport,
                       "states": {"neutral": rest, "moderate": moderate, "near": near,
                                  "neutral_again": rest_again, "far": far, "maximum": maximum}},
                      metadata, indent=2)
    for sample in (rest, moderate, near, rest_again, far, maximum):
        assert sample["debug"] is None
        _inside(sample)
        assert sample["stage"] == rest["stage"], (rest["stage"], sample["stage"])
    neutral = (rest["unit"] + rest_again["unit"]) / 2
    assert moderate["unit"] / neutral >= 1.02, (rest, moderate)
    assert near["unit"] / neutral >= 1.05, (rest, near)
    assert near["unit"] >= moderate["unit"] * .995, (moderate, near)
    assert far["unit"] / neutral <= .97, (rest, far)
    assert abs(rest_again["unit"] / rest["unit"] - 1) < .015
    # Headroom is inside the final inset, including the narrow portrait stage.
    stage, bounds = rest["stage"], rest["bounds"]
    pad = min(12, stage["w"] / 8, stage["h"] / 8)
    fill = max(bounds["w"] / (stage["w"] - 2 * pad), bounds["h"] / (stage["h"] - 2 * pad))
    assert .82 <= fill <= .92, (stage, bounds, fill)


def test_depth_off_and_face_loss_return_to_a_stable_neutral_view(scene):
    page, _, _ = scene
    _reset(page)
    page.evaluate("FridayTracking.apply({depth_strength:0})")
    rest, near, far = (_hold(page, width) for width in (.24, .36, .16))
    assert abs(near["unit"] / rest["unit"] - 1) < .015
    assert abs(far["unit"] / rest["unit"] - 1) < .015
    page.evaluate("FridayTracking.apply({depth_strength:.7})")
    rest = _hold(page, .24)
    _hold(page, .36)
    page.evaluate("__faceInput.present=false")
    page.wait_for_function("() => !fridayVibe.getHologramState().faceVisible"
                           " && !FridayTracking.head.seen && Math.abs(FridayTracking.head.z)<.01")
    lost = _settled(page)
    assert abs(lost["unit"] / rest["unit"] - 1) < .015
    restored = _hold(page, .24)
    assert abs(restored["unit"] / rest["unit"] - 1) < .015
    assert page.evaluate("FridayTracking.get().neutral_face_width") == .24


def test_classic_and_reduced_motion_keep_their_existing_depth_limits(scene):
    page, _, _ = scene
    _reset(page)
    page.evaluate("FridayDisplayStyle.set('classic')")
    rest, near, far = (_hold(page, width) for width in (.24, .30, .16))
    assert rest["stage"] is None
    assert near["unit"] / rest["unit"] >= 1.02
    assert far["unit"] / rest["unit"] <= .97
    page.evaluate("FridayDisplayStyle.set('simple')")
    page.emulate_media(reduced_motion="reduce")
    rest, near, far = (_hold(page, width) for width in (.24, .60, .084))
    for sample in (rest, near, far):
        _inside(sample)
        assert 1 / 1.12 <= sample["zoom"] <= 1.12
    assert .9 <= far["unit"] / rest["unit"] <= 1.015
    assert .985 <= near["unit"] / rest["unit"] <= 1.1


def test_camera_off_debug_lean_keeps_the_existing_geometry_safety_envelope(scene):
    page, _, _ = scene
    _reset(page)
    viewport = dict(page.viewport_size)
    try:
        page.set_viewport_size({"width": 1200, "height": 440})
        page.evaluate("() => fridayVibe.setHologram(false,'lean-regression')")
        page.evaluate("""() => {
          FridayTracking.apply({depth_strength:2,zoom_in_max:2.5});
          FridayTracking.debugHead(0,0,.60);
        }""")
        page.wait_for_function("() => FridayTracking.head.zoom>2.49")
        sample = _settled(page)
        _inside(sample)
        assert math.isfinite(sample["unit"]) and sample["unit"] > 0
        assert not page.evaluate("fridayVibe.isHologramOn()")
    finally:
        page.evaluate("FridayTracking.debugHead(0,0,null)")
        page.set_viewport_size(viewport)
        _reset(page)
        page.evaluate("() => fridayVibe.setHologram(true,'lean-regression')")
        _hold(page, .24)


def test_simple_calibration_uses_the_current_detector_and_preserves_other_preferences(scene):
    page, saved, writes = scene
    _reset(page)
    # An uncalibrated real-sized face need not be the old .18 test constant.
    page.evaluate("FridayHolographicWorkspace.cameraCalibration.resetCurrent()")
    _hold(page, .31)
    page.evaluate("FridayHolographicWorkspace.open()")
    dialog = page.get_by_role("dialog", name="Scene & depth", exact=True)
    button = dialog.get_by_role("button", name="Calibrate distance", exact=True)
    before = len(writes)
    button.click()
    page.wait_for_function("() => FridayTracking.head.baseline===.31")
    page.wait_for_function("() => FridayHolographicWorkspace.cameraCalibration.state.saved")
    assert writes[before:] == []
    assert saved["tracking"]["neutral_face_width"] == SEED["neutral_face_width"]
    binding = page.evaluate("JSON.parse(localStorage.getItem('friday_camera_calibration_v1')).cameras")
    assert binding == [{"id": "synthetic-lean-camera", "width": .31}]
    assert page.evaluate("FridayTracking.get().hand_gain") == SEED["hand_gain"]
    assert abs(_hold(page, .31)["z"]) < .015
    page.evaluate("__faceInput.present=false")
    page.wait_for_function("() => !fridayVibe.getHologramState().faceVisible")
    before = len(writes)
    button.click()
    assert "No fresh face seen" in dialog.locator("[data-holo-tracking-status]").inner_text()
    assert page.evaluate("FridayTracking.head.baseline") == .31
    assert len(writes) == before
    _hold(page, .31)
    page.evaluate("FridayTracking.debugHead(0,0,.31)")
    button.click()
    assert "No fresh face seen" in dialog.locator("[data-holo-tracking-status]").inner_text()
    assert page.evaluate("FridayTracking.head.baseline") == .31
    assert len(writes) == before
    page.evaluate("FridayTracking.debugHead(0,0,null)")
    dialog.get_by_role("button", name="Close Scene and depth", exact=True).click()
