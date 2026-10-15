"""The runtime's server archive is picked by rule, not by name suffix.

A CUDA release carries two assets whose names END the same way:

    cudart-llama-bin-win-cuda-12.4-x64.zip          (the CUDA DLL pack)
    llama-prism-b10743-bin-win-cuda-12.4-x64.zip     (the server archive)

Matching by suffix took whichever came first. GitHub lists assets by name, so
the DLL pack came first: it was fetched as the "server" and again as the DLL
pack, the server archive never arrived, and the first-run card stopped at
"the runtime unpacked but no server binary was found".
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.services import model_download as md
from agent_friday.services import model_shortlist as sl


def _asset(name: str, fill: str, size: int) -> dict:
    return {"name": name, "size": size, "digest": "sha256:" + fill * 64,
            "browser_download_url": "https://example.invalid/%s" % name}


CUDART = "cudart-llama-bin-win-cuda-12.4-x64.zip"
SERVER = "llama-prism-b10743-bin-win-cuda-12.4-x64.zip"
CPU = "llama-prism-b10743-bin-win-cpu-x64.zip"

# Sorted by name, as the release API returns them: the DLL pack first.
RELEASE = {"assets": [_asset(CUDART, "b", 20), _asset(SERVER, "a", 10), _asset(CPU, "c", 5)]}


def test_the_server_archive_is_chosen_when_the_cudart_pack_sorts_first():
    picked = sl.pick_release_assets(RELEASE, "cuda", "windows")
    assert [p["name"] for p in picked] == [SERVER, CUDART], picked
    assert picked[0]["sha256"] == "a" * 64, "the server archive's own digest"
    assert picked[1]["sha256"] == "b" * 64


def test_the_same_asset_is_never_fetched_twice():
    picked = sl.pick_release_assets(RELEASE, "cuda", "windows")
    names = [p["name"] for p in picked]
    assert len(names) == len(set(names)), names


def test_a_cudart_pack_is_never_taken_for_the_server():
    """A release with only the DLL pack for this backend has no server for it."""
    release = {"assets": [_asset(CUDART, "b", 20), _asset(CPU, "c", 5)]}
    picked = sl.pick_release_assets(release, "cuda", "windows")
    assert all(not p["name"].startswith("cudart-") for p in picked[:1]), picked
    names = [p["name"] for p in picked]
    assert len(names) == len(set(names)), names


def test_the_cpu_build_takes_no_dll_pack():
    assert [p["name"] for p in sl.pick_release_assets(RELEASE, "cpu", "windows")] == [CPU]


def test_an_unpacked_runtime_without_its_server_names_the_binary_it_looked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    from agent_friday.services import hardware_profile as hwp
    monkeypatch.setattr(hwp, "get", lambda *a, **k: {"gpus": [{"vendor": "nvidia"}]})
    monkeypatch.setattr(sl, "os_family", lambda: "windows")
    monkeypatch.setattr(md, "_release_json", lambda url: RELEASE)
    monkeypatch.setattr(md, "fetch_file", lambda *a, **k: {"status": "done"})
    monkeypatch.setattr(md, "_unpack", lambda archive, into: into)   # unpacks nothing
    ctrl = {"pause": threading.Event(), "cancel": threading.Event()}

    with pytest.raises(md.DownloadError) as err:
        md._install_runtime(ctrl)

    wanted = sl.runtime_spec()["server_binary"]["windows"]
    assert wanted in str(err.value), str(err.value)
