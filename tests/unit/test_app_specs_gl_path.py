"""The app specs do not composite every page through the SwiftShader GL emulator.

playwright.app.config.ts forced --use-gl=angle --use-angle=swiftshader on every spec so the vision judge
saw the holographic scene drawn properly. On the Windows CI runner (no GPU) that kept Chromium's GPU
process at 3-3.5 of 4 cores for the whole run (CPU sampled by process), every spec crawled, and
connect_services.spec.ts, the specs on the desktop page, ran past its 180 s budget on about half the
runs. With the default path the same specs take 4-11 s each, and the GPU process uses about 1.5 cores.
Only the specs that judge the scene by eye opt into the GL path.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
FORCED = ("--use-gl=angle", "--use-angle=swiftshader")


def _default_launch_args() -> str:
    src = (ROOT / "playwright.app.config.ts").read_text(encoding="utf-8")
    m = re.search(r"launchOptions:\s*\{\s*args:\s*\[([^\]]*)\]", src)
    assert m, "playwright.app.config.ts sets no launch args"
    return m.group(1)


def test_the_suite_default_does_not_force_the_gl_emulator():
    args = _default_launch_args()
    for flag in FORCED:
        assert flag not in args, "every spec would composite through SwiftShader GL: %s" % flag
    assert "--enable-unsafe-swiftshader" in args, "WebGL must still be available to the scene"


def test_the_specs_that_judge_the_scene_opt_in():
    fixtures = (ROOT / "tests" / "app" / "fixtures.ts").read_text(encoding="utf-8")
    m = re.search(r"export const SCENE_GL_ARGS = \[([^\]]*)\]", fixtures)
    assert m and all(flag in m.group(1) for flag in FORCED)
    vision = (ROOT / "tests" / "app" / "specs" / "vision.spec.ts").read_text(encoding="utf-8")
    assert "test.use({ launchOptions: { args: SCENE_GL_ARGS } })" in vision
