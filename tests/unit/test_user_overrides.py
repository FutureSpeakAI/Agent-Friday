"""Local fixes survive an update: ~/.friday/overrides, and the update guard.

A local model registered with an 8k context was fixed by hand in the generated
registry (~/.friday/runtime/models/models.json): 32k context, every layer on
the GPU. The next registration rewrote the file and the fix was gone. Fixes to
installed files went the same way on the next install, without a word.

These tests hold the replacement:
  * a per-model override in the overrides folder is merged over the registry
    on read, reaches the llama-server command line, and survives the registry
    being regenerated, without ever being written into it;
  * an override may only say allow-listed things; anything else is refused
    and logged, and the rest of the entry still applies;
  * the update guard finds an installed file that differs from the shipped
    manifest (and one the release never shipped), backs both up under
    overrides/replaced-<version>/, and reports them through the in-app
    update check.

Fixtures are synthetic: made-up model ids, a temp registry, a temp app folder.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess

import pytest

from agent_friday.services import model_store as ms
from agent_friday.services import residency_arbiter as ra

MID = "example-27b:test"
HELPER = "example-1.7b:test"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A registry and an overrides folder under tmp_path, defaults settings."""
    from agent_friday import core
    reg = tmp_path / "runtime" / "models" / "models.json"
    ovr = tmp_path / "overrides"
    monkeypatch.setattr(ms, "registry_path", lambda: reg)
    monkeypatch.setenv("FRIDAY_OVERRIDES_DIR", str(ovr))
    monkeypatch.setattr(core, "_load_settings", lambda: {}, raising=False)
    try:
        from agent_friday.services import user_overrides as uo
        uo.reset_cache()
    except ImportError:
        pass
    return {"registry": reg, "overrides": ovr}


def _write_registry(models: dict) -> None:
    """What `model_store.register` leaves behind, written the same way."""
    ms._save({"version": ms.REGISTRY_VERSION, "models": models})


def _generated(ctx=8192, ngl=56) -> dict:
    return {
        MID: {"model_id": MID, "path": "/nowhere/example-27b.gguf",
              "can_generate": True, "serve_num_ctx": ctx,
              "serve_args": ["-ngl", str(ngl), "-np", "1",
                             "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]},
        HELPER: {"model_id": HELPER, "path": "/nowhere/example-1.7b.gguf",
                 "can_generate": True},
    }


def _write_overrides(root, data) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "models.json").write_text(json.dumps(data), encoding="utf-8")


def _flag(cmd, name):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == name]


def _spawned_cmd(monkeypatch, tmp_path, model_id, num_ctx=131072):
    """The llama-server argv the arbiter builds for `model_id` (never run)."""
    cmds = []

    class DeadProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            cmds.append(list(cmd))

        def poll(self):
            return 1

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(ra.LlamaServerBackend, "_kv_cache_type", lambda self: "q8_0")
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", model_id, num_ctx,
                       gguf_path=tmp_path / "m.gguf", port=8199)
    return cmds[0]


# ── overrides/models.json ────────────────────────────────────────────────────

def test_an_override_is_merged_over_the_registry_and_reaches_the_seat(home, monkeypatch, tmp_path):
    _write_registry(_generated())
    _write_overrides(home["overrides"], {"models": {MID: {"num_ctx": 32768, "n_gpu_layers": 99}}})

    rec = ms.get(MID)
    assert rec["serve_num_ctx"] == 32768
    assert _flag(rec["serve_args"], "-ngl") == ["99"]
    # Everything the override did not mention is the generated record's.
    assert _flag(rec["serve_args"], "--cache-type-k") == ["q8_0"]

    assert ra.LlamaServerBackend.seat_cap(MID) == 32768
    cmd = _spawned_cmd(monkeypatch, tmp_path, MID, num_ctx=32768)
    assert _flag(cmd, "-c") == ["32768"]
    assert _flag(cmd, "-ngl") == ["99"], "the override's GPU layers must win over the default and the record"


def test_an_override_survives_the_registry_being_regenerated(home):
    _write_registry(_generated())
    data = {"models": {MID: {"num_ctx": 32768, "n_gpu_layers": 99}}}
    _write_overrides(home["overrides"], data)
    before = (home["overrides"] / "models.json").read_bytes()
    assert ms.get(MID)["serve_num_ctx"] == 32768

    # A re-download / re-registration rewrites the registry with the
    # installer's numbers, exactly as it did on the machine that lost its fix.
    _write_registry(_generated(ctx=8192, ngl=56))
    rec = ms.get(MID)
    assert rec["serve_num_ctx"] == 32768
    assert _flag(rec["serve_args"], "-ngl") == ["99"]

    # Merged on read, never baked into the generated file...
    on_disk = json.loads(home["registry"].read_text(encoding="utf-8"))["models"][MID]
    assert on_disk["serve_num_ctx"] == 8192
    assert "user_override" not in on_disk
    # ...and the overrides file is never written by anything.
    assert (home["overrides"] / "models.json").read_bytes() == before


def test_a_registry_write_through_the_store_does_not_absorb_the_override(home):
    """retire() reads and saves the registry; the override must stay out of it."""
    _write_registry(_generated())
    _write_overrides(home["overrides"], {"models": {MID: {"num_ctx": 32768}}})
    assert ms.get(MID)["serve_num_ctx"] == 32768
    ms.retire(HELPER, "test")
    on_disk = json.loads(home["registry"].read_text(encoding="utf-8"))["models"][MID]
    assert on_disk["serve_num_ctx"] == 8192


def test_cpu_placement_spawns_with_no_gpu_layers(home, monkeypatch, tmp_path):
    _write_registry(_generated())
    _write_overrides(home["overrides"], {HELPER: {"placement": "cpu"}})   # bare map form
    cmd = _spawned_cmd(monkeypatch, tmp_path, HELPER, num_ctx=32768)
    assert _flag(cmd, "-ngl") == ["0"]


def test_disallowed_keys_and_flags_are_refused_and_logged(home, monkeypatch, tmp_path, caplog):
    _write_registry(_generated())
    _write_overrides(home["overrides"], {"models": {MID: {
        "num_ctx": 32768,
        "command": "calc.exe",
        "extra_args": ["--port", "9999", "-m", "elsewhere.gguf", "--api-key", "x",
                       "-ub", "512", "-np", "99"],
    }}})
    with caplog.at_level(logging.INFO, logger="friday.overrides"):
        rec = ms.get(MID)

    # The allowed parts apply...
    assert rec["serve_num_ctx"] == 32768
    assert _flag(rec["serve_args"], "-ub") == ["512"]
    # ...the rest never reaches the command line.
    args = rec["serve_args"]
    for bad in ("--port", "9999", "-m", "elsewhere.gguf", "--api-key", "calc.exe"):
        assert bad not in args, "%r slipped through the allow-list" % bad
    assert _flag(args, "-np") == ["1"], "an out-of-range value must be refused, not clamped"

    cmd = _spawned_cmd(monkeypatch, tmp_path, MID, num_ctx=32768)
    assert _flag(cmd, "--port") == ["8199"]
    assert _flag(cmd, "-m") == [str(tmp_path / "m.gguf")]

    # Every refusal names the component, the model and the reason.
    text = caplog.text
    assert "refused" in text and "model_store" in text and MID in text
    for name in ("command", "--port", "-m", "--api-key", "-np"):
        assert name in text, "refusal of %r was not logged" % name
    # And what WAS applied is logged too.
    assert "applied" in text and "num_ctx" in text


def test_the_folder_is_created_with_a_readme_and_reports_itself(home):
    from agent_friday.services import user_overrides as uo
    _write_registry(_generated())
    _write_overrides(home["overrides"], {"models": {MID: {"num_ctx": 32768, "nope": 1},
                                                    "not-installed:1b": {"placement": "cpu"}}})
    st = uo.status()
    assert st["path"] == str(home["overrides"])
    assert (home["overrides"] / "README.md").is_file()
    assert (home["overrides"] / "static").is_dir()
    assert st["models"][MID] == {"num_ctx": 32768}
    assert any(r["key"] == "nope" for r in st["rejected"])
    assert st["unknown_models"] == ["not-installed:1b"]
    # A README the person edited is theirs: never rewritten.
    (home["overrides"] / "README.md").write_text("mine", encoding="utf-8")
    uo.ensure_dir()
    assert (home["overrides"] / "README.md").read_text(encoding="utf-8") == "mine"


def _static(home):
    static = home["overrides"] / "static"
    static.mkdir(parents=True, exist_ok=True)
    (static / "user.css").write_text("body{}", encoding="utf-8")
    (static / "user.js").write_text("void 0", encoding="utf-8")
    return static


def test_stylesheets_are_opt_in_and_say_how_to_turn_them_on(home, monkeypatch):
    """A stylesheet can restyle approval cards, so none loads by default."""
    from agent_friday import core
    from agent_friday.services import user_overrides as uo
    _static(home)
    assert uo.DEFAULTS["static_css"] is False
    assert core.DEFAULT_SETTINGS["overrides"]["static_css"] is False
    assert uo.page_tags() == ""
    assert uo.static_path("user.css") is None
    skipped = {s["name"]: s["why"] for s in uo.static_files()["skipped"]}
    assert "static_css" in skipped["user.css"] and "true" in skipped["user.css"]
    st = uo.status()
    assert st["static"]["css_enabled"] is False
    assert "static_css" in st["static"]["how_to_enable"]
    assert "static_css" in (home["overrides"] / "README.md").read_text(encoding="utf-8")

    # Opted in through the real settings loader: the stylesheet loads, after the page.
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"overrides": {"static_css": True}}, raising=False)
    assert '<link rel="stylesheet" href="/user-overrides/static/user.css">' == uo.page_tags()
    assert uo.static_path("user.css") is not None


def test_the_served_file_is_the_one_the_folder_listing_names(home, monkeypatch):
    """static_asset hands back the folder and the LISTED entry name, so the
    route serves through send_from_directory and never builds a path from
    the request; a name the listing does not hold is None."""
    from agent_friday import core
    from agent_friday.services import user_overrides as uo
    static = _static(home)
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"overrides": {"static_css": True}}, raising=False)
    folder, listed = uo.static_asset("user.css")
    assert listed == "user.css" and listed in [e.name for e in static.iterdir()]
    assert folder.resolve() == static.resolve()
    assert uo.static_asset("nope.css") is None
    assert uo.static_asset("../static/user.css") is None
    assert uo.static_path("user.css") == (static / "user.css").resolve()


def test_a_script_is_never_injected_or_served_even_when_settings_say_yes(home, monkeypatch):
    """Any /api/settings POST can change the overrides block, so no setting
    may put a script (which holds the session token) into Friday's page."""
    from agent_friday import core
    from agent_friday.services import user_overrides as uo
    _static(home)
    monkeypatch.setattr(core, "_load_settings", lambda: {"overrides": {
        "enabled": True, "static_css": True, "static_js": True}}, raising=False)
    uo.reset_cache()

    tags = uo.page_tags()
    assert "user.css" in tags
    assert "user.js" not in tags and "<script" not in tags
    assert uo.static_path("user.js") is None
    assert "static_js" not in uo.settings()
    assert "static_js" not in core.DEFAULT_SETTINGS["overrides"]
    why = {s["name"]: s["why"] for s in uo.static_files()["skipped"]}
    assert "not supported" in why["user.js"]

    # Not through the route either.
    from flask import Flask
    from agent_friday.routes.core_routes import core_bp
    app = Flask(__name__)
    app.register_blueprint(core_bp)
    with app.test_client() as c:
        assert c.get("/user-overrides/static/user.js").status_code == 404
        assert c.get("/user-overrides/static/user.css").status_code == 200


def test_a_name_that_resolves_outside_the_static_folder_is_never_served(home, monkeypatch):
    from agent_friday import core
    from agent_friday.services import user_overrides as uo
    static = _static(home)
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"overrides": {"static_css": True}}, raising=False)
    outside = home["overrides"] / "secret.css"
    outside.write_text("body{}", encoding="utf-8")

    # The resolve-escape rule itself, with no link needed: `..` resolves out.
    assert uo._inside(static / ".." / "secret.css", static) is False
    assert uo._inside(static / "user.css", static) is True
    assert uo.static_path("../secret.css") is None

    # A real symlink, where this machine allows creating one.
    link = static / "linked.css"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this machine does not allow creating a symlink")
    assert uo.static_path("linked.css") is None
    assert "linked.css" not in uo.page_tags()
    assert any(s["name"] == "linked.css" and "outside" in s["why"]
               for s in uo.static_files()["skipped"])


# ── the update guard ─────────────────────────────────────────────────────────

def _fake_install(tmp_path):
    """<root>/app with three shipped files and the manifest they shipped with."""
    app = tmp_path / "install" / "app"
    files = {"src/agent_friday/services/example.py": b"print('shipped')\n",
             "static/app.css": b"body{}\n",
             "index.html": b"<html></html>\n"}
    lines = []
    for rel, data in files.items():
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        lines.append("%s  %s" % (hashlib.sha256(data).hexdigest(), rel))
    (app / "app-files.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Noise the guard must ignore: bytecode, and the user files the installer
    # already preserves (they hold secrets and are never copied).
    (app / "src/agent_friday/services/__pycache__").mkdir()
    (app / "src/agent_friday/services/__pycache__/example.cpython-312.pyc").write_bytes(b"\0")
    (app / "start.bat").write_text("set FRIDAY_PASSWORD=synthetic", encoding="utf-8")
    return app


def test_the_update_guard_finds_backs_up_and_reports_changed_files(tmp_path, monkeypatch):
    from agent_friday.services import update_guard as ug
    app = _fake_install(tmp_path)
    (app / "src/agent_friday/services/example.py").write_bytes(b"print('patched locally')\n")
    (app / "src/agent_friday/services/added_by_helper.py").write_bytes(b"x = 1\n")

    found = ug.scan(app)
    assert found["basis"] == "manifest"
    assert found["modified"] == ["src/agent_friday/services/example.py"]
    assert found["added"] == ["src/agent_friday/services/added_by_helper.py"]
    assert found["count"] == 2

    ovr = tmp_path / "overrides"
    out = ug.backup(app, "1.1.0b1", dest_root=ovr)
    dest = ovr / "replaced-1.1.0b1"
    assert out["dest"] == str(dest)
    assert (dest / "src/agent_friday/services/example.py").read_bytes() == b"print('patched locally')\n"
    assert (dest / "src/agent_friday/services/added_by_helper.py").is_file()
    assert not (dest / "start.bat").exists()
    listing = (dest / "REPLACED.txt").read_text(encoding="utf-8")
    assert "modified  src/agent_friday/services/example.py" in listing
    assert "added  src/agent_friday/services/added_by_helper.py" in listing
    # A second backup never overwrites the first.
    again = ug.backup(app, "1.1.0b1", dest_root=ovr)
    assert again["dest"] == str(ovr / "replaced-1.1.0b1-2")


def test_an_untouched_install_reports_nothing(tmp_path):
    from agent_friday.services import update_guard as ug
    app = _fake_install(tmp_path)
    found = ug.scan(app)
    assert found["count"] == 0
    assert ug.backup(app, "1.1.0b1", dest_root=tmp_path / "o")["dest"] is None


def test_the_in_app_update_check_says_it_before_the_update(tmp_path, monkeypatch):
    """Friday can tell the person BEFORE they run the installer."""
    from agent_friday.services import update_check as uc
    from agent_friday.services import update_guard as ug
    app = _fake_install(tmp_path)
    (app / "static/app.css").write_bytes(b"body{color:red}\n")
    monkeypatch.setattr(ug, "app_root", lambda: app)
    monkeypatch.setenv("FRIDAY_OVERRIDES_DIR", str(tmp_path / "overrides"))
    ug.reset_cache()
    monkeypatch.setattr(uc, "is_enabled", lambda: False)

    st = uc.status()
    lc = st["local_changes"]
    assert lc["count"] == 1 and lc["files"] == ["static/app.css"]
    assert "1 file you or a helper changed" in lc["message"]
    assert "replaced-" in lc["message"]


def test_the_installer_guard_agrees_with_the_python_one_and_backs_up_before_replacing(tmp_path):
    """lib/AppFiles.ps1 is what actually runs before the install folder is
    deleted. Same fixture, same answer as update_guard; and install.ps1 calls
    it BEFORE the delete, inside app.copy."""
    from tests.unit._installer_ps import LIB, POWERSHELL, WINDOWS_DIR, ps_json, read_text
    install = read_text(WINDOWS_DIR / "install.ps1")
    step = install[install.index("-Id 'app.copy'"):]
    assert step.index("Backup-ChangedAppFiles") < step.index("Remove-Item -LiteralPath $AppDir")
    assert "Write-AppFilesManifest" in read_text(WINDOWS_DIR / "build-installer.ps1")
    if POWERSHELL is None:
        pytest.skip("the installer is Windows PowerShell 5.1; this part runs it for real")

    app = _fake_install(tmp_path)
    (app / "src/agent_friday/services/example.py").write_bytes(b"print('patched locally')\n")
    (app / "static/tray-extra.js").write_bytes(b"void 0\n")
    ovr = tmp_path / "overrides"
    out = ps_json(
        ". '%s'\n"
        "$f = Get-ChangedAppFiles -AppDir '%s'\n"
        "$b = Backup-ChangedAppFiles -AppDir '%s' -Version '1.1.0b1' -OverridesDir '%s' -Found $f\n"
        "@{ basis = $f.Basis; modified = @($f.Modified); added = @($f.Added); dest = $b.Dest;"
        " files = @($b.Files) } | ConvertTo-Json -Compress\n"
        % (LIB / "AppFiles.ps1", app, app, ovr), tmp_path)
    assert out["basis"] == "manifest"
    assert out["modified"] == ["src/agent_friday/services/example.py"]
    assert out["added"] == ["static/tray-extra.js"]
    assert out["dest"] == str(ovr / "replaced-1.1.0b1")
    assert (ovr / "replaced-1.1.0b1" / "src" / "agent_friday" / "services" / "example.py").read_bytes() \
        == b"print('patched locally')\n"
    assert (ovr / "replaced-1.1.0b1" / "REPLACED.txt").is_file()


# ── the exclusion list, and a clean install reporting nothing ───────────────

def _ps_array(text, name):
    import re
    m = re.search(r"\$script:%s\s*=\s*@\(([^)]*)\)" % re.escape(name), text)
    assert m, "AppFiles.ps1 no longer defines $script:%s" % name
    return sorted(s.lower() for s in re.findall(r"'([^']*)'", m.group(1)))


def test_the_exclusion_list_is_the_same_in_the_installer_and_in_python():
    """AppFiles.ps1 decides what the installer backs up; update_guard.py what
    Friday warns about. One list, written twice: they must never drift."""
    from tests.unit._installer_ps import LIB, read_text
    from agent_friday.services import update_guard as ug
    ps = read_text(LIB / "AppFiles.ps1")
    assert _ps_array(ps, "AppFilesSkipDirs") == sorted(ug.SKIP_DIRS)
    assert _ps_array(ps, "AppFilesSkipDirSuffixes") == sorted(ug.SKIP_DIR_SUFFIXES)
    assert _ps_array(ps, "AppFilesSkipSuffixes") == sorted(ug.SKIP_SUFFIXES)
    assert _ps_array(ps, "AppFilesSkipNames") == sorted(ug.SKIP_NAMES)
    assert _ps_array(ps, "AppFilesSkipPrefixes") == sorted(ug.SKIP_PREFIXES)
    assert _ps_array(ps, "AppFilesUserFiles") == sorted(ug.USER_FILES)
    # ...and the user-file list is install.ps1's own preserved set.
    install = read_text(LIB.parent / "install.ps1")
    assert _ps_array(install, "AppUserFiles") == sorted(ug.USER_FILES)


def _install_then_run(tmp_path):
    """Build a payload, write its manifest the way the build does, install it
    (copy), then do what happens after the copy before anyone changes
    anything: the wizard writes its launch files, Python writes bytecode,
    Explorer drops its files, the seat gate writes evidence."""
    import shutil as _sh
    from agent_friday.services import update_guard as ug
    payload = tmp_path / "staging" / "payload"
    for rel, data in {"src/agent_friday/__init__.py": b"",
                      "src/agent_friday/services/example.py": b"x = 1\n",
                      "static/app.css": b"body{}\n", "index.html": b"<html></html>\n",
                      "pyproject.toml": b"[project]\nversion='1.0'\n"}.items():
        (payload / rel).parent.mkdir(parents=True, exist_ok=True)
        (payload / rel).write_bytes(data)
    ug.write_manifest(payload)
    app = tmp_path / "install" / "app"
    _sh.copytree(payload, app)
    after = {"start.bat": b"rem written by the setup wizard\r\n",
             "config.yaml": b"a: 1\n",
             "src/agent_friday/__pycache__/__init__.cpython-312.pyc": b"\0",
             "src/agent_friday/services/__pycache__/example.cpython-312.pyc": b"\0",
             "static/Thumbs.db": b"\0", "assets/desktop.ini": b"[.ShellClassInfo]\r\n",
             "tests/conformance/results/example-model.json": b"{}"}
    for rel, data in after.items():
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_bytes(data)
    return app


def test_a_clean_install_followed_by_an_upgrade_reports_no_changed_files(tmp_path):
    from agent_friday.services import update_guard as ug
    app = _install_then_run(tmp_path)
    found = ug.scan(app)
    assert found["basis"] == "manifest"
    assert (found["modified"], found["added"], found["count"]) == ([], [], 0)
    # And a real change is still seen next to all that noise.
    (app / "static/app.css").write_bytes(b"body{color:red}\n")
    assert ug.scan(app)["modified"] == ["static/app.css"]


def test_a_clean_install_reports_no_changed_files_to_the_installer_either(tmp_path):
    from tests.unit._installer_ps import LIB, POWERSHELL, ps_json
    if POWERSHELL is None:
        pytest.skip("the installer is Windows PowerShell 5.1; this part runs it for real")
    app = _install_then_run(tmp_path)
    out = ps_json(". '%s'\n$f = Get-ChangedAppFiles -AppDir '%s'\n"
                  "@{ basis = $f.Basis; total = $f.Total } | ConvertTo-Json -Compress\n"
                  % (LIB / "AppFiles.ps1", app), tmp_path)
    assert out == {"basis": "manifest", "total": 0}


def _link_loop(app):
    """app/loop -> app: a directory link pointing at its own parent."""
    loop = app / "loop"
    try:
        loop.symlink_to(app, target_is_directory=True)
        return loop
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(str(app), str(loop))
        return loop
    except Exception:
        pytest.skip("this machine allows neither a symlink nor a junction here")


def test_a_junction_loop_is_never_followed(tmp_path):
    from agent_friday.services import update_guard as ug
    app = _fake_install(tmp_path)
    _link_loop(app)
    found = ug.scan(app)          # a followed loop would never return
    assert found["count"] == 0
    assert found["reparse"] == ["loop"]
    assert not any(r.startswith("loop/") for r in ug.iter_files(app))


def test_the_installer_never_follows_a_junction_loop(tmp_path):
    from tests.unit._installer_ps import LIB, POWERSHELL, ps_json
    if POWERSHELL is None:
        pytest.skip("the installer is Windows PowerShell 5.1; this part runs it for real")
    app = _fake_install(tmp_path)
    _link_loop(app)
    out = ps_json(". '%s'\n$f = Get-ChangedAppFiles -AppDir '%s'\n"
                  "$r = Remove-AppReparsePoints -AppDir '%s' -Found $f\n"
                  "@{ total = $f.Total; reparse = @($f.Reparse); removed = @($r) } | ConvertTo-Json -Compress\n"
                  % (LIB / "AppFiles.ps1", app, app), tmp_path, timeout=60)
    assert out == {"total": 0, "reparse": ["loop"], "removed": ["loop"]}
    assert not (app / "loop").exists()
    assert (app / "index.html").is_file(), "unlinking must never touch the link's target"


def test_a_backup_that_cannot_copy_stops_before_anything_is_deleted_and_says_what(tmp_path):
    """Fail-closed, but never silent: the message names every file that could
    not be copied, the replaced-<version> folder, and how to go on."""
    from tests.unit._installer_ps import LIB, POWERSHELL, run_ps
    from agent_friday.services import update_guard as ug
    app = _fake_install(tmp_path)
    (app / "src/agent_friday/services/example.py").write_bytes(b"print('patched locally')\n")
    (app / "static/tray-extra.js").write_bytes(b"void 0\n")
    blocked = tmp_path / "overrides-is-a-file"
    blocked.write_text("not a folder", encoding="utf-8")   # nothing can be created under it

    out = ug.backup(app, "1.1.0b1", dest_root=blocked)
    assert sorted(f["rel"] for f in out["failed"]) == [
        "src/agent_friday/services/example.py", "static/tray-extra.js"]
    assert out["files"] == []

    install = (LIB.parent / "install.ps1").read_text(encoding="utf-8-sig")
    step = install[install.index("-Id 'app.copy'"):]
    guarded = step[step.index("Backup-ChangedAppFiles"):step.index("Remove-Item -LiteralPath $AppDir")]
    assert "catch" in guarded and "throw" in guarded, "a failed backup must stop app.copy before the delete"
    if POWERSHELL is None:
        pytest.skip("the installer is Windows PowerShell 5.1; this part runs it for real")
    res = run_ps(
        ". '%s'\n$f = Get-ChangedAppFiles -AppDir '%s'\n"
        "try { Backup-ChangedAppFiles -AppDir '%s' -Version '1.1.0b1' -OverridesDir '%s' -Found $f | Out-Null; 'NO-THROW' }\n"
        "catch { 'THREW'; $_.Exception.Message }\n" % (LIB / "AppFiles.ps1", app, app, blocked), tmp_path)
    text = res.stdout
    assert "THREW" in text and "NO-THROW" not in text
    for rel in ("src/agent_friday/services/example.py", "static/tray-extra.js"):
        assert rel in text, "%s is not named in the message" % rel
    assert "~\\.friday\\overrides\\replaced-1.1.0b1" in text
    assert "Nothing in" in text and "run setup again" in text
    assert (app / "src/agent_friday/services/example.py").read_bytes() == b"print('patched locally')\n"


def test_a_backup_deeper_than_260_characters_still_copies(tmp_path):
    from tests.unit._installer_ps import LIB, POWERSHELL, ps_json
    """The source fits in 260 characters; its copy under the overrides folder
    does not. Windows PowerShell 5.1's Copy-Item refuses that path."""
    from tests.unit._installer_ps import LIB, POWERSHELL, ps_json
    from agent_friday.services import update_guard as ug
    app = _fake_install(tmp_path)
    room = 245 - len(str(app / "static" / "x.css"))
    if room < 10:
        pytest.skip("the temp folder is too deep to build a source path under 260 characters")
    name = "n" * min(room, 200) + ".css"
    rel = "static/" + name
    assert len(str(app / "static" / name)) < 260
    (app / "static" / name).write_bytes(b"body{color:red}\n")

    ovr = tmp_path / ("o" * 40)
    assert len(str(ovr / "replaced-1.1.0b1" / "static" / name)) > 260
    out = ug.backup(app, "1.1.0b1", dest_root=ovr)
    assert out["failed"] == [] and out["files"] == [rel]
    if POWERSHELL is None:
        pytest.skip("the installer is Windows PowerShell 5.1; this part runs it for real")
    ovr2 = tmp_path / ("p" * 40)
    res = ps_json(". '%s'\n$b = Backup-ChangedAppFiles -AppDir '%s' -Version '1.1.0b1' -OverridesDir '%s'\n"
                  "@{ files = @($b.Files); failed = @($b.Failed).Count } | ConvertTo-Json -Compress\n"
                  % (LIB / "AppFiles.ps1", app, ovr2), tmp_path)
    assert res == {"files": [rel], "failed": 0}


def test_the_update_check_needs_no_network_for_the_comparison(tmp_path, monkeypatch):
    from agent_friday.services import update_check as uc
    from agent_friday.services import update_guard as ug

    def _boom(*a, **k):
        raise AssertionError("the changed-files comparison made a network call")

    monkeypatch.setattr(uc.requests, "get", _boom)
    monkeypatch.setattr(ug, "app_root", lambda: _fake_install(tmp_path))
    ug.reset_cache()
    monkeypatch.setattr(uc, "is_enabled", lambda: False)
    assert uc.status()["local_changes"]["count"] == 0
