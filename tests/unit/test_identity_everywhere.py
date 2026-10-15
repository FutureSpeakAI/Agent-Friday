"""Her name and voice follow her identity everywhere (field install report, items
P1 #9 and P2 #11).

On an install whose owner named her (a synthetic "Agent Juniper" here):

  * the weekly editorial's directive says her name, not "You are Agent
    Friday writing...";
  * a podcast's host is her, by name and in her voice, when the owner never
    set the show's host (absent or blank in the saved settings): the intro
    says "I'm Agent Juniper", never "I'm Friday". A host or local voice the
    owner saved is theirs, even one equal to an old default ("Friday",
    af_heart);
  * the host brief uses the pronouns her voice gives her (am_* he/him, af_*
    she/her, an unknown voice they/them), and the owner's override wins;
  * with no local voice chosen and a cloud voice chosen, the local voice is
    the cloud voice's twin (Puck -> am_puck) when it is installed;
  * podcasts are one voice by default, and one episode can ask for two;
  * the page's wordmark and tab titles read her name through the name helper.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
NAME = "AGENT JUNIPER"
SAID = "Agent Juniper"

#: Saved settings of an install whose owner named her and chose a cloud voice,
#: but never chose a local voice nor set the show's host (blank, as the
#: defaults seed it). The old "any" format is in the saved file.
SEEDED = {
    "agent_name": NAME,
    "tts_voice": "Puck",
    "local_voice_kokoro_voice": "",
    "podcasts": {"hosts": {"a": {"name": "", "voice": ""},
                           "b": {"name": "Emma", "voice": "bf_emma"}},
                 "format": {"briefing": "solo", "front_page": "solo", "editorial": "solo",
                            "weekly": "duo", "any": "duo"}},
}


@pytest.fixture
def named_install(monkeypatch, tmp_path):
    import agent_friday.core as core
    from agent_friday.services import podcast_engine as pe
    from agent_friday.services import podcast_render
    saved = {"s": json.loads(json.dumps(SEEDED))}
    # The raw file agrees with what the loader returns (agent_identity reads
    # it raw to tell the owner's choice from a default).
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(saved["s"]), encoding="utf-8")

    def save(delta):
        saved["s"].update(json.loads(json.dumps(delta)))
        path.write_text(json.dumps(saved["s"]), encoding="utf-8")

    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", path)
    monkeypatch.setattr(core, "_load_settings", lambda: saved["s"])
    monkeypatch.setattr(core, "_save_settings", save)
    monkeypatch.setattr(podcast_render, "voice_installed", lambda v: True)
    monkeypatch.setattr(pe, "wake", lambda: None)
    return saved


# ── the editorial ────────────────────────────────────────────────────────────

def test_the_editorial_directive_says_her_name(named_install):
    from agent_friday.services import news_engine as ne
    fn = getattr(ne, "editorial_directive", None)
    text = fn() if fn else ne.EDITORIAL_SYSTEM_PROMPT
    assert text.startswith("You are %s writing your weekly editorial." % SAID), text[:80]
    assert "Agent Friday" not in text


# ── podcasts ─────────────────────────────────────────────────────────────────

def test_the_podcast_host_is_her_when_the_owner_never_set_one(named_install):
    from agent_friday.services import podcast_engine as pe
    hosts = pe.settings()["hosts"]
    assert hosts["a"]["name"] == SAID
    ep = {"show": "The Week", "hosts": hosts, "format": "solo", "attached": {"routine": "weekly"}}
    opening, closing = pe.signature_lines(ep)
    spoken = " ".join(x["text"] for x in opening + closing)
    assert "I'm %s." % SAID in spoken
    assert "I'm Friday" not in spoken
    # the product is still credited, in plain words
    assert "from Agent Friday." in spoken


def test_the_podcast_host_speaks_in_her_matched_voice(named_install):
    from agent_friday.services import podcast_engine as pe
    assert pe.settings()["hosts"]["a"]["voice"] == "am_puck"


def test_the_host_brief_says_her_name_and_the_pronouns_of_her_voice(named_install):
    from agent_friday.services import podcast_engine as pe
    hosts = pe.settings()["hosts"]
    solo = pe._host_brief(hosts, "solo")
    assert SAID in solo and "Friday" not in solo
    words = set(re.findall(r"[a-z]+", solo.lower()))
    assert {"his", "he"} & words and not {"she", "her", "hers", "herself"} & words, solo
    duo = pe._host_brief(hosts, "duo")
    a_part = duo[:duo.index('Speaker "b"')]
    assert "He is calm and perceptive" in a_part and " she " not in a_part.lower()
    # the co-host's voice (bf_emma) is a woman's
    assert "she asks what a sharp listener" in duo


def test_the_owner_can_set_her_pronouns(named_install):
    from agent_friday.services import podcast_engine as pe
    named_install["s"]["identity"] = {"pronouns": "they/them"}
    solo = pe._host_brief(pe.settings()["hosts"], "solo")
    words = set(re.findall(r"[a-z]+", solo.lower()))
    assert "their" in words and not {"he", "his", "she", "her"} & words, solo


def test_a_host_the_owner_named_is_kept(named_install):
    from agent_friday.services import podcast_engine as pe
    named_install["s"]["podcasts"]["hosts"]["a"] = {"name": "Ada", "voice": "bf_alice"}
    a = pe.settings()["hosts"]["a"]
    assert a["name"] == "Ada" and a["voice"] == "bf_alice"


def _resave(named_install, **changes):
    """Change the saved settings the way a save would: loader and raw file."""
    from agent_friday import core
    named_install["s"].update(json.loads(json.dumps(changes)))
    core.SETTINGS_FILE.write_text(json.dumps(named_install["s"]), encoding="utf-8")


def test_a_host_saved_as_friday_in_af_heart_is_the_owners_and_is_kept(named_install):
    """"Friday" in af_heart is a host an owner can choose. Saved, it is
    theirs: not taken for a seed and replaced by her name and matched voice."""
    from agent_friday.services import podcast_engine as pe
    _resave(named_install, podcasts=dict(SEEDED["podcasts"], hosts={
        "a": {"name": "Friday", "voice": "af_heart"}, "b": {"name": "Emma", "voice": "bf_emma"}}))
    a = pe.settings()["hosts"]["a"]
    assert a["name"] == "Friday" and a["voice"] == "af_heart", a
    assert a["pronouns"] == "she/her"


def test_a_local_voice_saved_as_af_heart_is_the_owners_and_is_kept(named_install):
    """The owner picked af_heart as her local voice and Puck in the cloud:
    af_heart is their choice, not "the default, so match Puck"."""
    from agent_friday.services import agent_identity
    from agent_friday.services import podcast_engine as pe
    _resave(named_install, local_voice_kokoro_voice="af_heart")
    assert agent_identity.local_kokoro_voice() == "af_heart"
    assert agent_identity.local_kokoro_voice(dict(named_install["s"]), installed=lambda v: True) == "af_heart"
    assert pe.settings()["hosts"]["a"]["voice"] == "af_heart"


def test_owner_chose_reads_the_saved_file_not_the_merged_defaults(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import agent_identity
    path = tmp_path / "settings.json"
    monkeypatch.setattr(core, "SETTINGS_FILE", path)
    # whatever the loader merges in, the raw file decides
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "tts_voice": "Puck", "local_voice_kokoro_voice": "af_heart"})
    assert agent_identity.owner_chose("local_voice_kokoro_voice") is False      # no file
    path.write_text(json.dumps({"tts_voice": "Puck"}), encoding="utf-8")
    assert agent_identity.owner_chose("local_voice_kokoro_voice") is False      # absent
    path.write_text(json.dumps({"local_voice_kokoro_voice": "  "}), encoding="utf-8")
    assert agent_identity.owner_chose("local_voice_kokoro_voice") is False      # blank
    path.write_text(json.dumps({"local_voice_kokoro_voice": "af_heart"}), encoding="utf-8")
    assert agent_identity.owner_chose("local_voice_kokoro_voice") is True       # = default, still chosen


def test_a_fresh_install_seeds_the_local_voice_blank():
    """The seed and every save write the whole defaults table; a non-blank
    default would be indistinguishable from the owner's choice."""
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["local_voice_kokoro_voice"] == ""
    assert DEFAULT_SETTINGS["podcasts"]["hosts"]["a"] == {"name": "", "voice": ""}


def test_podcasts_are_one_voice_by_default_and_an_episode_can_ask_for_two(named_install):
    from agent_friday.services import podcast_engine as pe
    # the seeded "any": "duo" table moves to the new default once
    assert pe.format_for("") == "solo"
    assert pe.format_for("weekly") == "duo"
    one = pe.create([{"kind": "text", "text": "x", "title": "Notes"}])
    two = pe.create([{"kind": "text", "text": "x", "title": "Notes"}], hosts="duo")
    assert one["format"] == "solo" and two["format"] == "duo"
    with pytest.raises(pe.PodcastRefused):
        pe.create([{"kind": "text", "text": "x", "title": "Notes"}], hosts="trio")


def test_a_format_table_the_owner_changed_is_not_migrated(named_install):
    from agent_friday.services import podcast_engine as pe
    named_install["s"]["podcasts"]["format"]["briefing"] = "duo"
    assert pe.format_for("") == "duo"


def test_the_podcast_tool_takes_hosts_for_one_episode():
    from agent_friday.services import podcast_tools as pt
    tool = next(t for t in pt.TOOLS if t["name"] == "make_podcast")
    assert tool["input_schema"]["properties"]["hosts"]["enum"] == ["solo", "duo"]
    from agent_friday.core import DEFAULT_SETTINGS
    from agent_friday.services import podcast_engine as pe
    assert DEFAULT_SETTINGS["podcasts"]["format"]["any"] == pe.RECOMMENDED_FORMAT["any"] == "solo"
    assert DEFAULT_SETTINGS["identity"] == {"pronouns": "auto", "local_voice_matches_cloud": True,
                                         "name_in_lockup": False}


# ── pronouns and the local voice ─────────────────────────────────────────────

@pytest.mark.parametrize("voice,expected", [
    ("am_puck", "he/him"), ("bm_george", "he/him"), ("af_heart", "she/her"),
    ("bf_emma", "she/her"), ("Puck", "he/him"), ("Charon", "he/him"), ("Aoede", "she/her"),
    ("en_US-amy-medium", "they/them"), ("", "they/them"), ("zz_unknown", "they/them"),
])
def test_pronouns_follow_the_voice(voice, expected):
    from agent_friday.services import agent_identity
    assert agent_identity.pronouns_for_voice(voice) == expected


def test_the_owners_pronouns_win_over_the_voice():
    from agent_friday.services import agent_identity
    s = {"local_voice_kokoro_voice": "am_adam"}
    assert agent_identity.pronouns(s) == "he/him"
    assert agent_identity.pronouns(dict(s, identity={"pronouns": "she/her"})) == "she/her"
    assert agent_identity.pronouns(dict(s, identity={"pronouns": "auto"})) == "he/him"


def test_the_local_voice_matches_the_cloud_voice_when_installed():
    from agent_friday.services.agent_identity import local_kokoro_voice
    yes, no = (lambda v: True), (lambda v: False)
    assert local_kokoro_voice({"tts_voice": "Puck"}, installed=yes) == "am_puck"
    assert local_kokoro_voice({"tts_voice": "Puck"}, installed=no) == "af_heart"
    # the owner's own local voice wins; the default cloud voice changes nothing
    assert local_kokoro_voice({"tts_voice": "Puck", "local_voice_kokoro_voice": "bf_emma"},
                              installed=yes) == "bf_emma"
    # ...even when it is the default voice: saved is chosen; blank is not
    assert local_kokoro_voice({"tts_voice": "Puck", "local_voice_kokoro_voice": "af_heart"},
                              installed=yes) == "af_heart"
    assert local_kokoro_voice({"tts_voice": "Puck", "local_voice_kokoro_voice": ""},
                              installed=yes) == "am_puck"
    assert local_kokoro_voice({"tts_voice": "Aoede"}, installed=yes) == "af_heart"
    # a cloud voice with no local twin keeps the default
    assert local_kokoro_voice({"tts_voice": "Schedar"}, installed=yes) == "af_heart"
    # and the owner can turn matching off
    assert local_kokoro_voice({"tts_voice": "Puck", "identity": {"local_voice_matches_cloud": False}},
                              installed=yes) == "af_heart"


def test_named_rewrites_only_a_custom_name():
    from agent_friday.services.agent_identity import named
    text = "You are Agent Friday, a sovereign assistant. You are Friday's fast voice."
    assert named(text, {"agent_name": NAME}) == (
        "You are %s, a sovereign assistant. You are %s's fast voice." % (SAID, SAID))
    assert named(text, {"agent_name": "AGENT FRIDAY"}) == text
    assert named(text, {}) == text


def test_the_voice_prompts_go_through_the_name_helper():
    """Voice parity: what the chat and the editorial say about her name, the
    voice prompts (local brain, local front, speaker, Gemini Live) say too."""
    src = (ROOT / "src/agent_friday/routes/voice.py").read_text(encoding="utf-8")
    assert src.count("_named(") >= 5
    assert "voice_prefix = _named(voice_prefix" in src


# ── the page ─────────────────────────────────────────────────────────────────

def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _fn(text, name):
    m = re.search(r"\nfunction %s\(" % name, text)
    assert m, name
    return text[m.start():text.index("\n}\n", m.start() + 1) + 2]


def _block(text, name):
    m = re.search(r"/\* %s:begin \*/\n(.*?)/\* %s:end \*/" % (name, name), text, re.S)
    assert m, name
    return m.group(1)


@pytest.mark.parametrize("rel", PAGES)
def test_the_lockup_and_the_tab_title_read_the_name_helper(rel):
    text = _read(rel)
    assert "fridayLockupName()" in _fn(text, "FridayLockup"), rel
    assert "fridayTitleName()" in _fn(text, "StandaloneShell"), rel
    block = _block(text, "fridayHerName")
    assert "function fridayLockupName(" in block and "function fridayTitleName(" in block, rel


_PROBE = r"""
const store = {};
global.window = {FRIDAY_BRAND: {name: 'Agent Friday™'}};
global.document = {title: 'Agent Friday™'};
global.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
eval(BLOCKS);
const seen = {defaultLockup: fridayLockupName(), defaultTitle: fridayTitleName()};
document.title = 'Mail · Agent Friday™';
fridaySetName('AGENT JUNIPER');
seen.offLockup = fridayLockupName(); seen.offTitle = fridayTitleName(); seen.offDoc = document.title;
fridaySetName('AGENT JUNIPER', true);
seen.lockup = fridayLockupName(); seen.title = fridayTitleName(); seen.doc = document.title;
console.log(JSON.stringify(seen));
"""


def test_the_page_shows_her_name_in_the_lockup_and_the_title():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    text = _read("index.html")
    blocks = _block(text, "fridayShortcuts") + _block(text, "fridayHerName")
    script = "const BLOCKS = %s;\n%s" % (json.dumps(blocks), _PROBE)
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    # identity.name_in_lockup off (the default): the wordmark and titles stay
    # the product's (BRAND.md); on: her own name, with no mark.
    assert seen == {"defaultLockup": "", "defaultTitle": "Agent Friday™",
                    "offLockup": "", "offTitle": "Agent Friday™",
                    "offDoc": "Mail · Agent Friday™",
                    "lockup": NAME, "title": SAID, "doc": "Mail · " + SAID}, seen


def test_her_name_on_the_wordmark_is_the_owners_choice_and_off_by_default():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["identity"]["name_in_lockup"] is False
    for rel in PAGES:
        assert "(agentSettings.identity || {}).name_in_lockup" in _read(rel), rel
