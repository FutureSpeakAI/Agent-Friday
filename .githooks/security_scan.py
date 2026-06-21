#!/usr/bin/env python3
"""Agent Friday — pre-commit secret/PII scanner.

Scans the *staged* additions of a commit for API keys, passwords, private
keys, personal emails, SSNs, phone numbers, and private filesystem paths.
Blocks the commit (exit 1) if anything is found.

Usage (normally invoked by .githooks/pre-commit):
    python .githooks/security_scan.py

Bypass a single false-positive line by appending a comment:
    secret_looking_thing            # pragma: allowlist secret

Emergency bypass for the whole commit (discouraged, logged in reflog):
    git commit --no-verify
"""
from __future__ import annotations

import re
import subprocess
import sys

# Windows consoles often default to cp1252 and choke on non-ASCII output, which
# previously crashed the scanner *after* detection and let the commit through.
# Force UTF-8 where possible; output below is ASCII-only as a second safeguard.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Allowlist ───────────────────────────────────────────────────────────────
# Lines containing any of these markers are skipped entirely.
LINE_ALLOW_MARKERS = ("pragma: allowlist secret", "nosec", "noqa: secret")

# Substrings that make a "key = value" match obviously a placeholder, not a real
# secret. Case-insensitive.
PLACEHOLDER_VALUES = (
    "your-key", "your_key", "yourkey", "changeme", "change-me", "example",
    "placeholder", "redacted", "xxxx", "<", "${", "os.environ", "getenv",
    "process.env", "your-", "dummy", "fake", "sample", "todo", "none", "null",
    "..", "***",
)

# Email domains / local-parts that are fine to commit (docs, business, no-reply).
EMAIL_ALLOW = (
    "@example.com", "@example.org", "@example.net", "@futurespeak.ai",
    "@domain.com", "@email.com", "@test.com", "@yourdomain.com",
    "noreply@", "no-reply@", "user@", "you@", "your-email@", "name@",
)
# Personal mail providers — committing one of these is almost always real PII.
PERSONAL_EMAIL_DOMAINS = (
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "proton.me", "protonmail.com", "live.com", "me.com",
)

# ── Detection rules ──────────────────────────────────────────────────────────
# Each rule: (category, compiled regex, optional validator(value)->bool).
def _not_placeholder(value: str) -> bool:
    low = value.lower()
    return not any(p in low for p in PLACEHOLDER_VALUES)

def _is_personal_email(value: str) -> bool:
    low = value.lower()
    if any(a in low for a in EMAIL_ALLOW):
        return False
    return any(low.endswith("@" + d) or low.endswith(d) for d in PERSONAL_EMAIL_DOMAINS)

RULES = [
    ("Google/Gemini API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}"), None),
    ("OpenAI/Anthropic API key", re.compile(r"sk-(?:ant-)?[A-Za-z0-9_\-]{20,}"), None),
    ("Google AI Studio (AQ.) key", re.compile(r"\bAQ\.[A-Za-z0-9_\-]{20,}"), None),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), None),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}"), None),
    ("GitHub token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}"), None),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), None),
    ("Hardcoded secret assignment",
     re.compile(r"(?i)(?:api[_-]?key|secret|token|passwd|password|pwd|access[_-]?key)\s*[:=]\s*[\"']?([^\s\"';]{8,})"),
     _not_placeholder),
    ("Personal email (PII)",
     re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
     _is_personal_email),
    ("Possible SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), None),
    ("Possible phone number", re.compile(r"\b(?:\+?1[-.\s]?)?\(\d{3}\)[-.\s]?\d{3}[-.\s]?\d{4}\b|\b\d{3}[-.]\d{3}[-.]\d{4}\b"), None),
    ("Private Windows user path",
     re.compile(r"[A-Za-z]:\\Users\\([A-Za-z0-9._\-]+)\\"),
     lambda v: v.lower() not in ("user", "username", "public", "default", "all users")
               and not v.startswith(("{", "%", "<"))),
]

# Never scan these (binary / generated / vendored). Staged-but-gitignored files
# normally won't appear, but guard anyway.
SKIP_PATH_SUBSTR = ("node_modules/", "/dist/", "/build/", ".min.js", ".lock",
                    "package-lock.json", "yarn.lock")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".pdf", ".woff",
            ".woff2", ".ttf", ".eot", ".zip", ".gz", ".bundle", ".wav", ".mp3",
            ".mp4", ".webp", ".pyc")


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


def staged_files() -> list[str]:
    out = _run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"])
    return [f for f in out.splitlines() if f.strip()]


def added_lines(path: str):
    """Yield (lineno_in_new_file, text) for lines ADDED to `path` in this commit."""
    diff = _run(["git", "diff", "--cached", "--unified=0", "--", path])
    new_lineno = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_lineno = int(m.group(1)) if m else new_lineno
            continue
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            yield new_lineno, line[1:]
            new_lineno += 1
        elif not line.startswith("-"):
            new_lineno += 1


def should_skip(path: str) -> bool:
    low = path.lower()
    return any(s in low for s in SKIP_PATH_SUBSTR) or low.endswith(SKIP_EXT)


def detect() -> list:
    findings = []
    for path in staged_files():
        if should_skip(path):
            continue
        for lineno, text in added_lines(path):
            if any(marker in text for marker in LINE_ALLOW_MARKERS):
                continue
            for category, rx, validator in RULES:
                m = rx.search(text)
                if not m:
                    continue
                value = m.group(1) if m.groups() else m.group(0)
                if validator and not validator(value):
                    continue
                snippet = text.strip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                findings.append((path, lineno, category, snippet))
    return findings


def _say(line: str) -> None:
    """ASCII-safe print: never let an encoding error swallow a real block."""
    try:
        print(line)
    except Exception:
        try:
            sys.stdout.buffer.write((line + "\n").encode("ascii", "replace"))
        except Exception:
            pass


def report(findings: list) -> None:
    _say("")
    _say("X COMMIT BLOCKED -- potential secrets / PII in staged changes")
    _say("")
    for path, lineno, category, snippet in findings:
        _say(f"  {path}:{lineno}  [{category}]")
        _say(f"      {snippet}")
    _say("")
    _say("What to do:")
    _say("  - Move secrets to environment variables or ~/.friday/ runtime config.")
    _say("  - Replace personal PII (emails, names, phone, SSN) with placeholders.")
    _say("  - Use ~ / Path.home() / %USERPROFILE% instead of C:\\Users\\<name>\\ paths.")
    _say("  - False positive? Append  '# pragma: allowlist secret'  to that line.")
    _say("  - See SECURITY.md and .github/SECURITY_POLICY.md for the full policy.")
    _say("")


if __name__ == "__main__":
    try:
        _findings = detect()
    except Exception as exc:  # never hard-fail a commit on a scanner/detection bug
        print(f"[security_scan] warning: scanner error, not blocking commit: {exc}",
              file=sys.stderr)
        sys.exit(0)
    # Detection succeeded: if anything was found, BLOCK — even if printing hiccups.
    if _findings:
        report(_findings)
        sys.exit(1)
    sys.exit(0)
