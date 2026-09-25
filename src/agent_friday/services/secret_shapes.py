"""The shapes credentials take, in one list the server and the browser share.

Two consumers:

* the setup chat's input guard. Anything typed into a chat box goes into a
  transcript and, often, to a model. A key pasted there by mistake must never
  leave the text box: the browser checks every message against these shapes
  BEFORE it sends, and offers to move the value into the secure field for the
  service it belongs to. The browser fetches this list from
  ``GET /api/setup-chat/secret-shapes`` rather than carrying its own copy.
* ``looks_like_secret`` on the server, which the setup chat also applies to
  every free-text answer it receives, as a second line: a client that skipped
  the guard still cannot land a key in the transcript.

The pre-commit scanner (``.githooks/security_scan.py``) keeps its own
``SHAPE_RULES`` because it runs outside the package; a unit test holds that
every shape it knows is also here, so the two cannot drift apart.

Patterns are written in the subset of regular-expression syntax that Python
and JavaScript read the same way: no inline flags, no lookbehind, no named
groups.
"""
from __future__ import annotations

import re

#: (id, human label, pattern, connection id the value most likely belongs to).
#: The connection id names an entry in services/setup_connections.py, or "" when
#: the shape does not say which service it is for.
SHAPES: tuple = (
    ("anthropic", "Anthropic API key",
     r"sk-ant-[A-Za-z0-9_\-]{20,}", "provider:anthropic"),
    ("openrouter", "OpenRouter API key",
     r"sk-or-(?:v1-)?[A-Za-z0-9_\-]{20,}", "provider:openrouter"),
    ("openai", "OpenAI / Anthropic / OpenRouter API key",
     r"sk-(?:ant-|or-(?:v1-)?|proj-)?[A-Za-z0-9_\-]{20,}", "provider:openai"),
    ("gemini", "Google/Gemini API key",
     r"AIza[0-9A-Za-z_\-]{35}", "provider:google-gemini"),
    ("gemini_aq", "Google AI Studio (AQ.) key",
     r"\bAQ\.[A-Za-z0-9_\-]{20,}", "provider:google-gemini"),
    ("google_client_secret", "Google OAuth client secret",
     r"\bGOCSPX-[A-Za-z0-9_\-]{20,}", ""),
    ("google_refresh", "Google OAuth refresh token",
     r"\b1//0[0-9A-Za-z_\-]{30,}", ""),
    ("google_access", "Google OAuth access token",
     r"\bya29\.[0-9A-Za-z_\-]{20,}", ""),
    ("aws", "AWS access key id",
     r"\bAKIA[0-9A-Z]{16}\b", ""),
    ("slack", "Slack token",
     r"\bxox[baprs]-[0-9A-Za-z\-]{10,}", "connector:slack"),
    ("github", "GitHub token",
     r"\b(?:gh[pousr]_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{40,})", "connector:github"),
    ("huggingface", "Hugging Face token",
     r"\bhf_[A-Za-z0-9]{30,}", "provider:huggingface"),
    ("groq_xai_pplx", "Groq / xAI / Perplexity key",
     r"\b(?:gsk_[A-Za-z0-9]{40,}|xai-[A-Za-z0-9]{40,}|pplx-[A-Za-z0-9]{40,})", ""),
    ("twilio", "Twilio API key or account SID",
     r"\b(?:SK|AC)[0-9a-f]{32}\b", "phone:twilio"),
    ("jwt", "JSON Web Token",
     r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", ""),
    ("private_key", "Private key block",
     r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----", ""),
    # Shapes the commit scanner does not need but a setup conversation does:
    # these are the keys the checklist asks for.
    ("elevenlabs", "ElevenLabs API key",
     r"\bsk_[0-9a-f]{40,}\b", "provider:elevenlabs"),
    ("firecrawl", "Firecrawl API key",
     r"\bfc-[0-9a-f]{32}\b", "provider:firecrawl"),
    ("telegram", "Telegram bot token",
     r"\b[0-9]{8,10}:[A-Za-z0-9_\-]{35}\b", "channel:telegram"),
    ("discord", "Discord bot token",
     r"\b[MNO][A-Za-z0-9_\-]{23,27}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}", "channel:discord"),
    ("linear", "Linear API key",
     r"\blin_api_[A-Za-z0-9]{30,}", "connector:linear"),
    ("notion", "Notion integration token",
     r"\b(?:secret_|ntn_)[A-Za-z0-9]{40,}", "connector:notion"),
)

#: A long unbroken run of letters and digits with no spaces, slashes or dots.
#: Catches keys whose vendor shape is not listed above (Brave, Inworld, a
#: custom endpoint). Deliberately conservative: a URL, a file path, a hash
#: someone is discussing inside a sentence all contain a separator or sit
#: among words, and a message that is ONLY such a run is almost always a
#: paste of a credential.
GENERIC = ("generic", "Something that looks like a key or token",
           r"^[A-Za-z0-9_\-]{32,}$", "")

_COMPILED = tuple((sid, label, re.compile(pat), target)
                  for sid, label, pat, target in SHAPES)
_GENERIC_RE = re.compile(GENERIC[2])


def looks_like_secret(text) -> dict | None:
    """The first shape `text` matches, as {id, label, target}, or None.

    Never returns the matched value: the caller only needs to know that the
    text must not travel onward, and which secure field to offer instead.
    """
    if not text:
        return None
    s = str(text)
    for sid, label, rx, target in _COMPILED:
        if rx.search(s):
            return {"id": sid, "label": label, "target": target}
    stripped = s.strip()
    if _GENERIC_RE.match(stripped) and any(c.isdigit() for c in stripped) \
            and any(c.isalpha() for c in stripped):
        return {"id": GENERIC[0], "label": GENERIC[1], "target": GENERIC[3]}
    return None


def for_client() -> dict:
    """The list as the browser needs it: patterns as strings, never values."""
    return {
        "shapes": [{"id": sid, "label": label, "pattern": pat, "target": target}
                   for sid, label, pat, target in SHAPES],
        "generic": {"id": GENERIC[0], "label": GENERIC[1],
                    "pattern": GENERIC[2], "target": GENERIC[3]},
    }
