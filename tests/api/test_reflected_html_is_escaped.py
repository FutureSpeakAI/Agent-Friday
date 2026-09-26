"""HTML pages built by Flask routes never carry request data unescaped.

Covered: the Google OAuth callback's error text, the /w/<workspace> tab
marker, and the framed /creation/<file> page (its file name, the Host header
behind its return link, and a Markdown document embedded in a <script>).
"""
from __future__ import annotations

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PAYLOAD = "<script>alert(1)</script>"


def test_google_callback_error_is_escaped(client):
    r = client.get("/api/google/auth/callback", query_string={"error": PAYLOAD},
                   environ_base=LOCAL)
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert PAYLOAD not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_workspace_name_with_trailing_newline_is_refused(client):
    assert client.get("/w/news%0A", environ_base=LOCAL).status_code == 404


def test_creation_frame_escapes_host_and_file_name(client, creations_dir):
    (creations_dir / "a b&c.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.get("/creation/a b&c.png", environ_base=LOCAL,
                   headers={"Host": 'localhost"onmouseover="alert(1)'})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'src="/api/creations/a%20b%26c.png"' in body
    assert '"onmouseover="alert(1)' not in body


def test_creation_frame_markdown_cannot_close_its_script(client, creations_dir):
    (creations_dir / "note.md").write_text("# hi\n</script>" + PAYLOAD, encoding="utf-8")
    r = client.get("/creation/note.md", environ_base=LOCAL)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "</script>" + PAYLOAD not in body
    assert PAYLOAD not in body
