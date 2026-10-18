"""The app specs do not composite every page through the SwiftShader GL emulator by default.

playwright.app.config.ts forced --use-gl=angle --use-angle=swiftshader on every spec so the vision judge
saw the holographic scene drawn properly. On the Windows CI runner (no GPU) that kept Chromium's GPU
process at 3-3.5 of 4 cores (CPU sampled by process), and connect_services.spec.ts, the specs on the
desktop page, ran past its 180 s budget on about half the runs. On the default path they take well
inside it. Two specs opt into the GL path, each for a measured reason: vision.spec.ts judges the scene
by eye, and settings_walk.spec.ts measured faster on it (16.6-19 min against 23-35 min).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
FORCED = ("--use-gl=angle", "--use-angle=swiftshader")
OPT_IN = "test.use({ launchOptions: { args: GL_EMULATOR_ARGS } })"


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


def test_only_the_measured_specs_opt_in():
    fixtures = (ROOT / "tests" / "app" / "fixtures.ts").read_text(encoding="utf-8")
    m = re.search(r"export const GL_EMULATOR_ARGS = \[([^\]]*)\]", fixtures)
    assert m and all(flag in m.group(1) for flag in FORCED)
    specs = ROOT / "tests" / "app" / "specs"
    opted = sorted(p.name for p in specs.glob("*.spec.ts") if OPT_IN in p.read_text(encoding="utf-8"))
    assert opted == ["settings_walk.spec.ts", "vision.spec.ts"], opted
    assert "connect_services.spec.ts" not in opted
