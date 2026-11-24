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


# -- round 2: link shapes the first pattern missed, prose in a sourced section, the briefing's title ----------------

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
             "[web:x%s]" % bare, "(see%s)" % bare]                    # round 4: an address glued to a word
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


# -- round 3: a www. host inside a known link; links made by raw HTML or a reference definition -------------------

GCAL = "https://www.google.com/calendar/event?eid=abc123"
WWW_NEWS = "https://www.gazette.example.test/budget"


def test_a_known_link_with_a_www_host_survives_validation_and_the_final_pass():
    src = bl.collect(news=[{"id": "N1", "title": "Budget", "url": WWW_NEWS}],
                     calendar=[{"id": "C1", "title": "Standup", "url": GCAL}])
    got = bl.validate("## Calendar\n- Standup at nine. [C1]\n## Sources\n- [Budget](%s)\n" % WWW_NEWS, src)
    final = bl.strip_unknown_links(got.text, bl.known_links(src))
    assert "(%s)" % GCAL in final and "(%s)" % WWW_NEWS in final and "(https://)" not in final
    assert bl.strip_unknown_links("see %s now" % GCAL, {GCAL}) == "see %s now" % GCAL


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


# -- round 8: a known address with round brackets in it ------------------------------------------------------------

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


def test_a_known_target_set_aside_is_never_swallowed_by_an_unknown_one_before_it():
    text = "[a](https://evil.example.test/x [b](%s) then)" % PAREN_CAL
    out = bl.strip_unknown_links(text, {PAREN_CAL})
    assert "evil.example.test" not in out and "](%s)" % PAREN_CAL in out and out.count("](") == 1
