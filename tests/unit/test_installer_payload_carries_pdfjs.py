"""The Library's pdf.js rides in the installer zip, whole.

build-installer.ps1 copies the repository's top-level entries into the payload, so
static/vendor/pdfjs-6.4.299/ ships unless a list in that script reaches into it. This reads the
build tool's own lists and applies them to every tracked file of the folder, and pins what the
build does about it: it names the files the Reader imports among the entry points the payload must
hold, and it checks each vendored library against its VERSION.json before anything is packed.
"""
from __future__ import annotations

import fnmatch
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "packaging" / "windows" / "build-installer.ps1").read_text(encoding="utf-8", errors="replace")
VENDOR = "static/vendor/pdfjs-6.4.299"
READER = (ROOT / "static" / "library_reader.js").read_text(encoding="utf-8")


def _quoted(block: str) -> list[str]:
    """The single-quoted words of a PowerShell list, comments left out."""
    return re.findall(r"'([^']+)'", re.sub(r"#[^\n]*", "", block))


def _function_list(name: str) -> list[str]:
    m = re.search(r"function %s \{.*?return @\((.*?)\)\n\}" % re.escape(name), SCRIPT, re.S)
    assert m, name
    return _quoted(m.group(1))


def _tracked(prefix: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", prefix], cwd=str(ROOT), capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _payload(rel_paths: list[str]) -> list[str]:
    """The files of `rel_paths` the build keeps, by the build's own rules: a root-level entry named in
    the exclusion list or matching a scratch pattern is not copied, then junk folders and junk
    extensions are removed wherever they are."""
    names = {n.lower() for n in _function_list("Get-PayloadExcludes")}
    patterns = [p.lower() for p in _function_list("Get-PayloadExcludePatterns")]
    junk_dirs = set(_quoted(re.search(r"foreach \(\$junk in @\((.*?)\)\)", SCRIPT, re.S).group(1)))
    junk_ext = set(_quoted(re.search(r"\$junkExt = @\((.*?)\)", SCRIPT, re.S).group(1)))
    kept = []
    for rel in rel_paths:
        parts = rel.split("/")
        top = parts[0].lower()
        if top in names or any(fnmatch.fnmatchcase(top, p) for p in patterns):
            continue
        if junk_dirs & set(parts[:-1]) or pathlib.PurePosixPath(rel).suffix.lower() in junk_ext:
            continue
        kept.append(rel)
    return kept


def test_every_file_of_the_vendored_pdfjs_survives_the_builds_own_exclusions():
    tracked = _tracked(VENDOR)
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / VENDOR).rglob("*") if p.is_file()}
    assert len(tracked) > 150 and set(tracked) == on_disk, "every vendored file is tracked, or the build refuses it"
    assert sorted(_payload(tracked)) == sorted(tracked)


def test_the_files_the_reader_imports_are_required_entry_points_of_the_payload():
    m = re.search(r"\$mustExist = @\((.*?)\n\)", SCRIPT, re.S)
    assert m
    required = {n.replace("\\", "/") for n in _quoted(m.group(1))}
    base = re.search(r"PDFJS_BASE = '/(static/vendor/pdfjs-[\d.]+)/'", READER).group(1)
    assert base == VENDOR, "the Reader and the installer name the same folder"
    imported = {f"{base}/{rel}" for rel in re.findall(r"PDFJS_BASE \+ '([^']+\.(?:mjs|js))'", READER)}
    assert imported == {f"{VENDOR}/legacy/pdf.min.mjs", f"{VENDOR}/legacy/pdf.worker.min.js"}
    assert imported | {f"{VENDOR}/VERSION.json"} <= required
    for rel in required:
        assert (ROOT / rel).is_file(), rel
    for folder in re.findall(r"PDFJS_BASE \+ '([a-z_]+/)'", READER):        # cmaps/, standard_fonts/, wasm/, iccs/
        assert any(p.startswith(f"{VENDOR}/{folder}") for p in _payload(_tracked(VENDOR))), folder


def test_the_build_checks_every_vendored_library_against_its_manifest_before_it_packs():
    assert "static\\vendor" in SCRIPT and "VERSION.json" in SCRIPT and "Get-FileHash" in SCRIPT
    assert "-FailedStep 'build.vendor'" in SCRIPT, "a failed check aborts through the build report like every other step"
    assert "$vendorLibs -eq 0" in SCRIPT, "no manifest at all is a failure: a check with nothing to check passes for the wrong reason"
    at = {k: SCRIPT.index(k) for k in ("$mustExist = @(", "Checking the vendored libraries", "$leakPatterns", "CreateFromDirectory")}
    assert at["$mustExist = @("] < at["Checking the vendored libraries"] < at["$leakPatterns"] < at["CreateFromDirectory"]


def test_the_manifest_the_build_reads_pins_every_file_it_ships():
    manifest = json.loads((ROOT / VENDOR / "VERSION.json").read_text(encoding="utf-8"))
    shipped = {rel[len(VENDOR) + 1:] for rel in _payload(_tracked(VENDOR))} - {"VERSION.json"}
    assert shipped == set(manifest["files"]), "the build checks what the manifest names, so it must name everything shipped"
