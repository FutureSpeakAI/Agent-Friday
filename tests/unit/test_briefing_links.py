"""Links in a briefing are enforced by code, not by the prompt.

Before a briefing is stored or shown, every item in its news, email, calendar and events sections must resolve to
a real link taken from the run's own tool output: a news story's URL, an email's Gmail link built from its real
message id, a calendar entry's or an outing's own page. An item that cannot is dropped and counted. A link the
model typed is removed unless it is exactly one the tools returned. Nothing here builds or guesses an id or a URL.
Synthetic stories, senders and events only.
"""
import re
import string

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


# -- by what an item says, not only where it sits (review of #1115) ---------------------------------

def test_an_address_in_round_or_angle_brackets_is_removed_like_any_other_typed_address():
    got = run("## News\n- Council passes the budget (https://evil.example.test/phish) [N1]\n"
              "- Storm warning <https://evil.example.test/storm> [N2]\n")
    assert "evil.example.test" not in got.text and got.dropped_total == 0


def test_one_unknown_id_sinks_the_item_even_beside_a_real_one():
    got = run("## Today's Calendar\n- Planning, then a call. [C1, C9]\n")
    assert "Planning" not in got.text and got.dropped == {"calendar": 1}


def test_agenda_and_meetings_headings_are_sourced_sections():
    got = run("## Today's Agenda\n- Something with no source.\n## Meetings\n- Planning at ten. [C1]\n")
    assert "no source" not in got.text.split("_")[0] and "calendar.example.test/event?eid=abc123" in got.text
    assert got.dropped == {"calendar": 1}


def test_tasks_and_insights_outside_the_sourced_sections_are_kept():
    got = run("## Tasks & Deadlines\n- File the grant report by Friday.\n## One insight\n- Block an hour for it.\n")
    assert "File the grant report" in got.text and "Block an hour" in got.text and got.dropped_total == 0


def test_a_heading_that_names_two_kinds_accepts_both():
    got = run("## Schedule & Inbox\n- Planning at ten. [C1]\n- Dana asks about the budget. [E1]\n")
    assert got.dropped_total == 0 and "calendar.example.test" in got.text and "mail.google.com" in got.text


def test_prose_that_cites_or_links_is_checked_too():
    got = run("## Tasks\nA note pointing at https://evil.example.test/x and a made-up [C9].\n")
    assert "evil.example.test" not in got.text and "made-up" not in got.text and got.dropped == {"other": 1}


def test_the_final_pass_keeps_only_known_links_anywhere_in_the_text():
    text = "## Extra\n- [Presale](https://evil.example.test/presale) and [ok](https://gazette.example.test/budget)\n"
    out = bl.strip_unknown_links(text, {"https://gazette.example.test/budget"})
    assert "evil.example.test" not in out and "Presale" in out and "](https://gazette.example.test/budget)" in out


# -- every link shape, prose in a sourced section, the briefing's title ---------------------------------------------

EVIL = "evil.example.test"


def test_a_typed_link_whose_label_has_brackets_loses_its_address():
    got = run("## Your Day\n- Read the [[draft] plan](https://evil.example.test/nested-body)\n"
              "## Calendar\n- Planning at ten [C1], see [[the] agenda](https://evil.example.test/agenda)\n")
    assert EVIL not in got.text and "Read the" in got.text and "calendar.example.test" in got.text


LABELS_ = ["plan", "[draft] plan", "[[deep]] plan", "a ] b", "![img]", "", "x [y] z"]
TARGETS = ["https://evil.example.test/p", "HTTPS://EVIL.EXAMPLE.TEST/p", "<https://evil.example.test/p>",
           'https://evil.example.test/p "title"', " https://evil.example.test/p ", "www.evil.example.test/p",
           "https://gazette.example.test/budget#evil.example.test"]


@pytest.mark.parametrize("label", LABELS_)
@pytest.mark.parametrize("target", TARGETS)
def test_no_link_shape_carries_an_address_the_tools_did_not_return(label, target):
    known = {"https://gazette.example.test/budget"}
    bare = target.strip()
    glued = ["See%s now" % bare, "_%s_" % bare, "a.%s" % bare, "a/%s" % bare, "1%s" % bare, "x%s" % bare,
             "[web:x%s]" % bare, "(see%s)" % bare]                    # an address glued to a word
    for text in ["[%s](%s)" % (label, target), "![%s](%s)" % (label, target), "see %s now" % target,
                 "[ref]: %s" % target, "<%s>" % bare.strip("<>")] + glued:
        out = bl.strip_unknown_links(text, known)
        assert EVIL not in out.lower(), (text, out)


@pytest.mark.parametrize("text", ["write to someone@evil.example.test", "a/someone@evil.example.test",
                                  "x.someone@evil.example.test", "_someone@evil.example.test_"])
def test_no_typed_mail_address_becomes_a_mailto_link_however_it_is_glued(text):
    gmail = "https://mail.google.com/mail/u/owner@example.test/#all/18f3a1b2c3d4e5f6"
    out = bl.strip_unknown_links("%s [Open email](%s)" % (text, gmail), {gmail})
    assert out.count("@") == 1 and "](%s)" % gmail in out                # the only @ left is in the known link


@pytest.mark.parametrize("label", LABELS_)
def test_a_link_to_any_other_scheme_is_no_link_at_all(label):
    for target in ("javascript:alert(1)", "data:text/html,hi", "mailto:someone@example.test", "/relative/page"):
        assert "](" not in bl.strip_unknown_links("[%s](%s)" % (label, target), set())


def test_a_known_link_survives_every_pass_with_its_label():
    text = "[Read [the] story](https://gazette.example.test/budget)"
    assert bl.strip_unknown_links(text, {"https://gazette.example.test/budget"}) == text


def test_an_uncited_line_of_prose_in_a_sourced_section_stands_and_an_uncited_bullet_does_not():
    # "Nothing today." under Calendar is how an empty day reads; dropping it would report a dropped item that was
    # never a claim (tests/api/test_workspace_jobs_visible). Whether uncited prose should go too is for the owner.
    got = run("## Calendar\nNothing on the calendar today.\n- Dinner with the mayor.\n- Planning at ten. [C1]\n")
    assert "Nothing on the calendar today." in got.text and "mayor" not in got.text
    assert "Planning at ten" in got.text and got.dropped == {"calendar": 1}
    linked = run("## Calendar\nSee https://evil.example.test/x for more.\n")     # prose that links is still checked
    assert "evil.example.test" not in linked.text and linked.dropped == {"calendar": 1}


def test_the_briefings_title_does_not_make_its_sections_sourced():
    got = run("# Morning: headlines & schedule\n## Tasks\n- Send the grant report\n## News\n- Budget passed. [N1]\n")
    assert "Send the grant report" in got.text and got.dropped_total == 0
    nested = run("## Calendar\n### Morning\n- Coffee with nobody in particular.\n")
    assert "Coffee" not in nested.text                       # a level-2 kind still carries down


# -- a www. host inside a known link; links made by raw HTML or a reference definition ------------------------------

GCAL = "https://www.google.com/calendar/event?eid=abc123"
WWW_NEWS = "https://www.gazette.example.test/budget"


def test_a_known_link_with_a_www_host_survives_validation_and_the_final_pass():
    src = bl.collect(news=[{"id": "N1", "title": "Budget", "url": WWW_NEWS}],
                     calendar=[{"id": "C1", "title": "Standup", "url": GCAL}])
    got = bl.validate("## Calendar\n- Standup at nine. [C1]\n## Sources\n- [Budget](%s)\n" % WWW_NEWS, src)
    final = bl.strip_unknown_links(got.text, bl.known_links(src))
    assert "(%s)" % GCAL in final and "(%s)" % WWW_NEWS in final and "(https://)" not in final
    assert bl.strip_unknown_links("see %s now" % GCAL, {GCAL}) == "see <%s> now" % GCAL    # an autolink nothing can bend


HTML_AND_REF_LINKS = ['<a href="https&#58;//evil.example.test/x">open</a>',
                      '<a href="https&colon;//evil.example.test/x">open</a>',
                      '<a href="//evil.example.test/x">open</a>',
                      "<A HREF='javascript:alert(1)'>open</A>",
                      "[r]: //evil.example.test/x", "   [r]: https&#58;//evil.example.test/x 'title'",
                      "[r]: <https://evil.example.test/x>"]


@pytest.mark.parametrize("shape", HTML_AND_REF_LINKS)
def test_raw_html_and_reference_definitions_make_no_link(shape):
    out = bl.strip_unknown_links("Read [this][r] first.\n%s\nThen the rest.\n" % shape, set())
    assert "<" not in out and "]:" not in out and "Then the rest." in out
    got = run("## Calendar\n- Planning at ten. [C1]\n  %s\n" % shape.strip())    # inside a cited item
    assert "<" not in got.text and "]:" not in got.text and "calendar.example.test" in got.text


@pytest.mark.parametrize("definition", ["> [q]: //evil.example.test/quote", "- [l]: //evil.example.test/list",
                                        "1. [o]: //evil.example.test/ordered", "> - [n]: //evil.example.test/nest",
                                        "[two\nlines]: //evil.example.test/split", "   [i]:  //evil.example.test/in"])
def test_no_reference_definition_forms_in_any_container(definition):
    text = "## Calendar\n- Planning at ten. [C1] See [agenda][q].\n\n%s\n" % definition
    out = bl.strip_unknown_links(bl.validate(text, sources()).text, bl.known_links(sources()))
    assert "]:" not in out and "calendar.example.test" in out
    assert bl.strip_unknown_links(out, bl.known_links(sources())) == out              # and it stays that way
    alone = bl.strip_unknown_links("See [agenda][q].\n\n%s\n" % definition, set())
    assert "]:" not in alone and "]\\:" in alone                                    # the colon still shows


def test_a_typed_email_address_shows_but_is_no_mailto_link_and_the_gmail_link_keeps_its_account():
    gmail = "https://mail.google.com/mail/u/owner@example.test/#all/18f3a1b2c3d4e5f6"
    out = bl.strip_unknown_links("Write to someone@evil.example.test. [Open email](%s)" % gmail, {gmail})
    assert "someone&#64;evil.example.test" in out and "](%s)" % gmail in out


@pytest.mark.parametrize("suffix", ["'@evil.example.test", '"x@evil.example.test', "#evil.example.test",
                                    "/../evil.example.test", "%40evil.example.test",
                                    "\x85.evil.example.test", "\x1c.evil.example.test", "​.evil.example.test"])
def test_a_known_address_with_anything_glued_after_it_is_not_kept_as_known(suffix):
    out = bl.strip_unknown_links("see %s%s now" % (GCAL, suffix), {GCAL})
    assert GCAL not in out and "@" not in out
    assert bl.strip_unknown_links("see %s. Then %s, now" % (GCAL, GCAL), {GCAL}).count(GCAL) == 2


@pytest.mark.parametrize("shape", ["[tickets](\n//evil.example.test/nl)", "[tickets](  \n  //evil.example.test/sp)",
                                   '[tickets](//evil.example.test/t \n"title")', "![x](\n//evil.example.test/img)",
                                   "[[a] b](\nhttps://evil.example.test/br)"])
def test_no_inline_link_opens_with_its_target_or_title_on_the_next_line(shape):
    known = bl.known_links(sources())
    out = bl.strip_unknown_links("Book the %s today." % shape, known)
    assert "](" not in out and bl.strip_unknown_links(out, known) == out
    got = bl.strip_unknown_links(run("## Calendar\n- Planning at ten [C1], book the %s\n" % shape.replace("\n", "\n  "))
                                 .text, known)
    assert "](https://calendar.example.test/event?eid=abc123)" in got and got.count("](") == 1


def test_the_final_pass_can_run_twice_without_changing_the_text_again():
    text = "## News\n- a < b, and [ok](https://gazette.example.test/budget) <https://gazette.example.test/budget>\n"
    known = {"https://gazette.example.test/budget"}
    once = bl.strip_unknown_links(text, known)
    assert bl.strip_unknown_links(once, known) == once and "a &lt; b" in once


# -- a known address with round brackets in it ----------------------------------------------------------------------

PAREN_NEWS = "https://en.wikipedia.example.test/wiki/Mercury_(planet)"
PAREN_CAL = "https://calendar.example.test/e(1)"


def test_a_known_link_with_round_brackets_in_its_address_survives_every_pass_and_nothing_is_counted():
    from agent_friday.services import news_links
    stories = [{"id": "N1", "title": "Mercury news", "source": "Example Wiki", "url": PAREN_NEWS}]
    src = bl.collect(news=stories, calendar=[{"id": "C1", "title": "Planning", "url": PAREN_CAL}])
    got = bl.validate("## News\n- Mercury news. [N1]\n## Calendar\n- Planning at ten. [C1]\n"
                      "## Notes\n- Typed: [Mercury](%s)\n" % PAREN_NEWS, src)
    assert got.dropped_total == 0
    known = bl.known_links(src)
    body = news_links.attach_links(got.text, stories, keep=known)          # the order news_engine assembles in
    final = bl.strip_unknown_links(body, known | {PAREN_NEWS})
    assert final.count("](%s)" % PAREN_NEWS) == 3                           # cited, typed, and in Sources
    assert "([Calendar event](%s))" % PAREN_CAL in final
    leftover = final.replace("](%s)" % PAREN_NEWS, "](N)").replace("](%s)" % PAREN_CAL, "](C)")
    assert "planet" not in leftover and "e(1" not in leftover          # no address cut short and left as text
    assert bl.strip_unknown_links(final, known | {PAREN_NEWS}) == final


KNOWN_SHAPES = ["https://calendar.example.test/event?eid=abc123", "https://calendar.example.test/e_(1)",
                "https://en.wikipedia.example.test/wiki/Mercury_(planet)", "https://calendar.example.test/a/",
                "https://www.calendar.example.test/e.html", "https://calendar.example.test/e?x=1&y=(2)",
                "https://calendar.example.test/e()", "https://mail.google.com/mail/u/dana@example.test/#all/18f3a1b2c3"]
PLACEMENTS = ["- Standup %s", "- Standup %s.", "- Standup **%s**", "- Standup \"%s\".", "- Standup '%s'?",
              "- Standup (%s)", "- Standup (%s).", "- Standup %s, then lunch.", "- Standup: %s; then lunch.",
              "- Standup [agenda](%s)", "- Standup [agenda](%s).", "- Standup ([agenda](%s))",
              "- Standup [[draft] agenda](%s)!"]


def _pipeline(briefing, src):
    """validate, then attach_links, then the final pass over the whole briefing, in the order
    news_engine._finish_briefing runs them."""
    from agent_friday.services import news_links
    got = bl.validate(briefing, src)
    known = bl.known_links(src)
    return got, known, bl.strip_unknown_links(news_links.attach_links(got.text, [], keep=known), known)


@pytest.mark.parametrize("url", KNOWN_SHAPES)
@pytest.mark.parametrize("placement", PLACEMENTS)
def test_every_known_address_shape_in_every_placement_survives_the_whole_pipeline_and_nothing_is_counted(url, placement):
    """No pass may cut, change or count a verified address, whatever characters it holds and wherever it stands."""
    line = placement % url
    got, known, final = _pipeline("## Calendar\n%s\n" % line, bl.collect(calendar=[{"id": "C1", "title": "Standup",
                                                                                      "url": url}]))
    assert got.dropped_total == 0 and url in _live_links(final, known), final   # a bare one comes back as <url>
    assert set(_live_links(final, known)) <= known, final
    assert bl.strip_unknown_links(final, known) == final


#: An unknown address with "()" put at every position of its scheme or "www.", so that no pattern sees an address
#: until a pass takes the brackets away.
UNKNOWN_SHAPES = sorted({prefix[:i] + gap + prefix[i:] + "evil.example.test/renew" for gap in ("()", "( )")
                         for prefix in ("www.", "https://", "http://") for i in range(len(prefix) + 1)})
UNKNOWN_PLACEMENTS = ["## Tasks\n- Renew at %s\n",                       # kept as is: not a sourced section
                      "## Calendar\nNothing today, see %s\n",           # prose in a sourced section
                      "## Calendar\n- Standup [C1] at %s\n",            # a cited item
                      "## Tasks %s\n- Renew\n",                         # a heading
                      "## Calendar\n- Standup [agenda](%s)\n"]          # a typed link


@pytest.mark.parametrize("address", UNKNOWN_SHAPES)
@pytest.mark.parametrize("placement", UNKNOWN_PLACEMENTS)
def test_no_unknown_address_is_left_or_made_by_the_whole_pipeline_wherever_it_stands(address, placement):
    """Nothing a renderer would link is left in the briefing: no http(s):// and no www. run, also one that only forms
    when a pass takes text away; and running the final pass again changes nothing."""
    got, known, final = _pipeline(placement % address, bl.collect(calendar=CAL))
    assert set(_live_links(final, known)) <= known, final
    assert bl.strip_unknown_links(final, known) == final


#: Everything that can be glued to a known address: each punctuation character alone (marked trims some, so the
#: address stands; the rest make another address) and followed by a word, a host, or an "@" and a host.
GLUED_TAILS = sorted({c + rest for c in string.punctuation for rest in ("", "x", "evil.example.test",
                                                                        "@evil.example.test/x")})
#: An item that cites, sourced prose, an item kept as it is, and a heading: the last two meet only the final pass.
GLUED_PLACEMENTS = ["## Calendar\n- Standup [C1] %s\n", "## Calendar\nNothing today: %s\n", "## Tasks\n- See %s\n",
                    "## Tasks %s\n- Renew\n", "## Tasks\n| Item | When |\n| --- | --- |\n| See %s | 9am |\n"]


#: A known link target with everything that can follow it, with a label that opens it and with none.
SPAN_SHAPES = ["[agenda](%s)", "](%s)", "\\[agenda](%s)", "[agenda]](%s)", "[C1] see ](%s)"]


@pytest.mark.parametrize("tail", GLUED_TAILS)
@pytest.mark.parametrize("shape", SPAN_SHAPES)
@pytest.mark.parametrize("placement", GLUED_PLACEMENTS)
def test_a_known_link_target_with_anything_glued_after_it_renders_only_known_links(tail, shape, placement):
    url = CAL[0]["url"]
    got, known, final = _pipeline(placement % (shape % url + tail), bl.collect(calendar=CAL))
    assert set(_live_links(final, known)) <= known, final
    assert bl.strip_unknown_links(final, known) == final


#: What can open emphasis before a known address that holds the same character: marked would end the link there.
EMPHASIS_OPENERS = ["_", "__", "*", "**", "~", "~~", "x _", "_x ", "*x ", "_[x](https://calendar.example.test/a/) "]


@pytest.mark.parametrize("opener", EMPHASIS_OPENERS)
@pytest.mark.parametrize("url", [u for u in KNOWN_SHAPES if "_" in u] + ["https://calendar.example.test/e*1*x"])
@pytest.mark.parametrize("placement", GLUED_PLACEMENTS)
def test_a_known_address_holding_an_emphasis_mark_is_never_cut_by_emphasis_opened_before_it(opener, url, placement):
    """A known address standing in the text comes back as <url>: an autolink marked parses before emphasis."""
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": url},
                               {"id": "C2", "title": "Review", "url": "https://calendar.example.test/a/"}])
    got, known, final = _pipeline(placement % (opener + url), src)
    assert "<%s>" % url in final, final
    assert bl.strip_unknown_links(final, known) == final


#: Everything that can stand right before a known address or a typed <address>: each punctuation character and
#: runs of one to three backslashes (an odd run escapes what follows).
BEFORE = sorted(set(string.punctuation) | {"\\\\", "\\\\\\"})


@pytest.mark.parametrize("before", BEFORE)
@pytest.mark.parametrize("form", ["%s", "<%s>"])
@pytest.mark.parametrize("placement", GLUED_PLACEMENTS)
def test_whatever_stands_before_a_known_address_it_renders_only_known_links(before, form, placement):
    url = CAL[0]["url"]
    got, known, final = _pipeline(placement % (before + form % url), bl.collect(calendar=CAL))
    assert set(_live_links(final, known)) <= known, final
    assert bl.strip_unknown_links(final, known) == final


@pytest.mark.parametrize("tail", GLUED_TAILS)
@pytest.mark.parametrize("placement", GLUED_PLACEMENTS)
def test_an_unknown_tail_glued_to_a_known_address_never_renders_as_a_link(tail, placement):
    """A known address with anything glued after it renders as a link only to itself: when marked would link the
    run as the address alone, it stays; otherwise the run is another address and goes. A second pass changes
    nothing."""
    url = CAL[0]["url"]
    got, known, final = _pipeline(placement % (url + tail), bl.collect(calendar=CAL))
    assert set(_live_links(final, known)) <= known, final
    assert bl.strip_unknown_links(final, known) == final


def test_a_known_address_with_only_what_a_renderer_trims_after_it_stands():
    url = CAL[0]["url"]
    for tail in (".", ",", ":", ";", "!", "?", "*", "_", "~", "'", '"', ")", ").", "**", "...", "?!"):
        got, known, final = _pipeline("## Tasks\n- See %s%s\n" % (url, tail), bl.collect(calendar=CAL))
        assert "<%s>%s" % (url, tail) in final, tail


#: marked 9.1.6's inline._backpedal, copied from static/vendor/marked-9.1.6.min.js: where an extended autolink ends.
MARKED_BACKPEDAL = re.compile(r"(?:[^?!.,:;*_'\"~()&]+|\([^)]*\)|&(?![a-zA-Z0-9]+;\Z)|[?!.,:;*_'\"~)]+(?!\Z))+")


def _marked_autolink(run):
    while True:
        found = MARKED_BACKPEDAL.match(run)
        cut = found.group(0) if found else ""
        if cut == run:
            return run
        run = cut


#: Every shape a known link can be glued to another known link in: bare to bare, bare to a link target, a link
#: to a bare address, and a bare address to a link.
KNOWN_GLUES = ["%s%s", "%s](%s)", "[x](%s)%s", "%s[x](%s)"]


@pytest.mark.parametrize("glue", KNOWN_GLUES)
@pytest.mark.parametrize("placement", GLUED_PLACEMENTS)
@pytest.mark.parametrize("first, second", [(a, b) for a in KNOWN_SHAPES for b in KNOWN_SHAPES if a != b])
def test_a_known_address_glued_to_another_known_link_never_renders_as_a_link_to_neither(glue, placement, first,
                                                                                         second):
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": first},
                               {"id": "C2", "title": "Review", "url": second}])
    got, known, final = _pipeline(placement % (glue % (first, second)), src)
    assert set(_live_links(final, known)) <= known, final
    assert bl.strip_unknown_links(final, known) == final


def _live_links(text, known):
    """Every link the app makes of the text: link and image targets, its own [web:address] links
    (ui_parts/app.html fridayCitationize), marked's extended autolinks (an http(s):// or www. run anywhere in a word,
    to the next space, ended by marked's own rule, MARKED_BACKPEDAL), and email autolinks (outside the known links).
    Code spans are not left out: no address may stand in one either."""
    links = [m.group(1) for m in re.finditer(r"\[web:([^\]\s]+)\]", text)]
    # Angle autolinks first, as marked parses them: each is its address, ended at the ">", and no other autolink
    # runs inside one. A "<" after an odd run of backslashes is escaped: a literal "<", no autolink.
    angles = [m for m in re.finditer(r"<((?:https?|ftp)://[^\s<>]*)>", text)
              if (len(text[:m.start()]) - len(text[:m.start()].rstrip("\\"))) % 2 == 0]
    links += [m.group(1) for m in angles]
    scanned = text
    for m in angles:
        scanned = scanned[:m.start()] + " " * (m.end() - m.start()) + scanned[m.end():]
    for m in re.finditer(r"\]\(", scanned):
        i = j = m.end()
        depth = 0
        while j < len(text) and not text[j].isspace():
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        links.append(text[i:j])
        # A real link (an unescaped "[" opens a label of no brackets but one balanced pair) is parsed before
        # autolinks, so none runs inside its target; "](" with nothing to open it is text, and one may.
        line_start = text.rfind("\n", 0, m.start()) + 1
        opened = re.search(r"(?<!\\)\[(?:[^\[\]\n\\]|\\.|\[[^\[\]\n]*\])*\Z", text[line_start:m.start()])
        if opened and j < len(text) and text[j] == ")":
            scanned = scanned[:i] + " " * (j - i) + scanned[j:]
    # In a table (a delimiter row under a header), every unescaped "|" on a row ends the cell, and the link in it.
    if re.search(r"(?m)^\s*\|?\s*:?-{3,}:?\s*\|", scanned):
        scanned = "\n".join(re.sub(r"(?<!\\)\|", " ", row) if row.lstrip().startswith("|") else row
                            for row in scanned.split("\n"))
    links += [_marked_autolink(m.group(0)) for m in re.finditer(r"(?i)(?:https?://|www\.)[^\s<]*", scanned)]
    outside = text
    for url in known:
        outside = outside.replace(url, " ")
    links += ["mailto:" + m.group(0) for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", outside)]
    return links


#: Text a pass takes away, standing between a backslash and a known address: once it goes, the backslash is right
#: before the address.
REMOVABLE = ["()", "( )", "(())", "www.evil.example.test ", "https://evil.example.test", "[x](https://evil.example.test)"]


@pytest.mark.parametrize("backslashes", ["\\", "\\\\\\"])
@pytest.mark.parametrize("mid", REMOVABLE)
@pytest.mark.parametrize("form", ["%s", "<%s>"])
@pytest.mark.parametrize("placement", GLUED_PLACEMENTS)
def test_a_known_address_left_after_a_backslash_by_a_removal_renders_only_known_links(backslashes, mid, form,
                                                                                     placement):
    url = CAL[0]["url"]
    got, known, final = _pipeline(placement % (backslashes + mid + form % url), bl.collect(calendar=CAL))
    assert set(_live_links(final, known)) <= known, final
    assert bl.strip_unknown_links(final, known) == final


#: Every printable ASCII character that is not a plain URL character, a few beyond ASCII, and entity-like runs.
URL_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~:/?#[]@!$&'()*+,;=%")
UNFIT_SOURCES = sorted({"https://a.example.test/a%sb" % c for c in map(chr, range(32, 127)) if c not in URL_ALLOWED}
                       | {"https://a.example.test/a%sb" % c for c in ("\u00e9", "\u00a0", "\u200b", "\uff5c", "\t")}
                       | {"https://a.example.test/?a=1%sb=2" % e for e in ("&amp;", "&#38;", "&#x26;", "&lt;", "&quot;")}
                       | {"https://a.example.test/a%sb" % e for e in ("&#41", "&#x29", "&#47..&#47evil")}
                       | {"https://a.example.test/?a=1&amp", "https://a.example.test/x&lt/y", "https://a.example.test/x&AMP"}
                       | {"https://a.example.test/a)(b", "https://a.example.test/a)", "https://a.example.test/(a"})


#: Query strings a browser leaves as written: "&" then a name it would only decode with ";" or before "=" or a
#: letter. Each is a working link, cited, in the Sources list and in a table, and the app's sanitizer keeps it whole.
AMPERSAND_SOURCES = ["https://news.example.test/story?id=7&tab=comments", "https://news.example.test/?t=9&ampm=pm",
                     "https://news.example.test/?a=1&lt=5", "https://news.example.test/?a=1&colon=2",
                     "https://news.example.test/?a=1&quote=x", "https://news.example.test/?a=1&nbsp2=y"]


@pytest.mark.parametrize("url", AMPERSAND_SOURCES)
@pytest.mark.parametrize("placement", ["## Calendar\n- Standup [C1]\n", "## Calendar\n| Item |\n| --- |\n| Standup [C1] |\n"])
def test_an_ampersand_query_a_browser_leaves_as_written_is_a_working_link(url, placement):
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": url}])
    got, known, final = _pipeline(placement, src)
    assert known == {url} and got.dropped_total == 0 and "](%s)" % url in final, final
    assert set(_live_links(final, known)) <= known


@pytest.mark.parametrize("url", UNFIT_SOURCES)
def test_a_source_address_marked_would_not_link_whole_is_no_link(url):
    """A source address marked would not link whole and as written (any character but plain URL ones, anything
    entity-like, brackets out of order) is never written: the item has no working link, so it is dropped and
    counted."""
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": url}])
    got, known, final = _pipeline("## Calendar\n- Standup [C1]\n", src)
    assert known == set() and got.dropped_total == 1 and not _live_links(final, known), final


# -- FOLLOW-UPS (PR #1115 body): a known link dropped or shown oddly, never an unknown link live --------------------

@pytest.mark.xfail(strict=True, reason="follow-up: a known address after a backslash goes rather than stays")
def test_follow_up_an_escaped_known_address_stays_linked():
    url = CAL[0]["url"]
    _, known, final = _pipeline("## Tasks\n- See \\%s\n" % url, bl.collect(calendar=CAL))
    assert url in _live_links(final, known)


@pytest.mark.xfail(strict=True, reason="follow-up: a known address that ends in what marked trims goes")
def test_follow_up_a_known_address_ending_in_a_trimmed_character_stays_linked():
    url = "https://calendar.example.test/e*1*"
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": url}])
    _, known, final = _pipeline("## Tasks\n- See %s\n" % url, src)
    assert url in _live_links(final, known)


@pytest.mark.xfail(strict=True, reason="follow-up: a source address with brackets out of order is no link at all")
def test_follow_up_a_source_address_with_brackets_out_of_order_is_still_linked():
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": "https://a.example.test/a)(b"}])
    got, known, final = _pipeline("## Calendar\n- Standup [C1]\n", src)
    assert got.dropped_total == 0


@pytest.mark.xfail(strict=True, reason="follow-up: a link whose label is its own address loses the label")
def test_follow_up_a_link_labelled_with_its_own_address_keeps_the_label():
    url = CAL[0]["url"]
    assert bl.strip_unknown_links("[%s](%s)" % (url, url), {url}) == "[%s](%s)" % (url, url)


@pytest.mark.xfail(strict=True, reason="follow-up: a known address in a code span shows as <address>")
def test_follow_up_a_known_address_in_a_code_span_is_shown_as_typed():
    url = CAL[0]["url"]
    assert bl.strip_unknown_links("`%s`" % url, {url}) == "`%s`" % url


@pytest.mark.parametrize("row", ["| Standup [C1] | 9am |", "| Standup [C1] %s | 9am |", "| Standup <%s> | 9am |"])
def test_a_known_address_holding_a_pipe_never_renders_a_cut_link_in_a_table(row):
    url = "https://calendar.example.test/x?eid=1|2"
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": url}])
    line = row % url if "%s" in row else row
    got, known, final = _pipeline("## Calendar\n| Item | When |\n| --- | --- |\n%s\n" % line, src)
    assert not _live_links(final, known), final


@pytest.mark.xfail(strict=True, reason="follow-up: a source address with a character beyond plain URL ones is no link")
@pytest.mark.parametrize("url", ["https://calendar.example.test/x?eid=1|2", "https://calendar.example.test/a\\_b",
                                 "https://calendar.example.test/caf\u00e9", "https://news.example.test/?a=1&amp;b=2"])
def test_follow_up_a_source_address_beyond_plain_url_characters_is_still_linked(url):
    src = bl.collect(calendar=[{"id": "C1", "title": "Standup", "url": url}])
    got, known, final = _pipeline("## Calendar\n- Standup [C1]\n", src)
    assert got.dropped_total == 0


@pytest.mark.xfail(strict=True, reason="follow-up: speakable leaves ')>' of <address_(x)> in spoken text")
def test_follow_up_speech_drops_an_angle_bracketed_address_whole():
    from agent_friday.services.start_my_day import speakable
    assert ">" not in speakable("See <https://calendar.example.test/e_(1)> now.")


def test_a_known_story_link_with_empty_brackets_keeps_them_in_the_sources_list():
    from agent_friday.services import news_links
    url = "https://news.example.test/story()"
    stories = [{"id": "N1", "title": "Budget passes", "source": "Example Gazette", "url": url}]
    final = bl.strip_unknown_links(news_links.attach_links("The budget passed. [N1]", stories), {url})
    assert "(%s)" % url in final and final.count(url) == 2


def test_a_known_target_set_aside_is_never_swallowed_by_an_unknown_one_before_it():
    text = "[a](https://evil.example.test/x [b](%s) then)" % PAREN_CAL
    out = bl.strip_unknown_links(text, {PAREN_CAL})
    assert "evil.example.test" not in out and "](%s)" % PAREN_CAL in out and out.count("](") == 1
