"""Friday's file tools reach only where the owner's files are (BD-26, NS-26.12-1, NS-31.12-1).

Before this, read_file opened any absolute path the account could read: another
program's data under AppData, Friday's own vault and governance folder, a file in
the temp folder or on another drive. write_file was held only by a hook settings can
switch off. Now one policy (services/path_policy) judges every file tool on the
resolved path: inside the home folder (minus application data and Friday's state),
Friday's output folders, Library and search roots for reading, and folders the owner
granted on screen. The fuzz cases at the end are spellings that tried to walk around
a path rule before (UNC, extended-length, device, URL, data streams, '..', links).

All files are synthetic, created under the test home or the test's own temp folder.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_friday.services import agent

SECRET = "synthetic-outside-content-7f3a"


def _read(path) -> str:
    return agent._tool_read_file({"path": str(path)})


def _write(path, text="x") -> str:
    return agent._tool_write_file({"path": str(path), "content": text})


@pytest.fixture
def outside(tmp_path):
    """A file outside the home folder (pytest's temp folder is a sibling of the test home)."""
    f = tmp_path / "elsewhere" / "notes.txt"
    f.parent.mkdir(parents=True)
    f.write_text(SECRET, encoding="utf-8")
    return f


@pytest.fixture
def home(test_home):
    return Path(test_home)


def _refused(out: str) -> bool:
    return out.startswith("[SANDBOX DENY]") and SECRET not in out


# ── reading ───────────────────────────────────────────────────────────────────

def test_a_file_in_the_owners_documents_is_read(home):
    f = home / "Documents" / "plan.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("the plan " + SECRET, encoding="utf-8")
    assert SECRET in _read(f)


def test_a_file_outside_the_home_folder_is_not_read(outside):
    assert _refused(_read(outside)), "read_file opened a file outside the folders it may use"


def test_another_programs_data_is_not_read(home):
    f = home / "AppData" / "Roaming" / "SomeApp" / "state.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SECRET, encoding="utf-8")
    assert _refused(_read(f))


def test_fridays_own_state_is_not_read_but_her_outputs_are(friday_dir):
    import agent_friday.core as core
    inner = Path(friday_dir) / "governance" / "notes.txt"
    inner.parent.mkdir(parents=True, exist_ok=True)
    inner.write_text(SECRET, encoding="utf-8")
    assert _refused(_read(inner))
    made = Path(core.DAILY_CREATIONS_DIR) / "made.txt"
    made.parent.mkdir(parents=True, exist_ok=True)
    made.write_text("made " + SECRET, encoding="utf-8")
    assert SECRET in _read(made)


# ── writing ───────────────────────────────────────────────────────────────────

def test_a_write_outside_the_home_folder_writes_nothing(tmp_path):
    target = tmp_path / "out" / "w.txt"
    out = _write(target, SECRET)
    assert out.startswith("[SANDBOX DENY]")
    assert not target.exists() and not target.parent.exists()


def test_a_write_into_another_programs_data_writes_nothing(home):
    target = home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "x.cmd"
    assert _write(target, "echo hi").startswith("[SANDBOX DENY]")
    assert not target.exists()


def test_the_sandbox_hook_judges_reads_too(outside):
    import agent_friday.core as core
    ok, why = core._sandbox_policy("read_file", {"path": str(outside)})
    assert ok is False and "not allowed" in why


# ── the owner's folder grants ─────────────────────────────────────────────────

def test_a_grant_opens_a_folder_for_its_ops_only_and_a_revoke_closes_it(outside):
    from agent_friday.services import path_policy as pp
    g, why = pp.add_grant(str(outside.parent), ["read"], days=7, purpose="test")
    assert g, why
    try:
        assert SECRET in _read(outside)
        assert _write(outside.parent / "new.txt").startswith("[SANDBOX DENY]"), "a read grant allowed a write"
    finally:
        assert pp.revoke(g["id"])
    assert _refused(_read(outside))


def test_a_tampered_grant_file_grants_nothing(outside):
    from agent_friday.services import path_policy as pp
    g, _ = pp.add_grant(str(outside.parent), ["read"], days=7)
    try:
        rows = json.loads(pp._store().read_text(encoding="utf-8"))
        for r in rows:
            r["ops"] = ["read", "write"]                     # widened by hand, signature unchanged
        pp._store().write_text(json.dumps(rows), encoding="utf-8")
        assert _write(outside.parent / "w.txt").startswith("[SANDBOX DENY]")
        assert _refused(_read(outside)), "a tampered grant still counted"
    finally:
        pp._store().unlink()


def test_an_expired_grant_grants_nothing(outside, monkeypatch):
    from agent_friday.services import path_policy as pp
    import time as _t
    g, _ = pp.add_grant(str(outside.parent), ["read"], days=1)
    try:
        real = _t.time
        monkeypatch.setattr(pp.time, "time", lambda: real() + 3 * 86400)
        assert _refused(_read(outside))
    finally:
        monkeypatch.undo()
        pp.revoke(g["id"])


@pytest.mark.parametrize("which", ["home", "drive", "friday", "missing"])
def test_too_broad_or_own_state_grants_are_refused(home, friday_dir, tmp_path, which):
    from agent_friday.services import path_policy as pp
    target = {"home": str(Path.home()), "drive": os.path.abspath(os.sep),
              "friday": str(friday_dir), "missing": str(tmp_path / "nope")}[which]
    g, why = pp.add_grant(target, ["read"])
    assert g is None and why


# ── escapes (NS-34.17-5) ──────────────────────────────────────────────────────

def _through(docs: Path, target: str) -> str:
    """`target` spelled as a walk up out of the Documents folder with '..'."""
    try:
        return str(docs) + os.sep + os.path.relpath(target, str(docs))
    except ValueError:          # another drive: there is no '..' route, use the plain path
        return target


def _escape_spellings(home: Path, outside: Path):
    o = str(outside)
    docs = home / "Documents"
    return {
        "dotdot": _through(docs, o),
        "relative": "elsewhere/notes.txt",
        "unc_local": "\\\\localhost\\c$\\" + o.replace(":", "", 1).lstrip("\\/"),
        "extended": "\\\\?\\" + o,
        "device": "\\\\.\\" + o,
        "url": "file:///" + o.replace("\\", "/"),
        "ads": str(docs / "notes.txt:hidden"),
        "nul": str(docs / "a\x00b"),
        "forward_slashes": o.replace("\\", "/"),
    }


@pytest.mark.parametrize("kind", ["dotdot", "relative", "unc_local", "extended", "device", "url", "ads", "nul",
                                  "forward_slashes"])
def test_no_spelling_walks_around_the_policy(home, outside, kind):
    spelled = _escape_spellings(home, outside)[kind]
    out = _read(spelled)
    assert SECRET not in out, (kind, spelled)
    assert _write(spelled, "y").startswith(("[SANDBOX DENY]", "Write error")), (kind, spelled)


def test_a_link_inside_the_home_folder_to_outside_is_judged_where_it_lands(home, outside):
    link = home / "Documents" / "shortcut-dir"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(outside.parent), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this runner cannot make a symbolic link")
    try:
        assert _refused(_read(link / "notes.txt"))
    finally:
        os.unlink(str(link))


def _junction_or_link(link: Path, target: Path) -> bool:
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))      # needs no privilege
        else:
            os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False


# ── security review round 1 ───────────────────────────────────────────────────

@pytest.mark.parametrize("spelling", ["AppData.", "AppData ", "APPDATA"])
def test_trailing_dots_spaces_and_case_do_not_reach_application_data(home, spelling):
    real = home / "AppData" / "Roaming" / "App"
    real.mkdir(parents=True, exist_ok=True)
    (real / "s.txt").write_text(SECRET, encoding="utf-8")
    spelled = str(home / spelling / "Roaming" / "App" / "s.txt")
    assert SECRET not in _read(spelled)
    assert _write(str(home / spelling / "Roaming" / "App" / "w.txt")).startswith("[SANDBOX DENY]")


@pytest.mark.parametrize("rel", [".config./git/config", ".npmrc", ".claude/.credentials.json", ".bash_history",
                                 ".docker/config.json"])
def test_sign_in_stores_and_dot_tricks_are_refused(home, rel):
    f = home / rel.replace("./", "/")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SECRET, encoding="utf-8")
    assert SECRET not in _read(str(home / rel))


@pytest.mark.parametrize("rel", [".gitconfig", ".bashrc", "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
                                 ".ssh/authorized_keys"])
def test_files_run_or_obeyed_at_start_up_are_never_written(home, rel):
    target = home / rel
    assert not _write(target, "malicious").startswith("Wrote"), rel
    assert not target.exists() or "malicious" not in target.read_text(encoding="utf-8")


def test_the_real_application_data_folder_is_off_limits_wherever_it_is(tmp_path, monkeypatch):
    elsewhere = tmp_path / "RoamingElsewhere"
    elsewhere.mkdir()
    (elsewhere / "s.txt").write_text(SECRET, encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(elsewhere))
    monkeypatch.setenv("FRIDAY_SANDBOX_ROOT", str(tmp_path))
    assert _refused(_read(elsewhere / "s.txt"))


def test_a_sandbox_root_above_the_profile_is_clamped_to_it(outside, monkeypatch):
    monkeypatch.setenv("FRIDAY_SANDBOX_ROOT", os.path.abspath(os.sep))
    assert _refused(_read(outside))


def test_a_grant_that_would_hold_fridays_state_is_refused(friday_dir):
    from agent_friday.services import path_policy as pp
    g, why = pp.add_grant(str(Path(friday_dir).parent), ["read"])
    assert g is None and why


def test_a_network_path_is_never_resolved(monkeypatch):
    """Resolving \\\\host\\share contacts it (and offers the owner's sign-in); it is refused from its text."""
    import pathlib
    real = pathlib.Path.resolve

    def guard(self, *a, **k):
        if str(self).replace("/", "\\").startswith("\\\\"):
            raise AssertionError("a network path was resolved")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "resolve", guard)
    out = _read("\\\\attacker.example\\share\\x.txt")
    assert out.startswith("[SANDBOX DENY]")


def test_a_read_only_command_that_reads_outside_is_carded(outside, home):
    from agent_friday.governance import action_gate as ag
    assert ag.classify_command(f'Get-Content "{outside}"')[0] == ag.OUTWARD
    assert ag.classify_command("Get-Content ~/AppData/Roaming/App/state.json")[0] == ag.OUTWARD
    assert ag.classify_command("Get-Content ..\\..\\x.txt")[0] == ag.OUTWARD
    assert ag.classify_command("Get-Content ~/Documents/report.txt")[0] == ag.INTERNAL


def test_search_does_not_follow_a_junction_into_application_data(home):
    from agent_friday.services import file_search
    app = home / "AppData" / "Roaming" / "Hidden"
    app.mkdir(parents=True, exist_ok=True)
    (app / "needle-7f3a.txt").write_text(SECRET, encoding="utf-8")
    docs = home / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    link = docs / "j"
    if not _junction_or_link(link, app):
        pytest.skip("this runner cannot make a junction or link")
    try:
        res = file_search.search_files(query="needle-7f3a", root="documents")
        assert not [r for r in (res.get("results") or []) if "needle-7f3a" in str(r.get("path"))]
    finally:
        try:
            os.rmdir(str(link)) if os.name == "nt" else os.unlink(str(link))
        except OSError:
            pass


def test_a_pdf_outside_the_folders_is_not_opened(outside):
    from agent_friday.services import pdf_forms
    pdf = outside.parent / "form.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%synthetic\n")
    with pytest.raises(pdf_forms.FormRefused):
        pdf_forms.open_pdf(str(pdf))
