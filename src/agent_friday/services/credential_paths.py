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


#: Basename globs that are credential material wherever they sit.
_NAME_GLOBS: tuple[tuple[str, str], ...] = (
    ("id_rsa", "an SSH private key"),
    ("id_ed25519", "an SSH private key"),
    ("id_ecdsa", "an SSH private key"),
    ("id_dsa", "an SSH private key"),
    ("*.pem", "a private key or certificate"),
    ("*.ppk", "a PuTTY private key"),
    ("*.dpapi", "a Windows-protected secret"),
    ("*.token.enc", "an encrypted account token"),
    ("*.oauth.enc", "an encrypted connector token"),
    (".attestation-key-*", "the vault attestation signing key"),
    ("keystore.json", "Friday's keystore"),
    ("start.bat", "a launcher that holds your API keys in plain text"),
    ("launch_now.bat", "a launcher that holds your API keys in plain text"),
    ("friday_startup.*", "a launcher that holds your API keys in plain text"),
)

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


def _within(child: Path, parent: Path) -> bool:
    c, pa = _norm(child), _norm(parent)
    return c == pa or c.startswith(pa + os.sep)


def check(path) -> str | None:
    """A short reason `path` is off-limits to Friday's tools, or None.

    Names the KIND of secret, for a message the owner can act on. Works on a
    path that does not exist yet (open_path targets), so it never depends on
    the file being present.
    """
    try:
        p = Path(path).expanduser()
    except Exception:
        return None
    for d, why in _deny_dirs():
        if _within(p, d):
            return why
    for fpath, why in _deny_files():
        if _norm(p) == _norm(fpath):
            return why
    name = p.name.lower()
    for pat, why in _NAME_GLOBS:
        if fnmatch.fnmatch(name, pat):
            return why
    full = _norm(p).replace("\\", "/")
    if any(b in full for b in _BROWSER_DIR_WORDS) and \
            any(s in full for s in _BROWSER_STORE_NAMES):
        return "a browser's saved-login or cookie store"
    return None


#: High-signal substrings for a command that builds a credential path from a
#: variable (`$env:USERPROFILE\.ssh\...`), where token resolution alone would
#: miss it. Slashes normalised to '/'.
_COMMAND_MARKERS: tuple[tuple[str, str], ...] = (
    ("/.ssh/", "an SSH key"),
    ("/.aws/", "your AWS credentials"),
    ("/.gnupg/", "your GnuPG keyring"),
    ("id_rsa", "an SSH private key"),
    ("id_ed25519", "an SSH private key"),
    ("id_ecdsa", "an SSH private key"),
    (".friday/security", "Friday's keystore"),
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
    ("start.bat", "a launcher that holds your API keys in plain text"),
    ("launch_now.bat", "a launcher that holds your API keys in plain text"),
    ("friday_startup", "a launcher that holds your API keys in plain text"),
)


def _expand(token: str) -> str:
    t = token.strip().strip('"').strip("'")
    t = re.sub(r"\$env:(\w+)", lambda m: "%" + m.group(1) + "%", t)
    return os.path.expandvars(os.path.expanduser(t))


def scan_command(cmd: str) -> str | None:
    """A reason a shell command reads a credential path, or None.

    Best-effort and fail-toward-refusing: a marker substring, a browser store
    named next to a browser directory, or any path-shaped token that `check`
    denies. run_command's read verbs (Get-Content/type/cat) are the reason
    this exists; a write/exfil command is already outward and carded.
    """
    if not cmd:
        return None
    low = cmd.replace("\\", "/").lower()
    for marker, why in _COMMAND_MARKERS:
        if marker in low:
            return why
    if any(b in low for b in _BROWSER_DIR_WORDS) and \
            any(s in low for s in _BROWSER_STORE_NAMES):
        return "a browser's saved-login or cookie store"
    for tok in re.split(r"[\s,;|=]+", cmd):
        if len(tok) < 3:
            continue
        if ("/" in tok or "\\" in tok or tok.startswith("~")
                or tok.startswith("$env:") or (len(tok) > 1 and tok[1] == ":")):
            why = check(Path(_expand(tok)))
            if why:
                return why
    return None


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
