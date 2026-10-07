"""Frozen static-site inputs and bounded output, using codebase command policy."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path
from contextlib import contextmanager, nullcontext

from agent_friday.paths import contained, friday_home

MAX_FILES = 10000
MAX_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
SKIP = {".git", ".friday", "node_modules", "__pycache__", ".venv", "venv"}
SECRET_NAMES = {".npmrc", ".pypirc", ".netrc", "credentials", "credentials.json", "id_rsa", "id_ed25519"}
OUTPUT_OMIT = {".gitignore", ".gitattributes", "readme.md", "license", "license.md", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
STATIC_EXTENSIONS = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".xml", ".svg", ".png",
                     ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf",
                     ".wasm", ".webmanifest", ".pdf", ".mp4", ".mp3", ".ogg", ".map"}


def build_dir(site_id, build_id):
    if not re.fullmatch(r"site-[a-f0-9]{32}", str(site_id)) or not re.fullmatch(r"build-[a-f0-9]{32}", str(build_id)):
        raise ValueError("Invalid site or build identity.")
    return friday_home() / "sites" / site_id / "builds" / build_id


def relative_path(value):
    if not isinstance(value, str) or len(value) > 250 or "\\" in value or ":" in value:
        raise ValueError("Build paths must be relative folders inside the repository.")
    if value == ".":
        return value
    if not value or value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("Build paths must be relative folders inside the repository.")
    return value


def _regular(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Site files cannot contain symbolic links or reparse points.")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("Only ordinary files and folders can enter a site build.")
    return info


def _secret_path(rel):
    return any(part.lower() in SECRET_NAMES or part.lower().startswith(".env")
               or part.lower().endswith((".pem", ".key", ".p12", ".pfx")) for part in Path(rel).parts)


def collect(root, *, output=False, _guard=None):
    """Read a bounded tree without following links or including credential files."""
    guard = _guard or (lambda: None)
    guard()
    root = Path(root)
    _regular(root)
    if not root.is_dir():
        raise ValueError("The configured site folder does not exist.")
    files, total = {}, 0
    for folder, dirs, names in os.walk(root, followlinks=False):
        guard()
        # Check even skipped directories: a junction must not masquerade as a dependency folder.
        for name in dirs:
            _regular(Path(folder) / name)
        dirs[:] = [name for name in dirs if name not in SKIP]
        for name in sorted(names):
            guard()
            path = Path(folder) / name
            info = _regular(path)
            rel = path.relative_to(root).as_posix()
            if name in SKIP:
                continue
            if _secret_path(rel):
                raise ValueError("A credential or environment file is in the selected tree; remove it from the build inputs/output.")
            if output and name.lower() in OUTPUT_OMIT:
                continue
            if output and (rel.split("/")[0] in {"_worker.js", "_worker.bundle", "functions"}
                           or rel in {"_routes.json", "functions-filepath-routing-config.json"}):
                raise ValueError("The output contains server functions. Saved Sites currently publishes static output only.")
            if output and name == "CNAME":
                raise ValueError("Choose the custom domain in Sites; a repository CNAME file cannot change the hosting target.")
            if output and path.suffix.lower() not in STATIC_EXTENSIONS and name not in ("CNAME", "_headers", "_redirects", ".nojekyll"):
                raise ValueError("The output contains non-static files. A server application needs another hosting adapter.")
            if info.st_size > MAX_FILE_BYTES or total + info.st_size > MAX_BYTES or len(files) >= MAX_FILES:
                raise ValueError("The site exceeds the static bundle size or file-count limit.")
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            guard()
            after = _regular(path)
            if len(data) > MAX_FILE_BYTES or (after.st_ino, after.st_size, after.st_mtime_ns) != (info.st_ino, info.st_size, info.st_mtime_ns):
                raise ValueError("Site files changed while being captured. Try a fresh build.")
            total += len(data)
            if total > MAX_BYTES:
                raise ValueError("The site exceeds the static bundle limit.")
            files[rel] = data
    guard()
    return files


def manifest(files):
    return [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in sorted(files.items())]


def digest(files):
    return hashlib.sha256(json.dumps(manifest(files), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def preview_output(site, build, *, guard):
    """Capture only manifest-verified ordinary files from a successful build."""
    guard()
    if build.get("status") != "built" or build.get("site_id") != site["site_id"]:
        raise ValueError("Choose a successful frozen build for this site.")
    root = build_dir(site["site_id"], build["build_id"]) / "output"
    # Reject links in every Friday-owned parent as well as collect's descendants.
    for parent in (root, *list(root.parents)[:4]):
        _regular(parent)
    files = collect(root, output=True, _guard=guard)
    if ("index.html" not in files or manifest(files) != build.get("files")
            or digest(files) != build.get("output_hash")):
        raise ValueError("Build output changed. Prepare a fresh build.")
    guard()
    return files


def write_files(root, files, *, generation):
    from agent_friday.services import sites_privacy
    sites_privacy.require_generation(generation)
    root.mkdir(parents=True, exist_ok=False)
    for rel, data in files.items():
        sites_privacy.require_generation(generation)
        target = contained(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        sites_privacy.require_generation(generation)


def freeze(site, build_id, *, generation):
    from agent_friday.services import codebases, sites_privacy
    sites_privacy.require_generation(generation)
    repo = codebases.repo_path(site["codebase_id"])
    if codebases.is_friday_checkout(repo):
        raise ValueError("Use a managed copy of Friday's source for a site build.")
    files = collect(repo)
    # The file manifest captures dirty and untracked source too; Git HEAD alone does not.
    sha = codebases._git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = codebases._git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirty = bool(codebases._git(repo, "status", "--porcelain", "--untracked-files=normal").stdout.strip())
    root = build_dir(site["site_id"], build_id)
    write_files(root / "source", files, generation=generation)
    sites_privacy.require_generation(generation)
    return {"source_revision": sha, "branch": branch, "dirty": dirty, "source_hash": digest(files),
            "source_files": manifest(files)}


def isolated_environment(snapshot):
    """Only OS launch paths survive; user config and provider credentials do not."""
    snapshot = Path(snapshot)
    home, temp = snapshot.parent / "home", snapshot.parent / "tmp"
    home.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), LOCALAPPDATA=str(home),
               TEMP=str(temp), TMP=str(temp), CI="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, NPM_CONFIG_USERCONFIG=os.devnull)
    return env


def execution_context(codebase_id, snapshot):
    """Internal seam used only after a Sites approval; rejects arbitrary cwd input."""
    from agent_friday.services import sites_operations
    snapshot = Path(snapshot).absolute()
    root = (friday_home() / "sites").absolute()
    try:
        parts = snapshot.relative_to(root).parts
    except ValueError as exc:
        raise ValueError("A site command needs its frozen build workspace.") from exc
    if len(parts) < 4 or parts[1] != "builds" or parts[3] != "source" or any(part in (".", "..") for part in parts):
        raise ValueError("A site command needs its frozen build workspace.")
    build_dir(parts[0], parts[2])
    site = sites_operations.get_site(parts[0])
    if not site or site["codebase_id"] != codebase_id:
        raise ValueError("The build workspace belongs to another codebase.")
    for path in [snapshot, *snapshot.parents]:
        if path == root:
            break
        _regular(path)
    return snapshot, isolated_environment(build_dir(parts[0], parts[2]) / "source")


def execution_generation(snapshot):
    from agent_friday.services import sites_operations as sites, sites_privacy
    parts = Path(snapshot).absolute().relative_to((friday_home() / "sites").absolute()).parts
    build = sites._build(sites.get_site(parts[0]), parts[2])
    generation = build.get("privacy_generation")
    sites_privacy.require_operation(generation, build.get("privacy_boot"))
    return generation


@contextmanager
def launch_guard(codebase_id, snapshot, command):
    """Linearize owner, exact approval and process creation after all slow setup."""
    from agent_friday.services import sites_operations as sites, conversations, approvals
    from agent_friday.governance import action_gate
    parts = Path(snapshot).absolute().relative_to((friday_home() / "sites").absolute()).parts
    with sites.LOCK, conversations._LOCK:
        site = sites.get_site(parts[0])
        build = sites._build(site, parts[2])
        operation = sites._operation(site, build.get("operation_id"))
        record = approvals.get_approval(operation.get("approval_id"))
        if (not record or record.get("status") != "approved" or not record.get("executing_at")
                or operation.get("status") != "applying" or operation.get("executor_instance") != sites._INSTANCE):
            raise ValueError("This build has no current, claimed approval.")
        current, _operation = sites._approved_operation(record)
        if current["codebase_id"] != codebase_id or current["build_command"] != command:
            raise ValueError("The build command or owner changed before process launch.")
        action_gate.record_external(sites.KIND, surface="approval_card", approval_id=record["approval_id"], target=codebase_id)
        yield


def _capture_process_tree(identities, uncertain):
    """Retain every observed lifetime; surviving children remain discovery roots."""
    import psutil
    for pid, born in list(identities):
        try:
            parent = psutil.Process(pid)
            if parent.create_time() != born or not parent.is_running():
                continue
            discovered = parent.children(recursive=True)
            parent_confirmed = False
            try:
                current = psutil.Process(pid)
                parent_confirmed = current.create_time() == born and current.is_running()
            except psutil.NoSuchProcess:
                pass
            for child in discovered:
                try:
                    child_birth = child.create_time()
                    if (child_birth < born or psutil.Process(child.pid).create_time() != child_birth
                            or not child.is_running()):
                        continue
                    identity = (child.pid, child_birth)
                    if not parent_confirmed:
                        if identity not in identities:
                            uncertain.append("unconfirmed descendant ancestry")
                        continue
                    identities.add(identity)
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error:
                    uncertain.append("descendant identity")
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            uncertain.append("descendant discovery")


def run_process(command, *, cwd, env, timeout_s, guard=None, check_current=None):
    """Drain output without retaining unbounded data; stop the owned process tree."""
    import psutil
    limit, captured = 20000, bytearray()
    proc, reader, root_identity = None, None, None
    identities, uncertain, reader_errors = set(), [], []
    reader_started, timed_out = False, False
    deadline = time.monotonic() + min(300, max(1, timeout_s))

    def capture_root():
        nonlocal root_identity
        if root_identity is not None:
            return
        if proc.poll() is not None:
            raise RuntimeError("Build command departed before its identity was captured.")
        owner = psutil.Process(proc.pid)
        born = owner.create_time()
        if proc.poll() is not None or not owner.is_running():
            raise RuntimeError("Build command identity changed during initialization.")
        root_identity = (owner.pid, born)
        identities.add(root_identity)

    def drain():
        try:
            while True:
                data = proc.stdout.read(4096)
                if not data:
                    break
                captured.extend(data[:max(0, limit - len(captured))])
        except Exception:
            reader_errors.append("output read")
        finally:
            try:
                proc.stdout.close()
            except Exception:
                reader_errors.append("output close")

    def children():
        try:
            _capture_process_tree(identities, uncertain)
        except Exception:
            uncertain.append("descendant discovery")

    def alive():
        remaining = []
        for pid, born in sorted(identities, key=lambda identity: identity[1]):
            try:
                child = psutil.Process(pid)
                if child.create_time() == born and child.is_running():
                    remaining.append((child, born))
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                uncertain.append("process identity")
        return remaining

    try:
        # Guard exit, identity lookup and reader startup can all fail after
        # launch. The Popen handle remains the authority for the direct child.
        with (guard if guard is not None else nullcontext()):
            proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                    cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, bufsize=0,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        capture_root()
        children()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        reader_started = True
        while proc.poll() is None:
            if check_current is not None:
                check_current()
            children()
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(.1)
    finally:
        if proc is not None:
            try:
                capture_root()
            except Exception:
                # The root can still be stopped by its Popen-owned handle.
                # Unknown descendants must remain an explicit failed cleanup.
                uncertain.append("root identity was not captured")
            try:
                cleanup_deadline = time.monotonic() + 5
                while True:
                    children()
                    remaining = alive()
                    if (not remaining and proc.poll() is not None) or time.monotonic() >= cleanup_deadline:
                        break
                    for child, born in reversed(remaining):
                        try:
                            current = psutil.Process(child.pid)
                            if current.create_time() == born and current.is_running():
                                current.kill()
                        except psutil.NoSuchProcess:
                            pass
                        except psutil.Error:
                            uncertain.append("process cleanup")
                    # On Windows this targets the original process handle,
                    # never a PID replacement, including failed psutil setup.
                    if proc.poll() is None:
                        try:
                            proc.kill()
                        except OSError:
                            uncertain.append("root cleanup")
                    try:
                        proc.wait(timeout=min(.1, max(0, cleanup_deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                    except OSError:
                        uncertain.append("root wait")
                    time.sleep(.05)
                if alive():
                    uncertain.append("processes remain")
                try:
                    proc.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
                except (subprocess.TimeoutExpired, OSError):
                    uncertain.append("root exit unconfirmed")
            except Exception:
                uncertain.append("process cleanup interrupted")
                # Even discovery failure cannot bypass direct-child cleanup.
                try:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=1)
                except (subprocess.TimeoutExpired, OSError):
                    uncertain.append("root exit unconfirmed")
            finally:
                try:
                    if reader is not None and (reader_started or reader.ident is not None):
                        reader.join(timeout=2)
                except Exception:
                    uncertain.append("output reader cleanup")
                finally:
                    # The unbuffered pipe has no BufferedReader lock that can
                    # block close behind the draining thread's pending read.
                    try:
                        proc.stdout.close()
                    except Exception:
                        uncertain.append("output handle cleanup")
                if reader is not None and reader.is_alive():
                    uncertain.append("output pipe remains")
            uncertain.extend(reader_errors)
            if uncertain:
                observed = ", ".join(f"{pid}@{born}" for pid, born in sorted(identities)) or "none"
                raise RuntimeError("Build cleanup could not be verified; output is not eligible for publishing. "
                                   + "; ".join(sorted(set(uncertain))) + ". Captured process lifetimes: " + observed)
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout_s)
    if check_current is not None:
        check_current()
    return subprocess.CompletedProcess(command, proc.returncode, captured.decode("utf-8", "replace"), "")


def execute_build(site, build):
    from agent_friday.services import codebases, publish_web, sites_privacy
    generation = build.get("privacy_generation")
    sites_privacy.require_operation(generation, build.get("privacy_boot"))
    root = build_dir(site["site_id"], build["build_id"])
    if digest(collect(root / "source")) != build["source_hash"]:
        raise ValueError("Frozen source changed after approval was prepared. Prepare a new build.")
    cwd = contained(root / "source", site["build_root"])
    run = {"status": "ok", "exit": 0, "output": "Static source needs no build command."}
    if site["build_command"]:
        run = codebases.run(site["codebase_id"], site["build_command"], timeout_s=300, _site_snapshot=cwd)
    sites_privacy.require_generation(generation)
    if run.get("status") != "ok" or run.get("exit") != 0:
        return {"status": "failed", "run": run, "error": "The build did not finish successfully."}
    files = collect(contained(cwd, site["output_dir"]), output=True)
    if "index.html" not in files:
        raise ValueError("The output needs an index.html. Server-rendered apps need another hosting adapter.")
    bundle = publish_web.Bundle(files, site["name"], site["site_id"], "site", site["site_id"], site["conversation_id"], site["revision"])
    scan = publish_web.scan(bundle)
    if not scan["ok"]:
        return {"status": "refused", "run": run, "scan": scan, "error": "The static output did not pass the publication scan."}
    write_files(root / "output", files, generation=generation)
    return {"status": "built", "run": run, "scan": scan, "files": manifest(files), "output_hash": digest(files)}
