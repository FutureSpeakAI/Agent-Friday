"""Recognising credentials in text: one definition for every layer that needs it.

Three consumers share these patterns so they cannot drift apart:

* the egress classifier (`sensitivity_classifier`) rates any text that carries
  a credential SENSITIVE, so it never reaches a cloud provider;
* the model's file and shell tools (`credential_paths.redact_secrets`) strip a
  key block or a token out of what they return, as the last line behind the
  path deny-list;
* the private-key sniff that denies a key file whatever it is named.

Detection runs on the text as written AND on the forms a determined caller
re-encodes it into: percent-encoding, escaped newlines, zero-width padding and
base64 (wrapped or not). Patterns split by how sure they are:

KEY_RES     private-key headers and the leading bytes of encoded key bodies.
TOKEN_RES   vendor token formats with a distinctive prefix. High precision.
ASSIGN_RES  keyword = value forms (`password: ...`, `"api_key": "..."`). These
            need context, so they gate egress but are not used to redact tool
            output, where the owner's own config files are legitimate reading.

Every pattern is deliberately narrow: ordinary prose about passwords, tokens
and certificates must not be swept up.
"""
from __future__ import annotations

import base64
import binascii
import re
import urllib.parse

_WS = r"\s+"

KEY_RES: tuple[re.Pattern, ...] = (
    # Any PRIVATE KEY armor header; whitespace (including newlines) between the
    # words is tolerated so a header wrapped over lines still matches.
    re.compile(r"-----BEGIN" + _WS + r"(?:[A-Z0-9]+" + _WS + r")*PRIVATE" + _WS
               + r"KEY(?:" + _WS + r"BLOCK)?-----"),
    # An armor body that lost its header: OpenSSH ("openssh-key-v1"), PKCS#8
    # Ed25519, and PKCS#1/#8 RSA at line start.
    re.compile(r"b3BlbnNzaC1rZXktdjE"),
    re.compile(r"MC4CAQAwBQYDK2VwBCIEI"),
    re.compile(r"(?m)^[ \t]*MII[EJ][A-Za-z0-9+/]{50,}"),
    re.compile(r"openssh-key-v1"),
)

TOKEN_RES: tuple[re.Pattern, ...] = (
    re.compile(r"\bgh[posur]_[A-Za-z0-9]{20,}\b"),                 # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),               # GitHub fine-grained
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                  # AWS key id
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),               # Slack
    re.compile(r"\bxapp-\d-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),                     # GitLab
    re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}"),                  # Stripe
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),                        # npm
    re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),   # SendGrid
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),                     # Google OAuth
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b"),               # Telegram bot
    re.compile(r"(?i)(?:twilio|auth[_\s-]?token)[^0-9a-f]{0,24}[0-9a-f]{32}\b"),
    re.compile(r"(?i)\"?root_key\"?\s*[:=]"),                      # Friday keystore JSON
)

_Q = r"""["']?"""
ASSIGN_RES: tuple[re.Pattern, ...] = (
    # A password assignment whose value carries a digit or a symbol, so
    # "Password: Required" and `password = request.form.get(...)` are not
    # secrets but "password=hunter2" is.
    re.compile(r"(?i)\b(?:password|passwd|pwd)" + _Q + r"\s*[:=]\s*" + _Q
               + r"""(?=[^\s"']{6,})[^\s"']*[0-9!@#$%^&*+=/-][^\s"']*"""),
    re.compile(r"(?i)(?<![A-Za-z0-9])(?:api[_-]?key|secret[_-]?(?:access[_-]?)?key|"
               r"client[_-]?secret|access[_-]?token|auth[_-]?token|private[_-]?key)"
               + _Q + r"\s*[:=]\s*" + _Q + r"(?=[A-Za-z0-9_\-/+=.]*\d)[A-Za-z0-9_\-/+=.]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=+/]{20,}"),
    # user:password@host inside a URL
    re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,}://[^\s/:@]+:[^\s/@]{3,}@[^\s/]+"),
)

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_B64_RUN = re.compile(
    r"[A-Za-z0-9+/_-]{20,}={0,2}(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/_-]{4,}={0,2})*")
_MAX_B64_RUNS = 64
_MAX_B64_CHARS = 400_000


def _decode_b64(run: str) -> str | None:
    s = re.sub(r"\s+", "", run).replace("-", "+").replace("_", "/").rstrip("=")
    if len(s) < 16:
        return None
    s += "=" * (-len(s) % 4)
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw.decode("latin-1")


def _decoded_runs(text: str):
    """Yield (match, decoded_text) for each base64-looking run that decodes."""
    n = 0
    for m in _B64_RUN.finditer(text):
        if n >= _MAX_B64_RUNS:
            return
        run = m.group(0)
        if len(run) > _MAX_B64_CHARS:
            continue
        dec = _decode_b64(run)
        if dec:
            n += 1
            yield m, dec


def _variants(text: str):
    """The text plus the re-encodings a caller might have hidden it in."""
    yield text
    if "%" in text and _PERCENT.search(text):
        yield urllib.parse.unquote(text)
    if "\\n" in text or "\\r" in text:
        yield text.replace("\\r", "").replace("\\n", "\n")
    if _ZERO_WIDTH.search(text):
        yield _ZERO_WIDTH.sub("", text)


def _direct(text: str, groups) -> bool:
    return any(rx.search(text) for g in groups for rx in g)


def contains_key_material(text: str) -> bool:
    """A private key block or the leading bytes of one, in any listed encoding."""
    if not text:
        return False
    for v in _variants(text):
        if _direct(v, (KEY_RES,)):
            return True
        for _m, dec in _decoded_runs(v):
            if _direct(dec, (KEY_RES,)):
                return True
    return False


def contains_secret(text: str) -> bool:
    """Any credential this module knows, in the text or a re-encoding of it."""
    if not text:
        return False
    groups = (KEY_RES, TOKEN_RES, ASSIGN_RES)
    for v in _variants(text):
        if _direct(v, groups):
            return True
        for _m, dec in _decoded_runs(v):
            if _direct(dec, groups):
                return True
    return False


_KEY_BLOCK = re.compile(
    r"-----BEGIN" + _WS + r"(?:[A-Z0-9]+" + _WS + r")*PRIVATE" + _WS + r"KEY(?:" + _WS
    + r"BLOCK)?-----(?:[\s\S]*?-----END" + _WS + r"[A-Z0-9 ]*-----|(?:\s*[A-Za-z0-9+/=]{16,})*)")
_KEY_BODY = re.compile(r"(?:b3BlbnNzaC1rZXktdjE|MC4CAQAwBQYDK2VwBCIEI|(?m:^[ \t]*MII[EJ]))"
                       r"[A-Za-z0-9+/=]*(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/=]{16,})*")

WITHHELD = "[credential withheld]"


def redact(text: str) -> str:
    """`text` with key blocks and vendor-format tokens replaced by a marker.

    Only KEY_RES and TOKEN_RES material is removed: keyword=value lines in the
    owner's own files are left alone. Encoded copies are replaced whole.
    """
    if not text:
        return text
    out = text
    if _PERCENT.search(out) and contains_key_material(urllib.parse.unquote(out)):
        # A percent-encoded key cannot be cut cleanly; withhold the run.
        out = re.sub(r"(?:%[0-9A-Fa-f]{2}|[A-Za-z0-9._~-]){60,}",
                     lambda m: WITHHELD if contains_secret(urllib.parse.unquote(m.group(0)))
                     else m.group(0), out)
    out = _KEY_BLOCK.sub(WITHHELD, out)
    out = _KEY_BODY.sub(WITHHELD, out)
    for rx in TOKEN_RES:
        out = rx.sub(WITHHELD, out)
    spans = []
    for m, dec in _decoded_runs(out):
        if _direct(dec, (KEY_RES, TOKEN_RES)):
            spans.append(m.span())
    for a, b in reversed(spans):
        out = out[:a] + WITHHELD + out[b:]
    return out
