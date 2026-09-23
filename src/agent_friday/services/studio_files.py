"""Read-only file browsing for the Studio 3D file browser.

Invariants:

* Only the folders Friday already exposes are browsable: the file-search
  roots (Documents, Downloads, Desktop, Creations, plus any configured
  ``file_search_roots``) and ``~/Projects`` (the Code workspace root). A
  request names a root by id and a relative path; the resolved real path
  must stay inside that root, so ``..`` and junctions cannot escape it.
* Friday's own home (including the vault) is never listed or served, even
  when a root happens to contain it -- and neither is any COPY of it (a
  backup folder on the Desktop), recognised by the files every Friday home
  holds.
* A user deny mark (``file_grants``) hides a file or folder completely:
  it is not listed, thumbnailed, served, opened or revealed.
* Dotfiles, dependency/build folders and key material are skipped.
* Nothing here writes to the browsed folders. Delete, move and rename are
  requested through the approval gate and run only after the owner
  approves; delete goes to the Recycle Bin, never a permanent unlink.
"""
from __future__ import annotations

import fnmatch
import hashlib
import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

_log = logging.getLogger(__name__)

# Mirrors code_engine._SKIP_DIRS (the Code workspace's file tree), plus the
# Windows system folders that only ever produce permission errors.
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist',
             'build', '.next', '.cache', 'test-results', '.pytest_cache',
             '$recycle.bin', 'system volume information'}

# Key material and credential stores: hidden even though their names are not
# dotfiles. Matched case-insensitively against the file name.
SECRET_PATTERNS = ('id_rsa*', 'id_dsa*', 'id_ecdsa*', 'id_ed25519*', '*.pem',
                   '*.key', '*.pfx', '*.p12', '*.kdbx', '*.ppk', '*.jks',
                   '*.keystore', 'credentials*.json', 'client_secret*.json',
                   'token*.json', '*.env')

IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff', 'ico'}
VIDEO_EXTS = {'mp4', 'webm', 'mov', 'mkv', 'avi', 'm4v', 'wmv'}
AUDIO_EXTS = {'mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac', 'opus'}
TEXT_EXTS = {'txt', 'md', 'markdown', 'rst', 'log', 'csv', 'tsv', 'json',
             'jsonl', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'html', 'htm',
             'css', 'scss', 'js', 'jsx', 'ts', 'tsx', 'mjs', 'py', 'rs', 'go',
             'java', 'kt', 'c', 'h', 'cpp', 'hpp', 'cs', 'rb', 'php', 'sh',
             'ps1', 'bat', 'sql', 'lua', 'swift', 'vue', 'svelte', 'srt', 'vtt'}

# Types the OS may launch with their default application. Anything that can
# execute code on open (.exe, .bat, .py, .js, .lnk, .hta ...) is absent, so
# "Open" never runs a program; Reveal still works for those.
OPENABLE_EXTS = (IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS |
                 {'svg', 'pdf', 'txt', 'md', 'csv', 'rtf', 'doc', 'docx', 'odt',
                  'xlsx', 'xls', 'ods', 'pptx', 'ppt', 'odp', 'epub', 'srt',
                  'glb', 'gltf', 'obj', 'fbx', 'blend', 'psd', 'ai', 'kra'})

# Served inline as their real type. Everything else is served as plain text
# (or octet-stream) under a sandbox CSP, so an .html or .svg file opened from
# the browser can never run script on Friday's origin.
_INLINE_TYPES = {
    **{e: f'image/{"jpeg" if e in ("jpg", "jpeg") else e}' for e in
       ('png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp')},
    'svg': 'image/svg+xml', 'ico': 'image/x-icon',
    'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime',
    'm4v': 'video/mp4', 'mp3': 'audio/mpeg', 'wav': 'audio/wav',
    'ogg': 'audio/ogg', 'm4a': 'audio/mp4', 'flac': 'audio/flac',
    'aac': 'audio/aac', 'opus': 'audio/ogg', 'pdf': 'application/pdf',
}

ROOT_LABELS = {'documents': 'Documents', 'downloads': 'Downloads',
               'desktop': 'Desktop', 'creations': 'Creations',
               'projects': 'Projects'}

DEFAULT_LIMIT = 6000
MAX_LIMIT = 20000
MAX_DEPTH = 12
SCAN_BUDGET_S = 6.0
TEXT_PREVIEW_BYTES = 256 * 1024
THUMB_SIZES = (96, 128, 256)
THUMB_VERSION = 'v1'
THUMB_CACHE_MAX_FILES = 20000

APPROVAL_KIND = 'studio_file_change'


class Denied(Exception):
    """A path request outside what the browser may expose. The message is
    safe to show: it names the rule, never the filesystem beyond the root."""


# ── roots ──────────────────────────────────────────────────────────────────

def roots() -> dict:
    from agent_friday.core import HOME
    from agent_friday.services import file_search
    out = dict(file_search._configured_roots())
    out['projects'] = Path(HOME) / 'Projects'
    return out


def list_roots() -> list:
    out = []
    for rid, p in roots().items():
        try:
            exists = p.is_dir()
        except OSError:
            exists = False
        out.append({'id': rid, 'label': ROOT_LABELS.get(rid, p.name or rid),
                    'available': bool(exists)})
    return out


def _excluded(root_real: Path = None) -> list:
    """Friday's home and vault, as normcased real paths. When an exposed root
    itself lives inside Friday's home (Creations does, when the Desktop is
    missing or FRIDAY_HOME is redirected) only the vault stays excluded:
    the root was exposed on purpose, and nothing outside it is reachable
    through it anyway."""
    from agent_friday.core import FRIDAY_DIR
    from agent_friday.services import file_search
    out = []
    for p in (Path(FRIDAY_DIR), file_search._vault_root()):
        try:
            out.append(os.path.normcase(str(p.resolve())))
        except OSError:
            pass
    if root_real is not None and out:
        nr = os.path.normcase(str(root_real))
        if _under(nr, out[0]):
            out = out[1:]
    return out


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip('\\/') + os.sep)


def is_friday_home_copy(dirpath: str) -> bool:
    """A folder holding a Friday home's own files: SOUL.md and settings.json
    together with the vault or the activity ledger."""
    j = os.path.join
    try:
        return (os.path.isfile(j(dirpath, 'SOUL.md')) and os.path.isfile(j(dirpath, 'settings.json'))
                and (os.path.isdir(j(dirpath, 'vault')) or os.path.isfile(j(dirpath, 'activity_ledger.jsonl'))))
    except OSError:
        return True


def is_secret_name(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in SECRET_PATTERNS)


def _hidden_name(name: str, is_dir: bool) -> bool:
    if name.startswith('.') or name.startswith('~$'):
        return True
    if is_dir:
        return name.lower() in SKIP_DIRS
    return is_secret_name(name)


class _DenyMatcher:
    """The owner's deny marks, prepared once per scan. Equivalent to
    file_grants.check_grant(p).state == 'denied' for resolved paths, without
    a resolve() syscall per rule per entry."""

    def __init__(self):
        self.files, self.folders, self.globs = set(), [], []
        try:
            from agent_friday.services import file_grants
            denies = file_grants._load_state().denies.values()
        except Exception as e:
            _log.warning('deny marks unavailable: %s', e)
            denies = []
        for d in denies:
            t, rp = d.get('type'), d.get('path') or ''
            if not rp:
                continue
            if t == 'glob':
                self.globs.append(rp.replace('\\', '/'))
                continue
            try:
                norm = os.path.normcase(str(Path(rp).resolve()))
            except OSError:
                norm = os.path.normcase(rp)
            if t == 'file':
                self.files.add(norm)
            elif t == 'folder':
                self.folders.append(norm)

    def denied(self, real: str) -> bool:
        n = os.path.normcase(real)
        if n in self.files or any(_under(n, f) for f in self.folders):
            return True
        if self.globs:
            s = real.replace('\\', '/')
            return any(fnmatch.fnmatch(s, g) for g in self.globs)
        return False


def resolve(root_id: str, rel: str = '') -> Path:
    """The real path for (root, relative path), or Denied."""
    rmap = roots()
    if root_id not in rmap:
        raise Denied(f'unknown folder {root_id!r}')
    root = rmap[root_id]
    try:
        root_real = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Denied(f'{ROOT_LABELS.get(root_id, root_id)} is not available on this computer')
    rel = (rel or '').replace('\\', '/').strip('/')
    parts = [s for s in rel.split('/') if s not in ('', '.')]
    for seg in parts:
        if seg == '..' or ':' in seg:
            raise Denied('path leaves the folder')
    try:
        real = root_real.joinpath(*parts).resolve(strict=True)
    except FileNotFoundError:
        raise Denied('no such file')
    except (OSError, RuntimeError):
        raise Denied('path could not be read')
    rs, ns = os.path.normcase(str(real)), os.path.normcase(str(root_real))
    if not _under(rs, ns):
        raise Denied('path leaves the folder')
    for i, seg in enumerate(parts):
        last = i == len(parts) - 1
        if _hidden_name(seg, is_dir=not last or real.is_dir()):
            raise Denied('hidden or private file')
    if any(_under(rs, ex) for ex in _excluded(root_real)):
        raise Denied("Friday's own data is not browsable")
    cur = root_real
    for seg in parts:
        cur = cur / seg
        if cur.is_dir() and is_friday_home_copy(str(cur)):
            raise Denied("that folder is a copy of Friday's own data")
    try:
        from agent_friday.services import file_grants
        if file_grants.check_grant(real).state == 'denied':
            raise Denied('you have marked this private')
    except Denied:
        raise
    except Exception as e:
        _log.warning('check_grant failed, refusing: %s', e)
        raise Denied('privacy check unavailable')
    return real


# ── scan ───────────────────────────────────────────────────────────────────

def scan(root_id: str, rel: str = '', *, limit: int = DEFAULT_LIMIT,
         max_depth: int = 6, budget_s: float = SCAN_BUDGET_S) -> dict:
    """Breadth-first listing under (root, rel). Breadth-first so a truncated
    scan keeps the whole top of the tree rather than one deep branch.

    entries: [relpath, is_dir, size, mtime] with relpath relative to the root
    (forward slashes). Folder sizes are the sum of listed descendants."""
    base = resolve(root_id, rel)
    if not base.is_dir():
        raise Denied('not a folder')
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    max_depth = max(1, min(int(max_depth or 6), MAX_DEPTH))
    root_real = roots()[root_id].resolve()
    excluded = _excluded(root_real)
    deny = _DenyMatcher()
    t0 = time.monotonic()
    entries: list = []
    index_of: dict = {}
    truncated = False
    skipped = 0
    base_rel = os.path.relpath(base, root_real).replace('\\', '/')
    base_rel = '' if base_rel == '.' else base_rel
    queue = deque([(str(base), base_rel, 0, -1)])
    while queue:
        dpath, drel, depth, parent_idx = queue.popleft()
        try:
            it = list(os.scandir(dpath))
        except OSError:
            continue
        it.sort(key=lambda e: e.name.lower())
        for de in it:
            if len(entries) >= limit or time.monotonic() - t0 > budget_s:
                truncated = True
                break
            try:
                is_link = de.is_symlink()
                is_dir = de.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_link or _hidden_name(de.name, is_dir):
                skipped += 1
                continue
            real = de.path
            nreal = os.path.normcase(real)
            if any(_under(nreal, ex) for ex in excluded) or deny.denied(real)                     or (is_dir and is_friday_home_copy(real)):
                skipped += 1
                continue
            try:
                st = de.stat(follow_symlinks=False)
            except OSError:
                continue
            erel = f'{drel}/{de.name}' if drel else de.name
            idx = len(entries)
            entries.append([erel, 1 if is_dir else 0,
                            0 if is_dir else int(st.st_size), int(st.st_mtime)])
            index_of[erel] = idx
            if is_dir and depth + 1 < max_depth:
                queue.append((real, erel, depth + 1, idx))
        if truncated:
            break
    # Roll file sizes up into their folders so the city view can size districts.
    for e in reversed(entries):
        if e[2] and '/' in e[0]:
            pr = e[0].rsplit('/', 1)[0]
            pi = index_of.get(pr)
            while pi is not None:
                entries[pi][2] += e[2]
                pr = entries[pi][0].rsplit('/', 1)[0] if '/' in entries[pi][0] else None
                pi = index_of.get(pr) if pr else None
    return {'root': root_id, 'label': ROOT_LABELS.get(root_id, root_id),
            'path': base_rel, 'entries': entries, 'truncated': truncated,
            'creations_prefix': _creations_prefix(root_real),
            'skipped': skipped, 'elapsed_ms': int((time.monotonic() - t0) * 1000)}


def _creations_prefix(root_real: Path):
    """Where Friday's creations folder sits inside this root ('' when the
    root is that folder, None when it is not inside), so the UI can offer
    Share, which posts creations only."""
    try:
        from agent_friday.core import CREATIONS_DIR
        rel = os.path.relpath(Path(CREATIONS_DIR).resolve(), root_real)
    except (ValueError, OSError):
        return None
    if rel == '.':
        return ''
    if rel.startswith('..') or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, '/')


# ── thumbnails ─────────────────────────────────────────────────────────────

_FFMPEG_SLOTS = threading.Semaphore(2)
_writes = {'n': 0}


def _cache_dir() -> Path:
    from agent_friday.core import FRIDAY_DIR
    d = Path(FRIDAY_DIR) / 'cache' / 'studio_thumbs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prune_cache(d: Path) -> None:
    try:
        files = [p for p in d.rglob('*.webp')]
        if len(files) <= THUMB_CACHE_MAX_FILES:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for p in files[:len(files) - int(THUMB_CACHE_MAX_FILES * 0.8)]:
            try:
                p.unlink()
            except OSError:
                pass
    except Exception as e:
        _log.debug('thumb prune failed: %s', e)


def ffmpeg_exe():
    try:
        from agent_friday.services.timeline_engine import ffmpeg_exe as _ff
        exe = _ff()
        if exe:
            return exe
    except Exception:
        pass
    return shutil.which('ffmpeg')


def _video_frame(p: Path, size: int):
    exe = ffmpeg_exe()
    if not exe:
        return None
    from PIL import Image
    flags = 0x08000000 if sys.platform == 'win32' else 0  # CREATE_NO_WINDOW
    with _FFMPEG_SLOTS:
        for ss in ('1.5', '0'):
            try:
                r = subprocess.run(
                    [exe, '-v', 'error', '-ss', ss, '-i', str(p), '-frames:v', '1',
                     '-vf', f'scale={size * 2}:-2', '-f', 'image2pipe',
                     '-vcodec', 'png', '-'],
                    capture_output=True, timeout=12, creationflags=flags)
            except (subprocess.TimeoutExpired, OSError):
                return None
            if r.returncode == 0 and r.stdout:
                try:
                    return Image.open(io.BytesIO(r.stdout)).convert('RGB')
                except Exception:
                    return None
    return None


def _text_card(p: Path, size: int):
    from PIL import Image, ImageDraw, ImageFont
    try:
        with open(p, 'rb') as f:
            raw = f.read(4096)
    except OSError:
        return None
    if b'\x00' in raw[:1024]:
        return None
    text = raw.decode('utf-8', errors='replace').replace('\t', '  ')
    W = 256
    img = Image.new('RGB', (W, W), (14, 18, 28))
    d = ImageDraw.Draw(img)
    font = None
    for name in ('consola.ttf', 'DejaVuSansMono.ttf', 'Menlo.ttc'):
        try:
            font = ImageFont.truetype(name, 11)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    y = 8
    for line in text.splitlines()[:22]:
        s = line.rstrip()[:44]
        st = s.lstrip()
        col = (120, 132, 150) if st.startswith(('#', '//', '--', '/*', '*', '<!--')) \
            else (126, 206, 255) if st.startswith(('def ', 'class ', 'function ', 'const ',
                                                   'import ', 'from ', 'export ', '<', '{', '[')) \
            else (214, 222, 235)
        d.text((8, y), s, fill=col, font=font)
        y += 11
        if y > W - 12:
            break
    return img


def _image(p: Path, size: int):
    from PIL import Image, ImageOps
    im = Image.open(p)
    try:
        im.draft('RGB', (size * 2, size * 2))
    except Exception:
        pass
    try:
        im.seek(0)
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if im.mode not in ('RGB', 'RGBA'):
        im = im.convert('RGBA' if 'A' in im.getbands() or im.mode == 'P' else 'RGB')
    return im


def thumbnail(root_id: str, rel: str, size: int = 128):
    """(webp bytes, etag) for a file's thumbnail, or None when the type has
    none. Cached under Friday's home keyed by path, mtime, size and version."""
    p = resolve(root_id, rel)
    if not p.is_file():
        return None
    size = min(THUMB_SIZES, key=lambda s: abs(s - int(size or 128)))
    ext = p.suffix.lower().lstrip('.')
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS and ext not in TEXT_EXTS:
        return None
    st = p.stat()
    key = hashlib.sha1(f'{p}|{st.st_mtime_ns}|{st.st_size}|{size}|{THUMB_VERSION}'
                       .encode('utf-8', 'surrogatepass')).hexdigest()
    d = _cache_dir()
    fp = d / key[:2] / f'{key}.webp'
    if fp.exists():
        try:
            return fp.read_bytes(), key
        except OSError:
            pass
    try:
        if ext in IMAGE_EXTS:
            im = _image(p, size)
        elif ext in VIDEO_EXTS:
            im = _video_frame(p, size)
        else:
            im = _text_card(p, size)
    except Exception as e:
        _log.debug('thumbnail failed for %s: %s', p.name, e)
        im = None
    if im is None:
        return None
    from PIL import Image
    im.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'WEBP', quality=80, method=4)
    data = buf.getvalue()
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_suffix('.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, fp)
        _writes['n'] += 1
        if _writes['n'] % 500 == 0:
            threading.Thread(target=_prune_cache, args=(d,), daemon=True).start()
    except OSError as e:
        _log.debug('thumb cache write failed: %s', e)
    return data, key


# ── preview, open, reveal ──────────────────────────────────────────────────

def serve_type(p: Path) -> tuple:
    """(mimetype, inline_ok). Non-media types are served as text/plain or
    octet-stream so the browser never renders them as a document."""
    ext = p.suffix.lower().lstrip('.')
    if ext in _INLINE_TYPES:
        return _INLINE_TYPES[ext], True
    if ext in TEXT_EXTS:
        return 'text/plain; charset=utf-8', False
    return 'application/octet-stream', False


def text_preview(root_id: str, rel: str) -> dict:
    p = resolve(root_id, rel)
    if not p.is_file():
        raise Denied('not a file')
    with open(p, 'rb') as f:
        raw = f.read(TEXT_PREVIEW_BYTES + 1)
    if b'\x00' in raw[:2048]:
        return {'binary': True, 'text': '', 'truncated': False}
    truncated = len(raw) > TEXT_PREVIEW_BYTES
    return {'binary': False, 'truncated': truncated,
            'text': raw[:TEXT_PREVIEW_BYTES].decode('utf-8', errors='replace')}


def open_default(root_id: str, rel: str) -> dict:
    p = resolve(root_id, rel)
    if p.is_file() and p.suffix.lower().lstrip('.') not in OPENABLE_EXTS:
        raise Denied(f'Friday does not launch {p.suffix or "this"} files; use Reveal instead')
    if sys.platform == 'win32':
        os.startfile(str(p))  # noqa: S606 - type allow-listed above
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(p)])
    else:
        subprocess.Popen(['xdg-open', str(p)])
    return {'opened': p.name}


def reveal(root_id: str, rel: str) -> dict:
    p = resolve(root_id, rel)
    if sys.platform == 'win32':
        subprocess.Popen(['explorer', f'/select,{p}'])
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', '-R', str(p)])
    else:
        subprocess.Popen(['xdg-open', str(p.parent)])
    return {'revealed': p.name}


# ── approval-gated changes ─────────────────────────────────────────────────

_VERBS = {'delete': 'Move to Recycle Bin', 'rename': 'Rename', 'move': 'Move',
          'copy': 'Copy'}


def _copy_target(src: Path, dest: Path) -> Path:
    """Where a copy of src lands in dest: its own name, or "name (copy).ext",
    "name (copy 2).ext" ... when that is taken. Never an existing file."""
    target = dest / src.name
    n = 1
    while target.exists():
        tag = ' (copy)' if n == 1 else f' (copy {n})'
        target = dest / f'{src.stem}{tag}{src.suffix}'
        n += 1
    return target


def _valid_new_name(name: str) -> str:
    name = (name or '').strip()
    if not name or name in ('.', '..') or any(c in name for c in '\\/:*?"<>|'):
        raise Denied('that is not a valid file name')
    if _hidden_name(name, is_dir=False):
        raise Denied('that name would hide the file')
    return name


def request_change(op: str, root_id: str, rel: str, *, new_name: str = '',
                   dest_root: str = '', dest_rel: str = '') -> dict:
    """File an approval card for a delete / rename / move / copy. Nothing is touched
    until the owner approves it in the Approvals queue."""
    if op not in _VERBS:
        raise Denied('unknown operation')
    src = resolve(root_id, rel)
    if not rel.strip('/'):
        raise Denied('a whole top-level folder cannot be changed from here')
    payload = {'handler': 'studio_files', 'op': op, 'root': root_id,
               'path': rel.strip('/')}
    label = ROOT_LABELS.get(root_id, root_id)
    if op == 'rename':
        payload['new_name'] = _valid_new_name(new_name)
        if (src.parent / payload['new_name']).exists():
            raise Denied('a file with that name already exists')
        what = f'Rename "{src.name}" to "{payload["new_name"]}" in {label}'
    elif op in ('move', 'copy'):
        dest = resolve(dest_root, dest_rel)
        if not dest.is_dir():
            raise Denied('destination is not a folder')
        if op == 'move' and (dest / src.name).exists():
            raise Denied('the destination already has a file with that name')
        if op == 'copy' and not src.is_file():
            raise Denied('only files can be copied from here')
        payload.update(dest_root=dest_root, dest_path=(dest_rel or '').strip('/'))
        what = (f'{_VERBS[op]} "{src.name}" from {label} to '
                f'{ROOT_LABELS.get(dest_root, dest_root)}/{payload["dest_path"]}')
    else:
        what = f'Delete "{src.name}" from {label} (it goes to the Recycle Bin)'
    from agent_friday.services import approvals
    out = approvals.gate_action(
        kind=APPROVAL_KIND, subject_type='studio_file',
        subject_id=f'{op}:{root_id}:{payload["path"]}:{uuid.uuid4().hex[:8]}',
        title=f'{_VERBS[op]}: {src.name}', action_description=what,
        description=what + '. Requested from the Studio file browser.',
        force_gate=True, payload=payload, requested_by='owner:studio_files')
    return out


def _recycle(p: Path) -> None:
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT),
                        ('pFrom', wintypes.LPCWSTR), ('pTo', wintypes.LPCWSTR),
                        ('fFlags', ctypes.c_ushort), ('fAnyOperationsAborted', wintypes.BOOL),
                        ('hNameMappings', ctypes.c_void_p),
                        ('lpszProgressTitle', wintypes.LPCWSTR)]
        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 0x10, 0x40, 0x400
        op = SHFILEOPSTRUCTW(None, FO_DELETE, str(p) + '\0', None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                             False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted:
            raise OSError(f'Recycle Bin refused the file (code {rc})')
        return
    try:
        from send2trash import send2trash
    except ImportError:
        raise OSError('no Recycle Bin available; Friday does not delete permanently')
    send2trash(str(p))


def apply_change(payload: dict) -> dict:
    """Carry out an APPROVED change. Paths are re-checked now: an approval
    authorises the action on a file that is still browsable, not a path."""
    op = payload.get('op')
    src = resolve(payload.get('root', ''), payload.get('path', ''))
    if op == 'delete':
        _recycle(src)
        return {'op': op, 'recycled': src.name}
    if op == 'rename':
        new = src.parent / _valid_new_name(payload.get('new_name', ''))
        if new.exists():
            raise OSError('a file with that name already exists')
        src.rename(new)
        return {'op': op, 'renamed_to': new.name}
    if op == 'move':
        dest = resolve(payload.get('dest_root', ''), payload.get('dest_path', ''))
        target = dest / src.name
        if target.exists():
            raise OSError('the destination already has a file with that name')
        shutil.move(str(src), str(target))
        return {'op': op, 'moved_to': payload.get('dest_path', '')}
    if op == 'copy':
        dest = resolve(payload.get('dest_root', ''), payload.get('dest_path', ''))
        if not src.is_file():
            raise OSError('only files can be copied')
        target = _copy_target(src, dest)
        shutil.copy2(str(src), str(target))
        return {'op': op, 'copied_to': payload.get('dest_path', ''), 'name': target.name}
    raise Denied('unknown operation')


def _on_decision(record: dict) -> None:
    payload = record.get('payload') or {}
    if payload.get('handler') != 'studio_files':
        return
    if (record.get('status') or '').lower() != 'approved' or record.get('consumed'):
        return
    from agent_friday.services import approvals
    try:
        detail = apply_change(payload)
        approvals.mark_used(record['approval_id'], 'studio_files', {'ok': True, **detail})
    except Exception as e:
        _log.warning('approved studio file change failed: %s', e)
        approvals.mark_used(record['approval_id'], 'studio_files',
                            {'ok': False, 'error': str(e)[:300]})
        try:
            import agent_friday.notifications_engine as ne
            ne.push(title='File change did not happen', body=str(e)[:300],
                    priority='high', kind='warning', source='studio_files')
        except Exception:
            pass


_HOOKS_REGISTERED = False


def register_hooks() -> None:
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    try:
        from agent_friday.services import approvals
        approvals.register_decision_hook(APPROVAL_KIND, _on_decision)
        _HOOKS_REGISTERED = True
    except Exception as e:
        _log.warning('could not register the studio file hook: %s', e)


register_hooks()
