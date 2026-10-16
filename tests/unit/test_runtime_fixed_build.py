"""The local model runtime comes from a fixed, known-good release.

Field case: the first-run banner "Your models did not finish downloading"
never cleared for Bonsai 2 27B. The publisher's launch runtime, prism-b10687,
shipped without its server binaries; builds from prism-b10709 fix it. The
downloader took whatever `releases/latest` said, and a runtime folder with
weights beside it but no llama-server counted as installed.

Pinned here: installs fetch the shortlist's pinned tag; a known-broken or too
old build is refused before anything downloads; an installed runtime without
its server is named as the problem on the first-run card and repaired.
"""
from __future__ import annotations

import json
import threading

import pytest

from agent_friday.services import first_run_models as frm
from agent_friday.services import model_download as md
from agent_friday.services import model_shortlist as sl


def _asset(name, size=10):
    return {"name": name, "size": size, "digest": "sha256:" + "a" * 64,
            "browser_download_url": "https://example.invalid/%s" % name}


def _release(tag):
    return {"tag_name": tag, "assets": [_asset("llama-%s-bin-win-cpu-x64.zip" % tag)]}


@pytest.fixture
def cpu_box(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    from agent_friday.services import hardware_profile as hwp
    monkeypatch.setattr(hwp, "get", lambda *a, **k: {"gpus": []})
    monkeypatch.setattr(sl, "os_family", lambda: "windows")
    fetched = []
    monkeypatch.setattr(md, "fetch_file", lambda url, *a, **k: fetched.append(url) or {"status": "done"})
    return fetched


def _ctrl():
    return {"pause": threading.Event(), "cancel": threading.Event()}


def test_install_fetches_the_pinned_release_not_latest(cpu_box, monkeypatch):
    asked = []
    pinned = sl.runtime_spec()["pinned_tag"]

    def release_json(url):
        asked.append(url)
        return _release(pinned)

    monkeypatch.setattr(md, "_release_json", release_json)

    def unpack(archive, into):
        into.mkdir(parents=True, exist_ok=True)
        (into / "llama-server.exe").write_bytes(b"MZ")
        return into
    monkeypatch.setattr(md, "_unpack", unpack)
    md._install_runtime(_ctrl())
    assert asked and asked[0].endswith("/releases/tags/" + pinned), asked
    rec = json.loads((md.runtime_dir_for() / "friday_runtime.json").read_text(encoding="utf-8"))
    assert rec["tag"] == pinned and rec["build"] >= 10709


@pytest.mark.parametrize("tag", ["prism-b10687-abc1234", "prism-b10690-def5678"])
def test_a_broken_or_too_old_runtime_is_refused_before_downloading(cpu_box, monkeypatch, tag):
    monkeypatch.setattr(md, "_release_json", lambda url: _release(tag))
    monkeypatch.setattr(md, "_unpack", lambda archive, into: into)
    with pytest.raises(md.DownloadError) as err:
        md._install_runtime(_ctrl())
    assert cpu_box == [], "a refused runtime must not be downloaded"
    assert tag in str(err.value)


def test_the_pin_is_at_or_past_the_fixed_build():
    spec = sl.runtime_spec()
    assert spec["minimum_build"] >= 10709
    assert "10687" in {str(k) for k in spec["known_bad_builds"]}
    assert md.release_build(spec["pinned_tag"]) >= spec["minimum_build"]


def test_an_installed_runtime_without_its_server_is_the_named_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    (md.runtime_dir_for() / "ggml.dll").parent.mkdir(parents=True)
    (md.runtime_dir_for() / "ggml.dll").write_bytes(b"x")
    why = md.installed_runtime_problem()
    assert why and "server" in why


def test_the_first_run_card_names_a_runtime_that_cannot_serve(tmp_path, monkeypatch):
    """Weights installed, runtime folder without llama-server: the card must not
    say done, and must not say only 'did not finish downloading'."""
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    md.runtime_dir_for().mkdir(parents=True)
    req = {"cloud": False, "consent": True, "problems": [], "release": "x",
           "seats": {"deep_thinker": {"model_id": "bonsai2:27b", "packing": "PTQ1_0"}}}
    monkeypatch.setattr(frm, "load_request", lambda: req)
    row = frm._entry("deep_thinker", "bonsai2:27b", "PTQ1_0")
    row["state"] = "installed"
    monkeypatch.setattr(frm, "_read_json", lambda p: {"seats": [row]})
    monkeypatch.setattr(frm, "_served_ids", lambda: [])
    st = frm.status()
    seat = st["seats"][0]
    assert st["state"] != "done"
    assert seat["state"] == "failed" and "server" in (seat.get("error") or "")


def test_first_run_repairs_the_runtime_when_the_weights_are_already_there(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    md.runtime_dir_for().mkdir(parents=True)          # broken: no server binary
    repaired = []
    monkeypatch.setattr(md, "repair_runtime", lambda: repaired.append(1) or md.runtime_dir_for())
    from agent_friday.services import model_store
    monkeypatch.setattr(model_store, "available", lambda: {"bonsai2:27b": {}})
    monkeypatch.setattr(frm, "apply_seat", lambda seat, mid: None)
    monkeypatch.setattr(frm, "_write_status", lambda s: None)
    monkeypatch.setattr(frm, "_retire_request", lambda: None)
    frm._run({"seats": {"deep_thinker": {"model_id": "bonsai2:27b", "packing": "PTQ1_0"}}})
    assert repaired == [1]
