"""Files Friday's own tools refuse to read, however she is asked.

A deny-list for the MODEL's file reach, not the user's. The user opens their
own SSH key, keystore or start.bat whenever they like; Friday does not, so a
prompt-injected "archive everything and send it" cannot turn her into the
courier for key material: an agent told to "archive everything you can see"
must not zip the runtime, SSH keys included, and ship it to a connected drive.

It holds even when the OWNER asks Friday directly. "Read my SSH key and paste
it here" is the same keystroke to an injected page as to the owner, and the
safe answer to both is "open it yourself". So the refusal is not a silent
nothing and not a nag: Friday says plainly what she will not read, names the
file's folder, and leaves opening it to the person.

Consulted by read_file, search_files, open_path and run_command's read verbs
(services/agent.py, services/file_search.py). Never consulted for a path the
user opens themselves through the UI — this only bounds what the model reaches.

A denied folder still gives up its lookups, which are not secrets: `~/.ssh/config`,
`known_hosts`, public keys, `~/.aws/config` and `~/.config/gh/config.yml` open
while they hold no credential (`_LOOKUPS`). A recursive read that starts above a
credential folder (`$HOME`, the current directory, an archive of the profile) is
refused as a walk, because it reaches the keys without naming one.

Deliberately NOT the whole vault: the vault holds the owner's own notes
(finances, legal, family) that Friday works with under the egress gate. Only
the vault's KEY material is denied (.vault_config.json salt, .governance-key,
the attestation signing key), never the owner's content.
"""
from __future__ import annotations

import fnmatch
import contextvars
import functools
import os
import re
from pathlib import Path

#: Set when a refusal line has been issued in this context. The tool runner
#: clears it before a handler runs and reads it after, so a refusal raised by
#: any layer is receipted as a denial and never as a successful read.
REFUSED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "credential_refusal_issued", default=False)


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
        (h / ".ssh", "your SSH keys folder"),
        (h / ".aws", "your AWS credentials folder"),
        (h / ".gnupg", "your GnuPG keyring folder"),
        (h / ".config" / "gcloud", "your Google Cloud credentials folder"),
        (h / ".config" / "gh", "your GitHub CLI sign-in folder"),
        (h / ".azure", "your Azure credentials folder"),
        (h / ".kube", "your Kubernetes credentials folder"),
        (f / "backups", "my backup folder, which holds copies of my vault and keys"),
        (f / "security", "my keystore folder"),
        (f / "providers" / "keys", "your provider API keys folder"),
        (f / "google_accounts" / "tokens", "your Google account tokens folder"),
        (f / "mcp_oauth", "your connector sign-in tokens folder"),
        (f / "phone" / "secrets", "your phone-service secrets folder"),
    ]


def _app_dir() -> Path:
    """The folder Friday is installed in: where an older release kept its API
    keys in `.env` and `secrets.yaml`, and where the installer carries them
    across an upgrade."""
    return Path(__file__).resolve().parents[3]


def _deny_files() -> list[tuple[Path, str]]:
    """Exact files that are key material rather than the owner's content."""
    f, a = _friday(), _app_dir()
    return [
        (f / "secret_key", "my web session secret"),
        (f / "vault" / ".vault_config.json", "the vault key-derivation salt"),
        (f / "vault" / ".governance-key", "the governance signing key"),
        (a / ".env", "the API keys an earlier release kept beside the app"),
        (a / "secrets.yaml", "the API keys an earlier release kept beside the app"),
    ]


#: Files inside a denied folder that are the owner's lookups rather than
#: secrets: where a host lives, who it trusts, which region a profile uses.
#: Keyed by the folder's path under the home directory. Each is readable only
#: while its content holds no credential, so a config that gained a secret
#: is refused for what it holds.
_LOOKUPS: dict[tuple[str, ...], tuple[str, ...]] = {
    (".ssh",): ("config", "known_hosts", "known_hosts.old", "authorized_keys", "*.pub"),
    (".aws",): ("config",),
    (".config", "gh"): ("config.yml",),
}
_LOOKUP_MAX_BYTES = 256 * 1024

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
    ("keystore.json", "my keystore"),
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


def _norms():
    return _deny_norms(str(_home()), str(_friday()), str(_app_dir()))


@functools.lru_cache(maxsize=8)
def _deny_norms(home: str, friday: str, app: str = ""):
    """Normalised deny directories and files, as written and as they resolve.

    Cached per (home, Friday dir, app dir) so judging one more file costs
    string comparisons, not a fresh round of path resolution.
    """
    dirs: dict[str, str] = {}
    files: dict[str, str] = {}
    lookups: dict[str, tuple[str, ...]] = {}
    for d, why in _deny_dirs():
        dirs.setdefault(_asis(d), why)
        dirs.setdefault(_norm(d), why)
    for f, why in _deny_files():
        files.setdefault(_asis(f), why)
        files.setdefault(_norm(f), why)
    for parts, names in _LOOKUPS.items():
        d = _home().joinpath(*parts)
        lookups.setdefault(_asis(d), names)
        lookups.setdefault(_norm(d), names)
    return tuple(dirs.items()), tuple(files.items()), lookups


def _is_clean_lookup(folder: str, cand: str, lookups: dict) -> bool:
    """True when `cand` is one of the owner's lookup files directly inside the
    denied `folder` and holds no credential (an absent file holds none)."""
    names = lookups.get(folder)
    rest = cand[len(folder) + 1:]
    if not names or os.sep in rest or not any(fnmatch.fnmatch(rest, n) for n in names):
        return False
    try:
        path = Path(cand)
        if not path.exists():
            return True
        if not path.is_file() or path.stat().st_size > _LOOKUP_MAX_BYTES:
            return False
        with open(path, "rb") as fh:
            text = fh.read(_LOOKUP_MAX_BYTES).decode("latin-1")
    except Exception:
        return False
    from agent_friday.services import secret_patterns
    return not secret_patterns.contains_secret(text)


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

_DEVICE = "a raw device path"
_SHARE = "this PC's own disks, reached through a network share"


@functools.lru_cache(maxsize=1)
def _own_names() -> tuple[frozenset, frozenset]:
    """(host names, IP addresses) that mean this machine, lower-cased."""
    import socket
    names = {"localhost", "localhost.localdomain"}
    ips: set[str] = {"127.0.0.1", "::1"}
    for n in (os.environ.get("COMPUTERNAME"), os.environ.get("USERDNSDOMAIN")):
        if n:
            names.add(n.lower())
    try:
        host = socket.gethostname()
        names.update({host.lower(), host.split(".")[0].lower(),
                      socket.getfqdn().lower()})
        ips.update(socket.gethostbyname_ex(host)[2])
        ips.update(ai[4][0] for ai in socket.getaddrinfo(host, None))
    except Exception:
        pass
    return frozenset(names), frozenset(i.split("%")[0] for i in ips)


def _is_this_machine(host: str) -> bool:
    """True when a UNC host names this PC: localhost, a loopback or own address
    in any spelling (short, decimal, hex, IPv6-literal), or its computer name."""
    import ipaddress
    import socket
    h = host.strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h.endswith(".ipv6-literal.net"):
        h = h[:-len(".ipv6-literal.net")].replace("-", ":").replace("s", "%")
    h = h.split("%")[0]
    names, ips = _own_names()
    if h in names or h in ips:
        return True
    addr = None
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        try:
            addr = ipaddress.ip_address(socket.inet_aton(h))
        except (OSError, ValueError):
            return False
    if addr.is_loopback or addr.is_unspecified:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped.is_loopback or str(addr.ipv4_mapped) in ips
    return str(addr) in ips


@functools.lru_cache(maxsize=1)
def _local_shares() -> dict[str, str]:
    r"""Lower-cased share name -> the local folder it exposes: the drive admin
    shares, ADMIN$, and every share the Server service lists."""
    import string
    table = {f"{c.lower()}$": f"{c}:\\" for c in string.ascii_uppercase}
    root = os.environ.get("SystemRoot")
    if root:
        table["admin$"] = root
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\LanmanServer\Shares")
        with key:
            i = 0
            while True:
                try:
                    name, value, _t = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                for entry in value if isinstance(value, list) else [str(value)]:
                    if entry.lower().startswith("path="):
                        table[name.lower()] = entry[5:]
    except Exception:
        pass
    return table


def _canonical_windows_spelling(text: str) -> tuple[str, str | None]:
    r"""`text` with the extended-length and same-machine share spellings of a
    local path rewritten to the plain `X:\...` form the deny rules are written
    in, and the reason it must be refused outright, when there is one.

    `\\?\C:\x`, `\\.\C:\x`, `\\?\UNC\host\C$\x`, `\\host\C$\x` and
    `\\localhost\Users\x` (any share of this PC) all reach the same file as
    `C:\x`; a comparison that does not fold them together lets a directory
    rule be walked around by spelling alone. A share of this PC that cannot
    be mapped to its folder, and any other `\\?\` or `\\.\` target (a volume
    GUID, GLOBALROOT, a device), is refused: Friday never needs one, and none
    can be judged by name. A share on another machine is not this disk.
    """
    t = text.replace("/", "\\")
    if not t.startswith("\\\\"):
        return text, None
    if _EXTENDED_DRIVE.match(t):
        return t[4:], None
    unc = _EXTENDED_UNC.match(t)
    if unc:
        t = "\\\\" + t[unc.end():]
    elif t[2:3] in ("?", "."):
        return text, _DEVICE
    host, _, rest = t[2:].partition("\\")
    if not host or not _is_this_machine(host):
        return text, None
    share, _, tail = rest.partition("\\")
    base = _local_shares().get(share.rstrip(". ").lower()) if share else None
    if not base:
        return text, _SHARE
    return base.rstrip("\\") + "\\" + tail, None


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
    hit = _verdict(path, sniff)
    return hit[0] if hit else None


def _verdict(path, sniff: bool = True) -> tuple[str, str] | None:
    """(reason, kind) when `path` is off-limits, else None.

    `kind` says what was matched, so the refusal can speak correctly:
    "device", "share", "folder" (the denied folder itself), "inside" (a
    file or folder under one), "file" (named or holding key material) and
    "unsure" (a .pem/.key that cannot be read, so cannot be told from a key).
    """
    try:
        text, problem = _canonical_windows_spelling(_expand_path_text(path))
        if problem:
            return problem, "device" if problem == _DEVICE else "share"
        p = Path(text)
    except Exception:
        return None
    try:
        rp = p.resolve()
    except Exception:
        rp = p
    # A link or junction may resolve to any spelling of a path; the deny rules
    # judge the target in the same plain form as the path as written.
    try:
        rtext, problem = _canonical_windows_spelling(str(rp))
        if problem:
            return problem, "device" if problem == _DEVICE else "share"
        if rtext != str(rp):
            rp = Path(rtext)
    except Exception:
        pass
    dirs, files, lookups = _norms()
    for cand in dict.fromkeys((_asis(p), os.path.normcase(str(rp)))):
        for d, why in dirs:
            if cand == d:
                return why, "folder"
            if cand.startswith(d + os.sep):
                if sniff and _is_clean_lookup(d, cand, lookups):
                    continue
                return why, "inside"
        for f, why in files:
            if cand == f:
                return why, "file"
    names = {_plain_name(p.name), _plain_name(rp.name)}
    for name in names:
        if name.endswith(".pub"):
            continue
        for pat, why in _NAME_GLOBS:
            if fnmatch.fnmatch(name, pat):
                return why, "file"
    for name in names:
        for pat, why in _CONTENT_GATED:
            if fnmatch.fnmatch(name, pat):
                held = _holds_private_key(rp)
                if held is False:
                    break
                if held:
                    return "a private key", "file"
                return why, "unsure"
    if sniff and _holds_private_key(rp):
        return "a private key", "file"
    full = os.path.normcase(str(rp)).replace("\\", "/")
    if any(b in full for b in _BROWSER_DIR_WORDS) and \
            any(s in full for s in _BROWSER_STORE_NAMES):
        return "a browser's saved-login or cookie store", "file"
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
    (".friday/security", "my keystore"),
    (".friday/backups", "a backup copy of my vault and keys"),
    ("vault-reencrypt", "a backup copy of my vault and keys"),
    ("providers/keys", "your stored provider API keys"),
    ("google_accounts/tokens", "your Google account tokens"),
    (".friday/mcp_oauth", "your connector sign-in tokens"),
    ("phone/secrets", "your phone-service secrets"),
    (".governance-key", "the governance signing key"),
    (".vault_config.json", "the vault key-derivation salt"),
    (".attestation-key", "the vault attestation signing key"),
    ("keystore.json", "my keystore"),
    ("/secret_key", "my web session secret"),
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
_DIR_WHY = {"ssh": "your SSH keys folder", "aws": "your AWS credentials folder",
            "gnupg": "your GnuPG keyring folder", "azure": "your Azure credentials folder",
            "kube": "your Kubernetes credentials folder"}

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


_TOKEN_RE = re.compile(r"""\"([^\"]+)\"|'([^']+)'|(\S+)""")
_SEGMENT_SPLIT = re.compile(r"([;|&\n])")


def _tokens(cmd: str):
    for m in _TOKEN_RE.finditer(cmd):
        yield next(g for g in m.groups() if g)


_HOME_SPELLINGS = ("~", "~/", "~\\")
_VERB_PREFIXES = {"sudo", "env", "command", "time", "nohup", "exec", "call", "start"}
#: Commands that USE a key named on their command line: the key authenticates
#: a connection and its content is never printed.
_SSH_VERBS = {"ssh", "scp", "sftp"}
_KUBE_VERBS = {"kubectl", "helm", "k9s", "kubectx", "kubens"}
#: Commands whose every folder argument is a source or a place to list.
_ARCHIVE_VERBS = {"tar", "zip", "7z", "7za", "rar", "compress-archive"}
#: Words that put the folder after them in the walk-root position.
_ROOT_LEADS = {"get-childitem", "gci", "ls", "dir", "find", "tree", "robocopy",
               "xcopy", "rsync", "-path", "-literalpath", "-lp"}


#: Commands that move the shell's current directory.
_CD_VERBS = {"cd", "chdir", "set-location", "sl", "pushd", "push-location"}
#: Commands that list names and nothing else, when run alone without recursion.
_LISTERS = {"ls", "dir", "gci", "get-childitem"}
#: Characters that make a command more than one plain listing: another command,
#: a redirect, a subexpression or a script block.
_NOT_A_PLAIN_LISTING = re.compile(r"[;&|<>(){}`\n]|\$\(|\$\{")


def _verb_index(tokens: list[str]) -> int:
    """Where the command a segment runs sits: its first word, past sudo-style
    prefixes. -1 when there is none."""
    for i, t in enumerate(tokens):
        w = t.strip("&.(").lower()
        if not w or w in _VERB_PREFIXES or ("=" in w and not w.startswith("-")):
            continue
        return i
    return -1


def _verb(tokens: list[str]) -> str:
    """The command a segment runs: its first word, past sudo-style prefixes."""
    i = _verb_index(tokens)
    if i < 0:
        return ""
    base = re.split(r"[\\/]", tokens[i].strip("&.(").lower())[-1]
    return base[:-4] if base.endswith(".exe") else base


def _prints_kubeconfig(tokens: list[str]) -> bool:
    """True for `kubectl config view --raw` (or --flatten): it prints the
    certificates and tokens the kubeconfig holds, so it reads the credential
    whether or not it names the file."""
    if _verb(tokens) not in _KUBE_VERBS:
        return False
    low = [t.lower() for t in tokens]
    try:
        at = low.index("config")
    except ValueError:
        return False
    if "view" not in low[at + 1:]:
        return False
    return any(t in ("--raw", "--raw=true", "--flatten", "--flatten=true") for t in low)


def _names_only_listing(cmd: str) -> bool:
    """True when `cmd` is one plain listing (`ls`, `dir`, `Get-ChildItem`)
    that does not recurse, feed another command or redirect: it shows which
    files a folder holds and reads none of them."""
    if not cmd.strip() or _NOT_A_PLAIN_LISTING.search(cmd):
        return False
    words = list(_tokens(cmd))
    return _verb(words) in _LISTERS and not _RECURSE_RE.search(_flat(cmd))


def _use_indexes(tokens: list[str]) -> set[int]:
    """Token positions that name a key for USE (`ssh -i KEY`, `kubectl
    --kubeconfig FILE`) in a segment whose command only uses it."""
    verb, out = _verb(tokens), set()
    for i, t in enumerate(tokens):
        low = t.lower()
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
        if verb in _SSH_VERBS:
            if low == "-i" and nxt:
                out.add(i + 1)
            elif low == "-o" and nxt.startswith("identityfile="):
                out.add(i + 1)
            elif low.startswith(("identityfile=", "-oidentityfile=")):
                out.add(i)
        elif verb in _KUBE_VERBS:
            if low == "--kubeconfig" and nxt:
                out.add(i + 1)
            elif low.startswith("--kubeconfig="):
                out.add(i)
    return out


_DOT_FORMS = (".", "..", ".\\", "./", "..\\", "../")
_STATEMENT_SPLIT = re.compile(r"(\|\||&&|[;&\n]|\|)")


def _statements(cmd: str) -> list[list[list[str]]]:
    """`cmd` as statements, each a list of the commands a pipe joins, each a
    list of words. ; && || & and a newline end a statement; | does not."""
    out: list[list[list[str]]] = [[]]
    for part in _STATEMENT_SPLIT.split(cmd):
        if part == "|":
            continue
        if part in (";", "&", "\n", "&&", "||"):
            out.append([])
            continue
        words = [next(g for g in m.groups() if g) for m in _TOKEN_RE.finditer(part)]
        if words:
            out[-1].append(words)
    return [s for s in out if s]


def _from_cwd(path: str, cwd: str) -> str:
    """`path` made absolute against `cwd`, the directory the chain has reached."""
    if os.path.isabs(path) or path.startswith(("%", "\\\\")) or (len(path) > 1 and path[1] == ":"):
        return path
    return os.path.normpath(os.path.join(cwd, path))


def _cd_target(words: list[str], cwd: str) -> str | None:
    """Where `cd X`, `Set-Location X` or `pushd X` leaves the shell, when X is
    a folder that exists; None when it names no folder or none that exists."""
    at = _verb_index(words)
    for t in words[at + 1:]:
        if t.startswith("-") or t.lower() == "/d":
            continue
        raw = t.strip(",;()").replace(_SPACE, " ")
        target = _from_cwd(_expand(raw), cwd)
        return target if _is_dir(target) else None
    return None


def _segments(cmd: str) -> list[list[tuple[int, int, str]]]:
    """`cmd` split at ; | & and newlines into lists of (start, end, token)."""
    out, pos = [], 0
    for part in _SEGMENT_SPLIT.split(cmd):
        if part not in (";", "|", "&", "\n"):
            out.append([(pos + m.start(), pos + m.end(), next(g for g in m.groups() if g))
                        for m in _TOKEN_RE.finditer(part)])
        pos += len(part)
    return out


#: A command that can carry file content somewhere: read, copy, archive,
#: filter, upload, or feed a listing to another command.
_READER_RE = re.compile(
    r"(?<![a-z0-9_-])(?:cat|type|gc|get-content|more|less|head|tail|sls|"
    r"select-string|findstr|grep|rg|copy|cp|xcopy|robocopy|copy-item|tar|zip|"
    r"compress-archive|7z|7za|rar|certutil|xargs|foreach(?:-object)?|iwr|irm|curl|wget|"
    r"invoke-webrequest|invoke-restmethod|readall\w+|base64|xxd|od|format-hex|"
    r"scp|sftp|ftp|rsync)(?![a-z0-9_-])")
#: Recursion asked for by a flag: -Recurse and its PowerShell abbreviations,
#: -recursive, -Depth, and the robocopy/xcopy switches /S /E /MIR.
_RECURSE_RE = re.compile(
    r"(?<![a-z0-9_-])(?:-r(?:e(?:c[a-z]*)?)?|-recursive|-depth)(?![a-z0-9_-])"
    r"|(?<=\s)/(?:s|e|mir)(?=\s|$)", re.I)
#: Commands that descend into a directory they are given without being asked.
_IMPLICIT_RECURSION_RE = re.compile(
    r"(?<![a-z0-9_-])(?:tar|zip|7z|7za|rar|compress-archive|find|rsync)(?![a-z0-9_-])")
_MAX_DIR_ENTRIES = 300

#: What a refused walk is called; `refusal_command` reads this to word the
#: refusal, since no single file was named.
_WALK_WHY = "a folder that holds your key and credential folders"

#: Symbolic spellings of a directory, resolved before the command is judged.
#: A space in a substituted path is carried as \x01 so a token stays whole.
_SPACE = "\x01"
_HOME_FORMS = re.compile(
    r"\[(?:system\.)?environment\]::getfolderpath\s*\([^)]*\)|\$\{home\}|\$home(?![a-z0-9_])",
    re.I)
_CWD_FORMS = re.compile(
    r"\$\(\s*(?:pwd|get-location)\s*\)(?:\.path)?|\(\s*(?:pwd|get-location|"
    r"(?:resolve-path|convert-path)\s+\.)\s*\)(?:\.path)?|\$\{?pwd\}?(?![a-z0-9_])(?:\.path)?",
    re.I)


def _resolve_symbolic(cmd: str) -> str:
    """`cmd` with `$HOME`, `$PWD`, `(Get-Location)` and
    `[Environment]::GetFolderPath(..)` replaced by the directory they name.

    Every special folder GetFolderPath names lies under the home directory,
    so it reads as the home directory: the walk check judges the widest reach.
    """
    home = str(_home()).replace(" ", _SPACE)
    try:
        cwd = os.getcwd().replace(" ", _SPACE)
    except OSError:
        cwd = home
    return _CWD_FORMS.sub(lambda m: cwd, _HOME_FORMS.sub(lambda m: home, cmd))


def _covers_credentials(path: str) -> bool:
    """True when `path` is a folder with a denied folder or file inside it.

    A recursive read that starts above the credential folders reaches them
    without naming one. Judged on what exists, so a profile without an .ssh
    folder is not held back by the walk rule.
    """
    try:
        text, problem = _canonical_windows_spelling(path)
        root = Path(text)
        if problem or not root.is_dir():
            return False
        dirs, files, _lookups = _norms()
        for cand in {_asis(root), _norm(root)}:
            prefix = cand.rstrip(os.sep) + os.sep
            if any(d.startswith(prefix) and os.path.exists(d) for d, _why in (*dirs, *files)):
                return True
    except Exception:
        return False
    return False


def _is_dir(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except Exception:
        return False


def _glob_base(pattern: str) -> str:
    """The folder a wildcard path expands inside: everything before the wildcard."""
    cut = min((i for i in (pattern.find(c) for c in "*?[") if i >= 0), default=len(pattern))
    head = pattern[:cut]
    return head if head.endswith(("\\", "/")) else os.path.dirname(head)


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
        for dp, dirs, names in walker:
            for sub in dirs:
                seen += 1
                if seen > _MAX_DIR_ENTRIES:
                    return None
                why = check(Path(dp) / sub, sniff=False)
                if why:
                    return why
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


def _path_token(tok: str) -> str | None:
    """`tok` reduced to the path it may name, or None for a plain word."""
    tok = tok.strip(",;|()").replace(_SPACE, " ")
    if tok in _HOME_SPELLINGS:
        tok = _expand("~")
    elif tok in (".", "..", ".\\", "./", "..\\", "../"):
        tok = os.path.abspath(tok)
    if len(tok) < 3:
        return None
    if "=" in tok and not tok.startswith(("/", "~", "$", "%")) and tok[1:2] != ":":
        tok = tok.split("=", 1)[1]
    if ("/" in tok or "\\" in tok or tok.startswith("~")
            or tok.startswith("$env:") or tok.startswith("%")
            or (len(tok) > 1 and tok[1] == ":")):
        return tok
    return None


def _flat(text: str) -> str:
    return re.sub(r"[\"'`^+()]", "", text.replace(_SPACE, " ")).replace("\\", "/").lower()


def _is_clean_credential_path(tok: str) -> bool:
    """True for a path in a credential area that `check` nevertheless allows:
    a lookup with no secret in it, or a public key."""
    path = _path_token(tok)
    if not path or any(ch in path for ch in "*?["):
        return False
    flat = _flat(path)
    if not (any(m in flat for m, _w in _COMMAND_MARKERS) or _COMMAND_DIR_RE.search(flat)):
        return False
    try:
        target = Path(_expand(path))
        if check(target) is not None:
            return False
        if _plain_name(target.name).endswith(".pub"):
            return True
        dirs, _files, _lookups = _norms()
        return any(_asis(target).startswith(d + os.sep) for d, _why in dirs)
    except Exception:
        return False


def _without_exempt(cmd: str) -> str:
    """`cmd` with the tokens that only look like reading a credential blanked.

    A key named for use (`ssh -i KEY`, `kubectl --kubeconfig FILE`, in the
    segment of the command that uses it), a lookup such as `~/.ssh/config` and
    a public key, each while `check` allows the path. Blanking is by position,
    so the same spelling elsewhere in the command is still judged.
    """
    spans = []
    for seg in _segments(cmd):
        use = _use_indexes([t for _a, _b, t in seg])
        for i, (a, b, t) in enumerate(seg):
            if i in use or _is_clean_credential_path(t):
                spans.append((a, b))
    for a, b in reversed(spans):
        cmd = cmd[:a] + " " + cmd[b:]
    return cmd


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
    if _names_only_listing(_resolve_symbolic(cmd)):
        return None
    cmd_r = _without_exempt(re.sub(r"[`^]", "", _resolve_symbolic(cmd)))
    flat = _flat(cmd_r)
    for marker, why in _COMMAND_MARKERS:
        if marker in flat:
            return why
    m = _COMMAND_DIR_RE.search(flat)
    if m:
        return _DIR_WHY[m.group(1)]
    if any(b in flat for b in _BROWSER_DIR_WORDS) and             any(s in flat for s in _BROWSER_STORE_NAMES):
        return "a browser's saved-login or cookie store"
    reads = bool(_READER_RE.search(flat))
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = str(_home())
    # A statement is what ; && || & and a newline separate; the commands a
    # pipe joins are one statement. A folder named in one statement is not the
    # root of the next, and cd / Set-Location / pushd move where a statement
    # that names no folder starts its walk.
    for stmt in _statements(cmd_r):
        stmt_text = _flat(" ".join(" ".join(seg) for seg in stmt))
        recurse = bool(_RECURSE_RE.search(stmt_text)
                       or _IMPLICIT_RECURSION_RE.search(stmt_text))
        walks = reads and recurse
        named_root = False
        for words in stmt:
            verb = _verb(words)
            if verb in _CD_VERBS:
                moved = _cd_target(words, cwd)
                if moved:
                    why = check(Path(moved), sniff=False)
                    if why:
                        return why
                    cwd = moved
                continue
            if _prints_kubeconfig(words):
                return _DIR_WHY["kube"]
            prev = ""
            for tok in words:
                lead, prev = prev, tok.lower()
                bare = tok.strip(",;|()").replace(_SPACE, " ")
                path = None if bare in _DOT_FORMS else _path_token(tok)
                if path is None:
                    # A folder named without a slash is a folder all the same:
                    # it is judged like a path, and it is the walk root when it
                    # stands where a root stands. A file named without a slash
                    # is judged like any other file.
                    if not (reads and bare and not bare.startswith("-")):
                        continue
                    full = os.path.normpath(os.path.join(cwd, bare))
                    if os.path.isfile(full):
                        why = check(Path(full))
                        if why:
                            return why
                        continue
                    if not _is_dir(full):
                        continue
                    if walks and _covers_credentials(full):
                        return _WALK_WHY
                    why = _directory_holds_credential(full, recurse)
                    if why:
                        return why
                    if lead in _ROOT_LEADS or verb in _ARCHIVE_VERBS or bare in _DOT_FORMS:
                        named_root = True
                    continue
                expanded = _from_cwd(_expand(path), cwd)
                why = check(Path(expanded))
                if why:
                    return why
                wild = any(ch in expanded for ch in "*?[")
                if reads and not wild:
                    if walks and _covers_credentials(expanded):
                        return _WALK_WHY
                    named_root = named_root or _is_dir(expanded)
                    why = _directory_holds_credential(expanded, recurse)
                    if why:
                        return why
                if wild:
                    if walks:
                        named_root = True
                        if _covers_credentials(_glob_base(expanded)):
                            return _WALK_WHY
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
        if walks and not named_root:
            # No folder was named in this statement, so its walk starts where
            # the chain of cd commands has left the shell.
            if _covers_credentials(cwd):
                return _WALK_WHY
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


_CLOSED = "Keys and credentials stay closed to me, even when you ask."


def refusal(path) -> str:
    """The plain line Friday says instead of reading `path`.

    Answer first, the reason once, and a way forward. What is named agrees with
    what was matched: a file under a denied folder is a file in that folder,
    a device path is not called key material, and a .pem she cannot read is
    not called a key.
    """
    REFUSED.set(True)
    hit = _verdict(path)
    why, kind = hit if hit else ("a credential or key file", "file")
    if kind == "device":
        return ("That's a raw device path, not a file, so I'm leaving it alone. "
                "Give me the ordinary path and I'll take it from there.")
    if kind == "share":
        return ("That goes into this PC's own disks through a network share, so "
                "I'm not following it. Give me the ordinary path and I'll check "
                "that one.")
    try:
        p = Path(str(path).strip().strip('"')).expanduser()
        name, where = p.name or str(p), p.parent
    except Exception:
        name, where = str(path), ""
    if kind == "unsure":
        return (f"I can't read {name} to tell whether it holds a private key, so "
                f"I'm leaving it closed. A certificate saved as .crt opens fine.")
    lead = f"it sits in {why}" if kind == "inside" else f"it's {why}"
    # A bare name has no folder to point at; "at ." is not a place.
    where_text = str(where)
    tail = "You can open it yourself" + (
        "." if where_text in ("", ".") else f" at {where_text}.")
    return f"I won't open {name}: {lead}. {_CLOSED} {tail}"


def refusal_command(why: str) -> str:
    """The plain line Friday says instead of running a credential-reading command."""
    REFUSED.set(True)
    if why == _WALK_WHY:
        return (f"I won't run that: it reads through {why}, and the walk would "
                f"pass over them. {_CLOSED} Name the folder you want searched "
                f"and I'll search that one.")
    return (f"I won't run that: it reaches {why}. {_CLOSED} "
            f"Open the file yourself if you need it.")
