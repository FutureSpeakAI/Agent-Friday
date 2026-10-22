"""Flint (Recursive's coding model) arrives the way every shortlist model does, plus pins.

Spec: ftv/program/specs/core_models_flint_laya_bonsai2_STORM_2026-10-22.md, Phase CT1
(§10; engineering calls CT-E05, CT-E06, CT-E16, CT-E24, CT-E25, CT-E26).

  * The shortlist names Flint as an experimental CODER: a pinned repo commit,
    per-file bytes and sha256, Apache-2.0, no telemetry, its own runtime
    (``llama.cpp-flint``) and a seat hold.
  * Its download URLs carry the commit (``resolve/<commit>/``), never ``main``.
  * Its runtime is the official llama.cpp release b11803. Every asset is
    sha256-pinned in the shortlist; another tag, a differing digest or a build
    with no pin is refused before anything is fetched.
  * An installed Flint is registered HELD: no seat, planner or fallback sees it
    until the code-session lease (CT2) exists.
  * A shortlist file without a checksum is refused when it is queued.
  * First run can never seat it.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

import pytest

from agent_friday.services import model_download as md
from agent_friday.services import model_shortlist as sl
from agent_friday.services import model_store

FLINT = "flint:40b"
RUNTIME = "llama.cpp-flint"
REPO_ROOT = Path(__file__).resolve().parents[2]

# What the Hub's LFS pointer and the GitHub release publish for the pinned
# commit and tag. A changed pin must change these on purpose.
Q2_FILE = "flint-UD-Q2_K_XL.gguf"
Q2_BYTES = 13562207680
Q2_SHA = "7f43dd99e7d574c43c3f1b24e0e8269cf1ec92ba6c25f11b3ff126cde4c7a0cb"
REVISION = "20468edfc38ab43d6914cc88b08cbecb05d0fc62"
CUDA_ZIP = "llama-b11803-bin-win-cuda-13.4-x64.zip"
CUDA_SHA = "04b36b76c3708a6ce6120a45011f880054a179b6d9906ad54b1a8eb79cbb3d8b"
CUDART_ZIP = "cudart-llama-bin-win-cuda-13.4-x64.zip"
CUDART_SHA = "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668"
CPU_ZIP = "llama-b11803-bin-win-cpu-x64.zip"
CPU_SHA = "68a11f1f0ab78587bfc42071d2dbd4aa1b621357ddc375c1ee25a24e76d27954"


@pytest.fixture(autouse=True)
def _fresh_shortlist():
    sl.reload_for_tests()
    yield
    sl.reload_for_tests()


# ── the shortlist entry ──────────────────────────────────────────────────────

def test_the_shortlist_names_flint_as_an_experimental_coder():
    m = sl.get(FLINT)
    assert m, "flint:40b is on the shortlist"
    assert m["roles"] == ["coder"], "Flint only ever writes code (CT-E04)"
    assert m["status"] == "experimental"
    assert m["runtime"] == RUNTIME
    assert m["revision"] == REVISION and re.fullmatch(r"[0-9a-f]{40}", m["revision"])
    assert m["repo"] == "unsloth/flint-GGUF" and m["base_repo"] == "RecursiveSI/flint"
    assert m["licence"] == "Apache-2.0" and m["telemetry"] == "none"
    assert m.get("seat_hold"), "an installed Flint is held out of every seat until CT2"
    assert not m.get("friday_standard")
    assert re.fullmatch(r"[0-9a-f]{64}", m["chat_template_sha256"])
    for f in m["files"]:
        assert isinstance(f["bytes"], int) and f["bytes"] > 0, f["file"]
        assert re.fullmatch(r"[0-9a-f]{64}", f["sha256"]), f["file"]
    d = sl.file_entry(FLINT)
    assert (d["file"], d["bytes"], d["sha256"]) == (Q2_FILE, Q2_BYTES, Q2_SHA)


def test_flint_downloads_from_the_pinned_commit_not_main():
    url = sl.file_url(FLINT, sl.file_entry(FLINT))
    assert url == "https://huggingface.co/unsloth/flint-GGUF/resolve/%s/%s" % (REVISION, Q2_FILE)
    assert "/resolve/main/" not in url


def test_a_revision_that_is_not_a_commit_is_never_used(monkeypatch):
    """A branch or tag name moves; only a full commit id pins the bytes."""
    m = dict(sl.get(FLINT), revision="main")
    monkeypatch.setattr(sl, "get", lambda mid: dict(m) if mid == FLINT else None)
    with pytest.raises(ValueError):
        sl.file_url(FLINT, sl.file_entry(FLINT))


# ── the runtime ──────────────────────────────────────────────────────────────

def test_the_flint_runtime_is_the_pinned_official_release():
    spec = sl.runtime_spec(RUNTIME)
    assert spec["repo"] == "ggml-org/llama.cpp"
    assert spec["pinned_tag"] == "b11803"
    assert md.release_build(spec["pinned_tag"]) >= spec["minimum_build"] >= 11803
    pins = spec["pinned_sha256"]
    assert pins[CUDA_ZIP] == CUDA_SHA and pins[CUDART_ZIP] == CUDART_SHA and pins[CPU_ZIP] == CPU_SHA
    assert sl.runtime_asset_names("cuda", "windows", name=RUNTIME) == [
        "bin-win-cuda-13.4-x64.zip", CUDART_ZIP]
    assert md.runtime_dir_for(RUNTIME).name == RUNTIME
    # The Bonsai fork's own assets are untouched by the second runtime.
    assert sl.runtime_asset_names("cuda", "windows")[0] == "bin-win-cuda-12.4-x64.zip"


def _asset(name, sha):
    return {"name": name, "size": 10, "digest": "sha256:" + sha,
            "browser_download_url": "https://github.com/ggml-org/llama.cpp/releases/download/b11803/" + name}


def _release(tag="b11803", cuda_sha=CUDA_SHA):
    return {"tag_name": tag, "assets": [
        _asset(CUDA_ZIP.replace("b11803", tag), cuda_sha),
        _asset(CUDART_ZIP, CUDART_SHA),
        _asset(CPU_ZIP.replace("b11803", tag), CPU_SHA),
        _asset("llama-%s-bin-win-vulkan-x64.zip" % tag, "d" * 64),
    ]}


def _ctrl():
    return {"pause": threading.Event(), "cancel": threading.Event()}


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A Windows machine with an NVIDIA card; fetches are recorded, not made."""
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    from agent_friday.services import hardware_profile as hwp
    gpu = {"vendor": "nvidia"}
    monkeypatch.setattr(hwp, "get", lambda *a, **k: {"gpus": [gpu]})
    monkeypatch.setattr(sl, "os_family", lambda: "windows")
    fetched = []

    def fetch(url, dest, **kw):
        fetched.append({"url": url, "sha256": kw.get("expected_sha256")})
        return {"status": "done"}
    monkeypatch.setattr(md, "fetch_file", fetch)

    def unpack(archive, into):
        into.mkdir(parents=True, exist_ok=True)
        (into / "llama-server.exe").write_bytes(b"MZ")
        return into
    monkeypatch.setattr(md, "_unpack", unpack)
    return {"fetched": fetched, "gpu": gpu, "tmp": tmp_path}


def test_the_pinned_runtime_installs_into_its_own_folder_with_its_pins(box, monkeypatch):
    asked = []
    monkeypatch.setattr(md, "_release_json", lambda url: asked.append(url) or _release())
    binary = md._install_runtime(_ctrl(), RUNTIME)
    assert asked == ["https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11803"]
    assert [f["sha256"] for f in box["fetched"]] == [CUDA_SHA, CUDART_SHA]
    assert binary == md.runtime_dir_for(RUNTIME) / "llama-server.exe"
    assert not (md.runtime_dir_for() / "llama-server.exe").exists(), "the Bonsai fork folder is not touched"
    rec = json.loads((md.runtime_dir_for(RUNTIME) / "friday_runtime.json").read_text(encoding="utf-8"))
    assert rec["tag"] == "b11803"


@pytest.mark.parametrize("case", ["digest_differs", "other_tag", "unpinned_build"])
def test_an_unpinned_or_different_runtime_is_refused_before_any_fetch(box, monkeypatch, case):
    if case == "digest_differs":
        monkeypatch.setattr(md, "_release_json", lambda url: _release(cuda_sha="e" * 64))
    elif case == "other_tag":
        monkeypatch.setattr(md, "_release_json", lambda url: _release(tag="b11804"))
    else:
        box["gpu"]["vendor"] = "intel"     # the Vulkan build: in the release, not pinned
        monkeypatch.setattr(sl, "runtime_asset_names", _with_vulkan(sl.runtime_asset_names))
        monkeypatch.setattr(md, "_release_json", lambda url: _release())
    with pytest.raises(md.DownloadError):
        md._install_runtime(_ctrl(), RUNTIME)
    assert box["fetched"] == [], "nothing is downloaded for a runtime that is not the pinned one"


def _with_vulkan(real):
    def names(backend, fam=None, name="prism-fork"):
        if name == RUNTIME and backend == "vulkan":
            return ["bin-win-vulkan-x64.zip"]
        return real(backend, fam, name=name)
    return names


# ── the job ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(model_store, "registry_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(md, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(md, "_disk_refusal", lambda profile, total: None)
    monkeypatch.setattr(model_store, "describe", lambda p: {
        "path": str(p), "size_bytes": Path(p).stat().st_size, "architecture": "flint",
        "quantization": "Q2_K", "can_generate": True, "is_embedding": False})
    md._JOBS.clear()
    md._CTRL.clear()
    return tmp_path


@pytest.fixture
def windows_nvidia(monkeypatch):
    monkeypatch.setattr(sl, "os_family", lambda: "windows")
    return {"gpus": [{"vendor": "nvidia"}]}


def test_queuing_flint_asks_for_its_own_runtime_even_when_the_fork_is_installed(store, windows_nvidia):
    fork = md.runtime_dir_for() / "llama-server.exe"
    fork.parent.mkdir(parents=True)
    fork.write_bytes(b"MZ")
    job = md.start_model(FLINT, profile=windows_nvidia, start_thread=False)
    assert job["status"] == "queued", job.get("error")
    assert job["needs_runtime"] is True and job["runtime"] == RUNTIME
    assert job["items"][0]["url"].endswith("/resolve/%s/%s" % (REVISION, Q2_FILE))
    assert job["items"][0]["sha256"] == Q2_SHA


@pytest.mark.parametrize("fam,vendor", [("linux", "nvidia"), ("linux", ""), ("darwin", "")])
def test_a_computer_whose_runtime_build_is_not_pinned_is_refused_before_the_weights(
        store, monkeypatch, fam, vendor):
    """13.5 GB is not fetched for a machine that could never get the runtime."""
    monkeypatch.setattr(sl, "os_family", lambda: fam)
    calls = []
    monkeypatch.setattr(md, "_http_get", lambda *a, **k: calls.append(a))
    prof = {"gpus": [{"vendor": vendor}] if vendor else []}
    job = md.start_model(FLINT, profile=prof, start_thread=False)
    assert job["status"] == "refused" and "not available for this computer" in job["error"]
    assert calls == [] and md.jobs() == []


def test_a_shortlist_file_without_a_checksum_is_refused(store, monkeypatch):
    entry = {"id": "x:1", "label": "X", "runtime": "prism-fork", "repo": "a/b",
             "files": [{"file": "x.gguf", "packing": "Q4", "bytes": 10, "sha256": "", "default": True}]}
    monkeypatch.setattr(sl, "get", lambda mid: dict(entry) if mid == "x:1" else None)
    job = md.start_model("x:1", start_thread=False)
    assert job["status"] == "refused" and "checksum" in job["error"]
    assert md.jobs() == []


def _gguf_bytes(n=4096) -> bytes:
    return b"GGUF" + (3).to_bytes(4, "little") + b"\x00" * (n - 8)


def test_an_installed_flint_is_held_out_of_every_seat(store, monkeypatch):
    payload = _gguf_bytes()
    entry = dict(sl.get(FLINT))
    entry["files"] = [{"file": "flint-test.gguf", "packing": "Q2_K_XL", "bytes": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest(), "default": True}]
    monkeypatch.setattr(sl, "get", lambda mid: dict(entry) if mid == FLINT else None)

    class _Resp:
        status_code = 200

        def iter_content(self, chunk_size=1):
            yield payload

        def close(self):
            pass
    monkeypatch.setattr(md, "_http_get", lambda url, **kw: _Resp())
    engine = store / "runtime" / RUNTIME / "llama-server.exe"
    job = md.start_model(FLINT, engine_override=str(engine), start_thread=False)
    out = md.run_job(job["id"])
    assert out["status"] == "installed", out.get("error")
    rec = model_store.get(FLINT)
    assert rec["held"] == entry["seat_hold"]
    assert rec["engine"] == str(engine)
    assert FLINT not in model_store.available()
    assert FLINT not in model_store.seat_file_map(), "the Arbiter must not be able to spawn it"
    assert model_store.missing()[FLINT]["why"].startswith("held")


def test_a_bad_checksum_registers_nothing(store, monkeypatch):
    """NS-34.4-5: a local model whose bytes are not the pinned ones is deleted
    and never registered, for Flint as for every shortlist model."""
    payload = _gguf_bytes()
    entry = dict(sl.get(FLINT))
    entry["files"] = [{"file": "flint-test.gguf", "packing": "Q2_K_XL", "bytes": len(payload),
                       "sha256": "0" * 64, "default": True}]
    monkeypatch.setattr(sl, "get", lambda mid: dict(entry) if mid == FLINT else None)

    class _Resp:
        status_code = 200

        def iter_content(self, chunk_size=1):
            yield payload

        def close(self):
            pass
    monkeypatch.setattr(md, "_http_get", lambda url, **kw: _Resp())
    job = md.start_model(FLINT, engine_override="x", start_thread=False)
    out = md.run_job(job["id"])
    assert out["status"] == "failed" and "checksum mismatch" in out["error"]
    assert model_store.get(FLINT) is None
    left = list((store / "gguf").glob("flint-test.gguf*")) if (store / "gguf").exists() else []
    assert left == [], "neither the file nor its .part survives a mismatch"


def test_the_after_install_bench_does_not_take_the_gpu_for_a_held_model(store, monkeypatch):
    """Measuring a held model would park the brain for a model no seat may use."""
    from agent_friday.services import model_bench
    p = store / "gguf" / "flint-test.gguf"
    p.parent.mkdir(parents=True)
    p.write_bytes(_gguf_bytes())
    model_store.register(FLINT, p, source=model_store.SOURCE_DOWNLOAD, held="waiting for CT2")
    ran = []
    out = model_bench.bench(FLINT, profile={"gpus": [{"vendor": "nvidia"}]},
                            runner=lambda *a: ran.append(a) or {}, through_lease=False)
    assert out["status"] == "skipped" and out["error"].startswith("held")
    assert ran == []


def test_the_models_screen_shows_a_held_flint_as_installed_and_held(store, monkeypatch):
    from agent_friday.services import model_catalog_rows as rows
    p = store / "gguf" / Q2_FILE
    p.parent.mkdir(parents=True)
    p.write_bytes(_gguf_bytes())
    model_store.register(FLINT, p, source=model_store.SOURCE_DOWNLOAD, held=sl.seat_hold(FLINT))
    from agent_friday.services import context_budget
    from agent_friday.services import model_fit as mf
    from agent_friday.services import residency_catalog as rc
    from tests import residency_fixtures as fx
    monkeypatch.setattr(mf, "calibration_path", lambda: store / "calibration.json")
    monkeypatch.setattr(rc, "measurements", lambda mid, fp: [])
    monkeypatch.setattr(context_budget, "overhead_tokens", lambda: 25000)
    monkeypatch.setattr(rows, "_measured_tok_s", lambda *a, **k: None)
    row = next(r for r in rows.catalog_payload(fx.P1)["models"] if r["id"] == FLINT)
    assert row["installed"] is True, "no second 13 GB download is offered for a held copy"
    assert row["held"] == sl.seat_hold(FLINT) and row["status"] == "experimental"
    assert row["roles"] == ["coder"]


def test_the_biggest_model_answer_is_never_the_coder(monkeypatch):
    """'What's the biggest model I can run?' asks about the model that talks
    with you; Flint only writes code, however big its file."""
    from agent_friday.services import local_models_tools as lmt
    from agent_friday.services import model_catalog_rows as rows
    inp = {"question": "Pretend I had 48 GB. What's the biggest I can run?",
           "pretend_vram_gb": 48, "pretend_ram_gb": 128}
    # The premise: on this pretend machine Flint's files run and are the
    # biggest on the list, so only the rule keeps it out of the answer (it
    # would otherwise be named, at least as "would also run").
    models = rows.catalog_payload(lmt._profile(inp))["models"]
    flint_row = next(r for r in models if r["id"] == FLINT)
    assert any(f["verdict"] != "wont_fit" for f in flint_row["files"])
    biggest_other = max(int(f["bytes"] or 0) for r in models if r["id"] != FLINT for f in r["files"])
    assert max(int(f["bytes"] or 0) for f in flint_row["files"]) > biggest_other
    out = lmt.advise(inp)
    assert out["kind"] == "biggest" and out["data"]["model"] not in (None, FLINT)
    assert "Flint" not in out["spoken"]


def test_a_hold_survives_a_record_rebuilt_without_it(store):
    """A rollback, a re-registration or an override rebuilds the record; the
    shipped list still holds the model, so none of them can lift the hold."""
    p = store / "gguf" / Q2_FILE
    p.parent.mkdir(parents=True)
    p.write_bytes(_gguf_bytes())
    model_store.register(FLINT, p, source=model_store.SOURCE_DOWNLOAD)          # no held=
    assert "held" not in model_store.get(FLINT)
    assert FLINT not in model_store.available()
    assert FLINT not in model_store.seat_file_map()
    assert model_store.missing()[FLINT]["why"].startswith("held")
    assert model_store.held_installed()[FLINT] == sl.seat_hold(FLINT)


def test_a_copy_of_flint_under_another_id_is_held_or_refused(store):
    """The same weights pasted by repo name must not become a seat the hold
    never covered."""
    out = md.start_pasted("unsloth/flint-GGUF", Q2_FILE, bytes_=Q2_BYTES, sha256=Q2_SHA,
                          start_thread=False)
    assert out["status"] == "refused" and md.jobs() == []
    out = md.start_pasted("RecursiveSI/flint", "flint.gguf", bytes_=10, sha256="a" * 64,
                          start_thread=False)
    assert out["status"] == "refused"
    p = store / "gguf" / "elsewhere.gguf"
    p.parent.mkdir(parents=True)
    p.write_bytes(_gguf_bytes())
    model_store.register("hf:flint-gguf", p, source=model_store.SOURCE_DOWNLOAD,
                         origin={"repo": "unsloth/flint-GGUF"})
    assert "hf:flint-gguf" not in model_store.available()


def test_a_held_flint_is_not_queued_again(store, windows_nvidia):
    p = store / "gguf" / Q2_FILE
    p.parent.mkdir(parents=True)
    p.write_bytes(_gguf_bytes())
    model_store.register(FLINT, p, source=model_store.SOURCE_DOWNLOAD, held=sl.seat_hold(FLINT))
    job = md.start_model(FLINT, packing="UD-Q4_K_XL", profile=windows_nvidia, start_thread=False)
    assert job["status"] == "refused" and "already installed" in job["error"]
    assert md.jobs() == []


def test_the_seat_lists_that_read_the_registry_file_skip_a_held_flint(tmp_path, monkeypatch):
    """local_seats and the model picker read models.json themselves; a held
    record is never a seat there either, while a normal one still is."""
    from agent_friday import paths
    from agent_friday.services import local_seats
    from agent_friday.services import model_catalog
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    gguf = tmp_path / "runtime" / "models" / "gguf"
    gguf.mkdir(parents=True)
    for name in ("flint.gguf", "bonsai.gguf"):
        (gguf / name).write_bytes(b"GGUF" + b"\x00" * 64)
    reg = {"version": 1, "models": {
        FLINT: {"path": str(gguf / "flint.gguf"), "size_bytes": 13562207680, "can_generate": True},
        "bonsai2:27b": {"path": str(gguf / "bonsai.gguf"), "size_bytes": 5946648928, "can_generate": True},
    }}
    (tmp_path / "runtime" / "models" / "models.json").write_text(json.dumps(reg), encoding="utf-8")
    seats = [mid for mid, _gb in local_seats._friday_store()]
    assert "bonsai2:27b" in seats and FLINT not in seats
    picker = [e["id"] for e in model_catalog._friday_store_entries_uncached()]
    assert "bonsai2:27b" in picker and FLINT not in picker


def test_a_pinned_runtime_folder_that_is_not_the_pinned_release_is_reinstalled(store, windows_nvidia):
    into = md.runtime_dir_for(RUNTIME)
    into.mkdir(parents=True)
    (into / "llama-server.exe").write_bytes(b"MZ")                 # no install record
    assert md.installed_runtime_problem(RUNTIME)
    job = md.start_model(FLINT, profile=windows_nvidia, start_thread=False)
    assert job["needs_runtime"] is True
    (into / "friday_runtime.json").write_text(json.dumps({"tag": "b11790", "build": 11790}),
                                              encoding="utf-8")
    assert "not the pinned" in md.installed_runtime_problem(RUNTIME)
    (into / "friday_runtime.json").write_text(json.dumps({"tag": "b11803", "build": 11803}),
                                              encoding="utf-8")
    assert md.installed_runtime_problem(RUNTIME) is None
    md._JOBS.clear()
    md._CTRL.clear()
    assert md.start_model(FLINT, profile=windows_nvidia, start_thread=False)["needs_runtime"] is False


def test_the_job_installs_flints_own_runtime_not_the_fork(store, windows_nvidia, monkeypatch):
    payload = _gguf_bytes()
    entry = dict(sl.get(FLINT))
    entry["files"] = [{"file": "flint-test.gguf", "packing": "Q2_K_XL", "bytes": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest(), "default": True}]
    monkeypatch.setattr(sl, "get", lambda mid: dict(entry) if mid == FLINT else None)

    class _Resp:
        status_code = 200

        def iter_content(self, chunk_size=1):
            yield payload

        def close(self):
            pass
    monkeypatch.setattr(md, "_http_get", lambda url, **kw: _Resp())
    asked = []
    monkeypatch.setattr(md, "_install_runtime",
                        lambda ctrl, name="prism-fork": asked.append(name) or Path("/x/llama-server.exe"))
    job = md.start_model(FLINT, profile=windows_nvidia, start_thread=False)
    out = md.run_job(job["id"])
    assert out["status"] == "installed", out.get("error")
    assert asked == [RUNTIME]


def test_a_companion_without_a_checksum_is_refused(store, monkeypatch):
    entry = {"id": "x:1", "label": "X", "runtime": "prism-fork", "repo": "a/b",
             "files": [{"file": "x.gguf", "packing": "Q4", "bytes": 10, "sha256": "a" * 64, "default": True}],
             "companions": [{"file": "mm.gguf", "kind": "mmproj", "bytes": 5}]}
    monkeypatch.setattr(sl, "get", lambda mid: dict(entry) if mid == "x:1" else None)
    monkeypatch.setattr(sl, "companions", lambda mid: list(entry["companions"]))
    job = md.start_model("x:1", start_thread=False)
    assert job["status"] == "refused" and "mm.gguf" in job["error"]


# ── who may seat it ──────────────────────────────────────────────────────────

def test_first_run_can_never_seat_flint():
    from agent_friday.services import first_run_models as frm
    assert sl.get(FLINT), "the entry exists, so the refusal below is about its role"
    for seat in frm.SEATS:
        assert frm.validate_choice(seat, FLINT) is not None, seat


def test_the_licence_record_names_flint_and_its_runtime():
    text = (REPO_ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    assert "unsloth/flint-GGUF" in text and "RecursiveSI/flint" in text
    row = next(line for line in text.splitlines() if "unsloth/flint-GGUF" in line)
    assert "Apache-2.0" in row and REVISION in row
    assert "b11803" in text
