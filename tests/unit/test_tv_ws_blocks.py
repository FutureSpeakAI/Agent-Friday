"""Markdown to the plain blocks a phone draws (tv_gateway/ws_blocks.py).

A wiki page reaches the phone as heading, para, list, table, quote, code and link blocks and nothing else: no
HTML, no image, no URL to follow. These tests hold each rule and walk hostile inputs.
"""
import pytest

from agent_friday.tv_gateway import ws_blocks as wb

PAGE = """---
title: ignored front matter
---
# Griff Jay

A **beta tester** with a [site](https://example.org/a?x=1) and a [project](../projects/brushfire.md).

## Who
- first thing
- second [link](https://other.example/p)
1. numbered

> a quote line
> and more

| When | What |
|------|------|
| Sep  | re-test |

```
code <b>stays</b> text
```
"""


def kinds(p):
    return [b["t"] for b in p.blocks]


def test_a_page_becomes_blocks_in_order_with_its_title():
    p = wb.parse(PAGE, "entities")
    assert p.title == "Griff Jay"
    assert kinds(p) == ["heading", "para", "link", "heading", "list", "quote", "table", "code"]
    assert p.blocks[0] == {"t": "heading", "level": 1, "text": "Griff Jay"}
    assert p.blocks[1]["text"] == "A beta tester with a site and a project."


def test_front_matter_is_dropped():
    assert "front matter" not in str(wb.parse(PAGE, "x").blocks)


def test_an_internal_link_becomes_a_ref_resolved_from_the_pages_folder():
    link = next(b for b in wb.parse(PAGE, "entities").blocks if b["t"] == "link")
    assert link == {"t": "link", "text": "project", "ref": "page:projects/brushfire.md"}


def test_an_external_link_is_a_source_with_its_host_and_no_url():
    p = wb.parse(PAGE, "entities")
    assert [(s["title"], s["host"]) for s in p.sources] == [("site", "example.org"), ("link", "other.example")]
    assert "https" not in str(p.blocks) and "example.org" not in str(p.blocks)


def test_a_table_has_columns_and_rows():
    t = next(b for b in wb.parse(PAGE, "x").blocks if b["t"] == "table")
    assert t == {"t": "table", "columns": ["When", "What"], "rows": [["Sep", "re-test"]]}


def test_code_is_kept_as_text_not_parsed():
    c = next(b for b in wb.parse(PAGE, "x").blocks if b["t"] == "code")
    assert c["text"] == "code <b>stays</b> text"


def test_html_and_images_are_dropped_from_text():
    p = wb.parse('Hello <script>alert(1)</script> ![pic](a.png) <img src="x"> world', "x")
    assert p.blocks == [{"t": "para", "text": "Hello alert(1) world"}]


def test_no_url_leaves_in_a_table_cell_a_bare_address_or_an_auto_link():
    page = ("| Site | Note |\n| --- | --- |\n| [site](https://evil.example/x?t=SECRET) | see www.bare.example/p?k=1 |\n\n"
            "para https://bare.example/p?token=Z and <https://auto.example/q> and <a href=\"https://h.example/\">the call</a>\n\n"
            "# Head [h](https://head.example/?s=1)\n")
    text = str(wb.parse(page).blocks)
    for leak in ("http", "www", "SECRET", "token=Z", "k=1", "evil.example", "bare.example", "auto.example", "h.example"):
        assert leak not in text, leak
    assert "site" in text and "the call" in text and "Head h" in text          # the words stay, only the addresses go


@pytest.mark.parametrize("text", [
    "_https://x.y/?t=SECRET_ after", "__https://x.y/?t=SECRET__ after", "a1https://x.y/?t=SECRET after",
    "[a](https://e.x/a_(b)?t=SECRET) after", "see ssh://h.x/?t=SECRET after", "~~www.x.y/?t=SECRET~~ after",
    "| A | B |\n| - | - |\n| _https://x.y/?t=SECRET_ | [l](https://e.x/a_(b)?t=SECRET) |",
])
def test_an_address_wrapped_in_emphasis_or_brackets_does_not_get_through(text):
    got = str(wb.parse(text).blocks)
    assert "SECRET" not in got and "://" not in got and "www" not in got


@pytest.mark.parametrize("target", ["/etc/passwd.md", "../../secret.md", "C:\\x\\y.md", "http://x/y.md",
                                    "../..", "notes.png", "mailto:a@b.c", "javascript:alert(1)"])
def test_a_link_that_leaves_the_wiki_or_is_not_a_page_gives_no_ref(target):
    assert wb.page_ref("entities", target) is None
    assert not [b for b in wb.parse("see [x](%s)" % target, "entities").blocks if b["t"] == "link"]


def test_a_relative_page_ref_keeps_inside_the_wiki():
    assert wb.page_ref("a/b", "../c.md#top") == "page:a/c.md"
    assert wb.page_ref("", "d.txt") == "page:d.txt"


def test_a_wiki_link_keeps_its_words_without_brackets():
    assert wb.parse("see [[Brushfire|the project]] and [[Garden]]", "x").blocks == [
        {"t": "para", "text": "see the project and Garden"}]


def test_every_text_field_is_capped():
    p = wb.parse("# %s\n\n%s\n\n- %s\n\n> %s\n\n```\n%s\n```" % (("h" * 9000,) * 5), "x")
    for b in p.blocks:
        for v in (b.get("text"), *(b.get("items") or [])):
            assert v is None or len(v) <= wb.TEXT_MAX


def test_long_lists_and_tables_are_capped():
    p = wb.parse("\n".join("- item %d" % i for i in range(500)), "x")
    assert len(p.blocks[0]["items"]) == wb.LIST_ITEMS_MAX
    t = wb.parse("| a |\n|---|\n" + "\n".join("| %d |" % i for i in range(500)), "x")
    assert len(t.blocks[0]["rows"]) == wb.TABLE_ROWS_MAX


@pytest.mark.parametrize("hostile", ["", "\x00\x01\x02", "#", "####### seven", "|", "| a |\n|", "```", "> ", "- ", "[](",
                                     "[a](", "![", "<", "---", "---\n", "\n\n\n", "[x](" + "a" * 5000 + ")",
                                     "# " + "\u202e" * 100, "| a | b |\n|--|--|\n| 1 |"])
def test_hostile_input_never_raises_and_gives_only_known_blocks(hostile):
    p = wb.parse(hostile, "x")
    assert {b["t"] for b in p.blocks} <= {"heading", "para", "list", "table", "quote", "code", "link"}


def test_none_is_an_empty_page():
    assert wb.parse(None, "x").blocks == []
