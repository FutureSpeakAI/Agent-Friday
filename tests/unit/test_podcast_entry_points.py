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


def test_the_player_moves_remembers_its_place_and_pops_out():
    b = _block(INDEX)
    assert "onPointerDown: startDrag" in b and "PODCAST_POS_KEY" in b and "localStorage.setItem(PODCAST_POS_KEY" in b
    assert "documentPictureInPicture" in b and "ReactDOM.createPortal(frame, popped.el)" in b
    # The popped window gets the page's styles, and fits its window.
    assert "querySelectorAll('style, link[rel=\"stylesheet\"]')" in b and "boxSizing: 'border-box'" in b
    assert _block(INDEX) == _block(APP)


def test_the_sourcing_is_synced_to_playback_and_scrubbable():
    b = _block(INDEX)
    assert "'aria-label': 'Now citing'" in b and "podcastSourceTimeline(ep)" in b
    assert "onClick: () => seek(s.at)" in b


def test_news_episodes_live_in_news_and_studio_keeps_the_owner_s_own():
    b = _block(INDEX)
    assert "eps.filter(ep => !ep.attached).map(ep =>" in b
    assert "PodcastChip" in INDEX           # each News run's episode sits with its edition


def test_news_opens_a_routine_episode_in_the_player():
    for src in (INDEX, APP):
        i = src.index("useNavTarget('news'")
        assert "fridayPodcast('open', t.episode)" in src[i:i + 600].replace("fridayPodcast('open',t.episode)",
                                                                             "fridayPodcast('open', t.episode)")
    assert "Studio → Podcasts" not in _block(INDEX)
