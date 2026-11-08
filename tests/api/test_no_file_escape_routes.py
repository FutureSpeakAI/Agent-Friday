"""No route that serves a file by a caller-chosen name serves one outside its folder (NS-34.17-5).

The model-facing file tools are held by path_policy (tests/unit/test_file_confinement.py). This
is the other side: the HTTP routes that hand a file back for a name or path in the URL. Each is
asked for a file that sits outside every folder it serves (a planted marker under the test's temp
folder) by every spelling that has walked around a path rule before: '..', backslashes,
percent-encoded and double-encoded dots and separators, an absolute path, a doubled separator,
a drive letter, a null byte. None may return the marker, whatever the status. The marker file is
synthetic.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import pytest

MARKER = "synthetic-outside-marker-91c2e7"

#: Routes that take the file name or path from the URL.
PATH_ROUTES = [
    "/static/{p}",
    "/assets/{p}",
    "/api/creations/{p}",
    "/briefing/{p}",
    "/api/content/drafts/{p}",
    "/api/audio/{p}",
]
QUERY_ROUTES = [
    "/api/studio-files/raw?root=&path={p}",
    "/api/studio-files/raw?root=creations&path={p}",
    "/api/studio-files/raw?root=home&path={p}",
]


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    f = d / "secret.txt"
    f.write_text(MARKER, encoding="utf-8")
    return f


def _spellings(f):
    """The ways of naming `f` from inside some folder deeper than the root of its drive."""
    full = str(f.resolve())
    nodrive = os.path.splitdrive(full)[1].replace("\\", "/").lstrip("/")
    up = "../" * 24
    out = {
        "dotdot": up + nodrive,
        "backslash": up.replace("/", "\\") + nodrive.replace("/", "\\"),
        "mixed": "..%5c" * 24 + nodrive,
        "encoded dots": "%2e%2e/" * 24 + nodrive,
        "encoded separators": "..%2f" * 24 + nodrive,
        "double encoded": "%252e%252e%252f" * 24 + nodrive,
        "doubled separators": "....//" * 24 + nodrive,
        "absolute": "/" + nodrive,
        "absolute encoded": "%2f" + nodrive,
        "double slash": "//" + nodrive,
        "full path": full.replace("\\", "/"),
        "full path backslashes": full,
        "drive letter": "C:/" + nodrive,
        "null byte": up + nodrive + "%00.png",
        "trailing dot": up + nodrive + ".",
        "dotdot after a real name": "static/" + up + nodrive,
    }
    return out


def _get(client, url):
    try:
        return client.get(url)
    except Exception:       # a spelling the server refuses outright is not a leak
        return None


def _assert_no_marker(resp, how, url):
    if resp is None:
        return
    body = resp.get_data()
    assert MARKER.encode() not in body, f"{how}: {url} returned the file outside its folder"
    resp.close()


@pytest.mark.parametrize("route", PATH_ROUTES)
def test_no_spelling_of_an_outside_path_is_served_by_a_path_route(client, outside, route):
    for how, spelling in _spellings(outside).items():
        for form in (spelling, quote(spelling, safe="/%")):
            url = route.format(p=form)
            _assert_no_marker(_get(client, url), how, url)


@pytest.mark.parametrize("route", QUERY_ROUTES)
def test_no_spelling_of_an_outside_path_is_served_by_the_studio_file_route(client, outside, route):
    for how, spelling in _spellings(outside).items():
        url = route.format(p=quote(spelling, safe=""))
        _assert_no_marker(_get(client, url), how, url)


def test_the_marker_is_really_there(outside):
    """The sweep proves something only if the file it asks for exists."""
    assert outside.read_text(encoding="utf-8") == MARKER


def test_the_routes_do_serve_a_file_inside_their_folder(client):
    """A control: the sweep above is not passing only because every request is refused."""
    resp = client.get("/static/favicon.ico")
    assert resp.status_code == 200, resp.status_code
    resp.close()
