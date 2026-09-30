"""A workspace customization is CSS, and only CSS.

The page puts a workspace's customization inside a <style> element of Friday's
own document, where a script would hold the session token. So whatever the
model (or a file on disk) says, what reaches that element must not be able to
close it, open a tag, or fetch from another host.
"""
import re

import pytest

from agent_friday.services import workspace_studio as ws

# Each survives a single non-recursive pass of "remove </style".
BREAKOUTS = [
    "<</style/style><script>alert(1)</script>",
    "</</style style>style><img src=x onerror=alert(1)>",
    "</STYLE ><script>x</script>",
    "</ /style><script>x</script>",
    "a{}<!--<script>x</script>-->",
    "<scr<script>ipt>x</script>",
    "</style\n><script>x</script>",
]


def _inert(text):
    """True when nothing in `text` could end a style element or open a tag."""
    return "<" not in text and not re.search(r"</\s*style", text, re.I)


@pytest.mark.parametrize("payload", BREAKOUTS)
def test_css_cannot_close_the_style_element(payload):
    assert _inert(ws._sanitize_css(".ws-custom-root{color:red}" + payload))


@pytest.mark.parametrize("payload", BREAKOUTS)
def test_hidden_selectors_cannot_close_the_style_element(payload):
    out = ws._sanitize_patch({"hidden": [".card", "x" + payload]})
    assert all(_inert(s) for s in out["hidden"]), out["hidden"]
    assert all("{" not in s and "}" not in s for s in out["hidden"])


def test_hidden_selector_cannot_add_a_rule():
    out = ws._sanitize_patch({"hidden": [".a{background:url(https://evil.example/x)}"]})
    assert out["hidden"] == [] or all("{" not in s for s in out["hidden"])


@pytest.mark.parametrize("css", [
    ".ws-custom-root{background:url(https://evil.example/p.png)}",
    ".ws-custom-root{background:url( 'http://evil.example/p.png' )}",
    ".ws-custom-root{background:URL(//evil.example/p.png)}",
    ".ws-custom-root{background-image:image-set('https://evil.example/p.png' 1x)}",
])
def test_css_cannot_fetch_from_another_host(css):
    out = ws._sanitize_css(css)
    assert "evil.example" not in out, out


def test_local_data_images_still_work():
    css = ".ws-custom-root{background:url(data:image/png;base64,AAAA)}"
    assert "data:image/png;base64,AAAA" in ws._sanitize_css(css)


def test_child_combinators_survive():
    css = ".ws-custom-root > .card { color: #0ff }"
    assert ">" in ws._sanitize_css(css)


def test_accent_cannot_carry_a_trailing_payload():
    assert "accent" not in ws._sanitize_patch({"accent": "#00d4ff\n</style><script>x</script>"})


def test_stored_customizations_are_sanitized_when_read(tmp_path, monkeypatch):
    """A doc written before the sanitizer was strict is cleaned on the way out."""
    import json
    monkeypatch.setattr(ws, "WS_STUDIO_DIR", tmp_path)
    (tmp_path / "news.json").write_text(json.dumps({
        "workspace": "news",
        "customization": {"css": BREAKOUTS[0], "hidden": ["a" + BREAKOUTS[1]]},
    }), encoding="utf-8")
    cust = ws.all_customizations()["news"]
    assert _inert(cust.get("css", ""))
    assert all(_inert(s) for s in cust.get("hidden", []))
