"""Recognising credentials in text: one definition for every layer that needs it.

Three consumers share these patterns so they cannot drift apart:

* the egress classifier (`sensitivity_classifier`) rates any text that carries
  a credential SENSITIVE, so it never reaches a cloud provider;
* the model's file and shell tools (`credential_paths.redact_secrets`) strip a
  key block or a token out of what they return, as the last line behind the
  path deny-list;
* the private-key sniff that denies a key file whatever it is named.

Detection runs on the text as written AND on the forms a determined caller
re-encodes it into: percent-encoding, escaped newlines, zero-width padding,
rot13, chunked armor, and base64 / base32 / hex runs (wrapped or not) that
decode to text, UTF-16 text or gzip, layered up to `_MAX_DEPTH` deep. Patterns
split by how sure they are:

KEY_RES     private-key headers and the leading bytes of encoded key bodies.
TOKEN_RES   vendor token formats with a distinctive prefix. High precision.
ASSIGN_RES  keyword = value forms (`password: ...`, `"api_key": "..."`). These
            need context, so they gate egress but are not used to redact tool
            output, where the owner's own config files are legitimate reading.

Every pattern is deliberately narrow: ordinary prose about passwords, tokens
and certificates, and public X.509 certificates, must not be swept up.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import re
import urllib.parse
import zlib

_WS = r"\s+"

#: Leading bytes of DER private keys, as base64, for a body that lost its
#: armor. Each is a SEQUENCE header followed by the fixed INTEGER version that
#: only a private key starts with, so an X.509 certificate (whose first field is
#: another SEQUENCE) never matches, however large its key:
#:   MII..  IB AAK / ADA   RSA PKCS#1 / PKCS#8 (two-byte length: 1024 bit up)
#:   MI[GH] AgE AMB        PKCS#8 with an EC key (P-256/384/521)
#:   MI[GH] AgE BBD/BBE    SEC1 EC private key
#:   MHcCAQEE              SEC1 P-256 (one-byte length)
#:   MC4CAQAwBQYDK2VwBCIEI PKCS#8 Ed25519
#:   b3BlbnNzaC1rZXktdjE   OpenSSH ("openssh-key-v1")
_B64C = r"[A-Za-z0-9+/]"
_KEY_BODY_STARTS: tuple[str, ...] = (
    r"MII" + _B64C + r"{3}IB(?:AAK|ADA)",
    r"MI[GH]" + _B64C + r"AgE(?:AMB|BBD|BBE)",
    r"MHcCAQEE",
    r"MC4CAQAwBQYDK2VwBCIEI",
    r"b3BlbnNzaC1rZXktdjE",
)
_BODY_LEAD = r"(?<![A-Za-z0-9+/])"

KEY_RES: tuple[re.Pattern, ...] = (
    # Any PRIVATE KEY armor header, in any case; whitespace (including
    # newlines) between the words is tolerated so a wrapped header still matches.
    re.compile(r"-----BEGIN" + _WS + r"(?:[A-Z0-9]+" + _WS + r")*PRIVATE" + _WS
               + r"KEY(?:" + _WS + r"BLOCK)?-----", re.I),
    re.compile(r"openssh-key-v1"),
) + tuple(re.compile(_BODY_LEAD + start) for start in _KEY_BODY_STARTS)

#: The armor header once every separator a caller might chunk it with is gone.
_SQUASHED_HEADER = re.compile(r"-----BEGIN[A-Z0-9]*PRIVATEKEY(?:BLOCK)?-----", re.I)
_SQUASH = re.compile(r"[\s\"'`+,;]")

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
    # secrets but "password=hunter2" is. A value that opens like a filesystem
    # path (`PWD=/c/Users/...`) is a directory, not a password.
    re.compile(r"(?i)\b(?:password|passwd|pwd)" + _Q + r"\s*[:=]\s*" + _Q
               + r"""(?![/~.\\]|[A-Za-z]:[\\/])"""
               + r"""(?=[^\s"']{6,})[^\s"']*[0-9!@#$%^&*+=/-][^\s"']*"""),
    re.compile(r"(?i)(?<![A-Za-z0-9])(?:api[_-]?key|secret[_-]?(?:access[_-]?)?key|"
               r"client[_-]?secret|access[_-]?token|auth[_-]?token|private[_-]?key)"
               + _Q + r"\s*[:=]\s*" + _Q + r"(?=[A-Za-z0-9_\-/+=.]*\d)[A-Za-z0-9_\-/+=.]{16,}"),
    # A bearer token always carries a digit; a hyphenated phrase does not.
    re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9_\-.=+/]*\d)[A-Za-z0-9_\-.=+/]{20,}"),
    # user:password@host inside a URL
    re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,}://[^\s/:@]+:[^\s/@]{3,}@[^\s/]+"),
)

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_B64_RUN = re.compile(
    r"[A-Za-z0-9+/_-]{20,}={0,2}(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/_-]{4,}={0,2})*")
_B32_RUN = re.compile(r"[A-Z2-7]{24,}={0,6}")
#: Sixteen or more hex bytes, joined by nothing or by `-`, `:`, `,` or spaces,
#: each optionally written `0x..` (BitConverter, xxd, Format-Hex, byte lists).
_HEX_RUN = re.compile(r"(?:(?:0[xX])?[0-9A-Fa-f]{2}[-:,\t ]{0,2}){16,}")
_MAX_RUNS = 64
_MAX_RUN_CHARS = 400_000
_MAX_DEPTH = 3
_MAX_INFLATE = 2_000_000


def _b64_bytes(run: str) -> bytes | None:
    s = re.sub(r"\s+", "", run).replace("-", "+").replace("_", "/").rstrip("=")
    if len(s) < 16:
        return None
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None


def _b32_bytes(run: str) -> bytes | None:
    s = run.rstrip("=")
    s += "=" * (-len(s) % 8)
    try:
        return base64.b32decode(s)
    except (binascii.Error, ValueError):
        return None


def _hex_bytes(run: str) -> bytes | None:
    digits = re.sub(r"0[xX]|[^0-9A-Fa-f]", "", run)
    digits = digits[:len(digits) - (len(digits) % 2)]
    try:
        return bytes.fromhex(digits) if len(digits) >= 32 else None
    except ValueError:
        return None


_DECODERS = ((_B64_RUN, _b64_bytes), (_HEX_RUN, _hex_bytes), (_B32_RUN, _b32_bytes))


def _line_prefixes(run: str):
    """`run` whole, then with trailing wrapped lines dropped one at a time.

    A wrapped base64 run greedily swallows any following word of four or more
    letters; the encoded part is the longest leading run of lines that decodes.
    """
    yield len(run), run
    cut = len(run)
    for _ in range(8):
        cut = run.rfind("\n", 0, cut)
        if cut <= 0:
            return
        yield cut, run[:cut]


def _encoded_runs(text: str):
    """Yield (span, raw_bytes) for each base64, hex or base32 run that decodes."""
    n = 0
    for rx, decode in _DECODERS:
        for m in rx.finditer(text):
            if n >= _MAX_RUNS:
                return
            run = m.group(0)
            if len(run) > _MAX_RUN_CHARS:
                continue
            for used, part in _line_prefixes(run):
                raw = decode(part)
                if raw:
                    n += 1
                    yield (m.start(), m.start() + used), raw
                    break


def _views(raw: bytes):
    """Every text a decoded byte string may be read as: Latin-1, UTF-16, gzip."""
    yield raw.decode("latin-1")
    if b"\x00" in raw[:4096]:
        for enc in ("utf-16-le", "utf-16-be"):
            yield raw.decode(enc, errors="ignore")
    if raw[:2] == b"\x1f\x8b":
        try:
            inflated = zlib.decompressobj(31).decompress(raw, _MAX_INFLATE)
        except zlib.error:
            inflated = b""
        if inflated:
            yield from _views(inflated)


def _decoded(text: str, depth: int = _MAX_DEPTH):
    """Texts hidden inside `text` by nested base64 / base32 / hex / gzip layers."""
    if depth <= 0:
        return
    for _span, raw in _encoded_runs(text):
        for view in _views(raw):
            yield view
            yield from _decoded(view, depth - 1)


def _variants(text: str):
    """The text plus the re-encodings a caller might have hidden it in."""
    yield text
    if "%" in text and _PERCENT.search(text):
        yield urllib.parse.unquote(text)
    if "\\n" in text or "\\r" in text:
        yield text.replace("\\r", "").replace("\\n", "\n")
    if _ZERO_WIDTH.search(text):
        yield _ZERO_WIDTH.sub("", text)
    if "-----" in text or "---" in text:
        yield _SQUASH.sub("", text)
    yield codecs.encode(text, "rot13")


def _direct(text: str, groups) -> bool:
    return any(rx.search(text) for g in groups for rx in g)


_SQUASHED = ((_SQUASHED_HEADER,),)


def _scan(text: str, groups) -> bool:
    for v in _variants(text):
        if _direct(v, groups) or _direct(v, _SQUASHED):
            return True
        for dec in _decoded(v):
            if _direct(dec, groups) or _direct(dec, _SQUASHED):
                return True
    return False


def contains_key_material(text: str) -> bool:
    """A private key block or the leading bytes of one, in any listed encoding."""
    return bool(text) and _scan(text, (KEY_RES,))


def contains_secret(text: str) -> bool:
    """Any credential this module knows, in the text or a re-encoding of it."""
    return bool(text) and _scan(text, (KEY_RES, TOKEN_RES, ASSIGN_RES))


_KEY_BLOCK = re.compile(
    r"-----BEGIN" + _WS + r"(?:[A-Z0-9]+" + _WS + r")*PRIVATE" + _WS + r"KEY(?:" + _WS
    + r"BLOCK)?-----(?:[\s\S]*?-----END" + _WS + r"[A-Z0-9 ]*-----|(?:\s*[A-Za-z0-9+/=]{16,})*)",
    re.I)
_KEY_BODY = re.compile(
    _BODY_LEAD + "(?:" + "|".join(_KEY_BODY_STARTS) + ")"
    r"[A-Za-z0-9+/=]*(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/=]{16,})*")

WITHHELD = "[credential withheld]"


def _rot13_lines(text: str) -> str:
    """Lines whose rot13 form carries key material or a token are withheld."""
    if not _direct(codecs.encode(text, "rot13"), (KEY_RES, TOKEN_RES)):
        return text
    return "\n".join(
        WITHHELD if _direct(codecs.encode(line, "rot13"), (KEY_RES, TOKEN_RES)) else line
        for line in text.split("\n"))


def redact(text: str) -> str:
    """`text` with key blocks and vendor-format tokens replaced by a marker.

    Only KEY_RES and TOKEN_RES material is removed: keyword=value lines in the
    owner's own files are left alone. Encoded copies are replaced whole; armor
    chunked to defeat a pattern withholds the whole text, since it cannot be
    cut cleanly.
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
    for span, raw in _encoded_runs(out):
        for view in _views(raw):
            if _direct(view, (KEY_RES, TOKEN_RES)) or any(
                    _direct(d, (KEY_RES, TOKEN_RES)) for d in _decoded(view, _MAX_DEPTH - 1)):
                spans.append(span)
                break
    merged: list[list[int]] = []
    for a, b in sorted(set(spans)):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in reversed(merged):
        out = out[:a] + WITHHELD + out[b:]
    out = _rot13_lines(out)
    if _SQUASHED_HEADER.search(_SQUASH.sub("", out)):
        # armor whose header only exists once its chunks are joined
        return WITHHELD
    return out
