#!/usr/bin/env python3
"""What Friday ships, its licences, and its known vulnerabilities.

North star §34.2-10 (dependency review gate), §34.2-17 (licence and SBOM
generation) and §34.17-6 (no unreviewed critical dependency exposure).

The Windows setup program installs Python packages offline from the wheelhouse
build-installer.ps1 stages in packaging/windows/staging/wheelhouse: every tier
file in packaging/windows/requirements/ resolved with ``pip download
--only-binary=:all:`` for CPython 3.12 on Windows, plus wheels it builds itself
for the sdist-only pyautogui family (WHEELHOUSE_TARGETS). It also carries the
vendored browser code under static/vendor/ and the embeddable CPython named in
packaging/windows/sources.json.

    resolve   collect that wheelhouse the way build-installer.ps1 does, into an
              empty folder (for a pull request, where no installer is built).
              Run it on Windows: pip evaluates environment markers for the
              platform it runs on, whatever --platform says.
    sbom      write a CycloneDX 1.5 SBOM of a wheelhouse (the release build
              passes the real staged one): every wheel with its exact version,
              purl, SHA-256 and the licence from its own METADATA; the vendored
              browser code; the bundled CPython
    check     judge an SBOM:
              licences     strong or weak copyleft, unknown or missing
                           licences fail unless reviewed for that exact licence
              attribution  every shipped Python package must be listed in
                           THIRD_PARTY_LICENSES.md
              advisories   every PyPI and npm component's exact version asked
                           of OSV (api.osv.dev); a HIGH, CRITICAL or
                           unreadable-severity advisory fails unless reviewed.
                           A component no advisory database covers (the
                           bundled CPython) fails unless its exact version is
                           reviewed.
              --report-only records everything and exits 0: a beta tag is
              exempt from §34 (amendment A19); the public 1.0 is not.

Reviews live in packaging/supply-chain/reviewed.json. A review counts only
with a reason, a named reviewer and an end date that has not passed; an entry
whose reviewer is empty or a placeholder is listed as awaiting review and
clears nothing. Nothing here edits that file.

Usage:
    python scripts/supply_chain.py resolve --wheels EMPTY_DIR
    python scripts/supply_chain.py sbom --wheels WHEELHOUSE --out OUT_DIR [--version V]
    python scripts/supply_chain.py check --sbom OUT_DIR/sbom.cdx.json --out OUT_DIR
        [--offline] [--report-only] [--base-sbom BASE/sbom.cdx.json]
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
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TIERS = ("core", "recommended", "memory", "judgment")
#: The sdist-only packages build-installer.ps1 builds wheels for (its $targets).
WHEELHOUSE_TARGETS = ("pyautogui", "pyscreeze", "pygetwindow", "pytweening", "pymsgbox",
                      "pyperclip", "pyrect")
REQ_DIR = REPO / "packaging" / "windows" / "requirements"
SOURCES = REPO / "packaging" / "windows" / "sources.json"
VENDOR_DIR = REPO / "static" / "vendor"
THIRD_PARTY = REPO / "THIRD_PARTY_LICENSES.md"
REVIEWED = REPO / "packaging" / "supply-chain" / "reviewed.json"
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
NO_LICENCE = "(none found)"

#: Licences that pass without a review (SPDX ids, compared case-insensitively).
#: Weak copyleft (LGPL, MPL) is NOT here: it needs a review like any copyleft.
PERMISSIVE = {
    "mit", "mit-0", "mit-cmu", "bsd-2-clause", "bsd-3-clause", "0bsd", "isc", "apache-2.0",
    "psf-2.0", "python-2.0", "zlib", "unlicense", "cc0-1.0", "hpnd", "bsl-1.0",
    "upl-1.0", "ncsa", "x11", "curl", "apache-1.1", "cnri-python",
}

#: Trove classifiers and free-text licence names, read as SPDX ids.
_ALIASES = {
    "mit license": "MIT", "mit": "MIT", "the mit license": "MIT", "expat": "MIT",
    "bsd license": "BSD-3-Clause", "bsd": "BSD-3-Clause", "new bsd license": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause", "3-clause bsd": "BSD-3-Clause", "modified bsd": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause", "3-clause bsd license": "BSD-3-Clause",
    "simplified bsd": "BSD-2-Clause", "bsd-2-clause": "BSD-2-Clause",
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

#: Vendored browser code: file-name pattern -> (component, licence, npm package,
#: npm version from the captured version). npm package None: not audited apart.
_VENDOR = (
    (r"^react-(\d[\w.]*)\.production", "react", "MIT", "react", lambda v: v),
    (r"^react-dom-(\d[\w.]*)\.production", "react-dom", "MIT", "react-dom", lambda v: v),
    (r"^marked-(\d[\w.]*)\.min\.js$", "marked", "MIT", "marked", lambda v: v),
    (r"^highlight-(\d[\w.]*)\.min\.js$", "highlight.js", "BSD-3-Clause", "highlight.js", lambda v: v),
    (r"^atom-one-dark-(\d[\w.]*)\.min\.css$", "highlight.js atom-one-dark theme", "BSD-3-Clause", None, None),
    (r"^three-(r\d+)[.-]", "three.js", "MIT", "three", lambda v: "0.%s.0" % v[1:]),
    (r"^pdfjs-(\d[\w.]*)$", "pdf.js", "Apache-2.0", "pdfjs-dist", lambda v: v),
)

def set_repo(root: Path) -> None:
    """Read requirement files, vendored code, sources.json and
    THIRD_PARTY_LICENSES.md from another checkout (the base of a pull request)."""
    global REQ_DIR, SOURCES, VENDOR_DIR, THIRD_PARTY
    root = Path(root)
    REQ_DIR = root / "packaging" / "windows" / "requirements"
    SOURCES = root / "packaging" / "windows" / "sources.json"
    VENDOR_DIR = root / "static" / "vendor"
    THIRD_PARTY = root / "THIRD_PARTY_LICENSES.md"


_PLACEHOLDER = re.compile(r"to confirm|carried over|tbd|todo|placeholder|unreviewed|\?\?", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


# ── resolve ──────────────────────────────────────────────────────────────────

def resolve(wheels: Path) -> None:
    """Collect the wheelhouse the way build-installer.ps1 does. Run on Windows."""
    if wheels.exists() and any(wheels.iterdir()):
        raise SystemExit("resolve needs an empty folder; %s is not" % wheels)
    if sys.platform != "win32":
        raise SystemExit("resolve runs on Windows: pip evaluates markers for its own platform")
    wheels.mkdir(parents=True, exist_ok=True)
    common = ["--disable-pip-version-check", "--no-input"]
    for target in WHEELHOUSE_TARGETS:
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels),
                        target] + common, check=True)
    for tier in TIERS:
        print("resolving", tier, flush=True)
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:"] + common +
                       ["--dest", str(wheels), "--find-links", str(wheels),
                        "-r", str(REQ_DIR / (tier + ".txt"))], check=True)


# ── read a wheel ─────────────────────────────────────────────────────────────

def _spdx(text: str) -> str:
    t = " ".join(str(text or "").split()).strip()
    return _ALIASES.get(t.lower(), t) if t else ""


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
        # More than one classifier: each may only be an option, but judging all
        # of them together is the conservative reading.
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


# ── the SPDX expression grammar ──────────────────────────────────────────────

def _parse(expr: str, leaf) -> bool | None:
    """Evaluate an SPDX expression with ``leaf(id) -> bool``: AND needs both
    sides, OR either, parentheses nest, ``X WITH exception`` is judged by X.
    None when the text is not a valid expression."""
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
        if tok is None or tok == ")" or tok.upper() in ("AND", "OR", "WITH"):
            raise ValueError(expr)
        if tok == "(":
            take()
            val = either()
            if take() != ")":
                raise ValueError(expr)
            return val
        ident = take()
        if not re.fullmatch(r"[A-Za-z0-9.+-]+", ident):
            raise ValueError(expr)
        if (peek() or "").upper() == "WITH":
            take()
            take()
        return leaf(ident)

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
        return None
    return ok if (pos == len(tokens) and tokens) else None


def is_spdx(expr: str) -> bool:
    return _parse(expr, lambda _i: True) is not None


def _expression_ok(expr: str) -> bool:
    """True when an SPDX expression is permissive (see PERMISSIVE)."""
    return bool(_parse(expr, lambda i: i.lower() in PERMISSIVE))


# ── sbom ─────────────────────────────────────────────────────────────────────

def bundled_python(sources: Path | None = None) -> tuple:
    sources = SOURCES if sources is None else sources
    data = json.loads(sources.read_text(encoding="utf-8"))
    return ("CPython", str(data["python"]["version"]), "PSF-2.0")


def vendored(vendor_dir: Path | None = None) -> list:
    """The vendored browser components, one per (name, version), with the files
    each covers. A file no pattern names is returned as "unknown"."""
    vendor_dir = VENDOR_DIR if vendor_dir is None else vendor_dir
    seen: dict = {}
    for p in sorted(vendor_dir.iterdir()) if vendor_dir.exists() else []:
        for pattern, comp, lic, npm, npm_version in _VENDOR:
            m = re.match(pattern, p.name)
            if m:
                entry = seen.setdefault((comp, m.group(1)), {
                    "name": comp, "version": m.group(1), "licence": lic, "files": [],
                    "npm": npm, "npm_version": npm_version(m.group(1)) if npm else None})
                entry["files"].append(p.name)
                break
        else:
            seen.setdefault(("unknown", p.name), {"name": "unknown", "version": "", "licence": "",
                                                  "files": [p.name], "npm": None, "npm_version": None})
    return list(seen.values())


def _licences_field(licence: str) -> list:
    if not licence:
        return []
    return [{"expression": licence}] if is_spdx(licence) else [{"license": {"name": licence}}]


def build_sbom(wheels: Path, *, vendor_dir: Path | None = None, sources: Path | None = None,
               version: str = "") -> dict:
    vendor_dir = VENDOR_DIR if vendor_dir is None else vendor_dir
    sources = SOURCES if sources is None else sources
    comps = []
    for whl in sorted(wheels.glob("*.whl")):
        md = wheel_metadata(whl)
        if _norm(md["name"]) in KNOWN_BUNDLED:
            md["licence"] = KNOWN_BUNDLED[_norm(md["name"])]
            md["licence_source"] = "THIRD_PARTY_LICENSES.md (bundled binary)"
        purl = "pkg:pypi/%s@%s" % (_norm(md["name"]), md["version"])
        comps.append({
            "type": "library", "bom-ref": purl, "name": md["name"], "version": md["version"],
            "purl": purl, "hashes": [{"alg": "SHA-256", "content": _sha256(whl)}],
            "licenses": _licences_field(md["licence"]),
            "properties": [{"name": "friday:licence", "value": md["licence"] or NO_LICENCE},
                           {"name": "friday:licence-source", "value": md["licence_source"]},
                           {"name": "friday:file", "value": whl.name}],
        })
    for v in vendored(vendor_dir):
        name = v["name"] if v["name"] != "unknown" else "static/vendor/" + v["files"][0]
        comp = {
            "type": "library", "bom-ref": "vendor:%s@%s" % (name, v["version"]),
            "name": name, "version": v["version"], "licenses": _licences_field(v["licence"]),
            "properties": [{"name": "friday:licence", "value": v["licence"] or NO_LICENCE},
                           {"name": "friday:file", "value": ", ".join("static/vendor/" + f for f in v["files"])}],
        }
        if v["npm"]:
            comp["purl"] = "pkg:npm/%s@%s" % (v["npm"], v["npm_version"])
        comps.append(comp)
    name, ver, lic = bundled_python(sources)
    comps.append({"type": "application", "bom-ref": "pkg:generic/cpython@%s" % ver,
                  "name": name, "version": ver, "purl": "pkg:generic/cpython@%s" % ver,
                  "cpe": "cpe:2.3:a:python:python:%s:*:*:*:*:*:*:*" % ver,
                  "licenses": _licences_field(lic),
                  "properties": [{"name": "friday:licence", "value": lic},
                                 {"name": "friday:file", "value": "embeddable distribution (sources.json)"}]})
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5",
        "serialNumber": "urn:uuid:%s" % uuid.uuid4(), "version": 1,
        "metadata": {"timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                     "component": {"type": "application", "name": "Agent Friday", "version": version,
                                   "licenses": [{"expression": "MIT"}]}},
        "components": comps,
    }


def _prop(comp: dict, key: str) -> str:
    return next((p["value"] for p in comp.get("properties") or [] if p.get("name") == key), "")


def _licence_of(comp: dict) -> str:
    v = _prop(comp, "friday:licence")
    return "" if v == NO_LICENCE else v


# ── reviews ──────────────────────────────────────────────────────────────────

#: The furthest a review's `until` may be; anything later does not count.
REVIEW_HORIZON_DAYS = 366

#: The most pages one OSV query may take before the audit gives up.
OSV_MAX_PAGES = 50


def load_reviews(path: Path = REVIEWED, today: _dt.date | None = None) -> dict:
    """The reviews in force: {"licences": {pkg: e}, "advisories": {(pkg, id): e},
    "components": {(pkg, version): e}, "expired": [...], "awaiting": [...]}."""
    today = today or _dt.date.today()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = {"licences": {}, "advisories": {}, "components": {}, "expired": [], "awaiting": []}
    for kind, key in (("licences", "licence"), ("advisories", "id"), ("components", "version")):
        for e in data.get(kind) or []:
            if not isinstance(e, dict) or not e.get("package") or not e.get(key) or not e.get("reason"):
                continue
            who = str(e.get("reviewed_by") or "").strip()
            if not who or _PLACEHOLDER.search(who):
                out["awaiting"].append(dict(e, kind=kind))
                continue
            try:
                until = _dt.date.fromisoformat(str(e.get("until")))
            except ValueError:
                out["awaiting"].append(dict(e, kind=kind))
                continue
            if until < today:
                out["expired"].append(dict(e, kind=kind))
                continue
            if until > today + _dt.timedelta(days=REVIEW_HORIZON_DAYS):
                # A review must be looked at again within a year; a far-off
                # date would clear a finding for good.
                out["awaiting"].append(dict(e, kind=kind, why="until is more than a year away"))
                continue
            k = _norm(e["package"])
            if kind == "licences":
                out[kind][k] = e
            else:
                out[kind][(k, str(e[key]))] = e
    return out


# ── licences and attribution ─────────────────────────────────────────────────

def judge_licences(sbom: dict, reviews: dict) -> list:
    """Every component whose licence is not permissive and not reviewed for it."""
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


def attributed_names(path: Path | None = None) -> set:
    """The component names THIRD_PARTY_LICENSES.md lists in its tables."""
    path = THIRD_PARTY if path is None else path
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*([^|]+?)\s*\|", line)
        if not m or m.group(1) in ("Component", "---") or set(m.group(1)) <= set("-: "):
            continue
        cell = m.group(1)
        names.add(_norm(cell))
        names.add(_norm(re.split(r"\s[(—]", cell, 1)[0]))
    return names


def unattributed(sbom: dict, path: Path | None = None) -> list:
    """Shipped Python packages THIRD_PARTY_LICENSES.md does not name."""
    known = attributed_names(path)
    return [{"name": c["name"], "version": c["version"]} for c in sbom["components"]
            if str(c.get("purl", "")).startswith("pkg:pypi/") and _norm(c["name"]) not in known]


# ── advisories ───────────────────────────────────────────────────────────────

def _request(url: str, body: dict | None = None, attempts: int = 4) -> dict:
    """GET or POST JSON with bounded retries on 429, 5xx and network errors.
    The last failure is raised: an audit that could not ask is not a pass."""
    for i in range(attempts):
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or i == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if i == attempts - 1:
                raise
        time.sleep(2 ** i)
    raise RuntimeError("unreachable")


def _post_json(url: str, body: dict) -> dict:
    return _request(url, body)


def _get_json(url: str) -> dict:
    return _request(url)


def _cvss_base(vector: str) -> float | None:
    """The base score of a CVSS v3 vector, or None when it does not parse."""
    m = dict(p.split(":", 1) for p in vector.split("/")[1:] if ":" in p)
    try:
        s = m["S"]
        if s not in ("U", "C"):
            return None
        av = {"N": .85, "A": .62, "L": .55, "P": .2}[m["AV"]]
        ac = {"L": .77, "H": .44}[m["AC"]]
        ui = {"N": .85, "R": .62}[m["UI"]]
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
    n = int(round(raw * 100000))                       # CVSS v3.1 Roundup
    return n / 100000.0 if n % 10000 == 0 else (n // 10000 + 1) / 10.0


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


def _ecosystem(comp: dict):
    purl = str(comp.get("purl", ""))
    if purl.startswith("pkg:pypi/"):
        return "PyPI", purl[len("pkg:pypi/"):].rsplit("@", 1)[0]
    if purl.startswith("pkg:npm/"):
        return "npm", purl[len("pkg:npm/"):].rsplit("@", 1)[0]
    return None, None


def _purl_version(comp: dict) -> str:
    return str(comp.get("purl", "")).rsplit("@", 1)[-1]


def audit(sbom: dict, reviews: dict, *, post=_post_json, get=_get_json) -> dict:
    """Every PyPI and npm component asked of OSV. Findings: an unreviewed HIGH,
    CRITICAL or UNKNOWN advisory, and a component no advisory database covers
    whose exact version is not reviewed. Lower advisories are reported."""
    asked = [(c,) + _ecosystem(c) for c in sbom["components"]]
    asked = [(c, eco, name) for c, eco, name in asked if eco]
    queries = [{"package": {"name": name, "ecosystem": eco}, "version": _purl_version(c)}
               for c, eco, name in asked]
    ids_per: list = [[] for _ in queries]
    pending = list(range(len(queries)))
    tokens: dict = {}
    pages: dict = {}
    while pending:
        batch = pending[:500]
        body = {"queries": [dict(queries[i], **({"page_token": tokens[i]} if i in tokens else {}))
                            for i in batch]}
        results = (post(OSV_BATCH, body) or {}).get("results") or []
        if len(results) != len(batch):
            raise RuntimeError("OSV answered %d of %d queries" % (len(results), len(batch)))
        pending = pending[500:]
        for i, res in zip(batch, results):
            ids_per[i] += [str(v.get("id")) for v in (res or {}).get("vulns") or []]
            tok = (res or {}).get("next_page_token")
            if tok:
                pages[i] = pages.get(i, 1) + 1
                if pages[i] > OSV_MAX_PAGES:
                    raise RuntimeError("OSV kept paging for %s" % queries[i]["package"]["name"])
                tokens[i] = tok
                pending.append(i)

    cache: dict = {}

    def detail(vid, *, alias=False):
        if vid not in cache:
            try:
                cache[vid] = get(OSV_VULN + vid) or {}
            except urllib.error.HTTPError as e:
                # An alias OSV does not hold is not a failed audit: the
                # advisory itself was read, and stays UNKNOWN (a finding).
                if not (alias and e.code == 404):
                    raise
                cache[vid] = {}
        return cache[vid]

    findings, reported = [], []
    for (comp, eco, name), ids in zip(asked, ids_per):
        for vid in dict.fromkeys(ids):
            d = detail(vid)
            if d.get("withdrawn"):
                continue
            sev = severity(d)
            aliases = [str(a) for a in d.get("aliases") or []]
            if sev == "UNKNOWN":
                for alias in aliases:
                    if alias.startswith("GHSA-"):
                        sev = severity(detail(alias, alias=True))
                        if sev != "UNKNOWN":
                            break
            reviewed = any((_norm(name), i) in reviews["advisories"] for i in [vid] + aliases)
            row = {"name": comp["name"], "version": comp["version"], "ecosystem": eco, "id": vid,
                   "aliases": sorted(set(aliases)), "severity": sev,
                   "summary": str(d.get("summary") or "")[:200], "reviewed": reviewed}
            (findings if sev in ("CRITICAL", "HIGH", "UNKNOWN") and not reviewed else reported).append(row)
    uncovered = [c for c in sbom["components"] if _ecosystem(c)[0] is None
                 and not str(c.get("name", "")).startswith(("static/vendor/", "highlight.js atom-one-dark"))]
    for c in uncovered:
        if (_norm(c["name"]), str(c["version"])) not in reviews["components"]:
            findings.append({"name": c["name"], "version": c["version"], "ecosystem": "none",
                             "id": "NO-ADVISORY-DATABASE", "aliases": [], "severity": "UNKNOWN",
                             "summary": "no advisory database covers this component; its exact version "
                                        "needs a review in reviewed.json (components)", "reviewed": False})
    return {"findings": findings, "reported": reported, "checked": len(asked) + len(uncovered)}


# ── command line ─────────────────────────────────────────────────────────────

def _new_only(rows: list, base: dict | None, *, by_name: bool = False) -> tuple:
    """(rows the change adds, rows main already has) against a base SBOM."""
    if base is None:
        return rows, []
    seen = {(_norm(c["name"]), "" if by_name else str(c["version"])) for c in base["components"]}
    added = [r for r in rows if (_norm(r["name"]), "" if by_name else str(r["version"])) not in seen]
    return added, [r for r in rows if r not in added]


def check(sbom: dict, out: Path, *, offline: bool = False, report_only: bool = False,
          base: dict | None = None) -> int:
    """Judge an SBOM. With ``base`` (a pull request's base), only what the
    change adds or upgrades can fail; what the base already has is reported."""
    reviews = load_reviews()
    out.mkdir(parents=True, exist_ok=True)
    for e in reviews["expired"]:
        print("EXPIRED REVIEW (no longer counts): %s %s" % (e.get("kind"), e.get("package")))
    for e in reviews["awaiting"]:
        print("AWAITING REVIEW (clears nothing until a named reviewer signs it): %s %s"
              % (e.get("kind"), e.get("package")))
    lic, lic_before = _new_only(judge_licences(sbom, reviews), base)
    unatt, unatt_before = _new_only(unattributed(sbom), base, by_name=True)
    for b in lic_before + unatt_before:
        print("already on the base (reported, not this change): %s %s" % (b["name"], b["version"]))
    for b in lic:
        print("LICENCE NOT CLEARED: %(name)s %(version)s: %(licence)s" % b)
    for u in unatt:
        print("NOT IN THIRD_PARTY_LICENSES.md: %(name)s %(version)s" % u)
    record = {"report_only": report_only, "against_base": base is not None,
              "licences": lic, "unattributed": unatt,
              "on_the_base": {"licences": lic_before, "unattributed": unatt_before},
              "awaiting_review": reviews["awaiting"], "expired_reviews": reviews["expired"]}
    if offline:
        print("AUDIT SKIPPED (--offline): advisories were not checked")
        record["audit"] = None
    else:
        try:
            res = audit(sbom, reviews)
        except Exception as e:  # noqa: BLE001 - recorded, and fatal unless report-only
            # An audit that could not ask is never a pass. A beta (report-only,
            # A19) records it and goes on; anything else fails.
            print("AUDIT COULD NOT RUN: %s" % type(e).__name__)
            res = {"findings": [{"name": "osv", "version": "", "ecosystem": "", "id": "AUDIT-NOT-RUN",
                                 "aliases": [], "severity": "UNKNOWN", "reviewed": False,
                                 "summary": ("%s: %s" % (type(e).__name__, e))[:200]}],
                   "reported": [], "checked": 0}
        res["findings"], res["findings_on_the_base"] = _new_only(res["findings"], base)
        record["audit"] = res
        for f in res["findings"]:
            print("UNREVIEWED %(severity)s ADVISORY: %(name)s %(version)s %(id)s %(summary)s" % f)
        for r in res["reported"]:
            print("reported (%s%s): %s %s %s" % (r["severity"], ", reviewed" if r["reviewed"] else "",
                                                r["name"], r["version"], r["id"]))
        print("audited %d components: %d findings" % (res["checked"], len(res["findings"])))
    failed = bool(lic or unatt or (record["audit"] and record["audit"]["findings"]))
    record["ok"] = not failed
    (out / "supply-chain.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    if failed and report_only:
        print("REPORT ONLY (a beta: exempt from §34 by amendment A19): recorded, not failing")
        return 0
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--wheels", type=Path, required=True)
    r.add_argument("--repo", type=Path, default=None, help="another checkout's files")
    s = sub.add_parser("sbom")
    s.add_argument("--wheels", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--version", default="")
    s.add_argument("--repo", type=Path, default=None, help="another checkout's files")
    c = sub.add_parser("check")
    c.add_argument("--sbom", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--offline", action="store_true")
    c.add_argument("--report-only", action="store_true")
    c.add_argument("--base-sbom", type=Path, default=None,
                   help="a pull request's base: only what the change adds can fail")
    a = ap.parse_args(argv)
    if getattr(a, "repo", None):
        set_repo(a.repo)
    if a.command == "resolve":
        resolve(a.wheels)
        return 0
    if a.command == "sbom":
        if not any(a.wheels.glob("*.whl")):
            print("no wheels in %s" % a.wheels)
            return 1
        sbom = build_sbom(a.wheels, version=a.version)
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "sbom.cdx.json").write_text(json.dumps(sbom, indent=1), encoding="utf-8")
        print("SBOM: %d components" % len(sbom["components"]))
        return 0
    sbom = json.loads(a.sbom.read_text(encoding="utf-8"))
    base = json.loads(a.base_sbom.read_text(encoding="utf-8")) if a.base_sbom else None
    return check(sbom, a.out, offline=a.offline, report_only=a.report_only, base=base)


if __name__ == "__main__":
    sys.exit(main())
