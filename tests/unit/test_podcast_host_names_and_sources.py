"""Podcast hosts are named by the owner's settings, and links in pasted
research become sources of their own (field bug log, round 2, items 6 and 7).

Hosts:
  * the co-host's name is never read off its voice id: "bf_emma" spoke as
    "Emma" because the defaults seeded that name. A blank co-host is the
    product's name ("Agent Friday"); the main host is her (the owner's name
    for her); both are the owner's to change in Settings (podcasts.hosts) and
    for one episode (make_podcast `host_names`), by chat and by voice;
  * an install whose saved co-host is the seeded voice-id name moves off it
    once; a name the owner gives afterwards is theirs, whatever it is.

Sources:
  * a block of pasted research with web addresses in it is not one "Notes"
    source: each address becomes its own linked source (once), titled by the
    line around it; the other lines stay the notes;
  * an address that cannot be listed is named in the episode's sources (and
    its transcript) as not used, never dropped in silence.

Synthetic names only ("Agent Nova", "Agent Orion", "Agent Vega").
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_sources as ps

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def install(monkeypatch, tmp_path):
    """Saved settings the loader returns and saves deep-merge into."""
    import agent_friday.core as core
    from agent_friday.services import podcast_render
    saved = {"s": {}}

    def merge(base, over):
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = json.loads(json.dumps(v))

    def save(delta):
        merge(saved["s"], json.loads(json.dumps(delta)))
        path.write_text(json.dumps(saved["s"]), encoding="utf-8")

    path = tmp_path / "settings.json"
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", path)
    monkeypatch.setattr(core, "_load_settings", lambda: saved["s"])
    monkeypatch.setattr(core, "_save_settings", save)
    monkeypatch.setattr(podcast_render, "voice_installed", lambda v: True)
    monkeypatch.setattr(pe, "wake", lambda: None)
    return saved


def _text_ref(text="The council voted 7 to 2 on the budget."):
    return [{"kind": "text", "text": text}]


# ── #6: who the hosts are ───────────────────────────────────────────────────

def test_a_fresh_install_s_co_host_is_agent_friday_never_the_voice_id_s_name(install):
    hosts = pe.settings()["hosts"]
    assert hosts["b"]["voice"] == "bf_emma"
    assert hosts["b"]["name"] == "Agent Friday", hosts["b"]
    assert hosts["a"]["name"] == "Friday"


def test_the_seeded_defaults_hold_no_voice_id_name():
    from agent_friday.core import DEFAULT_SETTINGS
    for key, host in DEFAULT_SETTINGS["podcasts"]["hosts"].items():
        assert not host["name"] or host["name"].lower() != pe.voice_label(host["voice"]).lower(), (key, host)
    for key, host in pe.DEFAULTS["hosts"].items():
        if key == "b":
            assert host["name"] == "", host


def test_an_install_seeded_with_the_voice_id_name_moves_off_it_once(install):
    install["s"] = {"agent_name": "AGENT NOVA",
                    "podcasts": {"hosts": {"a": {"name": "", "voice": ""},
                                           "b": {"name": "Emma", "voice": "bf_emma"}}}}
    hosts = pe.settings()["hosts"]
    assert (hosts["a"]["name"], hosts["b"]["name"]) == ("Agent Nova", "Agent Friday")
    assert install["s"]["podcasts"].get(pe.HOSTS_MIGRATION_KEY) is True
    # A name the owner gives afterwards is theirs, even that one.
    pe.set_host_names(b="Emma")
    assert pe.settings()["hosts"]["b"]["name"] == "Emma"


def test_a_co_host_name_the_owner_chose_is_kept(install):
    install["s"] = {"podcasts": {"hosts": {"b": {"name": "Agent Orion", "voice": "bf_emma"}}}}
    assert pe.settings()["hosts"]["b"]["name"] == "Agent Orion"


def test_settings_name_both_hosts_and_a_blank_name_is_the_default(install):
    install["s"] = {"agent_name": "AGENT NOVA"}
    names = pe.set_host_names(a="Agent Vega", b="Agent Orion")
    assert (names["a"]["name"], names["b"]["name"]) == ("Agent Vega", "Agent Orion")
    hosts = pe.settings()["hosts"]
    assert (hosts["a"]["name"], hosts["b"]["name"]) == ("Agent Vega", "Agent Orion")
    names = pe.set_host_names(a="", b="")
    assert (names["a"]["name"], names["b"]["name"]) == ("Agent Nova", "Agent Friday")
    assert (names["a"]["default"], names["b"]["default"]) == ("Agent Nova", "Agent Friday")


def test_two_hosts_cannot_share_a_name(install):
    with pytest.raises(pe.PodcastRefused):
        pe.set_host_names(b="friday")            # the main host is "Friday"
    with pytest.raises(pe.PodcastRefused):
        pe.create(_text_ref(), hosts="duo", host_names=["Agent Vega", "agent vega"])


def test_a_host_name_is_one_plain_line():
    assert pe.clean_host_name("  Agent\n Nova: <b>  ") == "Agent Nova b"
    assert len(pe.clean_host_name("x" * 200)) == pe.HOST_NAME_MAX


def test_one_episode_can_name_its_hosts_without_changing_the_settings(install):
    ep = pe.create(_text_ref(), hosts="duo", host_names=["Agent Vega", "Agent Orion"])
    assert (ep["hosts"]["a"]["name"], ep["hosts"]["b"]["name"]) == ("Agent Vega", "Agent Orion")
    # A blank one keeps the setting.
    ep2 = pe.create(_text_ref(), hosts="duo", host_names={"b": "Agent Orion"})
    assert (ep2["hosts"]["a"]["name"], ep2["hosts"]["b"]["name"]) == ("Friday", "Agent Orion")
    hosts = pe.settings()["hosts"]
    assert (hosts["a"]["name"], hosts["b"]["name"]) == ("Friday", "Agent Friday")


def test_the_co_host_introduces_themself_by_name_not_by_voice(install):
    ep = pe.create(_text_ref(), hosts="duo")
    lines, _ = pe.signature_lines(dict(ep, show="The Front Page"))
    said = " ".join(ln["text"] for ln in lines)
    assert "Emma" not in said
    assert "I'm Agent Friday" in said, said


def test_make_podcast_takes_host_names_in_chat_and_voice(install):
    from agent_friday.services import podcast_tools as pt
    from agent_friday.services import voice_engine as ve
    schema = next(t for t in pt.TOOLS if t["name"] == "make_podcast")["input_schema"]["properties"]
    assert schema["host_names"]["type"] == "array"
    assert "make_podcast" in set(ve._VOICE_SHARED_TOOLS)
    out = json.loads(pt._tool_make_podcast({"sources": _text_ref(), "hosts": "duo",
                                            "host_names": ["Agent Vega", "Agent Orion"]}))
    assert out["status"] == "queued", out
    ep = pe.load(out["episode_id"])
    assert (ep["hosts"]["a"]["name"], ep["hosts"]["b"]["name"]) == ("Agent Vega", "Agent Orion")


def test_the_voice_contract_still_fits_with_host_names():
    from agent_friday.services import voice_engine as ve
    c = ve.build_voice_tool_contract()
    assert "make_podcast" in set(c["names"])
    # Voice names hosts through set_setting; the spoken contract (fixed budget)
    # leaves the per-episode host_names and podcast_format's host/name out.
    spoken = next(t["function"] for t in c["tools"] if t["function"]["name"] == "make_podcast")
    assert "host_names" not in spoken["parameters"]["properties"]
    from agent_friday.services.agent import CLAUDE_TOOLS, WORKSPACE_TOOLS
    chat = {t["name"]: t for t in CLAUDE_TOOLS}
    for lst in WORKSPACE_TOOLS.values():
        for t in lst:
            chat.setdefault(t.get("name"), t)
    assert chat["make_podcast"]["input_schema"]["properties"]["host_names"]["type"] == "array"
    assert "set_setting" in set(c["names"])
    assert c["fits"] and c["tokens"] <= ve.VOICE_CONTRACT_MAX_TOKENS - 300, c["tokens"]


def test_a_spoken_or_typed_setting_names_the_co_host_after_the_owners_yes(install, monkeypatch):
    from agent_friday.services import setting_proposals as sp
    from agent_friday.services import podcast_tools as pt
    routed = sp._route("settings.podcasts.hosts.b", "Agent Orion")
    assert routed == ("podcast_format", {"host": "b", "name": "Agent Orion"}), routed
    asked = []
    monkeypatch.setattr(sp, "hold", lambda tool, inp, **kw: asked.append(kw) or None)
    out = json.loads(pt.HANDLERS["podcast_format"](routed[1]))
    assert out["status"] == "ok", out
    assert asked and asked[0]["old"] == "Agent Friday" and asked[0]["new"] == "Agent Orion"
    assert pe.settings()["hosts"]["b"]["name"] == "Agent Orion"


# ── #7: links in pasted research ────────────────────────────────────────────

RESEARCH = """Research for the episode
- Council budget vote recap https://example.org/council/budget-vote.
- The same story again: https://www.example.org/council/budget-vote/
* [Transit plan explained](https://news.example.com/transit?id=4)
Roads take most of the rise.
An internal draft is at http://localhost:8080/draft and http://192.168.1.4/x
"""


def test_links_in_pasted_text_become_their_own_sources_and_the_rest_stays_notes():
    docs = ps.resolve({"kind": "text", "text": RESEARCH})
    links = [d for d in docs if d.get("url")]
    assert [d["url"] for d in links] == ["https://example.org/council/budget-vote",
                                         "https://news.example.com/transit?id=4"]
    assert links[0]["title"] == "Council budget vote recap"
    assert links[1]["title"] == "Transit plan explained"
    notes = [d for d in docs if not d.get("url")]
    assert len(notes) == 1 and notes[0]["title"] == "Notes"
    assert "Roads take most of the rise." in notes[0]["text"]
    assert "http" not in notes[0]["text"]
    _same, warnings = ps.resolve_with_warnings({"kind": "text", "text": RESEARCH})
    assert len(warnings) == 2 and all("not used" in w for w in warnings)
    assert "http://localhost:8080/draft" in warnings[0] and "private or local" in warnings[0]


def test_a_line_with_only_a_link_is_titled_by_its_site():
    docs = ps.resolve({"kind": "text", "text": "https://www.example.net/a/b"})
    assert [(d["title"], d["url"]) for d in docs] == [("example.net", "https://www.example.net/a/b")]


def test_a_link_a_model_did_not_type_in_is_never_added():
    docs = ps.resolve({"kind": "text", "text": "No links here at all."})
    assert [(d["title"], d.get("url")) for d in docs] == [("Notes", "")]


def test_the_episode_lists_unusable_links_and_one_page_is_one_source(install, monkeypatch):
    monkeypatch.setitem(ps._READERS, "url", lambda ref: [ps._doc("Budget vote", "url", "The vote.",
                                                                  url=ref["url"], private=False)])
    ep = {"id": "x", "mode": "conversation",
          "refs": [{"kind": "url", "url": "https://example.org/council/budget-vote"},
                   {"kind": "text", "text": RESEARCH}]}
    docs = pe._gather(ep)
    urls = [d["url"] for d in docs if d.get("url")]
    assert urls == ["https://example.org/council/budget-vote", "https://news.example.com/transit?id=4"]
    assert len(ep["source_errors"]) == 2 and "localhost" in ep["source_errors"][0]
    text = pe.transcript_bytes(dict(ep, lines=[], sources=pe._public_sources(docs))).decode("utf-8-sig")
    assert "Not used: a link in your notes was not used: http://localhost:8080/draft" in text


def test_the_player_shows_what_was_not_used_and_the_hosts_names_can_be_set():
    for page in ("index.html", "ui_parts/app.html"):
        src = (ROOT / page).read_text(encoding="utf-8")
        assert "function podcastNotUsed(ep)" in src and "'⚠ Not used: ' + x" in src, page
        assert "fetch('/api/podcasts/hosts', { method: 'PUT'" in src, page
        assert "The hosts\\u2019 names" in src, page
