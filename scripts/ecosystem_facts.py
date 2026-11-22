"""Extract the facts Friday states about her companion apps from the app repos.

    python scripts/ecosystem_facts.py --repos <dir holding the app repos> [--write] [--app phone]

For each app this reads the Gradle build file, the Android manifest, the
capability files and a few wire constants in the main sources, and produces one
snapshot: ``src/agent_friday/resources/ecosystem/facts/<app>.json``. The
snapshot's ``fingerprint`` is what ``resources/ecosystem/ecosystem.json`` must
carry for that app (tests/unit/test_ecosystem_knowledge.py), so a build that
changes a fact cannot land without the knowledge file being looked at again.

Only build files, manifests, capability JSON and source constants are read.
Documentation in the app repos is never read, so nothing written there
(addresses, device names) can reach the snapshot.

Without ``--write`` it prints what differs from the checked-in snapshots and
exits 1 when anything does (tests/unit/test_ecosystem_facts_current.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA = 1
FACTS_DIR = (Path(__file__).resolve().parent.parent
             / "src" / "agent_friday" / "resources" / "ecosystem" / "facts")

#: Wire markers looked for in main sources. A device that pairs with the PC's
#: device gateway calls GATEWAY_PAIR; one that pairs with its own relay calls
#: RELAY_PAIR. APP_REPORT is the body key a device uses to tell the PC which
#: build it is when it opens a session.
GATEWAY_PAIR = "/tv/v1/pair/start"
RELAY_PAIR = "/v1/pair/complete"
DISCOVERY_PROBE = "FRIDAY-TV-DISCOVER v1"
APP_REPORT = '"app_report"'


@dataclass(frozen=True)
class AppSource:
    """Where one app's facts are read from, relative to the repos folder."""
    app: str
    repo: str
    ref: str | None            # a git ref, or None to read the working tree
    gradle: str
    manifest: str
    capabilities: str | None = None


APPS = (
    AppSource("phone", "friday-phone", "HEAD", "app/build.gradle.kts",
              "app/src/main/AndroidManifest.xml", "app/src/main/assets/capabilities"),
    AppSource("tablet", "friday-phone", "tablet/w11", "app/build.gradle.kts",
              "app/src/main/AndroidManifest.xml", "app/src/main/assets/capabilities"),
    AppSource("watch", "friday-watch", "HEAD", "app/build.gradle.kts",
              "app/src/main/AndroidManifest.xml"),
    AppSource("tv", "friday-tv", None, "app/build.gradle",
              "app/src/main/AndroidManifest.xml"),
)


class MissingRepo(Exception):
    """An app repo, ref or file the extractor needs is not there."""


class Tree:
    """Read-only view of one repo at one ref (or its working tree)."""

    def __init__(self, root: Path, ref: str | None):
        if not root.is_dir():
            raise MissingRepo(f"{root.name}: repo folder not found")
        self.root, self.ref = root, ref
        if ref is not None:
            self.commit = self._git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
        else:
            self.commit = None

    def _git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if done.returncode != 0:
            raise MissingRepo(f"{self.root.name}: git {args[0]} failed for {self.ref}")
        return done.stdout

    def read(self, path: str) -> str:
        if self.ref is not None:
            return self._git("show", f"{self.ref}:{path}")
        file = self.root / path
        if not file.is_file():
            raise MissingRepo(f"{self.root.name}: {path} not found")
        return file.read_text(encoding="utf-8", errors="replace")

    def files(self, under: str = "") -> list[str]:
        if self.ref is not None:
            out = self._git("ls-tree", "-r", "--name-only", self.ref, "--", under or ".")
            return [line for line in out.splitlines() if line]
        base = self.root / under
        found = []
        for folder, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("build", ".gradle", ".git", "node_modules")]
            for name in names:
                found.append(Path(folder, name).relative_to(self.root).as_posix())
        return sorted(found)

    def main_sources(self) -> list[str]:
        return [p for p in self.files()
                if "/src/main/" in p and p.endswith((".java", ".kt"))]


def _first(pattern: str, text: str) -> str | None:
    hit = re.search(pattern, text, re.M)
    return hit.group(1) if hit else None


def _strip_comments(text: str) -> str:
    """Line comments removed; a `//` inside a URL (after a colon) is kept."""
    return re.sub(r"(?<!:)//[^\n]*", "", text)


def _block(text: str, opener: str) -> str:
    """The brace-balanced body that follows `opener`, or ''."""
    hit = re.search(opener, text)
    if not hit:
        return ""
    start = text.find("{", hit.end() - 1)
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return ""


def gradle_facts(text: str) -> dict:
    """Identity, version and signing from a Groovy or Kotlin Gradle file."""
    text = _strip_comments(text)
    num = r"\s*=?\s*(\d+)"
    word = r"""\s*=?\s*["']([^"']+)["']"""
    release = _block(text, r"\brelease\s*\{")
    if "signingConfigs.debug" in release or 'getByName("debug")' in release:
        signing = "debug_key"
    elif "signingConfig" in release:
        signing = "release_key"
    else:
        signing = "none"
    code = _first(r"\bversionCode" + num, text)
    min_sdk = _first(r"\bminSdk" + num, text)
    target = _first(r"\btargetSdk" + num, text)
    facts = {
        "application_id": _first(r"\bapplicationId" + word, text),
        "version_name": _first(r"\bversionName" + word, text),
        "version_code": int(code) if code else None,
        "min_sdk": int(min_sdk) if min_sdk else None,
        "target_sdk": int(target) if target else None,
        "abis": sorted({abi for line in text.splitlines() if "abiFilters" in line
                        for abi in re.findall(r"""["']([\w-]+)["']""", line)}),
        "debug_suffix": _first(r"""\bdebug\s*\{[^}]*applicationIdSuffix\s*=?\s*["']([^"']+)["']""", text),
        "release_signing": signing,
        "form": _first(r"""\bval\s+form\s*=\s*["'](\w+)["']""", text),
    }
    missing = [k for k in ("application_id", "version_name", "version_code", "min_sdk") if facts[k] is None]
    if missing:
        raise MissingRepo("gradle file has no " + ", ".join(missing))
    return facts


def manifest_facts(text: str) -> dict:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    permissions = sorted(set(re.findall(r'<uses-permission[^>]*android:name="([^"]+)"', text)))
    required = []
    for tag in re.findall(r"<uses-feature[^>]*>", text):
        name = _first(r'android:name="([^"]+)"', tag)
        if name and 'android:required="false"' not in tag:
            required.append(name)
    return {
        "permissions": permissions,
        "features_required": sorted(set(required)),
        "bound_services": sorted(set(re.findall(r'android:permission="android\.permission\.(BIND_[A-Z_]+)"', text))),
    }


def capability_facts(tree: Tree, folder: str | None) -> dict:
    if not folder:
        return {}
    out = {}
    for path in tree.files(folder):
        if not path.endswith(".json"):
            continue
        doc = json.loads(tree.read(path))
        name = str(doc.get("capability") or Path(path).stem)
        verbs = doc.get("verbs") if isinstance(doc.get("verbs"), list) else []
        gates = {}
        for v in verbs:
            verb, gate = (v.get("verb"), v.get("gate")) if isinstance(v, dict) else (None, None)
            if not isinstance(verb, str) or not verb or not isinstance(gate, str) or not gate:
                raise MissingRepo(f"{path}: a verb with no name or no gate")
            gates[verb] = gate
        out[name] = {"version": doc.get("version"), "owner_only": bool(doc.get("owner_only")),
                     "verbs": dict(sorted(gates.items()))}
    return dict(sorted(out.items()))


def pairing_facts(tree: Tree) -> dict:
    gateway = relay = discovery = report = False
    kind = None
    for path in tree.main_sources():
        text = tree.read(path)
        if GATEWAY_PAIR in text:
            gateway = True
            arg = _first(r'\.put\("kind",\s*(\w+)\)', text)
            if arg and kind is None:
                # A constant names the kind outright; anything else is chosen
                # at run time from the build's form.
                kind = _first(r"\b" + re.escape(arg) + r'\s*=\s*"(\w+)"', text) or "by_form"
        relay = relay or RELAY_PAIR in text
        discovery = discovery or DISCOVERY_PROBE in text
        report = report or APP_REPORT in text
    return {"gateway": gateway, "relay": relay, "discovery": discovery,
            "declares_kind": kind, "reports_app_version": report}


def fingerprint(facts: dict) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def extract(source: AppSource, repos: Path) -> dict:
    """The snapshot for one app. Raises MissingRepo when it cannot be read."""
    tree = Tree(repos / source.repo, source.ref)
    try:
        facts = gradle_facts(tree.read(source.gradle))
    except MissingRepo as e:
        raise MissingRepo(f"{source.app}: {e}") from None
    facts.update(manifest_facts(tree.read(source.manifest)))
    facts["capabilities"] = capability_facts(tree, source.capabilities)
    facts["pairing"] = pairing_facts(tree)
    return {"schema": SCHEMA, "app": source.app,
            "source": {"repo": source.repo, "ref": source.ref or "working tree", "commit": tree.commit},
            "facts": facts, "fingerprint": fingerprint(facts)}


def dump(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def differences(repos: Path, facts_dir: Path = FACTS_DIR, apps=APPS) -> list[str]:
    """One line per app whose real facts differ from its checked-in snapshot."""
    lines = []
    for source in apps:
        try:
            now = extract(source, repos)
        except MissingRepo as e:
            lines.append(f"{source.app}: cannot read the app repo ({e})")
            continue
        file = facts_dir / f"{source.app}.json"
        if not file.is_file():
            lines.append(f"{source.app}: no checked-in snapshot")
            continue
        was = json.loads(file.read_text(encoding="utf-8"))
        if was.get("fingerprint") != now["fingerprint"]:
            changed = sorted(k for k in set(was.get("facts", {})) | set(now["facts"])
                             if was.get("facts", {}).get(k) != now["facts"].get(k))
            lines.append(f"{source.app}: facts changed ({', '.join(changed)}); "
                         "run with --write and review ecosystem.json")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repos", required=True, type=Path)
    ap.add_argument("--app", choices=[a.app for a in APPS])
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    apps = tuple(a for a in APPS if args.app in (None, a.app))
    if not args.write:
        lines = differences(args.repos, apps=apps)
        print("\n".join(lines) if lines else "ecosystem facts are current")
        return 1 if lines else 0
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    for source in apps:
        snapshot = extract(source, args.repos)
        (FACTS_DIR / f"{source.app}.json").write_text(dump(snapshot), encoding="utf-8", newline="\n")
        print(f"{source.app}: {snapshot['facts']['version_name']} "
              f"(code {snapshot['facts']['version_code']}) {snapshot['fingerprint'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
