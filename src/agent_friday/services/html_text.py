"""Readable text out of HTML, without a parser dependency.

This is the fallback when BeautifulSoup is missing and the one path for a
stored briefing or a voice-context page. Its output is plain text for a model
or for speech; nothing it returns is rendered as HTML.

Two properties the regexes it replaces did not have:

  * Script and style bodies are dropped however the tags are written:
    any case, attributes on either tag, whitespace or junk before the ">" of
    the end tag (`</script >`, `</SCRIPT foo>`). An unterminated script or
    style runs to the end of the document, as it does in a browser.
  * Linear time. It is a single forward scan, so a page full of "<" or "<!--"
    costs one pass. The standard library's HTMLParser is not used for the same
    reason: on some inputs it is quadratic.
"""
from __future__ import annotations

import html as _html
import re

# A start or end tag: "<" or "</", then a letter.
_TAG_START = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:-]*)")

# Elements whose content is never text: everything up to the end tag is
# dropped. An end tag's name ends at whitespace, "/" or ">".
_RAWTEXT_END = {
    name: re.compile(r"</%s(?=[\s/>])" % name, re.IGNORECASE | re.ASCII)
    for name in ("script", "style")
}

_WS = re.compile(r"\s+")


def html_to_text(markup: str) -> str:
    """Visible text of `markup`, whitespace collapsed to single spaces.

    Tags become word breaks, comments and script/style bodies disappear, and
    character references (&amp;, &#39;, &nbsp;...) are decoded.
    """
    if not markup:
        return ""
    s = str(markup)
    n = len(s)
    out: list[str] = []
    i = 0
    while i < n:
        lt = s.find("<", i)
        if lt == -1:
            out.append(s[i:])
            break
        out.append(s[i:lt])
        if s.startswith("<!--", lt):
            end = s.find("-->", lt + 4)
            i = n if end == -1 else end + 3
            out.append(" ")
            continue
        m = _TAG_START.match(s, lt)
        if m is None and not s.startswith(("<!", "<?"), lt):
            out.append("<")            # a bare "<" in text, as in "a < b"
            i = lt + 1
            continue
        gt = s.find(">", lt + 1)
        if gt == -1:
            break                      # an unterminated tag runs to the end
        i = gt + 1
        out.append(" ")
        if m is not None and not m.group(1):
            end_re = _RAWTEXT_END.get(m.group(2).lower())
            if end_re is not None:
                e = end_re.search(s, i)
                if e is None:
                    break              # unterminated script/style: drop the rest
                close = s.find(">", e.end())
                i = n if close == -1 else close + 1
    text = _html.unescape("".join(out))
    return _WS.sub(" ", text).strip()
