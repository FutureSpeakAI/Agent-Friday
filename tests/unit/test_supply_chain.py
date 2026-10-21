"""What Friday ships, its licences and its known vulnerabilities (north star
§34.2-10 dependency review, §34.2-17 licence and SBOM, §34.17-6 no unreviewed
critical dependency exposure): scripts/supply_chain.py and its workflow.

Wheels here are tiny synthetic zips with a METADATA file, and OSV is a fake:
nothing is downloaded. The real resolve and audit run in CI
(.github/workflows/supply-chain.yml).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "supply_chain.py"
WORKFLOW = REPO / ".github" / "workflows" / "supply-chain.yml"
REVIEWED = REPO / "packaging" / "supply-chain" / "reviewed.json"


@pytest.fixture(scope="module")
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


NO_REVIEWS = {"licences": {}, "advisories": {}, "expired": []}


# ── reading a wheel's licence ────────────────────────────────────────────────

def test_the_licence_expression_wins_then_classifiers_then_free_text(sc, tmp_path):
    a = _wheel(tmp_path, "alpha", "1.0", "License-Expression: Apache-2.0\nClassifier: License :: OSI Approved :: MIT License")
    b = _wheel(tmp_path, "beta", "2.0", "Classifier: License :: OSI Approved :: MIT License\nLicense: whatever")
    c = _wheel(tmp_path, "gamma", "3.0", "License: BSD License")
    d = _wheel(tmp_path, "delta", "4.0", "Summary: no licence at all")
    assert sc.wheel_metadata(a)["licence"] == "Apache-2.0"
    assert sc.wheel_metadata(b)["licence"] == "MIT"
    assert sc.wheel_metadata(c)["licence"] == "BSD-3-Clause"
    assert sc.wheel_metadata(d)["licence"] == ""


def test_the_sbom_is_cyclonedx_with_exact_versions_hashes_and_purls(sc, tmp_path):
    w = tmp_path / "wheels"
    whl = _wheel(w, "Flask_Sock", "0.7.0", "License-Expression: MIT")
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "react-18.3.1.production.min.js").write_text("x")
    (vendor / "three-r128.min.js").write_text("x")
    (vendor / "three-r128-RenderPass.js").write_text("x")
    sbom = sc.build_sbom(w, vendor_dir=vendor, version="v9.9.9")
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    assert sbom["metadata"]["component"]["version"] == "v9.9.9"
    by = {c["name"]: c for c in sbom["components"]}
    flask_sock = by["Flask_Sock"]
    assert flask_sock["purl"] == "pkg:pypi/flask-sock@0.7.0"
    assert flask_sock["hashes"][0]["content"] == hashlib.sha256(whl.read_bytes()).hexdigest()
    assert by["react"]["version"] == "18.3.1" and by["react"]["licenses"][0]["expression"] == "MIT"
    threes = [c for c in sbom["components"] if c["name"] == "three.js"]
    assert len(threes) == 1, "one component per library and version, however many files"
    assert by["CPython"]["version"] == "3.12.10"


def test_every_vendored_file_in_the_tree_is_a_known_component(sc):
    unknown = [v for v in sc.vendored() if v["name"] == "unknown"]
    assert unknown == [], "a vendored file with no licence on record: %s" % unknown


def test_imageio_ffmpeg_is_judged_by_the_gpl_binary_it_bundles(sc, tmp_path):
    _wheel(tmp_path / "w", "imageio-ffmpeg", "0.6.0", "License-Expression: BSD-2-Clause")
    sbom = sc.build_sbom(tmp_path / "w", vendor_dir=tmp_path / "none")
    comp = next(c for c in sbom["components"] if c["name"] == "imageio-ffmpeg")
    assert "GPL-3.0" in comp["licenses"][0]["expression"]


# ── judging licences ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,ok", [
    ("MIT", True), ("Apache-2.0 OR GPL-2.0-only", True), ("MIT AND BSD-3-Clause", True),
    ("Apache-2.0 WITH LLVM-exception", True), ("MPL-2.0 AND (Apache-2.0 OR MIT)", True),
    ("(GPL-3.0-only OR MIT) AND GPL-2.0-only", False), ("MIT AND (", False), ("BSD-3-Clause, Apache-2.0", False),
    ("GPL-3.0-or-later", False), ("MIT AND GPL-2.0-only", False), ("", False), ("Proprietary", False),
])
def test_only_permissive_expressions_pass_unreviewed(sc, expr, ok):
    assert sc._expression_ok(expr) is ok


def test_a_copyleft_or_unknown_licence_fails_until_reviewed_for_that_exact_licence(sc, tmp_path):
    _wheel(tmp_path / "w", "copyleft", "1.0", "License-Expression: GPL-3.0-or-later")
    _wheel(tmp_path / "w", "mystery", "1.0", "Summary: none")
    sbom = sc.build_sbom(tmp_path / "w", vendor_dir=tmp_path / "none")
    bad = {b["name"]: b["licence"] for b in sc.judge_licences(sbom, NO_REVIEWS)}
    assert bad == {"copyleft": "GPL-3.0-or-later", "mystery": sc.NO_LICENCE}
    reviews = {"licences": {"copyleft": {"licence": "GPL-3.0-or-later"},
                            "mystery": {"licence": sc.NO_LICENCE}}, "advisories": {}, "expired": []}
    assert sc.judge_licences(sbom, reviews) == []
    changed = {"licences": {"copyleft": {"licence": "GPL-2.0-only"}}, "advisories": {}, "expired": []}
    assert [b["name"] for b in sc.judge_licences(sbom, changed)] == ["copyleft", "mystery"]


def test_a_review_counts_only_when_complete_and_until_its_date(sc, tmp_path):
    p = tmp_path / "reviewed.json"
    base = {"package": "x", "licence": "GPL-3.0-only", "reason": "r", "reviewed_by": "me"}
    p.write_text(json.dumps({"licences": [
        dict(base, until="2026-12-31"),
        dict(base, package="old", until="2026-01-01"),
        {"package": "half", "licence": "GPL-3.0-only", "until": "2027-01-01"},
    ]}), encoding="utf-8")
    got = sc.load_reviews(p, today=dt.date(2026, 10, 21))
    assert set(got["licences"]) == {"x"}
    assert [e["package"] for e in got["expired"]] == ["old"]


def test_the_shipped_reviews_are_complete_and_in_force(sc):
    data = json.loads(REVIEWED.read_text(encoding="utf-8"))
    for e in data["licences"] + data["advisories"]:
        for field in ("package", "reason", "reviewed_by", "until"):
            assert e.get(field), (e, field)
        assert dt.date.fromisoformat(e["until"]) >= dt.date(2026, 10, 21)
    loaded = sc.load_reviews(today=dt.date(2026, 10, 21))
    assert len(loaded["licences"]) == len(data["licences"]) and not loaded["expired"]


# ── advisories ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vuln,want", [
    ({"database_specific": {"severity": "CRITICAL"}}, "CRITICAL"),
    ({"database_specific": {"severity": "MEDIUM"}}, "MODERATE"),
    ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}, "CRITICAL"),
    ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:N/A:N"}]}, "MODERATE"),
    ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}]}, "HIGH"),
    ({}, "UNKNOWN"),
])
def test_severity_is_read_from_the_record_or_its_cvss_vector(sc, vuln, want):
    assert sc.severity(vuln) == want


def _osv(by_pkg: dict, details: dict):
    def post(url, body):
        return {"results": [{"vulns": [{"id": i} for i in by_pkg.get(q["package"]["name"], [])]}
                            for q in body["queries"]]}

    def get(url):
        return details[url.rsplit("/", 1)[1]]
    return post, get


def test_an_unreviewed_high_or_unknown_advisory_is_a_finding_and_lower_ones_are_reported(sc, tmp_path):
    for n in ("hot", "warm", "odd"):
        _wheel(tmp_path / "w", n, "1.0", "License-Expression: MIT")
    sbom = sc.build_sbom(tmp_path / "w", vendor_dir=tmp_path / "none")
    post, get = _osv({"hot": ["GHSA-1"], "warm": ["GHSA-2"], "odd": ["PYSEC-3"]},
                     {"GHSA-1": {"database_specific": {"severity": "HIGH"}, "aliases": ["CVE-1"]},
                      "GHSA-2": {"database_specific": {"severity": "LOW"}},
                      "PYSEC-3": {}})
    res = sc.audit(sbom, NO_REVIEWS, post=post, get=get)
    assert sorted(f["name"] for f in res["findings"]) == ["hot", "odd"]
    assert [r["name"] for r in res["reported"]] == ["warm"]
    reviewed = {"licences": {}, "advisories": {("hot", "CVE-1"): {}, ("odd", "PYSEC-3"): {}}, "expired": []}
    res = sc.audit(sbom, reviewed, post=post, get=get)
    assert res["findings"] == [] and {r["name"] for r in res["reported"]} == {"hot", "warm", "odd"}


def test_an_incomplete_answer_from_osv_is_an_error_not_a_pass(sc, tmp_path):
    _wheel(tmp_path / "w", "one", "1.0", "License-Expression: MIT")
    sbom = sc.build_sbom(tmp_path / "w", vendor_dir=tmp_path / "none")
    with pytest.raises(RuntimeError):
        sc.audit(sbom, NO_REVIEWS, post=lambda u, b: {"results": []}, get=lambda u: {})


def test_the_command_fails_on_an_uncleared_licence(sc, tmp_path, monkeypatch):
    _wheel(tmp_path / "w", "copyleft", "1.0", "License-Expression: AGPL-3.0-only")
    monkeypatch.setattr(sc, "VENDOR_DIR", tmp_path / "none")
    monkeypatch.setattr(sc, "load_reviews", lambda *a, **k: NO_REVIEWS)
    out = tmp_path / "out"
    assert sc.main(["all", "--wheels", str(tmp_path / "w"), "--out", str(out), "--offline"]) == 1
    assert (out / "sbom.cdx.json").exists()
    assert json.loads((out / "licences.json").read_text(encoding="utf-8"))[0]["name"] == "copyleft"


# ── the workflow ─────────────────────────────────────────────────────────────

def test_the_workflow_runs_on_tags_weekly_and_on_changes_to_what_ships():
    assert WORKFLOW.exists(), "no supply-chain workflow"
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = wf.get("on") or wf.get(True)
    assert "v*" in on["push"]["tags"] and on["schedule"] and "workflow_dispatch" in on
    paths = on["pull_request"]["paths"]
    assert "packaging/windows/requirements/**" in paths and "static/vendor/**" in paths
    job = wf["jobs"]["supply-chain"]
    assert job["runs-on"].startswith("windows"), "markers resolve for the platform pip runs on"
    run = " ".join(s.get("run", "") for s in job["steps"])
    assert "scripts/supply_chain.py all --resolve" in run
