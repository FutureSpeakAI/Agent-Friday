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
