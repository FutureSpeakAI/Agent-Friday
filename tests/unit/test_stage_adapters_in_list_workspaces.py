"""Each list workspace plugs into See & Touch the same way: the stage layer loads first, the rows carry
data-fr-ref, an adapter registers on mount, filters go through the workspace's own state, and what is
private stays out of the stage (a vault document's title is never reported).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_stage_layer_loads_before_every_workspace_that_uses_it():
    for page in ("index.html", "ui_parts/styles_and_scene.html"):
        text = _read(page)
        stage = text.index('src="/static/friday_stage.js"')
        for later in ("media_ws.js", "library_ws.js", "friday_mail.js"):
            assert stage < text.index('src="/static/%s"' % later), (page, later)
        assert text.count("friday_stage.js") == 1 or page == "index.html", page


def test_the_registry_names_the_stage_layer_beside_each_list_workspaces_scripts():
    reg = _read("static/workspace_registry.js")
    for script in ("static/media_ws.js", "static/library_ws.js", "static/friday_mail.js"):
        assert re.search(r'"static/friday_stage\.js",\s*"%s"' % re.escape(script), reg), script


def test_each_list_registers_an_adapter_and_its_rows_carry_a_ref():
    mail = _read("static/friday_mail.js")
    assert "FS.register('messages'" in mail and "'data-fr-ref': rf" in mail
    media = _read("static/media_ws.js")
    assert "FS.register('media', FS.makeAdapter('media'" in media and "'data-fr-ref': 'media:' + c.id" in media
    lib = _read("static/library_ws.js")
    assert "FS.register('library', FS.makeAdapter('library'" in lib and "'lib:' + d.id" in lib
    for page in ("index.html", "ui_parts/app.html"):
        news = _read(page)
        assert "stageFS.register('news', stageFS.makeAdapter('news'" in news, page
        assert re.search(r"data-fr-ref['\"]?[:=]\s*\{?\s*'news:' ?\+ ?\(it\.id ?\|\| ?i\)", news), page


def test_the_news_adapter_is_the_same_in_the_compiled_page_and_its_jsx_mirror():
    def body(text):
        a = text.index("// See & Touch: the stories on screen")
        b = text.index("stageFS.touch(); }, [feed, streamCat, sortBy, tab]);")
        return re.sub(r"\s+", " ", text[a:b])
    assert body(_read("index.html")) == body(_read("ui_parts/app.html"))


def test_a_vault_document_is_never_reported_to_the_stage():
    lib = _read("static/library_ws.js")
    items = lib[lib.index("items: () => shown.filter("):lib.index("setFilter: (key, value) => {")]
    assert "d.shelf !== 'vault'" in items
    assert "'data-fr-ref': d.shelf === 'vault' ? undefined" in lib, "and a vault row has no ref to point at"


def test_every_filter_goes_through_the_workspaces_own_state():
    mail = _read("static/friday_mail.js")
    for needle in ("setLane(value)", "setUnreadOnly(value === '1')", "setQuery(value)", "pickFolder(value)", "setAcct(value || 'all')"):
        assert needle in mail, needle
    media = _read("static/media_ws.js")
    assert "setFilters(f => Object.assign({}, f, { view: 'all' }, { [key]: value }))" in media
    news = _read("index.html")
    assert "setStreamCat(hit)" in news and "setSortBy(value)" in news


def test_a_filter_friday_set_is_marked_and_the_owners_change_reads_as_the_owners():
    mail = _read("static/friday_mail.js")
    assert "const byOf = (key, val) => (fridayFilters.current[key] !== undefined && String(fridayFilters.current[key]) === String(val))" in mail
    assert "FS.chipsRow(h, currentFilters()" in mail


def test_the_stage_css_uses_tokens_only_and_the_page_loads_nothing_remote():
    js = _read("static/friday_stage.js")
    css = js[js.index("var STYLE = ["):js.index("].join('');")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css) and "rgb(" not in css
    assert "http://" not in js and "https://" not in js
