"""A podcast can be made from the items every workspace shows, not only News.

Holds the entry points in the served page (index.html) and, where the
component exists there too, its ui_parts/app.html mirror, so a large UI
change elsewhere cannot silently drop one.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")


def test_a_wiki_page_and_a_graph_entry_have_the_button_in_knowledge():
    for src in (INDEX, APP):
        assert "h(PodcastButton, {\n    sources: [{ kind: 'wiki', path: path }]" in src
        assert "sources: [{ kind: 'kg_node', id: sel.id }]" in src


def test_a_studio_creation_has_the_button_and_a_spreadsheet_goes_in_as_data():
    assert "React.createElement(PodcastButton, {\n      sources: [podcastCreationSource(selected.name)]" in INDEX
    assert "<PodcastButton sources={[podcastCreationSource(selected.name)]}" in APP
    assert "['csv', 'tsv', 'xlsx', 'xlsm', 'parquet'].includes(ext)" in INDEX


def test_the_chat_conversation_has_the_button():
    assert "sources: [{ kind: 'conversation', id: convId }]" in INDEX


def test_send_to_news_and_the_player_are_still_there():
    for src in (INDEX, APP):
        assert "dest: 'podcast'" in src or "dest:'podcast'" in src
        assert "PodcastChip" in src and "function PodcastPlayer(" in src


def _block(src):
    return src[src.index("// ═══ PODCASTS — player"):src.index("const NEWS_PODCAST_ROUTINE = {")]


def test_source_chips_are_titled_and_spaced_never_a_bare_id():
    b = _block(INDEX)
    assert "h(React.Fragment, { key: c }, ' ', h('button'" in b
    assert "podcastSourceLabel(srcById[c], c)" in b and "}, c);" not in b


def test_the_listening_check_is_not_named_as_an_editorial_pass():
    b = _block(INDEX)
    assert "checked by ear" not in b
    assert "'✓ audio matches script'" in b and "This checks the audio, not the writing." in b
    assert "'✓ script checked'" in b and "'⚠ script: '" in b


def test_the_transcript_lists_its_linked_sources():
    b = _block(INDEX)
    assert "'aria-label': 'Sources'" in b and "podcastLinked(ep).map(" in b
    assert "cited.has(s.id)" in b          # only the stories heard, not every one offered


def test_who_is_on_each_show_is_a_setting_in_studio_with_the_recommendation_shown():
    b = _block(INDEX)
    assert "'Who is on each show'" in b and "' (recommended)'" in b
    assert "fetch('/api/podcasts/formats', { method: 'PUT'" in b
