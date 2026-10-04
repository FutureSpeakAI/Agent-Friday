"""The hosted adapters: Cloudflare Pages and GitHub Pages, on the user's own
account (docs/design/active/vibe-coding-salon.md §4.10.1).

Every request goes through one HTTP function that these tests replace with
a fake; nothing here touches the network. A deployment is a full snapshot of
every page published through that adapter, kept in a local mirror, so
taking one page down is a redeploy without it. The token travels only in
the Authorization header and never appears in a URL, a message or a log.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from agent_friday.services import publish_adapters as pa
from agent_friday.services import publish_web as pw

TOKEN = "test-token-not-real-abc123"  # pragma: allowlist secret


class Fake:
    """Canned HTTP. Records every call; answers by (method, url suffix)."""

    def __init__(self):
        self.calls = []
        self.answers = []          # list of (predicate, status, body)

    def on(self, method, contains, status=200, body=None):
        self.answers.append((method, contains, status, body if body is not None else {}))

    def __call__(self, method, url, *, headers=None, json_body=None, data=None, files=None, timeout=30):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                           "json": json_body, "data": data, "files": files})
        for m, contains, status, body in self.answers:
            if m == method and contains in url:
                return status, body, json.dumps(body)
        return 404, {"message": "no canned answer for %s %s" % (method, url)}, ""


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = Fake()
    monkeypatch.setattr(pa, "_http", f)
    monkeypatch.setattr(pa, "_mirror_root", lambda: tmp_path / "published-remote")
    return f


def _bundle(slug="letter", text="<h1>hi</h1>", version=1):
    return pw.Bundle(files={"index.html": text.encode(), "source.md": b"# hi\n"}, title=slug.title(),
                     slug=slug, kind="markdown", artifact_id="art-1", conversation_id="conv-1", version=version)


# ── Cloudflare Pages ─────────────────────────────────────────────────────────

CF = {"token": TOKEN, "account_id": "acct1", "project": "friday-pages"}


def test_cloudflare_creates_the_project_once_and_deploys_the_whole_site(fake):
    fake.on("GET", "/pages/projects/friday-pages", 404, {"success": False, "errors": [{"message": "not found"}]})
    fake.on("POST", "/pages/projects", 200, {"success": True, "result": {"name": "friday-pages", "subdomain": "friday-pages.pages.dev"}})
    fake.on("POST", "/pages/projects/friday-pages/deployments", 200, {"success": True, "result": {"url": "https://abc123.friday-pages.pages.dev"}})
    url = pa.publish("cloudflare_pages", _bundle(), CF)
    assert url == "https://friday-pages.pages.dev/letter/"
    methods = [(c["method"], c["url"].split("/accounts/acct1")[-1]) for c in fake.calls]
    assert methods[0] == ("GET", "/pages/projects/friday-pages")
    assert methods[1][0] == "POST" and methods[1][1] == "/pages/projects"
    assert fake.calls[1]["json"]["name"] == "friday-pages"
    dep = fake.calls[2]
    assert dep["method"] == "POST" and dep["url"].endswith("/pages/projects/friday-pages/deployments")
    manifest = json.loads(dep["data"]["manifest"])
    assert set(manifest) == {"/letter/index.html", "/letter/source.md"}
    assert manifest["/letter/index.html"] == hashlib.sha256(b"<h1>hi</h1>").hexdigest()
    assert set(dep["files"]) == {"/letter/index.html", "/letter/source.md"}
    for c in fake.calls:
        assert c["headers"].get("Authorization") == "Bearer " + TOKEN
        assert TOKEN not in c["url"]


def test_cloudflare_a_second_page_redeploys_both_and_take_down_redeploys_without_it(fake):
    fake.on("GET", "/pages/projects/friday-pages", 200, {"success": True, "result": {"name": "friday-pages"}})
    fake.on("POST", "/pages/projects/friday-pages/deployments", 200, {"success": True, "result": {"url": "https://x.friday-pages.pages.dev"}})
    pa.publish("cloudflare_pages", _bundle("letter"), CF)
    pa.publish("cloudflare_pages", _bundle("chart", "<svg/>"), CF)
    dep = [c for c in fake.calls if c["url"].endswith("/deployments")][-1]
    assert set(json.loads(dep["data"]["manifest"])) >= {"/letter/index.html", "/chart/index.html"}
    pa.unpublish("cloudflare_pages", "letter", CF)
    dep = [c for c in fake.calls if c["url"].endswith("/deployments")][-1]
    paths = set(json.loads(dep["data"]["manifest"]))
    assert "/chart/index.html" in paths and not any(p.startswith("/letter/") for p in paths)
    # Taking the last page down leaves a placeholder, never an empty deployment.
    pa.unpublish("cloudflare_pages", "chart", CF)
    dep = [c for c in fake.calls if c["url"].endswith("/deployments")][-1]
    assert set(json.loads(dep["data"]["manifest"])) == {"/index.html"}


def test_cloudflare_errors_are_plain_and_never_carry_the_token(fake):
    fake.on("GET", "/pages/projects/friday-pages", 200, {"success": True, "result": {}})
    fake.on("POST", "/deployments", 403, {"success": False, "errors": [{"code": 8000, "message": "Authentication error"}]})
    with pytest.raises(pa.AdapterError) as ei:
        pa.publish("cloudflare_pages", _bundle(), CF)
    assert "Cloudflare Pages" in str(ei.value) and "403" in str(ei.value) and "Authentication error" in str(ei.value)
    assert TOKEN not in str(ei.value)


# ── GitHub Pages ─────────────────────────────────────────────────────────────

GH = {"token": TOKEN, "repo": "alex/pages", "branch": "gh-pages"}


def test_github_first_publish_creates_the_branch_and_enables_pages(fake):
    fake.on("GET", "/git/ref/heads/gh-pages", 404, {"message": "Not Found"})
    fake.on("POST", "/git/blobs", 201, {"sha": "blob0"})
    fake.on("POST", "/git/trees", 201, {"sha": "tree0"})
    fake.on("POST", "/git/commits", 201, {"sha": "commit0"})
    fake.on("POST", "/git/refs", 201, {"ref": "refs/heads/gh-pages"})
    fake.on("POST", "/pages", 201, {"html_url": "https://alex.github.io/pages/"})
    url = pa.publish("github_pages", _bundle(), GH)
    assert url == "https://alex.github.io/pages/letter/"
    blobs = [c for c in fake.calls if c["url"].endswith("/git/blobs")]
    assert len(blobs) == 3, "index.html, source.md and .nojekyll"
    assert all(c["json"]["encoding"] == "base64" for c in blobs)
    assert base64.b64decode(blobs[0]["json"]["content"]) in (b"<h1>hi</h1>", b"# hi\n", b"")
    tree = [c for c in fake.calls if c["url"].endswith("/git/trees")][-1]["json"]
    assert "base_tree" not in tree, "an orphan branch starts from an empty tree"
    assert {e["path"] for e in tree["tree"]} == {"letter/index.html", "letter/source.md", ".nojekyll"}
    commit = [c for c in fake.calls if c["url"].endswith("/git/commits")][-1]["json"]
    assert commit["parents"] == [] and "letter" in commit["message"]
    ref = [c for c in fake.calls if c["url"].endswith("/git/refs")][-1]["json"]
    assert ref == {"ref": "refs/heads/gh-pages", "sha": "commit0"}
    pages = [c for c in fake.calls if c["url"].endswith("/pages") and c["method"] == "POST"]
    assert pages and pages[0]["json"]["source"] == {"branch": "gh-pages", "path": "/"}
    for c in fake.calls:
        assert c["headers"].get("Authorization") == "Bearer " + TOKEN
        assert TOKEN not in c["url"]


def test_github_later_publishes_build_on_the_branch_and_take_down_deletes_the_paths(fake):
    fake.on("GET", "/git/ref/heads/gh-pages", 200, {"object": {"sha": "head0"}})
    fake.on("GET", "/git/commits/head0", 200, {"sha": "head0", "tree": {"sha": "basetree"}})
    fake.on("POST", "/git/blobs", 201, {"sha": "blobX"})
    fake.on("POST", "/git/trees", 201, {"sha": "treeX"})
    fake.on("POST", "/git/commits", 201, {"sha": "commitX"})
    fake.on("PATCH", "/git/refs/heads/gh-pages", 200, {"object": {"sha": "commitX"}})
    fake.on("POST", "/pages", 409, {"message": "already enabled"})
    pa.publish("github_pages", _bundle("letter"), GH)
    tree = [c for c in fake.calls if c["url"].endswith("/git/trees")][-1]["json"]
    assert tree["base_tree"] == "basetree"
    commit = [c for c in fake.calls if c["url"].endswith("/git/commits")][-1]["json"]
    assert commit["parents"] == ["head0"]
    patch = [c for c in fake.calls if c["method"] == "PATCH"][-1]
    assert patch["url"].endswith("/git/refs/heads/gh-pages") and patch["json"] == {"sha": "commitX", "force": False}
    n = len(fake.calls)
    pa.unpublish("github_pages", "letter", GH)
    tree = [c for c in fake.calls[n:] if c["url"].endswith("/git/trees")][-1]["json"]
    deleted = {e["path"]: e for e in tree["tree"] if e.get("sha") is None}
    assert set(deleted) == {"letter/index.html", "letter/source.md"}
    assert all(e["mode"] == "100644" and e["type"] == "blob" for e in deleted.values())


def test_github_user_site_repo_has_no_repo_segment_in_the_url(fake):
    fake.on("GET", "/git/ref/heads/gh-pages", 200, {"object": {"sha": "h"}})
    fake.on("GET", "/git/commits/h", 200, {"tree": {"sha": "t"}})
    for m, suffix in (("POST", "/git/blobs"), ("POST", "/git/trees"), ("POST", "/git/commits")):
        fake.on(m, suffix, 201, {"sha": "s"})
    fake.on("PATCH", "/git/refs/heads/gh-pages", 200, {})
    fake.on("POST", "/pages", 409, {})
    url = pa.publish("github_pages", _bundle(), {"token": TOKEN, "repo": "Alex/alex.github.io", "branch": "gh-pages"})
    assert url == "https://alex.github.io/letter/"


def test_github_errors_are_plain_and_never_carry_the_token(fake):
    fake.on("GET", "/git/ref/heads/gh-pages", 401, {"message": "Bad credentials"})
    with pytest.raises(pa.AdapterError) as ei:
        pa.publish("github_pages", _bundle(), GH)
    assert "GitHub Pages" in str(ei.value) and "401" in str(ei.value) and "Bad credentials" in str(ei.value)
    assert TOKEN not in str(ei.value)


# ── the mirror and the hand-off from hosting ─────────────────────────────────

def test_the_local_mirror_holds_every_page_published_through_an_adapter(fake, tmp_path):
    fake.on("GET", "/pages/projects/friday-pages", 200, {"success": True, "result": {}})
    fake.on("POST", "/deployments", 200, {"success": True, "result": {}})
    pa.publish("cloudflare_pages", _bundle("letter"), CF)
    assert (tmp_path / "published-remote" / "cloudflare_pages" / "letter" / "index.html").read_bytes() == b"<h1>hi</h1>"
    pa.unpublish("cloudflare_pages", "letter", CF)
    assert not (tmp_path / "published-remote" / "cloudflare_pages" / "letter").exists()


def test_hosting_hands_remote_publishes_to_the_adapters(monkeypatch):
    from agent_friday.services import publish_hosting as ph
    seen = {}
    monkeypatch.setattr(ph, "connection", lambda adapter: {"token": TOKEN, "account_id": "a", "project": "p"} if adapter == "cloudflare_pages" else None)
    monkeypatch.setattr(pa, "publish", lambda adapter, bundle, conn: seen.setdefault("pub", (adapter, bundle.slug, conn["project"])) and "https://p.pages.dev/letter/")
    monkeypatch.setattr(pa, "unpublish", lambda adapter, slug, conn: seen.setdefault("unpub", (adapter, slug)))
    assert ph.publish_remote("cloudflare_pages", _bundle()) == "https://p.pages.dev/letter/"
    assert seen["pub"] == ("cloudflare_pages", "letter", "p")
    ph.unpublish_remote({"adapter": "cloudflare_pages", "slug": "letter"})
    assert seen["unpub"] == ("cloudflare_pages", "letter")
    with pytest.raises(RuntimeError):
        ph.publish_remote("github_pages", _bundle())      # not connected
