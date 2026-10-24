"""services/injection_signals.py -- does this outside text try to instruct Friday? (BD-24 U2)

North Star 19.5-1/-4, 13.2-4, 14.9-1: content from mail, the web, documents, calendars, chat
exports and image descriptions informs but cannot instruct, and "untrusted content never
modifies a plan by itself". Provenance (services/taint) catches an instruction whose words or
values Friday copies into an action; it cannot see one that was paraphrased, or that arrived
encoded. This module looks at the outside text itself:

    scan(text) -> list of signals, e.g. ["override", "action_request:base64"]

A signal names the shape of the instruction (override, role, directive, action_request,
secrecy, trigger) and, when it was only visible after undoing a disguise, how it was hidden
(lookalike letters, spaced-out letters, base64, hex, rot13, split across lines). An empty list
means nothing instruction-shaped was found. It never raises and never changes the text.

services/taint records the signals with what Friday read; an action that follows reading such
text is shown to the owner on a card even when none of its values match the text.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata
from typing import List

#: Scanning stops after this many characters (a huge page is judged on its first part).
MAX_SCAN = 200_000

_ACTORS = r"(?:assistant|ai|a\.i\.|friday|agent|model|chatbot|bot|llm|copilot|claude|chatgpt|gpt)"
#: Who an instruction to Friday is addressed to (narrower: "the model must be retrained" is not one).
_ADDRESSEE = (r"(?:(?:the |any |all |every |main )*(?:ai|a\.i\.|llm|language model|chatbot|copilot|claude|chatgpt|"
              r"friday|(?:ai |main |the )?assistants?|ai (?:agents?|models?)))")
#: Where an imperative starts: a new sentence or line, or a softener ("please forward ...").
#: Text is whitespace-normalised before matching (see _squash), so " ?" here is enough and
#: nothing in these patterns can backtrack over long blank runs.
_IMPERATIVE = r"(?:^|[.!?:;>\]\n] ?|\b(?:please|kindly|also|now|quietly|immediately|then|just|you|and) )"
_SENSITIVE = (r"(?:all|every|each|the last \d+|passwords?|passport|credentials?|api keys?|private keys?|card numbers?|"
              r"bank details|account details|contacts|inbox|files|contracts?|invoices?|documents|(?:the )?vault|contents)")

_PATTERNS = [
    ("role", re.compile(r"\b(?:SYSTEM|ASSISTANT|DEVELOPER|AI MODEL|TOOL SERVER|ADMIN)\b(?: (?:NOTICE|MESSAGE|NOTE|"
                        r"INSTRUCTIONS?|OVERRIDE))?(?: TO [A-Z ]{2,30})? ?[:>]")),
    ("role", re.compile(r"\b(?:you are now (?:a|an|my|in|acting|operating|playing|free|allowed|permitted|unrestricted|"
                        r"dan)\b|from now on,? you (?:are|will|must|should)|act as (?:a|an|my|if)\b|pretend (?:to be|"
                        r"you are)|you must now|(?:switch to|enter|enable|activate) (?:developer|admin|god|dan) mode)",
                        re.I)),
    ("role", re.compile(r"^ ?(?:system|developer) ?(?:[:>\]]|\(|message\b)", re.I | re.M)),
    ("override", re.compile(r"\b(?:priority|urgent|system|admin|new|updated|revised|real|actual) (?:instructions?|"
                            r"overrides?|directives?|orders?) ?(?:[:\-]|for\b|to\b)", re.I)),
    ("override", re.compile(r"\b(?:system|security|admin) notice (?:to|for) " + _ADDRESSEE + r"\b", re.I)),
    ("override", re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass|drop)\b[^.\n]{0,40}?\b(?:previous|prior|above|earlier|"
        r"all|any|your|the|these|system|existing)\b[^.\n]{0,25}?\b(?:instructions?|rules?|prompts?|directions?|"
        r"guidelines?|polic(?:y|ies)|guardrails?|constraints?|safeguards?)\b", re.I)),
    ("authority_claim", re.compile(r"\bthe (?:owner|user)\b[^.\n]{0,25}?\b(?:wants (?:you|me)|says to|said to|told (?:me|you)|asked (?:you|me)|"
                                   r"said yes|has (?:already )?(?:pre-?)?approved|already (?:approved|agreed|said)|"
                                   r"gave (?:you )?permission)", re.I)),
    ("authority_claim", re.compile(r"\b(?:turn off|disable|skip|bypass)\b[^.\n]{0,30}?\b(?:approval|confirmation|"
                                   r"asking (?:the )?(?:owner|user)|safeguards?)\b", re.I)),
    ("authority_claim", re.compile(r"\bgrant\b[^.\n]{0,30}?\b(?:permanent|full|unrestricted|admin|blanket) (?:permissions?|"
                                   r"access|rights?)\b", re.I)),
    ("directive", re.compile(r"\b(?:hey|hi|dear|ok|okay|attention) friday ?[,:!]", re.I)),
    ("directive", re.compile(r"\b(?:note|message|instructions?|reminder)\b(?: from [^.\n]{0,40}?)? (?:to|for) "
                             + _ADDRESSEE + r"\b", re.I)),
    ("directive", re.compile(r"\b(?:have|ask|tell|get|let|instruct|make) " + _ADDRESSEE + r" (?:to )?"
                             r"(?:send|forward|email|e-mail|transfer|pay|delete|post|share|upload|run|open|write|"
                             r"approve|accept|book|buy|reply)\b", re.I)),
    ("directive", re.compile(_ADDRESSEE + r" (?:reading|working|processing|parsing|summari[sz]ing|that (?:read|see)s?|"
                             r"who (?:read|see)s?)\b", re.I)),
    ("directive", re.compile(r"\b" + _ADDRESSEE + r"\b[^.\n]{0,40}?\b(?:must|should|shall|needs? to|is (?:required|"
                             r"instructed|asked) to|has to|will now) (?!be\b)\w", re.I)),
    ("action_request", re.compile(_IMPERATIVE + r"(?:call|run|use|invoke|execute) `?[a-z]+_[a-z_]+\b", re.I | re.M)),
    ("action_request", re.compile(
        _IMPERATIVE + r"(?:forward|send|e-?mail|transfer|wire|pay|upload|share|copy|export|post|leak|exfiltrate)\b"
        r"[^.\n]{0,30}?\b" + _SENSITIVE + r"\b[^.\n]{0,60}?\b(?:to|into|at|onto)\b[^.\n]{0,50}?(?:[\w.+-]+@[\w-]+\.[\w.]+|"
        r"https?://|\b(?:iban|account|wallet|webhook|server|endpoint)\b)", re.I | re.M)),
    ("action_request", re.compile(_IMPERATIVE + r"(?:press|click|tap|accept|approve|allow)\b[^.\n]{0,30}?\b(?:every|all|"
                                  r"each|any)\b[^.\n]{0,30}?\b(?:dialogs?|prompts?|requests?|permissions?|invitations?|"
                                  r"items?|buttons?)\b", re.I | re.M)),
    ("action_request", re.compile(r"\bwrite\b[^.\n]{0,40}?\b(?:startup|autorun|authorized_keys|\.bashrc|\.ssh|registry)\b"
                                  r"|\bauthorized_keys\b|\bdownloads? and runs?\b|\| ?(?:sh|bash|powershell)\b", re.I)),
    ("action_hint", re.compile(
        _IMPERATIVE + r"(?:forward|send|e-?mail|transfer|wire|pay|upload|share|copy|export|post|leak|exfiltrate)\b"
        r"[^.\n]{0,80}?\b(?:to|into|at|onto)\b[^.\n]{0,50}?(?:[\w.+-]+@[\w-]+\.[\w.]+|https?://|\b(?:iban|account|"
        r"wallet|webhook|server|endpoint)\b)", re.I | re.M)),
    ("action_hint", re.compile(r"\b(?:delete|erase|wipe|remove)\b[^.\n]{0,30}?\b(?:all|every|the user'?s|the owner'?s)\b"
                               r"[^.\n]{0,30}?\b(?:files?|emails?|messages?|data|memor(?:y|ies)|contacts?)\b", re.I)),
    ("secrecy", re.compile(r"\b(?:do not|don'?t|never|without)\b[^.\n]{0,25}?\b(?:tell|telling|inform|informing|mention|"
                           r"mentioning|notify|notifying|alert|alerting|asking|show|showing)\b[^.\n]{0,25}?"
                           r"\b(?:the )?(?:user|owner|human)\b", re.I)),
    ("trigger", re.compile(r"\b(?:when you (?:read|see|process|summari[sz]e|open) this|upon reading this|"
                           r"whoever (?:reads|processes) this|if you are an? " + _ACTORS + r")\b", re.I)),
    ("trigger", re.compile(r"\b(?:on behalf of|instead of|without asking) the (?:user|owner)\b", re.I)),
]

#: An address or a "delete all" on its own is ordinary mail ("send feedback to ..."): it counts only
#: beside another signal.
_WEAK = {"action_hint"}


def _squash(text: str) -> str:
    """Runs of blanks become one space and runs of line breaks one line break, so no pattern can
    be made to backtrack over a long blank run (a hostile page full of newlines)."""
    t = re.sub(r"[ \t\f\v ]+", " ", text)
    return re.sub(r" ?\n[\s]*", "\n", t)


#: Letters that look like Latin ones (Cyrillic, Greek, fullwidth handled by NFKC).
_LOOKALIKE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "һ": "h", "ӏ": "l", "к": "k", "м": "m", "н": "h", "т": "t", "в": "b",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "У": "Y", "І": "I", "К": "K", "М": "M",
    "Н": "H", "Т": "T", "В": "B",
    "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "Ο": "O", "Α": "A", "Ε": "E", "Ι": "I", "Κ": "K", "Ν": "N", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿­]")
_B64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{20,}={0,2}(?![A-Za-z0-9+/=])")
_B64URL = re.compile(r"(?<![A-Za-z0-9_\-=])[A-Za-z0-9_\-]{24,}={0,2}(?![A-Za-z0-9_\-=])")
_HEX = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[\s:]?){16,}(?![0-9A-Fa-f])")


def _kinds(text: str) -> List[str]:
    text = _squash(text)
    out = []
    for kind, pat in _PATTERNS:
        if kind not in out and pat.search(text):
            out.append(kind)
    if out and all(k in _WEAK for k in out):
        return []
    return out


def _printable(b: bytes) -> str:
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if not s or sum(ch.isprintable() or ch in "\n\r\t" for ch in s) < 0.9 * len(s):
        return ""
    return s


def _decoded_b64(text: str) -> List[str]:
    out = []
    for m in list(_B64.finditer(text))[:50] + list(_B64URL.finditer(text))[:50]:
        tok = m.group(0)
        pad = "=" * (-len(tok.rstrip("=")) % 4)
        for fn in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                s = _printable(fn(tok.rstrip("=") + pad))
            except (binascii.Error, ValueError):
                s = ""
            if s:
                out.append(s)
                break
    return out


def _decoded_hex(text: str) -> List[str]:
    out = []
    for m in list(_HEX.finditer(text))[:50]:
        tok = re.sub(r"[\s:]", "", m.group(0))
        if len(tok) % 2:
            tok = tok[:-1]
        s = ""
        # The run can swallow a hex-looking word that follows it ("... 6c65 CA" of "CANARY"):
        # drop up to three trailing bytes before giving up.
        for cut in range(0, 4):
            body = tok[:len(tok) - 2 * cut] if cut else tok
            try:
                s = _printable(bytes.fromhex(body))
            except ValueError:
                s = ""
            if s:
                break
        if s:
            out.append(s)
    return out


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = _INVISIBLE.sub("", t)
    return t.translate(_LOOKALIKE)


def _unspace(text: str) -> str:
    """'I g n o r e   a l l' -> 'Ignore all': single letters joined by one separator are a word;
    a wider gap (two or more separators) is the space between words."""
    def word(chunk: str) -> str:
        return re.sub(r"(?<=\b[A-Za-z])[ .\-_*|](?=[A-Za-z]\b)", "", chunk)
    parts = re.split(r"([ .\-_*|]{2,})", text)
    return "".join(word(p) if i % 2 == 0 else " " for i, p in enumerate(parts))


def scan(text) -> List[str]:
    """The instruction signals in `text` (see the module docstring). Never raises."""
    try:
        if not isinstance(text, str):
            text = str(text or "")
        t = text[:MAX_SCAN]
        if not t.strip():
            return []
        found = _kinds(t)
        seen = set(found)
        out = list(found)

        def add(kinds, how):
            for k in kinds:
                if k not in seen:
                    seen.add(k)
                    out.append("%s:%s" % (k, how))

        folded = _fold(t)
        if folded != t:
            add(_kinds(folded), "lookalike")
        unspaced = _unspace(folded)
        if unspaced != folded:
            add(_kinds(unspaced), "spaced")
        joined = re.sub(r"\n+", " ", _squash(unspaced))
        if joined != unspaced:
            add(_kinds(joined), "split")
        for s in _decoded_b64(t):
            add(_kinds(_fold(s)), "base64")
        for s in _decoded_hex(t):
            add(_kinds(_fold(s)), "hex")
        if not found:
            add(_kinds(codecs.decode(folded, "rot13")), "rot13")
        return out
    except Exception:
        return []


def describe(signals: List[str]) -> str:
    """Plain words for a card: what kind of instruction, and how it was hidden."""
    if not signals:
        return ""
    words = {"override": "telling her to ignore her rules", "role": "trying to give her a new role",
             "directive": "addressed to her as an instruction", "action_request": "asking her to act",
             "secrecy": "asking her to keep something from you", "trigger": "written to be acted on when read",
             "authority_claim": "claiming you already agreed or asking her to drop a safeguard",
             "action_hint": "asking her to send or delete something"}
    hides = {"lookalike": "look-alike letters", "spaced": "spaced-out letters", "split": "split across lines",
             "base64": "base64", "hex": "hex", "rot13": "rot13"}
    kinds, hidden = [], []
    for s in signals:
        k, _, h = s.partition(":")
        if words.get(k) and words[k] not in kinds:
            kinds.append(words[k])
        if h and hides.get(h) and hides[h] not in hidden:
            hidden.append(hides[h])
    text = "text " + " and ".join(kinds[:2]) if kinds else "instruction-shaped text"
    if hidden:
        text += " (hidden with %s)" % ", ".join(hidden)
    return text
