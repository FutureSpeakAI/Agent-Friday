"""Links in a reply, as a phone or tablet reads them (MOBILE_FULL_PARITY_STORM MP-1).

* Only a marker the PC already checked becomes a link: a verified ``[web:https://...]``, ``[lib:d#b]``,
  ``[wiki:name]``, ``[memory:...]`` / ``[conversation:...]``. ``[unverified-web:...]`` and ``[unverified-lib:...]``
  never do.
* A web link is ``https`` to a public DNS name, with no user name, no IP literal, no local name and no control
  character; its ``host`` is shown in ASCII so a look-alike name reads as itself.
* Every in-app target is ``friday://<kind>/<id>``, built from the marker's checked parts, never from model text.
* The phone gets the list on the final turn event and on each assistant message of a chat it reads.
"""
import json

import pytest

from agent_friday import core
from agent_friday.tv_gateway import links
from tests.unit import test_owner_turn_stream as turn_t
from tests.unit import test_tv_pc_chats as chats_t
from tests.unit.test_tv_pc_read import get, paired

client = turn_t.client
_clean = turn_t._clean
wire = turn_t.wire
desk = chats_t.desk


def kinds(text):
    return [(link.kind.value, link.uri) for link in links.found(text)]


# ── what becomes a link ──────────────────────────────────────────────────────

def test_each_checked_marker_kind_becomes_one_link_in_order():
    text = ("See [web:https://example.org/a?b=1#c], your note [wiki:Solar roof], the source [lib:12#3] "
            "and what we said [memory:2026-11-02/kitchen].")
    assert kinds(text) == [
        ("web", "https://example.org/a?b=1#c"),
        ("wiki", "friday://wiki/Solar%20roof"),
        ("lib", "friday://lib/12#3"),
        ("search", "friday://search/2026-11-02"),
    ]
    assert [link.n for link in links.found(text)] == [1, 2, 3, 4]


def test_an_unverified_marker_is_never_a_link():
    assert links.found("[unverified-web:https://example.org] and [unverified-lib:4#2]") == []


@pytest.mark.parametrize("url", [
    "http://example.org/",                      # not https
    "https://user:pw@example.org/",             # a user name hides the real host
    "https://user@example.org/",
    "https://192.168.1.10/admin",               # an IP literal
    "https://[::1]/",
    "https://1.2.3/",                           # digits and dots: read as an address by browsers
    "https://localhost/",
    "https://printer.local/",
    "https://router.lan/",
    "https://intranet/",                        # no dot: not a public name
    "https://example.org:99999/",               # a port out of range
    "javascript:alert(1)",
    "data:text/html,hi",
    "file:///C:/Windows",
    "https://exa mple.org/",                     # a space
    "https://example.org/\x07",                  # a control character
])
def test_a_web_address_that_could_mislead_or_reach_inside_the_house_is_never_a_link(url):
    assert links.found("[web:%s]" % url) == []


def test_a_look_alike_host_is_shown_in_ascii():
    (link,) = links.found("[web:https://аpple.com/]")       # the first letter is Cyrillic
    assert link.host == "xn--pple-43d.com" and link.label == link.host
    assert link.uri == "https://xn--pple-43d.com/"                         # what opens is the host that was shown


def test_a_repeated_marker_is_one_link_and_the_list_is_capped():
    assert len(links.found("[wiki:A] [wiki:A] [wiki:A]")) == 1
    many = " ".join("[wiki:page %d]" % i for i in range(links.MAX_LINKS + 10))
    assert len(links.found(many)) == links.MAX_LINKS


def test_a_wiki_name_that_is_blank_too_long_or_holds_a_control_character_is_not_a_link():
    assert links.found("[wiki:   ]") == []
    assert links.found("[wiki:%s]" % ("x" * (links.MAX_NAME + 1))) == []
    assert links.found("[wiki:bad\x1bname]") == []


def test_an_invisible_format_character_that_could_disguise_a_name_or_address_is_refused():
    assert links.found("[wiki:‮emanelif]") == []                    # a right-to-left override
    assert links.found("[web:https://example.org/​path]") == []     # a zero-width space


def test_a_wiki_name_cannot_steer_the_uri_somewhere_else():
    (link,) = links.found("[wiki:../../settings?x=1#y]")
    assert link.uri == "friday://wiki/..%2F..%2Fsettings%3Fx%3D1%23y"


def test_every_uri_is_https_or_the_one_friday_scheme():
    """Q11: walk every marker kind over a small set of bodies and check the scheme rule for every link made."""
    bodies = ["https://example.org", "https://a.b/c d", "1#2", "x#y", "Page", "", " ", "a/b", "../x", "https://x.local",
              "%2e%2e", "javascript:x", "Ä", "\u202ename"]
    for kind in ("web", "lib", "wiki", "memory", "conversation", "news", "unverified-web"):
        for body in bodies:
            for link in links.found("[%s:%s]" % (kind, body)):
                assert link.uri.startswith("https://") if link.kind is links.Kind.WEB else link.uri.startswith("friday://")
                assert link.marker == "[%s:%s]" % (kind, body)


# ── where the phone gets them ────────────────────────────────────────────────

LINKED = {"done": True, "payload": {"response": "It is in [wiki:Solar roof] and [web:https://example.org/x].",
                                    "model": "bonsai2:27b", "seat": "local"}}


def final_block(r):
    return next(e for e in turn_t.events(r) if e.get("type") == "message_delta")["friday"]


def test_the_final_turn_event_carries_the_links_of_the_answer(client, wire):
    wire([{"started": True}, LINKED])
    block = final_block(turn_t.ask(client, turn_t.phone()))
    assert [(row["kind"], row["uri"]) for row in block["links"]] == [
        ("wiki", "friday://wiki/Solar%20roof"), ("web", "https://example.org/x")]
    assert block["links"][1]["host"] == "example.org"


def test_a_spoken_turn_still_carries_the_links_of_the_screen_copy(client, wire):
    wire([{"started": True}, LINKED])
    block = final_block(turn_t.ask(client, turn_t.phone(), voice=True))
    assert [row["kind"] for row in block["links"]] == ["wiki", "web"]


def test_an_answer_with_no_marker_carries_an_empty_list(client, wire):
    wire([{"started": True}, turn_t.DONE])
    assert final_block(turn_t.ask(client, turn_t.phone()))["links"] == []


def test_each_assistant_message_of_a_chat_carries_its_links(client, desk):
    desk.msgs["c1"] = [{"id": "m1", "role": "user", "content": "[wiki:Typed by me]"},
                       {"id": "m2", "role": "assistant", "content": "See [lib:4#2]."},
                       {"id": "m3", "role": "assistant", "text": "Nothing to open."}]
    got = get(client, paired(), "/tv/v1/chats/c1/messages").get_json()["messages"]
    assert "links" not in got[0]
    assert [row["uri"] for row in got[1]["links"]] == ["friday://lib/4#2"]
    assert got[2]["links"] == []
