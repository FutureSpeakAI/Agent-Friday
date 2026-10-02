"""The Media workspace is wired into the served page and its mirror, and keeps
the rules the spec sets (docs/design/active/media-workspace.md §2).

The workspace lives in static/media_ws.js as a plain script, the Files 3D
precedent, so index.html and ui_parts/app.html each carry one mapping line.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "media_ws.js").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
SCENE = (ROOT / "ui_parts" / "styles_and_scene.html").read_text(encoding="utf-8")
REGISTRY = (ROOT / "static" / "workspace_registry.js").read_text(encoding="utf-8")


def test_the_script_is_loaded_by_the_page_and_its_mirror():
    tag = '<script src="/static/media_ws.js"></script>'
    assert INDEX.count(tag) == 1
    assert SCENE.count(tag) == 1


def test_the_workspace_is_mapped_in_the_page_and_its_mirror():
    assert "media: window.MediaWS ? with3D('media', 'media', /*#__PURE__*/React.createElement(window.MediaWS, null))" in INDEX
    assert "media:window.MediaWS?with3D('media','media',<window.MediaWS/>)" in APP


def test_the_calendar_workspace_shows_media_cards_as_a_layer():
    """Decision D6: Media's timed cards are one layer among the owner's events,
    with a toggle, in the page and its mirror."""
    for text in (INDEX, APP):
        assert "/api/media/calendar?from=" in text
        assert "friday_cal_media" in text and "cal-layers" in text
        assert "timedAll" in text and "openMediaCard" in text
    HEAD = (ROOT / "ui_parts" / "head.html").read_text(encoding="utf-8")
    for css in (INDEX, HEAD):
        assert ".cal-event.media { --ev:var(--fr-cat-blue); }" in css


def test_the_library_has_a_3d_layout_and_the_records_view_knows_media():
    """Decision D4: Files 3D is a layout of the Library, and "View in 3D" shows the cards."""
    assert "window.MediaFiles3D = MediaFiles3D" in JS and "root: 'creations'" in JS and "fill: true" in JS
    assert "R.SOURCES.media = {" in JS and "window.__mediaRegister3D = registerSource" in JS
    assert "onClick: () => setLayout('3d') }, '3D')" in JS


def test_the_registry_declares_media_as_a_core_work_workspace():
    m = re.search(r'\{"id": "media",[^\n]*', REGISTRY)
    assert m, "media is in the registry"
    assert '"group": "work"' in m.group(0) and '"core": true' in m.group(0)
    assert (ROOT / "assets" / "icons" / "media.svg").exists()


def test_draft_content_and_studio_are_retired_into_media():
    """The three workspaces fold into Media (decision D1, 2026-09-30): their
    registry entries are gone, their words resolve to Media, their old tab
    URLs redirect, and the page maps their ids onto Media's views."""
    for old in ("draft", "content", "studio"):
        assert not re.search(r'\{"id": "%s",' % old, REGISTRY), old + " is no longer a workspace"
    media = re.search(r'\{"id": "media",.*?\]\}', REGISTRY, re.S).group(0)
    for word in ("draft", "drafts", "content", "posts", "studio", "creations", "gallery", "writer"):
        assert '"%s"' % word in media, word + " resolves to Media"
    for text in (INDEX, APP):
        for old, view in (("draft", "board"), ("content", "board"), ("studio", "library")):
            assert re.search(r"%s:\s*\{\s*workspace:\s*'media',\s*view:\s*'%s'" % (old, view), text), old
    routes = (ROOT / "src" / "agent_friday" / "routes" / "core_routes.py").read_text(encoding="utf-8")
    for old in ("draft", "content", "studio"):
        assert "'%s': ('media'" % old in routes, "/w/" + old + " redirects to Media"
    # accounts live under Settings, analytics under Media → Insights
    assert "window.MediaChannelsSettings" in INDEX and "window.MediaChannelsSettings" in APP
    assert "window.MediaInsights = MediaInsights" in JS


def test_the_script_defines_the_workspace_and_its_views():
    for name in ("window.MediaWS = MediaWS", "window.MediaBoard = MediaBoard", "window.MediaCalendar = MediaCalendar", "window.MediaCard = MediaCard"):
        assert name in JS
    assert "push(['media', {" in JS, "the views are declared for navigation"


def test_the_five_statuses_are_the_one_vocabulary():
    m = re.search(r"const STATUSES = \[\s*(.*?)\];", JS, re.S)
    assert m
    ids = re.findall(r"\['(\w+)', '[^']+'\]", m.group(1))
    assert ids == ["idea", "draft", "review", "scheduled", "published"]


def test_kept_is_the_sixth_word_and_not_a_lane():
    """A thing made and kept on this PC is "kept": it has a word, a pill and a
    rail view, and the board's five lanes stay five."""
    assert "const KEPT = ['kept', 'Kept']" in JS
    assert "STATUS_WORD = Object.fromEntries(STATUSES.concat([KEPT]))" in JS
    assert "['kept', 'Kept here']" in JS
    assert "kept: 'md-pill-neutral'" in JS


def test_every_card_shows_a_preview_and_its_details_and_the_provenance_renders():
    """A thumbnail for every kind (video scrubs on hover, audio plays in place,
    a type with no picture shows its icon and size), one line of facts on the
    card, and the side panel's provenance block: made with, prompt, sources."""
    assert "function Thumb({ c, onQuick })" in JS
    assert "backgroundImage: 'url(' + c.strip + ')'" in JS, "the hover scrub reads the frame strip"
    assert "togglePlay(c)" in JS and "new Audio()" in JS, "audio plays in place, one player for the workspace"
    assert "className: 'ico' }, glyph(c.kind, '')" in JS and "fmtBytes(d.bytes)" in JS
    assert "className: 'md-facts'" in JS
    assert "'data-provenance': 'true'" in JS
    for word in ("'Made with'", "'Prompt'", "'From'", "'Created'", "'Modified'", "'Dimensions'", "'Duration'", "'Pages'", "'Size'"):
        assert word in JS, word + " is a row in the details"
    assert "d.prompt ? [h('dt', { key: 'pt' }, 'Prompt')" in JS


def test_the_quick_look_opens_on_space_and_walks_with_the_arrows():
    assert "function QuickLook({ c, cards, setSel, onClose, onOpen })" in JS
    assert "if (e.key === ' ' && selCard) { e.preventDefault(); setQl(true); return; }" in JS
    assert "e.key === 'ArrowRight' && i < ids.length - 1" in JS and "e.key === 'ArrowLeft' && i > 0" in JS
    assert "sandbox: ''" in JS, "a page opens in a sandboxed frame"
    assert "act('open')" in JS and "act('reveal')" in JS and "'Open in app'" in JS and "'Show in folder'" in JS
    routes = (ROOT / "src" / "agent_friday" / "routes" / "media.py").read_text(encoding="utf-8")
    for r in ("/api/media/<card_id>/preview", "/api/media/<card_id>/strip", "/api/media/<card_id>/open", "/api/media/<card_id>/reveal", "/api/media/previews/status"):
        assert r in routes
    assert "setTimeout(reload, 3000)" in JS, "the list asks again while previews are still being made"


def test_a_routine_episode_card_opens_in_news():
    """The routine shows' episodes are listed so nothing is missing; opening one
    goes to its show's tab in News, never into Media's editor (spec §4.8)."""
    assert "const NEWS_TAB = { front_page: 'frontpage', briefing: 'briefings', weekly: 'weekly', editorial: 'editorial' }" in JS
    assert "window.fridayNavigate({ workspace: 'news', tab: NEWS_TAB[routine] || 'frontpage' })" in JS
    assert re.search(r"id: 'frontpage'", INDEX) and re.search(r"id: 'briefings'", INDEX)


def test_the_library_says_it_is_indexing_and_never_shows_a_silent_empty_grid():
    """While the index builds the grid says so with a count, and asks again
    until it is done; the server's list carries that state."""
    assert "Indexing your library" in JS
    assert "state.indexing.state === 'indexing'" in JS
    assert "(state.indexing.indexed || 0) + ' so far." in JS
    assert "setTimeout(reload, 1500)" in JS
    routes = (ROOT / "src" / "agent_friday" / "routes" / "media.py").read_text(encoding="utf-8")
    assert 'mi.ensure_fresh("open")' in routes and 'res["indexing"] = indexing' in routes
    server = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "_media_index.start_background()" in server, "the index builds at boot, beside the other daemons"


def test_the_root_fills_the_frame_and_sets_no_width_of_its_own():
    assert "className: 'md-root ws-fill'" in JS
    root_rule = re.search(r"\.md-root\{([^}]*)\}", JS).group(1)
    assert not re.search(r"(^|;)(max-)?width:", root_rule), "the root sets no width; the shell owns the frame"
    assert "100vh" not in JS, "a workspace never measures the viewport; the shell pads for the bar and the tray"


def test_amber_only_means_needs_you():
    css = "\n".join(re.findall(r"const \w*CSS = `(.*?)`;", JS, re.S))
    for line in css.splitlines():
        if "f59e0b" in line or "--fr-warn" in line or "245,158,11" in line:
            assert "needs-you" in line or ".md-ev.review" in line, line
    # Hex literals: only the stage's black; every other colour is a token or a token's rgba.
    assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)) <= {"#000", "#fff"}, "the stage is black and a page frame white; every other colour is a token"


def test_publishing_never_happens_in_the_page():
    """Every move into Published is a server call; the page never sets the status itself."""
    assert "status: 'published' }" not in JS
    assert "/publish'" in JS


def test_a_post_card_opens_compose_on_the_post_itself():
    """Compose takes a post id and fetches the post; the card hands it that id."""
    assert "prefillPost: c.source_ref" in JS
    assert re.search(r"setPostId\(prefillPost\);\s*fetch\('/api/content/posts/' \+ prefillPost\)", INDEX)


def test_a_selected_segment_is_the_brand_control():
    assert "className: 'btn' + (view === v[0] && !card ? ' active' : '')" in JS
    assert "'aria-pressed': view === v[0] && !card" in JS
