"""Codebases: a chat's panel with a repository behind it.

docs/design/active/vibe-coding-salon.md §4.1, §4.8, Phase 2. "+ Codebase"
opens a chat whose panel has Preview, Files and Changes. What backs it:

* **A git repository** at `~/.friday/codebases/<id>/repo/`, or an existing
  folder the user points at, which gets a `salon/<slug>` branch rather than
  commits on its own branch.
* **Every applied change is a commit** with an author line naming the model
  and the key profile, a one-line summary Friday writes for people, and a
  step receipt under `.friday/receipts/` (files with hashes, the commit,
  tests, the preview screenshot hash, network events, model, key, cost).
  Friday may not say "done" unless the receipt shows it.
* **Undo is a revert and is itself a step.** It walks backwards through the
  steps not yet undone and never oscillates; the receipts say which step an
  undo removed.
* **The preview is one document.** The frame (§4.3) renders `index.html`
  with its relative stylesheets and scripts inlined, so a static codebase
  runs with no process and no install. Pinned remote scripts (esm.sh) stay.
* **The model is told** the files (small ones inline, large ones by name)
  and the last steps each turn, and edits with `codebase_edit`.

Tier is B0 here. B1/B2 arrive with Phase 4 and change nothing in this file's
contract: a codebase is a repo plus a box, and this module owns the repo.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent_friday.paths import contained, safe_name

_log = logging.getLogger(__name__)

CONTEXT_HEADER = "== CODEBASE (this chat's panel: Preview, Files, Changes) =="
TEMPLATES = ("static", "react", "bundle")
_LOCK = threading.RLock()
_POPEN_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_SKIP_DIRS = {".git", ".friday", "node_modules", "__pycache__", ".venv", "venv"}
_INLINE_MAX = 12_000          # a file this size or smaller rides in the prompt
_CONTEXT_MAX = 40_000         # all inlined files together
_TEXT_EXT = (".html", ".htm", ".css", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt",
             ".svg", ".csv", ".yaml", ".yml", ".toml", ".py")


class NothingToUndo(Exception):
    """Every step has been undone already; only the starting point is left."""


# ── places ───────────────────────────────────────────────────────────────────

def _root() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / "codebases"


def new_id() -> str:
    return "cb-" + secrets.token_hex(4)


def slug_for(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")[:40].strip("-")
    return s or "codebase"


def _dir(cid: str) -> Path:
    return _root() / safe_name(cid, what="codebase id")


def load(cid: str) -> Optional[dict]:
    p = _dir(cid) / "codebase.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save(rec: dict) -> dict:
    d = _dir(rec["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "codebase.json.tmp"
    tmp.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    tmp.replace(d / "codebase.json")
    return rec


def list_all() -> list:
    root = _root()
    if not root.exists():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        rec = load(d.name)
        if rec:
            out.append(rec)
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def repo_path(cid: str) -> Path:
    rec = load(cid)
    if rec is None:
        raise KeyError(cid)
    return Path(rec["repo"])


def is_managed(cid: str) -> bool:
    """True when the repo is Friday's own, under the codebases folder."""
    rec = load(cid)
    if rec is None:
        return False
    try:
        contained(_root(), Path(rec["repo"]).resolve().relative_to(_root().resolve()))
        return True
    except (ValueError, OSError):
        return False


# ── git ──────────────────────────────────────────────────────────────────────

_GIT_IDENTITY = ["-c", "user.name=Friday", "-c", "user.email=friday@local",
                 "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false"]


def _git(repo, *args, check=True, timeout=60) -> subprocess.CompletedProcess:
    cp = subprocess.run(["git", "-C", str(repo), *_GIT_IDENTITY, *args], capture_output=True,
                        text=True, timeout=timeout, creationflags=_POPEN_FLAGS)
    if check and cp.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args[0] if args else "", (cp.stderr or cp.stdout).strip()[:300]))
    return cp


# ── templates ────────────────────────────────────────────────────────────────

_FRAME_NOTE = "<!-- Runs in Friday's sandboxed frame: one document, relative css/js inlined, packages only from https://esm.sh pinned to exact versions. -->\n"


def template_files(template: str, title: str) -> dict:
    """The starting files of a new codebase, by template."""
    slug = slug_for(title)
    if template == "static":
        return {
            "index.html": _FRAME_NOTE + (
                "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
                "<title>%s</title>\n<link rel=\"stylesheet\" href=\"styles.css\">\n</head>\n<body>\n"
                "<main>\n  <h1>%s</h1>\n  <p class=\"lead\">Say what this should do, and Friday changes it. Every change is a step you can undo.</p>\n"
                "  <ul id=\"items\"></ul>\n  <form id=\"add\"><input id=\"text\" placeholder=\"Add something\u2026\" autocomplete=\"off\"><button>Add</button></form>\n"
                "</main>\n<script src=\"app.js\"></script>\n</body>\n</html>\n" % (title, title)),
            "styles.css": (
                ":root{--bg:#0b0e14;--fg:#e6eef8;--accent:#00d4ff;--line:rgba(255,255,255,.08)}\n"
                "html,body{margin:0;background:var(--bg);color:var(--fg);font-family:Inter,system-ui,sans-serif;line-height:1.5}\n"
                "main{max-width:720px;margin:0 auto;padding:32px 20px}\nh1{color:var(--accent);font-size:22px;margin:0 0 6px}\n"
                ".lead{color:rgba(230,238,248,.7);margin:0 0 18px}\n#items{list-style:none;padding:0;margin:0 0 14px}\n"
                "#items li{padding:8px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}\n"
                "#add{display:flex;gap:8px}#add input{flex:1;padding:8px 10px;border-radius:6px;border:1px solid var(--line);background:rgba(255,255,255,.05);color:var(--fg)}\n"
                "#add button{padding:8px 14px;border:0;border-radius:6px;background:var(--accent);color:#04121a;font-weight:600;cursor:pointer}\n"),
            "app.js": (
                "// Plain, framework-free. State lives in memory while the page is open.\n"
                "const items = [];\nconst list = document.getElementById('items');\nconst form = document.getElementById('add');\n"
                "const text = document.getElementById('text');\nfunction render() {\n  list.innerHTML = '';\n  items.forEach((it, i) => {\n"
                "    const li = document.createElement('li');\n    li.textContent = it;\n    const del = document.createElement('button');\n"
                "    del.textContent = '\\u00d7';\n    del.onclick = () => { items.splice(i, 1); render(); };\n    li.appendChild(del);\n    list.appendChild(li);\n  });\n}\n"
                "form.onsubmit = e => { e.preventDefault(); if (text.value.trim()) { items.unshift(text.value.trim()); text.value = ''; render(); } };\nrender();\n"),
            "README.md": "# %s\n\nMade in Friday's salon. Static app: one page, runs in the browser.\n" % title,
        }
    if template == "react":
        return {
            "index.html": _FRAME_NOTE + (
                "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
                "<title>%s</title>\n<link rel=\"stylesheet\" href=\"styles.css\">\n</head>\n<body>\n<div id=\"root\">Loading\u2026</div>\n"
                "<script type=\"text/jsx\" src=\"app.jsx\"></script>\n"
                "<script type=\"module\">\n// esbuild-wasm compiles the JSX above in the browser; packages come from esm.sh at exact versions (spike S1).\n"
                "const CDN = 'https://esm.sh';\nconst PINS = { 'react': 'react@18.3.1', 'react-dom/client': 'react-dom@18.3.1/client', 'react/jsx-runtime': 'react@18.3.1/jsx-runtime' };\n"
                "const esbuild = await import('https://esm.sh/esbuild-wasm@0.25.12/esm/browser.min.js');\n"
                "await esbuild.initialize({ wasmURL: 'https://esm.sh/esbuild-wasm@0.25.12/esbuild.wasm', worker: false });\n"
                "const src = document.querySelector('script[type=\"text/jsx\"]').textContent;\n"
                "const plugin = { name: 'cdn', setup(b) {\n  b.onResolve({ filter: /.*/ }, a => {\n    if (a.path === '/app.jsx') return { path: a.path, namespace: 'local' };\n"
                "    if (a.path.startsWith('http')) return { path: a.path, namespace: 'http' };\n    if ((a.path.startsWith('/') || a.path.startsWith('.')) && a.namespace === 'http') return { path: new URL(a.path, a.importer).href, namespace: 'http' };\n"
                "    if (PINS[a.path]) return { path: CDN + '/' + PINS[a.path], namespace: 'http' };\n    return { errors: [{ text: 'unpinned import refused: ' + a.path }] };\n  });\n"
                "  b.onLoad({ filter: /.*/, namespace: 'local' }, () => ({ contents: src, loader: 'jsx' }));\n"
                "  b.onLoad({ filter: /.*/, namespace: 'http' }, async a => ({ contents: await (await fetch(a.path)).text(), loader: 'js' }));\n} };\n"
                "try {\n  const out = await esbuild.build({ entryPoints: ['/app.jsx'], bundle: true, write: false, format: 'iife', jsx: 'automatic', plugins: [plugin], target: 'es2020' });\n"
                "  const s = document.createElement('script'); s.textContent = out.outputFiles[0].text; document.body.appendChild(s);\n"
                "} catch (e) { document.getElementById('root').textContent = 'Build failed: ' + (e && e.message || e); }\n</script>\n</body>\n</html>\n" % title),
            "app.jsx": (
                "import React, { useState } from 'react';\nimport { createRoot } from 'react-dom/client';\n\n"
                "function App() {\n  const [count, setCount] = useState(0);\n  return (\n    <main>\n      <h1>%s</h1>\n"
                "      <p className=\"lead\">A React app, built in the browser. Ask Friday for changes; each one is a step you can undo.</p>\n"
                "      <button onClick={() => setCount(count + 1)}>Clicked {count} times</button>\n    </main>\n  );\n}\n\n"
                "createRoot(document.getElementById('root')).render(<App />);\n" % title),
            "styles.css": (
                "html,body{margin:0;background:#0b0e14;color:#e6eef8;font-family:Inter,system-ui,sans-serif}\nmain{max-width:720px;margin:0 auto;padding:32px 20px}\n"
                "h1{color:#00d4ff;font-size:22px;margin:0 0 6px}.lead{color:rgba(230,238,248,.7)}\nbutton{padding:8px 14px;border:0;border-radius:6px;background:#00d4ff;color:#04121a;font-weight:600;cursor:pointer}\n"),
            "README.md": "# %s\n\nMade in Friday's salon. React in the frame: JSX compiled in the browser, packages pinned on esm.sh.\n" % title,
        }
    if template == "bundle":
        manifest = {
            "id": slug, "name": title, "version": "0.1.0",
            "author": {"name": "you", "pubkey": ""},
            "authored_by_agent": True, "friday_api": 1,
            "capabilities": {"read": [], "write": [], "network": ["none"]},
            "integrity": {"sha256": ""},
        }
        return {
            "manifest.json": json.dumps(manifest, indent=2) + "\n",
            "index.html": _FRAME_NOTE + (
                "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<title>%s</title>\n"
                "<style>html,body{margin:0;background:#0b0e14;color:#e6eef8;font-family:Inter,system-ui,sans-serif}main{padding:18px}h1{color:#00d4ff;font-size:18px;margin:0 0 8px}</style>\n"
                "</head>\n<body>\n<main>\n  <h1>%s</h1>\n  <p>A workspace bundle: one HTML file, installed disabled, granted nothing until you say so.</p>\n</main>\n"
                "<script>\n// Everything crosses to Friday by postMessage to the broker; nothing else is reachable from here.\n"
                "window.addEventListener('message', e => { if (e.data && e.data.__friday) console.log('broker:', e.data); });\n</script>\n</body>\n</html>\n" % (title, title)),
            "icon.svg": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#0b0e14\"/><circle cx=\"32\" cy=\"32\" r=\"16\" fill=\"none\" stroke=\"#00d4ff\" stroke-width=\"4\"/></svg>\n",
            "README.md": "# %s\n\nA workspace bundle for Friday (workspace-ecosystem.md §4.2). One HTML file, no build step.\n" % title,
        }
    raise ValueError("unknown template %r; one of %s" % (template, ", ".join(TEMPLATES)))


# ── creating ─────────────────────────────────────────────────────────────────

def create(title: str, template: str = "static", *, conversation_id: Optional[str] = None,
           existing_path: Optional[str] = None) -> dict:
    """A new codebase from a template, or an existing folder on a salon branch."""
    title = str(title or "").strip() or "Untitled"
    slug = slug_for(title)
    cid = new_id()
    now = datetime.now().isoformat(timespec="seconds")
    if existing_path:
        folder = Path(os.path.expanduser(str(existing_path)))
        if not folder.is_dir():
            raise ValueError("that folder does not exist: %s" % existing_path)
        folder = folder.resolve()
        if not (folder / ".git").exists():
            _git(folder, "init", "-q")
            if _git(folder, "rev-parse", "--verify", "HEAD", check=False).returncode != 0:
                _git(folder, "add", "-A")
                _git(folder, "commit", "-q", "--allow-empty", "-m", "Start: %s (as found)" % title)
        branch = "salon/" + slug
        _git(folder, "checkout", "-q", "-B", branch)
        rec = {"id": cid, "title": title, "slug": slug, "template": None, "tier": "B0",
               "repo": str(folder), "branch": branch, "existing": True,
               "conversation_id": conversation_id, "created_at": now}
    else:
        if template not in TEMPLATES:
            raise ValueError("unknown template %r; one of %s" % (template, ", ".join(TEMPLATES)))
        repo = _dir(cid) / "repo"
        repo.mkdir(parents=True, exist_ok=False)
        _git(repo, "init", "-q", "-b", "main")
        for path, content in template_files(template, title).items():
            (repo / path).write_text(content, encoding="utf-8", newline="\n")
        (repo / ".gitignore").write_text(".friday/\nnode_modules/\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "Start: %s" % title)
        rec = {"id": cid, "title": title, "slug": slug, "template": template, "tier": "B0",
               "repo": str(repo), "branch": "main", "existing": False,
               "conversation_id": conversation_id, "created_at": now}
    (Path(rec["repo"]) / ".friday" / "receipts").mkdir(parents=True, exist_ok=True)
    _save(rec)
    if conversation_id:
        bind(cid, conversation_id)
    return rec


def bind(cid: str, conversation_id: str) -> None:
    """The conversation carries the codebase id, the way a project chat carries a project."""
    rec = load(cid)
    if rec is None:
        raise KeyError(cid)
    rec["conversation_id"] = conversation_id
    _save(rec)
    try:
        from agent_friday.services import conversations as _convs
        _convs.patch(conversation_id, codebase=cid)
    except Exception as e:
        _log.warning("could not bind conversation %s to codebase %s: %s", conversation_id, cid, e)


def for_conversation(conversation_id: Optional[str]) -> Optional[dict]:
    if not conversation_id:
        return None
    try:
        from agent_friday.services import conversations as _convs
        conv = _convs.load(conversation_id) or {}
    except Exception:
        return None
    cbid = conv.get("codebase")
    return load(cbid) if cbid else None


# ── files ────────────────────────────────────────────────────────────────────

def _check_rel(repo: Path, rel: str) -> Path:
    s = str(rel or "")
    if not s or "\x00" in s or "\\" in s or s.startswith("/") or ":" in s:
        raise ValueError("invalid path %r" % rel)
    first = s.split("/", 1)[0]
    if first in (".git", ".friday") or any(part in ("", ".", "..") for part in s.split("/")):
        raise ValueError("invalid path %r" % rel)
    return contained(repo, s)


def files(cid: str) -> list:
    repo = repo_path(cid)
    out = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(repo).as_posix()
            if rel == ".gitignore":
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out.append({"path": rel, "bytes": size, "text": p.suffix.lower() in _TEXT_EXT})
        if len(out) >= 400:
            break
    out.sort(key=lambda f: f["path"])
    return out


def read(cid: str, rel: str) -> Optional[str]:
    repo = repo_path(cid)
    p = _check_rel(repo, rel)
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


# ── steps ────────────────────────────────────────────────────────────────────

def _receipt_dir(repo: Path) -> Path:
    d = repo / ".friday" / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_receipt(repo: Path, rec: dict) -> None:
    (_receipt_dir(repo) / (rec["commit"] + ".json")).write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")


def _read_receipt(repo: Path, sha: str) -> Optional[dict]:
    p = _receipt_dir(repo) / (sha + ".json")
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def step(cid: str, changes: dict, summary: str, *, author: str = "friday", model: str = "",
         key_profile: str = "mine", tests: Optional[dict] = None, cost_usd: Optional[float] = None) -> Optional[dict]:
    """Apply `changes` ({path: text, or None to delete}) as one commit with a
    receipt. Returns the step, or None when nothing changed."""
    with _LOCK:
        repo = repo_path(cid)
        targets = {rel: _check_rel(repo, rel) for rel in (changes or {})}
        if not targets:
            return None
        deleted, written = [], []
        for rel, p in targets.items():
            content = changes[rel]
            if content is None:
                if p.exists():
                    p.unlink()
                    deleted.append(rel)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(content), encoding="utf-8", newline="\n")
            written.append(rel)
        _git(repo, "add", "-A")
        if _git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        who = "you" if author == "you" else ("%s via %s" % (model or "Friday", key_profile or "mine"))
        summary = " ".join(str(summary or "Change").split())[:200]
        _git(repo, "commit", "-q", "-m", summary, "--author", "%s <salon@local>" % who)
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        rec = {
            "commit": sha, "kind": "step", "summary": summary, "author": author, "who": who,
            "model": model, "key_profile": key_profile, "cost_usd": cost_usd,
            "files": [{"path": rel, "bytes": targets[rel].stat().st_size,
                       "sha256": hashlib.sha256(targets[rel].read_bytes()).hexdigest()} for rel in written],
            "deleted": deleted, "tests": tests, "preview_hash": None, "network_events": [],
            "ts": time.time(), "at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_receipt(repo, rec)
        out = {"sha": sha, "kind": "step", "summary": summary, "author": author, "who": who, "receipt": rec}
        _announce(cid, out)
        return out


def write(cid: str, rel: str, content: str) -> Optional[dict]:
    """A hand edit in the Files tab: a step authored by "you"."""
    return step(cid, {rel: content}, "You edited %s" % rel, author="you", model="", key_profile="")


def _log_entries(repo: Path, limit: int = 200) -> list:
    cp = _git(repo, "log", "--format=%H%x1f%an%x1f%s%x1f%at", "-n", str(limit))
    out = []
    for line in cp.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, who, summary, ts = parts
        kind = "undo" if summary.startswith("Undo: ") else ("start" if summary.startswith("Start: ") else "step")
        out.append({"sha": sha, "who": who, "summary": summary, "ts": int(ts or 0), "kind": kind})
    return out


def steps(cid: str, limit: int = 50) -> list:
    """Newest first: sha, kind, summary, who, when, the receipt, and for an
    undo which step it removed."""
    repo = repo_path(cid)
    out = []
    for e in _log_entries(repo, limit):
        r = _read_receipt(repo, e["sha"]) or {}
        e = dict(e)
        e["receipt"] = r or None
        e["author"] = r.get("author") or ("you" if e["who"] == "you" else "friday")
        e["at"] = datetime.fromtimestamp(e["ts"]).isoformat(timespec="seconds") if e["ts"] else None
        if e["kind"] == "undo":
            e["undoes"] = r.get("undoes")
        out.append(e)
    return out


def undo(cid: str) -> dict:
    """Revert the newest step not yet undone. Walks backwards: after undoing
    step 3, the next undo removes step 2, never step 3 again."""
    with _LOCK:
        repo = repo_path(cid)
        entries = _log_entries(repo, 500)
        undone = set()
        for e in entries:
            if e["kind"] == "undo":
                r = _read_receipt(repo, e["sha"]) or {}
                if r.get("undoes"):
                    undone.add(r["undoes"])
        target = next((e for e in entries if e["kind"] == "step" and e["sha"] not in undone), None)
        if target is None:
            raise NothingToUndo("every step has been undone; only the starting point is left")
        cp = _git(repo, "revert", "--no-commit", "--no-edit", target["sha"], check=False)
        if cp.returncode != 0:
            _git(repo, "revert", "--abort", check=False)
            raise RuntimeError("that step cannot be undone cleanly: %s" % (cp.stderr or cp.stdout).strip()[:200])
        summary = "Undo: %s" % target["summary"]
        _git(repo, "commit", "-q", "-m", summary, "--author", "you <salon@local>")
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        rec = {"commit": sha, "kind": "undo", "summary": summary, "author": "you", "who": "you",
               "undoes": target["sha"], "files": [], "deleted": [], "tests": None, "preview_hash": None,
               "network_events": [], "model": "", "key_profile": "", "cost_usd": None,
               "ts": time.time(), "at": datetime.now().isoformat(timespec="seconds")}
        _write_receipt(repo, rec)
        out = {"sha": sha, "kind": "undo", "summary": summary, "author": "you", "undoes": target["sha"], "receipt": rec}
        _announce(cid, out)
        return out


def _announce(cid: str, st: dict) -> None:
    """Tell every open chat page a step landed. No content rides along; the
    panel re-reads the codebase."""
    try:
        from agent_friday.services import desktop_bus
        rec = load(cid) or {}
        desktop_bus.broadcast({"type": "codebase_step", "codebase_id": cid,
                               "conversation_id": rec.get("conversation_id"),
                               "sha": st["sha"], "kind": st["kind"], "summary": st["summary"],
                               "author": st.get("author")}, kind="chat")
    except Exception:
        pass


def diff(cid: str, sha: str) -> str:
    repo = repo_path(cid)
    if not re.fullmatch(r"[0-9a-f]{7,40}", str(sha or "")):
        raise ValueError("invalid commit")
    return _git(repo, "show", "--format=", "--no-color", sha).stdout


# ── the preview document ─────────────────────────────────────────────────────

_LINK_RE = re.compile(r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>""", re.I)
_SCRIPT_RE = re.compile(r"""<script\b([^>]*)\bsrc\s*=\s*["']([^"']+)["']([^>]*)>\s*</script>""", re.I | re.S)


def preview(cid: str) -> str:
    """index.html with its relative stylesheets and scripts inlined: one
    document the sandboxed frame can run. Remote pinned scripts stay."""
    repo = repo_path(cid)
    index = repo / "index.html"
    if not index.is_file():
        return ("<!doctype html><html><body style=\"background:#0b0e14;color:#e6eef8;font-family:Inter,system-ui\">"
                "<p style=\"padding:20px\">No index.html in this codebase yet. Ask Friday to make the first page.</p></body></html>")
    html = index.read_text(encoding="utf-8", errors="replace")

    def local(src: str) -> Optional[str]:
        if re.match(r"^[a-z]+:|^//", src, re.I):
            return None
        try:
            p = _check_rel(repo, src.split("?", 1)[0].split("#", 1)[0].lstrip("./"))
        except ValueError:
            return None
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    def link(m):
        css = local(m.group(1))
        return m.group(0) if css is None else "<style>" + css.replace("</style", "<\\/style") + "</style>"

    def script(m):
        before, src, after = m.group(1), m.group(2), m.group(3)
        js = local(src)
        if js is None:
            return m.group(0)
        attrs = (before + " " + after).strip()
        attrs = re.sub(r"\s+", " ", attrs)
        return "<script%s>%s</script>" % ((" " + attrs) if attrs else "", js.replace("</script", "<\\/script"))

    html = _LINK_RE.sub(link, html)
    html = _SCRIPT_RE.sub(script, html)
    return html


# ── export: a plain project, no lock-in ─────────────────────────────────────

_EXPORT_NOTE = "Exported from Friday's salon as a plain project: no lock-in, nothing of Friday's inside."


def export_zip(cid: str) -> tuple:
    """(filename, bytes): the working tree under one folder named by the slug,
    with a plain README and nothing of Friday's (§4.11 item 10). Reads only;
    the codebase is untouched."""
    rec = load(cid)
    if rec is None:
        raise KeyError(cid)
    repo = Path(rec["repo"])
    slug = rec["slug"]
    readme = None
    entries = []
    for f in files(cid):
        p = repo / f["path"]
        if not p.is_file():
            continue
        data = p.read_bytes()
        if f["path"] == "README.md":
            readme = data.decode("utf-8", "replace")
            continue
        entries.append((f["path"], data))
    if readme is None:
        readme = "# %s\n\n" % rec["title"]
    if _EXPORT_NOTE not in readme:
        readme = readme.rstrip("\n") + "\n\n---\n\n" + _EXPORT_NOTE + " Open index.html in a browser, or serve the folder with any static server.\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(slug + "/README.md", readme.encode("utf-8"))
        for rel, data in entries:
            z.writestr(slug + "/" + rel, data)
    return slug + ".zip", buf.getvalue()


# ── what the model is told ───────────────────────────────────────────────────

def context_block_for(cid: str) -> str:
    rec = load(cid)
    if rec is None:
        return ""
    lines = ["", "", CONTEXT_HEADER,
             "Codebase %s \u00b7 \"%s\" \u00b7 %s \u00b7 tier %s (the browser frame: one index.html, relative css/js inlined, no server, packages only from https://esm.sh pinned to exact versions)."
             % (rec["id"], rec["title"], rec.get("template") or "existing folder", rec.get("tier", "B0")),
             "Change files with codebase_edit(files={path: full new content, or null to delete}, summary=one plain line for the user). "
             "Every call is a step the user can undo with \"undo that\" (codebase_undo). Read a file you were not shown with codebase_read. "
             "Do not say a change is done until the tool result names the step. "
             "For a BIG ask (a new feature, several files), call plan_first with a short plan and 3-7 milestones and stop; "
             "build only after the user approves it."]
    budget = _CONTEXT_MAX
    lines.append("Files:")
    for f in files(cid):
        text = None
        if f["text"] and f["bytes"] <= _INLINE_MAX and budget - f["bytes"] > 0:
            text = read(cid, f["path"])
        if text is not None:
            budget -= len(text)
            lines.append("--- %s (%d bytes) ---" % (f["path"], f["bytes"]))
            lines.append(text.rstrip("\n"))
        else:
            lines.append("--- %s (%d bytes, not shown; codebase_read to see it) ---" % (f["path"], f["bytes"]))
    lines.append("Last steps (newest first):")
    for s in steps(cid, limit=6):
        lines.append("- %s \u00b7 %s \u00b7 %s" % (s["sha"][:7], s["kind"], s["summary"]))
    return "\n".join(lines) + "\n"


def context_block(conversation_id: Optional[str]) -> str:
    rec = for_conversation(conversation_id)
    return context_block_for(rec["id"]) if rec else ""
