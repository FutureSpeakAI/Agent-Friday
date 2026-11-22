"""Links in a briefing are enforced by code, not by the prompt.

Before a briefing is stored or shown, every item in its news, email, calendar and events sections must resolve to
a real link taken from the run's own tool output: a news story's URL, an email's Gmail link built from its real
message id, a calendar entry's or an outing's own page. An item that cannot is dropped and counted. A link the
model typed is removed unless it is exactly one the tools returned. Nothing here builds or guesses an id or a URL.
Synthetic stories, senders and events only.
"""
import pytest

from agent_friday.services import briefing_links as bl

NEWS = [{"id": "N1", "title": "Council passes the budget", "source": "Example Gazette",
         "url": "https://gazette.example.test/budget"},
        {"id": "N2", "title": "Storm warning for the coast", "source": "Coast Daily",
         "url": "https://coastdaily.example.test/storm"}]
MAIL = [{"id": "E1", "sender": "Dana Reyes", "subject": "Budget question", "message_id": "18f3a1b2c3d4e5f6"},
        {"id": "E2", "sender": "Sam Lee", "subject": "No id came back", "message_id": ""}]
CAL = [{"id": "C1", "title": "Planning meeting", "url": "https://calendar.example.test/event?eid=abc123"},
       {"id": "C2", "title": "Dentist", "url": ""}]
OUT = [{"id": "V1", "title": "Harbour Lights Festival", "url": "https://harbourlights.example.test/2027"}]


def sources():
    return bl.collect(news=NEWS, email=MAIL, calendar=CAL, events=OUT)


def run(text):
    return bl.validate(text, sources())


def lines(result):
    return [x for x in result.text.splitlines() if x.strip()]


# -- news ---------------------------------------------------------------------------------

def test_a_briefing_with_an_unlinked_news_item_drops_it_and_reports_the_count():
    got = run("## News\n- The council passed the budget. [N1]\n- Everyone is talking about something.\n")
    assert "council passed the budget" in got.text and "Everyone is talking" not in got.text
    assert got.dropped == {"news": 1} and got.dropped_total == 1
    assert "1 item dropped: no source" in got.text


def test_a_news_item_that_cites_a_story_with_no_working_url_is_dropped():
    broken = bl.collect(news=[{"id": "N9", "title": "Pulled story", "source": "X", "url": ""}])
    got = bl.validate("## News\n- A story that lost its link. [N9]\n", broken)
    assert got.dropped == {"news": 1} and "lost its link" not in got.text


def test_a_cited_news_marker_is_left_for_the_news_link_attacher_and_counts_as_kept():
    got = run("## News\n- The council passed the budget. [N1]\n")
    assert "[N1]" in got.text and got.dropped_total == 0 and "dropped" not in got.text


# -- email --------------------------------------------------------------------------------

def test_an_email_item_renders_a_link_built_from_its_real_message_id():
    got = run("## Email\n- Dana asks about the budget. [E1]\n")
    assert "https://mail.google.com/mail/u/0/#all/18f3a1b2c3d4e5f6" in got.text
    assert "[E1]" not in got.text and got.dropped_total == 0


def test_an_email_from_a_named_account_opens_in_that_accounts_mailbox_and_a_malformed_account_falls_back():
    for account, segment in (("owner.two@example.test", "owner.two@example.test"), ("not an address", "0"),
                             ("x@y.z/../../evil", "0"), ("", "0")):
        sources = bl.collect(email=[{"id": "E1", "message_id": "18f3a1b2c3d4e5f6", "account": account}])
        got = bl.validate("## Email\n- Dana asks. [E1]\n", sources)
        assert "https://mail.google.com/mail/u/%s/#all/18f3a1b2c3d4e5f6" % segment in got.text, account


def test_an_email_item_whose_message_id_is_missing_or_malformed_is_dropped_never_guessed():
    got = run("## Email\n- Sam wrote something. [E2]\n- Another one. [E7]\n")
    assert got.dropped == {"email": 2} and "mail.google.com" not in got.text
    odd = bl.collect(email=[{"id": "E3", "sender": "x", "subject": "y", "message_id": "../../etc"}])
    assert bl.validate("## Email\n- Item. [E3]\n", odd).dropped == {"email": 1}


def test_an_email_item_cannot_borrow_a_news_or_calendar_citation():
    got = run("## Email\n- Dana asks about the budget. [N1]\n- Something. [C1]\n")
    assert got.dropped == {"email": 2}


# -- calendar and events ------------------------------------------------------------------

def test_a_calendar_item_links_to_its_own_event_and_one_without_a_url_is_dropped():
    got = run("## Calendar\n- 10:00 Planning meeting. [C1]\n- 14:00 Dentist. [C2]\n")
    assert "https://calendar.example.test/event?eid=abc123" in got.text and "Dentist" not in got.text
    assert got.dropped == {"calendar": 1}


def test_an_outing_in_a_nested_events_section_needs_its_own_page():
    text = ("## Events\n### This week\n#### Just you\n- Harbour Lights Festival. [V1]\n- A thing I made up.\n"
            "## On the horizon\n### Festivals\n- Harbour Lights again. [V1]\n- Unlinked festival.\n")
    got = run(text)
    assert got.text.count("https://harbourlights.example.test/2027") == 2
    assert "made up" not in got.text and "Unlinked festival" not in got.text and got.dropped == {"events": 2}


# -- links the model typed ------------------------------------------------------------------

def test_a_link_the_model_typed_is_removed_unless_it_is_exactly_one_the_tools_returned():
    text = ("## News\n- See [the gazette](https://gazette.example.test/budget) and [elsewhere](https://evil.example.test/x)"
            " and https://bare.example.test/y [N1]\n")
    got = run(text)
    assert "https://gazette.example.test/budget" in got.text
    assert "evil.example.test" not in got.text and "bare.example.test" not in got.text and "elsewhere" in got.text


def test_an_item_with_only_a_typed_link_and_no_citation_is_dropped():
    got = run("## News\n- Look at [this](https://evil.example.test/x).\n")
    assert got.dropped == {"news": 1} and "evil.example.test" not in got.text


# -- what is left alone ------------------------------------------------------------------------

def test_sections_that_are_not_news_email_or_events_are_not_touched():
    text = "## Tasks\n- Send the grant report\n- Renew the lease\n"
    got = run(text)
    assert got.text.strip() == text.strip() and got.dropped_total == 0


def test_a_continuation_line_goes_with_its_item():
    got = run("## News\n- Unlinked headline\n  with a second line\n- Linked one. [N2]\n  and its detail\n")
    assert "second line" not in got.text and "its detail" in got.text and got.dropped == {"news": 1}


def test_the_count_line_names_every_kind_that_lost_items_and_says_item_or_items():
    got = run("## News\n- No source here\n## Email\n- No id either\n- Nor this\n")
    assert "3 items dropped: no source" in got.text
    assert got.dropped == {"news": 1, "email": 2}


def test_an_empty_briefing_and_one_with_nothing_to_drop_get_no_count_line():
    assert run("").text == "" and "dropped" not in run("## News\n- Fine. [N1]\n").text


def test_a_briefing_with_no_sources_drops_every_item_in_the_checked_sections():
    got = bl.validate("## News\n- One. [N1]\n## Email\n- Two. [E1]\n", bl.collect())
    assert got.dropped == {"news": 1, "email": 1}


# -- the shape of what the tools return ----------------------------------------------------------

def test_collect_keeps_only_well_formed_sources_and_never_invents_an_id():
    got = bl.collect(news=[{"id": "N1", "title": "T", "url": "https://x.example.test/a"}, {"title": "no id"}, "junk", None],
                     email=[{"sender": "no id", "message_id": "18f3a1b2c3d4e5f6"}])
    assert sorted(got) == ["N1"]
