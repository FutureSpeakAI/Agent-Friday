"""What Friday ships, its licences and its known vulnerabilities (north star
§34.2-10 dependency review, §34.2-17 licence and SBOM, §34.17-6 no unreviewed
critical dependency exposure): scripts/supply_chain.py and its workflows.

Wheels here are tiny synthetic zips with a METADATA file, and OSV is a fake:
nothing is downloaded. The real resolve and audit run in CI
(.github/workflows/supply-chain.yml, called by installer.yml on every tag).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "supply_chain.py"
WORKFLOW = REPO / ".github" / "workflows" / "supply-chain.yml"
INSTALLER = REPO / ".github" / "workflows" / "installer.yml"
REVIEWED = REPO / "packaging" / "supply-chain" / "reviewed.json"
BUILD = REPO / "packaging" / "windows" / "build-installer.ps1"


@pytest.fixture()
def sc():
    assert SCRIPT.exists(), "no scripts/supply_chain.py"
    spec = importlib.util.spec_from_file_location("supply_chain", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wheel(folder: Path, name: str, version: str, headers: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("%s-%s-py3-none-any.whl" % (name.replace("-", "_"), version))
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("%s-%s.dist-info/METADATA" % (name.replace("-", "_"), version),
                    "Metadata-Version: 2.4\nName: %s\nVersion: %s\n%s\n" % (name, version, headers))
    return path


def _sbom(sc, tmp_path, wheels: dict, vendor: dict | None = None):
    w = tmp_path / "wheels"
    for (name, version), headers in wheels.items():
        _wheel(w, name, version, headers)
    vdir = tmp_path / "vendor"
    vdir.mkdir(exist_ok=True)
    for f in vendor or {}:
        (vdir / f).write_text("x")
    src = tmp_path / "sources.json"
    src.write_text(json.dumps({"python": {"version": "3.12.10"}}), encoding="utf-8")
    return sc.build_sbom(w, vendor_dir=vdir, sources=src, version="v9")


def NO_REVIEWS():
    return {"licences": {}, "advisories": {}, "components": {}, "expired": [], "awaiting": []}


# ── what the SBOM holds ──────────────────────────────────────────────────────

def test_the_licence_expression_wins_then_classifiers_then_free_text(sc, tmp_path):
    a = _wheel(tmp_path, "alpha", "1.0", "License-Expression: Apache-2.0\nClassifier: License :: OSI Approved :: MIT License")
    b = _wheel(tmp_path, "beta", "2.0", "Classifier: License :: OSI Approved :: MIT License\nLicense: whatever")
    c = _wheel(tmp_path, "gamma", "3.0", "License: BSD License")
    d = _wheel(tmp_path, "delta", "4.0", "Summary: no licence at all")
    assert [sc.wheel_metadata(x)["licence"] for x in (a, b, c, d)] == ["Apache-2.0", "MIT", "BSD-3-Clause", ""]


def test_the_sbom_is_cyclonedx_with_exact_versions_hashes_and_purls(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {("Flask_Sock", "0.7.0"): "License-Expression: MIT",
                                ("odd", "1.0"): "License: Apache licensed, see LICENSE"},
                 {"react-18.3.1.production.min.js": 1, "three-r128.min.js": 1, "three-r128-RenderPass.js": 1})
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    by = {c["name"]: c for c in sbom["components"]}
    whl = next((tmp_path / "wheels").glob("Flask_Sock*.whl"))
    assert by["Flask_Sock"]["purl"] == "pkg:pypi/flask-sock@0.7.0"
    assert by["Flask_Sock"]["hashes"][0]["content"] == hashlib.sha256(whl.read_bytes()).hexdigest()
    assert by["Flask_Sock"]["licenses"] == [{"expression": "MIT"}]
    assert by["odd"]["licenses"] == [{"license": {"name": "Apache licensed, see LICENSE"}}], \
        "text that is not an SPDX expression goes in license.name"
    assert by["react"]["purl"] == "pkg:npm/react@18.3.1"
    assert [c["purl"] for c in sbom["components"] if c["name"] == "three.js"] == ["pkg:npm/three@0.128.0"]
    assert by["CPython"]["purl"] == "pkg:generic/cpython@3.12.10" and "cpe:2.3:a:python:python:3.12.10" in by["CPython"]["cpe"]


def test_the_bundled_python_version_comes_from_sources_json(sc):
    want = json.loads((REPO / "packaging" / "windows" / "sources.json").read_text(encoding="utf-8"))["python"]["version"]
    assert sc.bundled_python()[1] == want


def test_the_wheelhouse_targets_are_the_ones_build_installer_builds(sc):
    m = re.search(r"\$targets = @\(([^)]*)\)", BUILD.read_text(encoding="utf-8"))
    assert m, "build-installer.ps1 has no $targets list"
    assert tuple(re.findall(r"'([^']+)'", m.group(1))) == sc.WHEELHOUSE_TARGETS


def test_every_vendored_file_in_the_tree_is_a_known_component(sc):
    unknown = [v for v in sc.vendored() if v["name"] == "unknown"]
    assert unknown == [], "a vendored file with no licence on record: %s" % unknown


def test_imageio_ffmpeg_is_judged_by_the_gpl_binary_it_bundles(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {("imageio-ffmpeg", "0.6.0"): "License-Expression: BSD-2-Clause"})
    comp = next(c for c in sbom["components"] if c["name"] == "imageio-ffmpeg")
    assert "GPL-3.0" in comp["licenses"][0]["expression"]


# ── licences ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,ok", [
    ("MIT", True), ("Apache-2.0 OR GPL-2.0-only", True), ("MIT AND BSD-3-Clause", True),
    ("Apache-2.0 WITH LLVM-exception", True), ("(MIT OR Apache-2.0) AND BSD-2-Clause", True),
    ("MPL-2.0", False), ("LGPL-3.0-or-later", False), ("MPL-2.0 AND (Apache-2.0 OR MIT)", False),
    ("(GPL-3.0-only OR MIT) AND GPL-2.0-only", False), ("MIT AND (", False),
    ("BSD-3-Clause, Apache-2.0", False), ("GPL-3.0-or-later", False), ("", False), ("Proprietary", False),
])
def test_only_permissive_expressions_pass_unreviewed_and_weak_copyleft_needs_a_review(sc, expr, ok):
    assert sc._expression_ok(expr) is ok


def test_a_copyleft_or_unknown_licence_fails_until_reviewed_for_that_exact_licence(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {("copyleft", "1.0"): "License-Expression: GPL-3.0-or-later",
                                ("mystery", "1.0"): "Summary: none"})
    bad = {b["name"]: b["licence"] for b in sc.judge_licences(sbom, NO_REVIEWS())}
    assert bad == {"copyleft": "GPL-3.0-or-later", "mystery": sc.NO_LICENCE}
    reviews = NO_REVIEWS()
    reviews["licences"] = {"copyleft": {"licence": "GPL-3.0-or-later"}, "mystery": {"licence": sc.NO_LICENCE}}
    assert sc.judge_licences(sbom, reviews) == []
    reviews["licences"]["copyleft"] = {"licence": "GPL-2.0-only"}
    assert [b["name"] for b in sc.judge_licences(sbom, reviews)] == ["copyleft"]


def test_a_review_counts_only_signed_by_a_named_reviewer_until_its_date(sc, tmp_path):
    p = tmp_path / "reviewed.json"
    base = {"package": "x", "licence": "GPL-3.0-only", "reason": "r", "reviewed_by": "Stephen Webster"}
    p.write_text(json.dumps({"licences": [
        dict(base, until="2026-12-31"),
        dict(base, package="old", until="2026-01-01"),
        dict(base, package="unsigned", reviewed_by="", until="2027-01-01"),
        dict(base, package="placeholder", reviewed_by="carried over; release lead to confirm", until="2027-01-01"),
    ], "components": [dict(base, package="CPython", version="3.12.10", until="2026-12-31")]}), encoding="utf-8")
    got = sc.load_reviews(p, today=dt.date(2026, 10, 21))
    assert set(got["licences"]) == {"x"}
    assert [e["package"] for e in got["expired"]] == ["old"]
    assert sorted(e["package"] for e in got["awaiting"]) == ["placeholder", "unsigned"]
    assert ("cpython", "3.12.10") in got["components"]


def test_the_shipped_reviews_file_is_well_formed_and_signs_nothing_on_the_builders_behalf(sc):
    data = json.loads(REVIEWED.read_text(encoding="utf-8"))
    for kind in ("licences", "advisories", "components"):
        for e in data[kind]:
            assert e.get("package") and e.get("reason"), e
            assert not str(e.get("reviewed_by") or "").strip(), \
                "only a named reviewer signs an entry; the builder proposes"
    assert any(e["package"] == "CPython" for e in data["components"])


def test_a_package_the_third_party_record_does_not_name_is_found(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {("anthropic", "1.0"): "License-Expression: MIT",
                                ("brand-new-thing", "1.0"): "License-Expression: MIT"})
    names = [u["name"] for u in sc.unattributed(sbom)]
    assert names == ["brand-new-thing"]


# ── advisories ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vuln,want", [
    ({"database_specific": {"severity": "CRITICAL"}}, "CRITICAL"),
    ({"database_specific": {"severity": "MEDIUM"}}, "MODERATE"),
    ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}, "CRITICAL"),
    ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:N/A:N"}]}, "MODERATE"),
    ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}]}, "HIGH"),
    ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:X/C:H/I:H/A:H"}]}, "UNKNOWN"),
    ({}, "UNKNOWN"),
])
def test_severity_is_read_from_the_record_or_its_cvss_vector(sc, vuln, want):
    assert sc.severity(vuln) == want


@pytest.mark.parametrize("vector,score", [
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
    ("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 7.8),
    ("CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:C/C:L/I:L/A:N", 4.7),
])
def test_the_cvss_base_score_matches_the_specification(sc, vector, score):
    assert sc._cvss_base(vector) == score


def _osv(by_pkg: dict, details: dict, pages: dict | None = None):
    asked = []

    def post(url, body):
        asked.append(body)
        out = []
        for q in body["queries"]:
            name = q["package"]["name"]
            token = q.get("page_token")
            page = (pages or {}).get((name, token))
            ids = page[0] if page else by_pkg.get(name, []) if token is None else []
            res = {"vulns": [{"id": i} for i in ids]}
            if page and page[1]:
                res["next_page_token"] = page[1]
            out.append(res)
        return {"results": out}

    def get(url):
        return details[url.rsplit("/", 1)[1]]
    return post, get, asked


def _reviewed_cpython(sc):
    r = NO_REVIEWS()
    r["components"] = {("cpython", "3.12.10"): {}}
    return r


def test_an_unreviewed_high_or_unknown_advisory_is_a_finding_and_lower_ones_are_reported(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {(n, "1.0"): "License-Expression: MIT" for n in ("hot", "warm", "odd")})
    post, get, _ = _osv({"hot": ["GHSA-1"], "warm": ["GHSA-2"], "odd": ["PYSEC-3"]},
                        {"GHSA-1": {"database_specific": {"severity": "HIGH"}, "aliases": ["CVE-1"]},
                         "GHSA-2": {"database_specific": {"severity": "LOW"}},
                         "PYSEC-3": {}})
    res = sc.audit(sbom, _reviewed_cpython(sc), post=post, get=get)
    assert sorted(f["name"] for f in res["findings"]) == ["hot", "odd"]
    assert [r["name"] for r in res["reported"]] == ["warm"]
    reviews = _reviewed_cpython(sc)
    reviews["advisories"] = {("hot", "CVE-1"): {}, ("odd", "PYSEC-3"): {}}
    res = sc.audit(sbom, reviews, post=post, get=get)
    assert res["findings"] == []


def test_an_unknown_severity_takes_its_ghsa_alias_and_withdrawn_records_are_skipped(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {("pkg", "1.0"): "License-Expression: MIT"})
    post, get, _ = _osv({"pkg": ["PYSEC-9", "GHSA-gone"]},
                        {"PYSEC-9": {"aliases": ["GHSA-low"]},
                         "GHSA-low": {"database_specific": {"severity": "LOW"}},
                         "GHSA-gone": {"withdrawn": "2026-01-01T00:00:00Z", "database_specific": {"severity": "CRITICAL"}}})
    res = sc.audit(sbom, _reviewed_cpython(sc), post=post, get=get)
    assert res["findings"] == [] and [r["severity"] for r in res["reported"]] == ["LOW"]


def test_every_page_of_advisories_is_read(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {("many", "1.0"): "License-Expression: MIT"})
    post, get, asked = _osv({}, {"A": {"database_specific": {"severity": "LOW"}},
                                 "B": {"database_specific": {"severity": "CRITICAL"}}},
                            pages={("many", None): (["A"], "t2"), ("many", "t2"): (["B"], None)})
    res = sc.audit(sbom, _reviewed_cpython(sc), post=post, get=get)
    assert [f["id"] for f in res["findings"]] == ["B"] and len(asked) == 2


def test_the_vendored_browser_code_is_audited_as_npm(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {}, {"marked-9.1.6.min.js": 1})
    post, get, asked = _osv({"marked": ["GHSA-m"]}, {"GHSA-m": {"database_specific": {"severity": "HIGH"}}})
    res = sc.audit(sbom, _reviewed_cpython(sc), post=post, get=get)
    assert asked[0]["queries"][0]["package"] == {"name": "marked", "ecosystem": "npm"}
    assert [f["ecosystem"] for f in res["findings"]] == ["npm"]


def test_the_bundled_cpython_needs_a_review_of_its_exact_version(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {})
    post, get, _ = _osv({}, {})
    res = sc.audit(sbom, NO_REVIEWS(), post=post, get=get)
    assert [(f["name"], f["id"]) for f in res["findings"]] == [("CPython", "NO-ADVISORY-DATABASE")]
    assert sc.audit(sbom, _reviewed_cpython(sc), post=post, get=get)["findings"] == []


def test_an_incomplete_answer_from_osv_is_an_error_not_a_pass(sc, tmp_path):
    sbom = _sbom(sc, tmp_path, {("one", "1.0"): "License-Expression: MIT"})
    with pytest.raises(RuntimeError):
        sc.audit(sbom, NO_REVIEWS(), post=lambda u, b: {"results": []}, get=lambda u: {})


# ── the verdict ──────────────────────────────────────────────────────────────

def _check(sc, monkeypatch, sbom, out, **kw):
    monkeypatch.setattr(sc, "load_reviews", lambda *a, **k: NO_REVIEWS())
    monkeypatch.setattr(sc, "unattributed", lambda s, path=None: [])
    return sc.check(sbom, out, offline=True, **kw)


def test_an_uncleared_licence_fails_and_a_beta_only_records(sc, tmp_path, monkeypatch):
    sbom = _sbom(sc, tmp_path, {("copyleft", "1.0"): "License-Expression: AGPL-3.0-only"})
    out = tmp_path / "out"
    assert _check(sc, monkeypatch, sbom, out) == 1
    rec = json.loads((out / "supply-chain.json").read_text(encoding="utf-8"))
    assert rec["ok"] is False and rec["licences"][0]["name"] == "copyleft" and rec["audit"] is None
    assert _check(sc, monkeypatch, sbom, out, report_only=True) == 0


def test_a_pull_request_fails_only_on_what_it_adds(sc, tmp_path, monkeypatch):
    base = _sbom(sc, tmp_path / "b", {("old-gpl", "1.0"): "License-Expression: GPL-3.0-only"})
    head = _sbom(sc, tmp_path / "h", {("old-gpl", "1.0"): "License-Expression: GPL-3.0-only"})
    assert _check(sc, monkeypatch, head, tmp_path / "o1", base=base) == 0
    head = _sbom(sc, tmp_path / "h2", {("old-gpl", "1.0"): "License-Expression: GPL-3.0-only",
                                       ("new-gpl", "2.0"): "License-Expression: GPL-3.0-only"})
    assert _check(sc, monkeypatch, head, tmp_path / "o2", base=base) == 1
    rec = json.loads((tmp_path / "o2" / "supply-chain.json").read_text(encoding="utf-8"))
    assert [b["name"] for b in rec["licences"]] == ["new-gpl"]
    assert [b["name"] for b in rec["on_the_base"]["licences"]] == ["old-gpl"]


# ── the workflows ────────────────────────────────────────────────────────────

def _yaml(path: Path) -> dict:
    assert path.exists(), path
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_release_build_writes_the_sbom_from_its_staged_wheelhouse_and_waits_for_the_verdict():
    inst = _yaml(INSTALLER)
    build_run = " ".join(s.get("run", "") for s in inst["jobs"]["build"]["steps"])
    assert "supply_chain.py sbom --wheels packaging\\windows\\staging\\wheelhouse" in build_run
    job = inst["jobs"]["supply-chain"]
    assert job["uses"] == "./.github/workflows/supply-chain.yml" and job["with"]["called"] is True
    assert "contains(github.ref_name, '-')" in str(job["with"]["report_only"])
    assert "supply-chain" in inst["jobs"]["release"]["needs"]
    publish = " ".join(s.get("run", "") for s in inst["jobs"]["release"]["steps"])
    assert "AgentFriday-Setup-*.sbom.cdx.json" in publish


def test_the_workflow_reviews_pull_requests_and_audits_weekly():
    wf = _yaml(WORKFLOW)
    on = wf.get("on") or wf.get(True)
    assert "workflow_call" in on and on["schedule"] and "workflow_dispatch" in on
    paths = on["pull_request"]["paths"]
    for p in ("packaging/windows/requirements/**", "static/vendor/**", "packaging/windows/sources.json",
              "packaging/windows/build-installer.ps1", "THIRD_PARTY_LICENSES.md"):
        assert p in paths, p
    review = wf["jobs"]["dependency-review"]
    assert review["runs-on"].startswith("windows"), "markers resolve for the platform pip runs on"
    run = " ".join(s.get("run", "") for s in review["steps"])
    assert "--base-sbom" in run and "supply_chain.py resolve" in run
    weekly = " ".join(s.get("run", "") for s in wf["jobs"]["weekly"]["steps"])
    assert "gh release download" in weekly and "*.sbom.cdx.json" in weekly
