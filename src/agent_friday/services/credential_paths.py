"""Files Friday's own tools refuse to read, however she is asked.

A deny-list for the MODEL's file reach, not the user's. The user opens their
own SSH key, keystore or start.bat whenever they like; Friday does not, so a
prompt-injected "archive everything and send it" cannot turn her into the
courier for key material — the Meta Muse incident, where an agent asked to
"archive everything you can see" zipped its whole runtime, SSH keys included,
and shipped it to a connected Drive.

It holds even when the OWNER asks Friday directly. "Read my SSH key and paste
it here" is the same keystroke to an injected page as to the owner, and the
safe answer to both is "open it yourself". So the refusal is not a silent
nothing and not a nag: Friday says plainly what she will not read, names the
file's folder, and leaves opening it to the person.

Consulted by read_file, search_files, open_path and run_command's read verbs
(services/agent.py, services/file_search.py). Never consulted for a path the
user opens themselves through the UI — this only bounds what the model reaches.

Deliberately NOT the whole vault: the vault holds the owner's own notes
(finances, legal, family) that Friday works with under the egress gate. Only
the vault's KEY material is denied (.vault_config.json salt, .governance-key,
the attestation signing key), never the owner's content.
"""
from __future__ import annotations

import fnmatch
import functools
import os
import re
from pathlib import Path


def _home() -> Path:
    from agent_friday.core import HOME
    return Path(HOME)


def _friday() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR)


def _deny_dirs() -> list[tuple[Path, str]]:
    """Directories whose every file is a private key or a live credential."""
    h, f = _home(), _friday()
    return [
        (h / ".ssh", "an SSH key directory"),
        (h / ".aws", "your AWS credentials"),
        (h / ".gnupg", "your GnuPG keyring"),
        (h / ".config" / "gcloud", "your Google Cloud credentials"),
        (h / ".config" / "gh", "your GitHub CLI sign-in"),
        (h / ".azure", "your Azure credentials"),
        (h / ".kube", "your Kubernetes credentials"),
        (f / "backups", "a backup copy of Friday's vault and keys"),
        (f / "security", "Friday's keystore and passphrase store"),
        (f / "providers" / "keys", "your stored provider API keys"),
        (f / "google_accounts" / "tokens", "your Google account tokens"),
        (f / "mcp_oauth", "your connector sign-in tokens"),
        (f / "phone" / "secrets", "your phone-service secrets"),
    ]


def _deny_files() -> list[tuple[Path, str]]:
    """Exact files that are key material rather than the owner's content."""
    f = _friday()
    return [
        (f / "secret_key", "Friday's web session secret"),
        (f / "vault" / ".vault_config.json", "the vault key-derivation salt"),
        (f / "vault" / ".governance-key", "the governance signing key"),
    ]


#: Basename globs that are credential material wherever they sit. Matched on
#: the lower-cased name with any NTFS stream suffix and trailing dots removed.
_NAME_GLOBS: tuple[tuple[str, str], ...] = (
    ("id_rsa*", "an SSH private key"),
    ("id_ed25519*", "an SSH private key"),
    ("id_ecdsa*", "an SSH private key"),
    ("id_dsa*", "an SSH private key"),
    ("*.ppk", "a PuTTY private key"),
    ("*.p12", "a certificate-and-key bundle"),
    ("*.pfx", "a certificate-and-key bundle"),
    ("*.jks", "a Java keystore"),
    ("*.keystore", "a Java keystore"),
    ("*.kdbx", "a password-manager database"),
    (".netrc", "a stored network login"),
    ("_netrc", "a stored network login"),
    (".git-credentials", "stored Git logins"),
    (".pgpass", "stored database passwords"),
    (".pypirc", "stored package-index logins"),
    ("*.dpapi", "a Windows-protected secret"),
    ("*.token.enc", "an encrypted account token"),
    ("*.oauth.enc", "an encrypted connector token"),
    (".attestation-key-*", "the vault attestation signing key"),
    (".governance-key", "the governance signing key"),
    (".vault_config.json", "the vault key-derivation salt"),
    ("keystore.json", "Friday's keystore"),
    ("start.bat", "a launcher that holds your API keys in plain text"),
    ("launch_now.bat", "a launcher that holds your API keys in plain text"),
    ("friday_startup.*", "a launcher that holds your API keys in plain text"),
)

#: Names that are key material only when the file is one: a certificate .pem
#: is public and stays readable; a .pem or .key that holds (or may hold, when
#: it cannot be read) a private key is denied.
_CONTENT_GATED: tuple[tuple[str, str], ...] = (
    ("*.pem", "a private key or certificate"),
    ("*.key", "a private key"),
)

#: Files no larger than this are sniffed for a private-key header.
_SNIFF_MAX_BYTES = 64 * 1024
_SNIFF_HEAD_BYTES = 16 * 1024

#: Browser saved-login / cookie stores, denied only alongside a browser-dir
#: word so a user's own file merely named "cookies.txt" is not caught.
_BROWSER_STORE_NAMES = ("login data", "cookies", "web data", "local state",
                        "key4.db", "logins.json", "signons.sqlite")
_BROWSER_DIR_WORDS = ("chrome", "chromium", "edge", "brave", "vivaldi", "opera",
                      "mozilla", "firefox", "user data")


def _norm(p: Path) -> str:
    try:
        return os.path.normcase(str(p.resolve()))
    except Exception:
        return os.path.normcase(str(p))


def _asis(p: Path) -> str:
    """`p` as written, made absolute, without touching the filesystem."""
    return os.path.normcase(os.path.abspath(str(p)))


@functools.lru_cache(maxsize=8)
def _deny_norms(home: str, friday: str):
    """Normalised deny directories and files, as written and as they resolve.

    Cached per (home, Friday dir) so judging one more file costs string
    comparisons, not a fresh round of path resolution.
    """
    dirs: dict[str, str] = {}
    files: dict[str, str] = {}
    for d, why in _deny_dirs():
        dirs.setdefault(_asis(d), why)
        dirs.setdefault(_norm(d), why)
    for f, why in _deny_files():
        files.setdefault(_asis(f), why)
        files.setdefault(_norm(f), why)
    return tuple(dirs.items()), tuple(files.items())


def _plain_name(name: str) -> str:
    """A basename with any NTFS stream suffix and trailing dots/spaces removed."""
    return name.split(":", 1)[0].rstrip(". ").lower()


def _holds_private_key(p: Path) -> bool | None:
    """True when a small existing file starts with private-key material,
    False when it does not, None when it cannot be read or is not small."""
    try:
        if not p.is_file():
            return None
        if p.stat().st_size > _SNIFF_MAX_BYTES:
            return None
        with open(p, "rb") as fh:
            head = fh.read(_SNIFF_HEAD_BYTES)
    except Exception:
        return None
    from agent_friday.services import secret_patterns
    return secret_patterns.contains_key_material(head.decode("latin-1"))


def _expand_path_text(path) -> str:
    """`path` as text with %VAR%, $VAR, $env:VAR and ~ expanded."""
    t = str(path).strip().strip('"').strip("'")
    t = re.sub(r"\$env:(\w+)", lambda m: "%" + m.group(1) + "%", t, flags=re.I)
    return os.path.expandvars(os.path.expanduser(t))


_EXTENDED_DRIVE = re.compile(r"^\\\\[?.]\\[A-Za-z]:(?:\\|$)")
_EXTENDED_UNC = re.compile(r"^\\\\[?.]\\UNC\\", re.I)
_ADMIN_SHARE = re.compile(r"^\\\\[^\\]+\\([A-Za-z])\$(?:\\|$)")


def _canonical_windows_spelling(text: str) -> tuple[str, bool]:
    r"""`text` with the extended-length and admin-share spellings of a local
    drive path rewritten to the plain `X:\...` form the deny rules are written
    in, and whether it is a device path that names no ordinary file.

    `\\?\C:\x`, `\\.\C:\x`, `\\?\UNC\host\C$\x` and `\\host\C$\x` all reach
    the same file as `C:\x`; a comparison that does not fold them together
    lets a directory rule be walked around by spelling alone. Any other
    `\\?\` or `\\.\` target (a volume GUID, GLOBALROOT, a device) is refused:
    Friday never needs one, and none can be judged by name.
    """
    t = text.replace("/", "\\")
    if not t.startswith("\\\\"):
        return text, False
    if _EXTENDED_DRIVE.match(t):
        return t[4:], False
    unc = _EXTENDED_UNC.match(t)
    if unc:
        t = "\\\\" + t[unc.end():]
    elif t[2:3] in ("?", "."):
        return text, True
    share = _ADMIN_SHARE.match(t)
    if share:
        return share.group(1) + ":\\" + t[share.end():], False
    return text, False


def check(path, *, sniff: bool = True) -> str | None:
    """A short reason `path` is off-limits to Friday's tools, or None.

    Names the KIND of secret, for a message the owner can act on. Works on a
    path that does not exist yet (open_path targets), so it never depends on
    the file being present. The path is judged as written AND as it resolves
    (symlink, junction, 8.3 short name, relative traversal), and with `sniff`
    an existing small file is judged by what it holds, so a key copied,
    hard-linked or renamed to anything is still a key. A bulk walk passes
    `sniff=False` and judges only by name and place; the content of what it
    then opens is checked where it is read.
    """
    try:
        text, device = _canonical_windows_spelling(_expand_path_text(path))
        if device:
            return "a device path"
        p = Path(text)
    except Exception:
        return None
    try:
        rp = p.resolve()
    except Exception:
        rp = p
    dirs, files = _deny_norms(str(_home()), str(_friday()))
    for cand in dict.fromkeys((_asis(p), os.path.normcase(str(rp)))):
        for d, why in dirs:
            if cand == d or cand.startswith(d + os.sep):
                return why
        for f, why in files:
            if cand == f:
                return why
    names = {_plain_name(p.name), _plain_name(rp.name)}
    for name in names:
        if name.endswith(".pub"):
            continue
        for pat, why in _NAME_GLOBS:
            if fnmatch.fnmatch(name, pat):
                return why
    for name in names:
        for pat, why in _CONTENT_GATED:
            if fnmatch.fnmatch(name, pat):
                if _holds_private_key(rp) is False:
                    break
                return why
    if sniff and _holds_private_key(rp):
        return "a private key"
    full = os.path.normcase(str(rp)).replace("\\", "/")
    if any(b in full for b in _BROWSER_DIR_WORDS) and             any(s in full for s in _BROWSER_STORE_NAMES):
        return "a browser's saved-login or cookie store"
    return None


#: High-signal substrings for a command that builds a credential path from a
#: variable (`$env:USERPROFILE\.ssh\...`), where token resolution alone would
#: miss it. Matched against the command with slashes normalised to '/', quoting,
#: backticks, carets, '+' and parentheses removed (so `'.s'+'sh'` reads `.ssh`).
_COMMAND_MARKERS: tuple[tuple[str, str], ...] = (
    ("id_rsa", "an SSH private key"),
    ("id_ed25519", "an SSH private key"),
    ("id_ecdsa", "an SSH private key"),
    ("id_dsa", "an SSH private key"),
    (".friday/security", "Friday's keystore"),
    (".friday/backups", "a backup copy of Friday's vault and keys"),
    ("vault-reencrypt", "a backup copy of Friday's vault and keys"),
    ("providers/keys", "your stored provider API keys"),
    ("google_accounts/tokens", "your Google account tokens"),
    (".friday/mcp_oauth", "your connector sign-in tokens"),
    ("phone/secrets", "your phone-service secrets"),
    (".governance-key", "the governance signing key"),
    (".vault_config.json", "the vault key-derivation salt"),
    (".attestation-key", "the vault attestation signing key"),
    ("keystore.json", "Friday's keystore"),
    ("/secret_key", "Friday's web session secret"),
    (".token.enc", "an encrypted account token"),
    (".oauth.enc", "an encrypted connector token"),
    (".dpapi", "a Windows-protected secret"),
    (".netrc", "a stored network login"),
    (".git-credentials", "stored Git logins"),
    (".pgpass", "stored database passwords"),
    (".pypirc", "stored package-index logins"),
    (".kdbx", "a password-manager database"),
    (".p12", "a certificate-and-key bundle"),
    (".pfx", "a certificate-and-key bundle"),
    (".jks", "a Java keystore"),
    ("config/gh/hosts", "your GitHub CLI sign-in"),
    ("start.bat", "a launcher that holds your API keys in plain text"),
    ("launch_now.bat", "a launcher that holds your API keys in plain text"),
    ("friday_startup", "a launcher that holds your API keys in plain text"),
)

#: A credential directory named as a path segment (`~/.ssh`, `cd .ssh`), not as
#: the middle of a host name (`console.aws.amazon.com`).
_COMMAND_DIR_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])\.(ssh|aws|gnupg|azure|kube)(?![A-Za-z0-9_-])")
_DIR_WHY = {"ssh": "an SSH key directory", "aws": "your AWS credentials",
            "gnupg": "your GnuPG keyring", "azure": "your Azure credentials",
            "kube": "your Kubernetes credentials"}

_ENCODED_RE = re.compile(r"(?i)\s-e(?:nc(?:odedcommand)?|c)?\s+([A-Za-z0-9+/]{16,}={0,2})")
_B64_LITERAL_RE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
_MAX_DEPTH = 3
_MAX_GLOB_HITS = 200


def _expand(token: str) -> str:
    return _expand_path_text(token)


def _decoded_command_texts(cmd: str):
    """Texts hidden in base64 inside a command (-EncodedCommand, FromBase64String)."""
    import base64
    seen = 0
    for m in list(_ENCODED_RE.finditer(cmd)) + list(_B64_LITERAL_RE.finditer(cmd)):
        if seen >= 16:
            return
        raw = m.group(1) if m.re is _ENCODED_RE else m.group(0)
        pad = raw + "=" * (-len(raw) % 4)
        try:
            blob = base64.b64decode(pad, validate=True)
        except Exception:
            continue
        for enc in ("utf-16-le", "utf-8"):
            try:
                text = blob.decode(enc)
            except Exception:
                continue
            if text and text.isprintable() or "\n" in text:
                seen += 1
                yield text


def _tokens(cmd: str):
    for m in re.finditer(r"""\"([^\"]+)\"|'([^']+)'|(\S+)""", cmd):
        yield next(g for g in m.groups() if g)


#: A command that can carry file content somewhere: read, copy, archive,
#: filter, upload, or feed a listing to another command.
_READER_RE = re.compile(
    r"(?<![a-z0-9_-])(?:cat|type|gc|get-content|more|less|head|tail|sls|"
    r"select-string|findstr|grep|rg|copy|cp|xcopy|robocopy|copy-item|tar|zip|"
    r"compress-archive|certutil|xargs|foreach(?:-object)?|iwr|irm|curl|wget|"
    r"invoke-webrequest|invoke-restmethod|readall\w+|base64|xxd|od|format-hex|"
    r"scp|sftp|ftp|rsync)(?![a-z0-9_-])")
_RECURSE_RE = re.compile(r"(?<![a-z0-9_-])(?:-recurse|-r|/s|-recursive|-R)(?![a-z0-9_-])",
                         re.I)
_MAX_DIR_ENTRIES = 300


def _directory_holds_credential(path: str, recurse: bool) -> str | None:
    """Why a directory named next to a reading verb holds a denied file, or None.

    Closes `Get-ChildItem <dir> -Filter x* | Get-Content`, where no token names
    the key itself. Bounded, and shallow unless the command recurses.
    """
    try:
        root = Path(path)
        if not root.is_dir():
            return None
        seen = 0
        walker = os.walk(root) if recurse else [(str(root), [], [
            e.name for e in os.scandir(root) if e.is_file(follow_symlinks=False)])]
        for dp, _dirs, names in walker:
            for name in names:
                seen += 1
                if seen > _MAX_DIR_ENTRIES:
                    return None
                why = check(Path(dp) / name)
                if why:
                    return why
    except Exception:
        return None
    return None


def scan_code(code: str) -> str | None:
    """A reason program text (the sandbox tool's Python) reads a credential path.

    Everything `scan_command` catches, plus the markers again once ALL
    whitespace is dropped, so `".s" "sh"` (adjacent string literals) reads
    `.ssh`. Output is redacted separately, so a route this misses still does
    not return key material.
    """
    why = scan_command(code)
    if why:
        return why
    squeezed = re.sub(r"[\s\"'`^+()\[\],]", "", code or "").replace("\\", "/").lower()
    for marker, reason in _COMMAND_MARKERS:
        if marker in squeezed:
            return reason
    m = _COMMAND_DIR_RE.search(squeezed)
    if m:
        return _DIR_WHY[m.group(1)]
    return None


def scan_command(cmd: str, _depth: int = 0) -> str | None:
    """A reason a shell command reads a credential path, or None.

    Best-effort and fail-toward-refusing: a marker substring in the command
    (after quoting tricks are stripped), a credential directory named as a
    path segment, a browser store named next to a browser directory, a
    base64-encoded command that does any of those, or any path-shaped token or
    wildcard expansion that `check` denies. The output of whatever does run is
    redacted separately (`redact_secrets`), so a route this misses still does
    not return key material.
    """
    if not cmd:
        return None
    flat = re.sub(r"[\"'`^+()]", "", cmd).replace("\\", "/").lower()
    for marker, why in _COMMAND_MARKERS:
        if marker in flat:
            return why
    m = _COMMAND_DIR_RE.search(flat)
    if m:
        return _DIR_WHY[m.group(1)]
    if any(b in flat for b in _BROWSER_DIR_WORDS) and \
            any(s in flat for s in _BROWSER_STORE_NAMES):
        return "a browser's saved-login or cookie store"
    reads = bool(_READER_RE.search(flat))
    recurse = bool(_RECURSE_RE.search(flat))
    for tok in _tokens(re.sub(r"[`^]", "", cmd)):
        tok = tok.strip(",;|()")
        if len(tok) < 3:
            continue
        if "=" in tok and not tok.startswith(("/", "~", "$", "%")) and tok[1:2] != ":":
            tok = tok.split("=", 1)[1]
        if ("/" in tok or "\\" in tok or tok.startswith("~")
                or tok.startswith("$env:") or tok.startswith("%")
                or (len(tok) > 1 and tok[1] == ":")):
            expanded = _expand(tok)
            why = check(Path(expanded))
            if why:
                return why
            if reads and not any(ch in expanded for ch in "*?["):
                why = _directory_holds_credential(expanded, recurse)
                if why:
                    return why
            if any(ch in expanded for ch in "*?["):
                import glob
                try:
                    for i, hit in enumerate(glob.iglob(expanded)):
                        if i >= _MAX_GLOB_HITS:
                            break
                        why = check(Path(hit))
                        if why:
                            return why
                except Exception:
                    pass
    if _depth < _MAX_DEPTH:
        for text in _decoded_command_texts(cmd):
            why = scan_command(text, _depth + 1)
            if why:
                return why
    return None


def redact_secrets(text: str) -> str:
    """`text` with private-key blocks and vendor-format tokens withheld.

    The last line behind the deny-list: whatever a tool returns, key material
    that slipped past the path checks (a key pasted inside a larger file, a
    command that listed one) does not reach the model.
    """
    from agent_friday.services import secret_patterns
    return secret_patterns.redact(text)


def refusal(path) -> str:
    """The plain line Friday says instead of reading `path`."""
    p = Path(path).expanduser()
    why = check(p) or "a credential or key file"
    return (f"I won't open {p.name} — it's {why}, and I don't read key material "
            f"even when asked, so nothing slipped into a page, email or document "
            f"can make me copy it out. You can open it yourself; it's in "
            f"{p.parent}.")


def refusal_command(why: str) -> str:
    """The plain line Friday says instead of running a credential-reading command."""
    return (f"I won't run that — it reads {why}, and I don't read key material "
            f"even when asked, so nothing slipped into a page, email or document "
            f"can make me copy it out. Open the file yourself if you need it.")
