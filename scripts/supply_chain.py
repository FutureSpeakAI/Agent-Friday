#!/usr/bin/env python3
"""What Friday ships, its licences, and its known vulnerabilities.

North star §34.2-10 (dependency review gate), §34.2-17 (licence and SBOM
generation) and §34.17-6 (no unreviewed critical dependency exposure).

The Windows setup program installs Python packages from a wheelhouse that
build-installer.ps1 collects with ``pip download --only-binary=:all:`` for
CPython 3.12 on win_amd64, from the tier files in
packaging/windows/requirements/. ``resolve`` collects the same set the same way
(it works on any OS: pip is told the target platform), so what this script
judges is what the installer carries, at the versions it carries them.

    sbom      write a CycloneDX 1.5 SBOM: every wheel (name, version, purl,
              SHA-256, licence from the wheel's own METADATA), the vendored
              JavaScript under static/vendor/, and the bundled CPython
    licences  judge every component's licence: permissive licences pass;
              copyleft, unknown or missing licences fail unless reviewed
    audit     ask OSV (api.osv.dev) about every component's exact version;
              a HIGH or CRITICAL advisory, or one whose severity cannot be
              read, fails unless reviewed
    all       resolve (optional), sbom, licences, audit

Reviews live in packaging/supply-chain/reviewed.json: each names the package,
the licence or advisory id, why it is acceptable, who reviewed it and until
when. An expired review no longer counts. Nothing here edits that file.

Usage:
    python scripts/supply_chain.py all --resolve --wheels WHEELS --out OUT_DIR
    python scripts/supply_chain.py all --wheels WHEELS --out OUT_DIR --offline
"""
from __future__ import annotations

import argparse
import datetime as _dt
import email.parser
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
import uuid
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TIERS = ("core", "recommended", "memory", "judgment")
REQ_DIR = REPO / "packaging" / "windows" / "requirements"
VENDOR_DIR = REPO / "static" / "vendor"
REVIEWED = REPO / "packaging" / "supply-chain" / "reviewed.json"
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"

#: The interpreter build-installer.ps1 bundles (the embeddable distribution).
BUNDLED_PYTHON = ("CPython", "3.12.10", "PSF-2.0")

#: Licences that pass without a review (SPDX ids, compared case-insensitively).
PERMISSIVE = {
    "mit", "mit-0", "mit-cmu", "bsd-2-clause", "bsd-3-clause", "0bsd", "isc", "apache-2.0",
    "psf-2.0", "python-2.0", "zlib", "unlicense", "cc0-1.0", "hpnd", "bsl-1.0", "wtfpl",
    "mpl-2.0", "lgpl-2.1-only", "lgpl-2.1-or-later", "lgpl-3.0-only", "lgpl-3.0-or-later",
    "upl-1.0", "ncsa", "x11", "curl", "openssl", "apache-1.1", "cnri-python",
}

#: Trove classifiers and free-text licence names, read as SPDX ids.
_ALIASES = {
    "mit license": "MIT", "mit": "MIT", "the mit license": "MIT", "expat": "MIT",
    "bsd license": "BSD-3-Clause", "bsd": "BSD-3-Clause", "new bsd license": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause", "3-clause bsd": "BSD-3-Clause", "modified bsd": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause", "3-clause bsd license": "BSD-3-Clause", "simplified bsd": "BSD-2-Clause", "bsd-2-clause": "BSD-2-Clause",
    "apache software license": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0", "apache 2.0": "Apache-2.0", "apache-2.0": "Apache-2.0",
    "apache 2": "Apache-2.0", "apache2": "Apache-2.0", "apache license version 2.0": "Apache-2.0",
    "isc license (iscl)": "ISC", "isc license": "ISC", "isc": "ISC",
    "python software foundation license": "PSF-2.0", "psf": "PSF-2.0", "psf-2.0": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0", "mpl-2.0": "MPL-2.0", "mpl 2.0": "MPL-2.0",
    "the unlicense (unlicense)": "Unlicense", "zlib/libpng license": "Zlib",
    "historical permission notice and disclaimer (hpnd)": "HPND",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1-or-later",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "gnu lesser general public license v3 or later (lgplv3+)": "LGPL-3.0-or-later",
    "gnu general public license v2 (gplv2)": "GPL-2.0-only",
    "gnu general public license v2 or later (gplv2+)": "GPL-2.0-or-later",
    "gnu general public license v3 (gplv3)": "GPL-3.0-only",
    "gnu general public license v3 or later (gplv3+)": "GPL-3.0-or-later",
    "gnu affero general public license v3": "AGPL-3.0-only",
    "boost software license 1.0 (bsl-1.0)": "BSL-1.0",
    "public domain": "LicenseRef-public-domain",
}

#: Components whose wheel metadata understates what the wheel carries, as
#: THIRD_PARTY_LICENSES.md records it: imageio-ffmpeg's wrapper is BSD, but the
#: ffmpeg.exe it bundles is GPL-3.0. The recorded expression is what is judged.
KNOWN_BUNDLED = {
    "imageio-ffmpeg": "BSD-2-Clause AND GPL-3.0-or-later",
}

#: Vendored browser code: file name pattern -> (component, licence).
_VENDOR = (
    (r"^react-(\d[\w.]*)\.production", "react", "MIT"),
    (r"^react-dom-(\d[\w.]*)\.production", "react-dom", "MIT"),
    (r"^marked-(\d[\w.]*)\.min\.js$", "marked", "MIT"),
    (r"^highlight-(\d[\w.]*)\.min\.js$", "highlight.js", "BSD-3-Clause"),
    (r"^atom-one-dark-(\d[\w.]*)\.min\.css$", "highlight.js atom-one-dark theme", "BSD-3-Clause"),
    (r"^three-(r\d+)[.-]", "three.js", "MIT"),
    (r"^pdfjs-(\d[\w.]*)$", "pdf.js", "Apache-2.0"),
)


# ── resolve ──────────────────────────────────────────────────────────────────

def resolve(wheels: Path) -> None:
    """Collect the wheelhouse the way build-installer.ps1 does, for win_amd64."""
    wheels.mkdir(parents=True, exist_ok=True)
    for tier in TIERS:
        cmd = [sys.executable, "-m", "pip", "download", "--only-binary=:all:",
               "--disable-pip-version-check", "--no-input",
               "--platform", "win_amd64", "--python-version", "3.12",
               "--implementation", "cp", "--abi", "cp312",
               "--dest", str(wheels), "--find-links", str(wheels),
               "-r", str(REQ_DIR / (tier + ".txt"))]
        print("resolving", tier, flush=True)
        subprocess.run(cmd, check=True)


# ── read a wheel ─────────────────────────────────────────────────────────────

def _spdx(text: str) -> str:
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return ""
    return _ALIASES.get(t.lower(), t)


def wheel_metadata(path: Path) -> dict:
    """Name, version and licence of one wheel, from its own METADATA."""
    with zipfile.ZipFile(path) as zf:
        meta_name = next((n for n in zf.namelist()
                          if n.endswith(".dist-info/METADATA") and n.count("/") == 1), None)
        raw = zf.read(meta_name).decode("utf-8", "replace") if meta_name else ""
    msg = email.parser.Parser().parsestr(raw, headersonly=True)
    name = msg.get("Name") or path.name.split("-")[0]
    version = msg.get("Version") or (path.name.split("-")[1] if "-" in path.name else "")
    expression = (msg.get("License-Expression") or "").strip()
    classifiers = [c.split("::")[-1].strip() for c in msg.get_all("Classifier") or []
                   if c.startswith("License ::") and c.strip() != "License :: OSI Approved"]
    free = (msg.get("License") or "").strip()
    if expression:
        licence, source = expression, "License-Expression"
    elif classifiers:
        licence, source = " AND ".join(dict.fromkeys(_spdx(c) for c in classifiers)), "classifier"
    elif free and len(free) <= 80 and "\n" not in free:
        licence, source = _spdx(free), "License"
    else:
        licence, source = "", "none"
    return {"name": name, "version": version, "licence": licence, "licence_source": source}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ── sbom ─────────────────────────────────────────────────────────────────────

def vendored(vendor_dir: Path = VENDOR_DIR) -> list:
    """The vendored browser components, one per (name, version), with the files
    each covers. A file no pattern names is returned under "unknown"."""
    seen: dict = {}
    for p in sorted(vendor_dir.iterdir()) if vendor_dir.exists() else []:
        for pattern, comp, lic in _VENDOR:
            m = re.match(pattern, p.name)
            if m:
                entry = seen.setdefault((comp, m.group(1)), {"name": comp, "version": m.group(1),
                                                             "licence": lic, "files": []})
                entry["files"].append(p.name)
                break
        else:
            seen.setdefault(("unknown", p.name), {"name": "unknown", "version": "", "licence": "",
                                                  "files": [p.name]})
    return list(seen.values())


def build_sbom(wheels: Path, *, vendor_dir: Path = VENDOR_DIR, version: str = "") -> dict:
    comps = []
    for whl in sorted(wheels.glob("*.whl")):
        md = wheel_metadata(whl)
        if _norm(md["name"]) in KNOWN_BUNDLED:
            md["licence"] = KNOWN_BUNDLED[_norm(md["name"])]
            md["licence_source"] = "THIRD_PARTY_LICENSES.md (bundled binary)"
        comps.append({
            "type": "library", "bom-ref": "pypi:%s@%s" % (_norm(md["name"]), md["version"]),
            "name": md["name"], "version": md["version"],
            "purl": "pkg:pypi/%s@%s" % (_norm(md["name"]), md["version"]),
            "hashes": [{"alg": "SHA-256", "content": _sha256(whl)}],
            "licenses": [{"expression": md["licence"]}] if md["licence"] else [],
            "properties": [{"name": "friday:licence-source", "value": md["licence_source"]},
                           {"name": "friday:file", "value": whl.name}],
        })
    for v in vendored(vendor_dir):
        comps.append({
            "type": "library", "bom-ref": "vendor:%s@%s" % (v["name"], v["version"] or v["files"][0]),
            "name": v["name"] if v["name"] != "unknown" else "static/vendor/" + v["files"][0],
            "version": v["version"],
            "licenses": [{"expression": v["licence"]}] if v["licence"] else [],
            "properties": [{"name": "friday:file", "value": ", ".join("static/vendor/" + f for f in v["files"])}],
        })
    name, ver, lic = BUNDLED_PYTHON
    comps.append({"type": "application", "bom-ref": "python:%s" % ver, "name": name, "version": ver,
                  "licenses": [{"expression": lic}],
                  "properties": [{"name": "friday:file", "value": "embeddable distribution"}]})
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5",
        "serialNumber": "urn:uuid:%s" % uuid.uuid4(), "version": 1,
        "metadata": {"timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                     "component": {"type": "application", "name": "Agent Friday", "version": version,
                                   "licenses": [{"expression": "MIT"}]}},
        "components": comps,
    }


def _licence_of(comp: dict) -> str:
    lic = comp.get("licenses") or []
    return str((lic[0] or {}).get("expression") or "") if lic else ""


# ── reviews ──────────────────────────────────────────────────────────────────

def load_reviews(path: Path = REVIEWED, today: _dt.date | None = None) -> dict:
    """{"licences": {pkg: entry}, "advisories": {(pkg, id): entry}} for reviews in force."""
    today = today or _dt.date.today()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = {"licences": {}, "advisories": {}, "expired": []}
    for kind, key in (("licences", "licence"), ("advisories", "id")):
        for e in data.get(kind) or []:
            if not isinstance(e, dict) or not e.get("package") or not e.get(key) or not e.get("reason") \
                    or not e.get("reviewed_by") or not e.get("until"):
                continue
            try:
                until = _dt.date.fromisoformat(str(e["until"]))
            except ValueError:
                continue
            if until < today:
                out["expired"].append(e)
                continue
            if kind == "licences":
                out[kind][_norm(e["package"])] = e
            else:
                out[kind][(_norm(e["package"]), str(e["id"]))] = e
    return out


# ── licences ─────────────────────────────────────────────────────────────────

#: How a component with no licence in its metadata is named (and reviewed).
NO_LICENCE = "(none found)"


def _expression_ok(expr: str) -> bool:
    """True when an SPDX expression is permissive: every id an AND joins, and at
    least one side of each OR. Parentheses nest; `X WITH exception` is judged
    by X. Anything that does not parse is not permissive."""
    tokens = re.findall(r"\(|\)|[^\s()]+", expr or "")
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def factor():
        tok = peek()
        if tok is None or tok in (")",) or tok.upper() in ("AND", "OR", "WITH"):
            raise ValueError(expr)
        if tok == "(":
            take()
            val = either()
            if take() != ")":
                raise ValueError(expr)
            return val
        ident = take()
        if (peek() or "").upper() == "WITH":
            take()
            take()
        return ident.lower() in PERMISSIVE

    def both():
        val = factor()
        while (peek() or "").upper() == "AND":
            take()
            val = factor() and val
        return val

    def either():
        val = both()
        while (peek() or "").upper() == "OR":
            take()
            val = both() or val
        return val

    try:
        ok = either()
    except (ValueError, IndexError):
        return False
    return ok and pos == len(tokens) and bool(tokens)


def judge_licences(sbom: dict, reviews: dict) -> list:
    """Every component whose licence is not permissive and not reviewed."""
    bad = []
    for c in sbom["components"]:
        lic = _licence_of(c)
        if _expression_ok(lic):
            continue
        r = reviews["licences"].get(_norm(c["name"]))
        if r and str(r.get("licence")) == (lic or NO_LICENCE):
            continue
        bad.append({"name": c["name"], "version": c["version"], "licence": lic or NO_LICENCE})
    return bad


# ── audit ────────────────────────────────────────────────────────────────────

def _post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def _cvss_base(vector: str) -> float | None:
    """The base score of a CVSS v3 vector, or None."""
    m = dict(p.split(":", 1) for p in vector.split("/")[1:] if ":" in p)
    try:
        av = {"N": .85, "A": .62, "L": .55, "P": .2}[m["AV"]]
        ac = {"L": .77, "H": .44}[m["AC"]]
        ui = {"N": .85, "R": .62}[m["UI"]]
        s = m["S"]
        pr = {"N": .85, "L": .62 if s == "U" else .68, "H": .27 if s == "U" else .5}[m["PR"]]
        c, i, a = ({"H": .56, "L": .22, "N": 0}[m[k]] for k in ("C", "I", "A"))
    except KeyError:
        return None
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    impact = 6.42 * iss if s == "U" else 7.52 * (iss - .029) - 3.25 * (iss - .02) ** 15
    if impact <= 0:
        return 0.0
    exploit = 8.22 * av * ac * pr * ui
    raw = min(impact + exploit, 10) if s == "U" else min(1.08 * (impact + exploit), 10)
    return float(int(raw * 10 + (0 if raw * 10 == int(raw * 10) else 1))) / 10   # round up to one decimal


def severity(vuln: dict) -> str:
    """CRITICAL, HIGH, MODERATE, LOW, or UNKNOWN for one OSV record."""
    ds = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(ds, str) and ds.upper() in ("CRITICAL", "HIGH", "MODERATE", "MEDIUM", "LOW"):
        return "MODERATE" if ds.upper() == "MEDIUM" else ds.upper()
    best = None
    for s in vuln.get("severity") or []:
        if str(s.get("type", "")).startswith("CVSS_V3"):
            score = _cvss_base(str(s.get("score") or ""))
            if score is not None:
                best = score if best is None else max(best, score)
    if best is None:
        return "UNKNOWN"
    return "CRITICAL" if best >= 9 else "HIGH" if best >= 7 else "MODERATE" if best >= 4 else "LOW"


def audit(sbom: dict, reviews: dict, *, post=_post_json, get=_get_json) -> dict:
    """Ask OSV about every PyPI component; a HIGH, CRITICAL or UNKNOWN advisory
    that is not reviewed is a finding. Returns {"findings", "reported", "checked"}."""
    pkgs = [c for c in sbom["components"] if str(c.get("purl", "")).startswith("pkg:pypi/")]
    queries = [{"package": {"name": c["name"], "ecosystem": "PyPI"}, "version": c["version"]} for c in pkgs]
    results = []
    for i in range(0, len(queries), 500):
        results += (post(OSV_BATCH, {"queries": queries[i:i + 500]}) or {}).get("results") or []
    if len(results) != len(queries):
        raise RuntimeError("OSV answered %d of %d queries" % (len(results), len(queries)))
    findings, reported = [], []
    for comp, res in zip(pkgs, results):
        for v in (res or {}).get("vulns") or []:
            vid = str(v.get("id"))
            detail = get(OSV_VULN + vid)
            sev = severity(detail)
            ids = {vid, *[str(a) for a in detail.get("aliases") or []]}
            reviewed = any((_norm(comp["name"]), i) in reviews["advisories"] for i in ids)
            row = {"name": comp["name"], "version": comp["version"], "id": vid,
                   "aliases": sorted(ids - {vid}), "severity": sev,
                   "summary": str(detail.get("summary") or "")[:200], "reviewed": reviewed}
            (findings if sev in ("CRITICAL", "HIGH", "UNKNOWN") and not reviewed else reported).append(row)
    return {"findings": findings, "reported": reported, "checked": len(pkgs)}


# ── command line ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("sbom", "licences", "audit", "all"))
    ap.add_argument("--wheels", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--resolve", action="store_true", help="collect the wheelhouse first")
    ap.add_argument("--offline", action="store_true", help="skip the OSV audit")
    ap.add_argument("--version", default="")
    a = ap.parse_args(argv)
    if a.resolve:
        resolve(a.wheels)
    a.out.mkdir(parents=True, exist_ok=True)
    sbom = build_sbom(a.wheels, version=a.version)
    (a.out / "sbom.cdx.json").write_text(json.dumps(sbom, indent=1), encoding="utf-8")
    print("SBOM: %d components" % len(sbom["components"]))
    if a.command == "sbom":
        return 0
    reviews = load_reviews()
    for e in reviews["expired"]:
        print("EXPIRED REVIEW (no longer counts): %s" % json.dumps(e))
    status = 0
    if a.command in ("licences", "all"):
        bad = judge_licences(sbom, reviews)
        (a.out / "licences.json").write_text(json.dumps(bad, indent=1), encoding="utf-8")
        for b in bad:
            print("LICENCE NOT CLEARED: %(name)s %(version)s: %(licence)s" % b)
        status |= 1 if bad else 0
    if a.command in ("audit", "all") and not a.offline:
        res = audit(sbom, reviews)
        (a.out / "audit.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        for f in res["findings"]:
            print("UNREVIEWED %(severity)s ADVISORY: %(name)s %(version)s %(id)s %(summary)s" % f)
        for r in res["reported"]:
            print("reported (%s%s): %s %s %s" % (r["severity"], ", reviewed" if r["reviewed"] else "",
                                                r["name"], r["version"], r["id"]))
        print("audited %d packages: %d unreviewed high/critical/unknown" % (res["checked"], len(res["findings"])))
        status |= 1 if res["findings"] else 0
    return status


if __name__ == "__main__":
    sys.exit(main())
