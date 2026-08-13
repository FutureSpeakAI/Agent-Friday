"""F4 (Incident 2, 2026-08-13) — the FR-2 leak detector must not flag honest
hypothetical/capability prose that merely *mentions* a tool-ish English word.

Live forensics: friday.log 10:53:24 and 10:57:39 show leaked_names=['click',
'navigate', 'browse_web', 'search_web'] fired by plain answers to capability
questions ("could you click a button for me?") that needed zero tools. The
old _LEAK_TEMPLATE made every piece of call syntax optional, so a bare word
matched. Each false positive cost ~90s of corrective-retry dead air.

The detector must require actual pseudo-call *syntax*: [name], [name(args)],
name(args), or name: {...} — never a bare English word.
"""
from __future__ import annotations

from agent_friday.services.tool_integrity import find_pseudo_toolcalls

# The browser/automation tool names from the live incident log lines.
INCIDENT_TOOLS = ["click", "navigate", "browse_web", "search_web", "type_text",
                  "query_calendar", "search_email"]


class TestBareWordsAreNotLeaks:
    def test_incident_1053_shape_capability_answer_not_flagged(self):
        # Paraphrase of the 10:53:24 trigger — an honest zero-tool answer.
        text = ("Yes, I could click a button and navigate to the news page "
                "for you — just say the word.")
        assert find_pseudo_toolcalls(text, INCIDENT_TOOLS) == []

    def test_incident_1057_shape_four_names_in_prose_not_flagged(self):
        # 10:57:39 fired on all four of these names at once, in plain prose.
        text = ("If you wanted, I can browse_web-style research things: I "
                "search the web, click through results, and navigate pages. "
                "In plain terms: I could search_web when you ask.")
        # Even awkward hyphenated/verb usage must not count without call syntax.
        assert find_pseudo_toolcalls(text, ["click", "navigate"]) == []

    def test_bare_name_with_no_syntax_not_flagged(self):
        assert find_pseudo_toolcalls(
            "You can navigate to Settings and click Save.", INCIDENT_TOOLS) == []

    def test_parenthetical_prose_after_space_not_flagged(self):
        # English parenthetical, not a call: space between word and paren.
        assert find_pseudo_toolcalls(
            "Just click (the blue one) and you're done.", INCIDENT_TOOLS) == []


class TestRealPseudoSyntaxStillCaught:
    def test_bracket_form_still_flagged(self):
        assert find_pseudo_toolcalls("[query_calendar] shows a 2pm meeting.",
                                     INCIDENT_TOOLS)

    def test_bracket_form_with_args_still_flagged(self):
        assert find_pseudo_toolcalls("[search_email(priority:high)] found 3.",
                                     INCIDENT_TOOLS)

    def test_call_parens_still_flagged(self):
        assert find_pseudo_toolcalls("search_web(\"AI news\") returned:",
                                     INCIDENT_TOOLS)

    def test_colon_brace_json_form_still_flagged(self):
        assert find_pseudo_toolcalls('navigate: {"url": "https://x.com"} done',
                                     INCIDENT_TOOLS)
