"""Blocks -> sections -> passages -> routing profiles.

Everything here is deterministic. No model writes any text that enters the
index: a routing profile is the title, the heading outline and the first
sentence, so a document cannot plant instructions into a summary that later
steers a search.
"""
from __future__ import annotations

import re

from agent_friday.services.library import caps
from agent_friday.services.library.caps import CapExceeded
from agent_friday.services.library.textclean import one_line

PASSAGE_MAX_CHARS = 900          # fits the encoder's 256-token window
SYNTH_PAGES = 10
SYNTH_WORDS = 4000
PROFILE_CHARS = 240

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def build_sections(blocks: list[dict], title: str) -> list[dict]:
    """Sections from the heading tree, each with the ordinal range of its own
    blocks (`first`..`last`, headings excluded) and its page span.

    A document with no headings gets synthetic sections every ~10 pages or
    ~4,000 words. Blocks before the first heading belong to an 'Opening'
    section so no text is outside the tree."""
    headings = [b for b in blocks if b["kind"] == "heading"]
    sections: list[dict] = []

    def new(heading, level, parent, start):
        s = {"id": len(sections), "parent": parent, "heading": heading, "level": level,
             "first": start, "last": start - 1, "page_from": None, "page_to": None, "blocks": []}
        sections.append(s)
        if len(sections) > caps.MAX_SECTIONS:
            raise CapExceeded("too many sections")
        return s

    if not headings:
        cur = new("Opening" if len(blocks) < 2 else "Part 1", 1, None, 0)
        words = 0
        part = 1
        p0 = None
        for b in blocks:
            w = len(b["text"].split())
            pg = b.get("page")
            if cur["blocks"] and (words + w > SYNTH_WORDS or (pg and p0 and pg - p0 >= SYNTH_PAGES)):
                part += 1
                cur = new(f"Part {part}", 1, None, b["ord"])
                words, p0 = 0, None
            cur["blocks"].append(b)
            words += w
            if pg and p0 is None:
                p0 = pg
        _finish(sections)
        _label_parts(sections)
        return sections

    stack: list[dict] = []
    cur = None
    for b in blocks:
        if b["kind"] == "heading":
            lvl = max(1, int(b["level"] or 1))
            while stack and stack[-1]["level"] >= lvl:
                stack.pop()
            s = new(b["text"], lvl, stack[-1]["id"] if stack else None, b["ord"] + 1)
            s["heading_block"] = b["ord"]
            if b.get("page"):
                s["page_from"] = b["page"]
            stack.append(s)
            cur = s
        else:
            if cur is None:
                cur = new("Opening", 1, None, b["ord"])
                stack.append(cur)
            cur["blocks"].append(b)
    _finish(sections)
    return sections


def _finish(sections: list[dict]) -> None:
    for s in sections:
        bl = s["blocks"]
        if bl:
            s["first"], s["last"] = bl[0]["ord"], bl[-1]["ord"]
            pages = [b["page"] for b in bl if b.get("page")]
            if pages:
                s["page_from"] = min(pages + ([s["page_from"]] if s["page_from"] else []))
                s["page_to"] = max(pages)
        if s["page_from"] and not s["page_to"]:
            s["page_to"] = s["page_from"]
    # A parent spans its children.
    by_id = {s["id"]: s for s in sections}
    for s in reversed(sections):
        par = by_id.get(s["parent"])
        if par and s["page_from"]:
            par["page_from"] = min(filter(None, [par["page_from"], s["page_from"]]))
            par["page_to"] = max(filter(None, [par["page_to"], s["page_to"]]))


def _label_parts(sections: list[dict]) -> None:
    for s in sections:
        if s["page_from"] and s["page_to"] and s["heading"].startswith("Part "):
            s["heading"] = (f"Pages {s['page_from']}–{s['page_to']}"
                            if s["page_to"] != s["page_from"] else f"Page {s['page_from']}")


def _split_sentences(text: str, limit: int) -> list[str]:
    out, cur = [], ""
    for sent in _SENT.split(text):
        while len(sent) > limit:                      # no sentence boundary: hard split at a space
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > limit:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return [o for o in out if o]


def _table_pieces(block: dict, limit: int) -> list[str]:
    """A table splits only between rows; every piece repeats the header row."""
    lines = block["text"].split("\n")
    header = block.get("header") or lines[0]
    rows = lines[1:] if lines and lines[0] == header else lines
    pieces, cur, size = [], [], len(header) + 1
    for r in rows:
        r = r[: max(80, limit - len(header) - 2)]       # a single enormous row is cut, not split mid-cell
        if cur and size + len(r) + 1 > limit:
            pieces.append("\n".join([header] + cur))
            cur, size = [], len(header) + 1
        cur.append(r)
        size += len(r) + 1
    pieces.append("\n".join([header] + cur) if cur else header)
    return pieces


def build_passages(section: dict, limit: int = PASSAGE_MAX_CHARS) -> list[dict]:
    """Passages of a section: whole paragraphs packed up to `limit` characters;
    a longer paragraph splits on sentences, a table between rows. Each passage
    lists the block ordinals it covers, so a footnote can point at a paragraph."""
    out: list[dict] = []
    cur_text, cur_ids = "", []

    def flush():
        nonlocal cur_text, cur_ids
        if cur_text.strip():
            out.append({"text": cur_text.strip(), "blocks": cur_ids, "chars": len(cur_text.strip())})
        cur_text, cur_ids = "", []

    for b in section["blocks"]:
        t = b["text"]
        if b["kind"] == "table":
            flush()
            for piece in _table_pieces(b, limit):
                out.append({"text": piece, "blocks": [b["ord"]], "chars": len(piece)})
            continue
        if len(t) > limit:
            flush()
            for piece in _split_sentences(t, limit):
                out.append({"text": piece, "blocks": [b["ord"]], "chars": len(piece)})
            continue
        if cur_text and len(cur_text) + 2 + len(t) > limit:
            flush()
        cur_text = f"{cur_text}\n\n{t}" if cur_text else t
        cur_ids = cur_ids + [b["ord"]]
    flush()
    return out


# ── profiles ─────────────────────────────────────────────────────────────────

def first_sentence(text: str, limit: int = 120) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    m = _SENT.split(t, maxsplit=1)
    return one_line(m[0], limit)


def document_profile(title: str, sections: list[dict]) -> str:
    tops = [s["heading"] for s in sections if s["parent"] is None and s["heading"] not in ("Opening",)
            and not s["heading"].startswith(("Pages ", "Page "))][:6]
    outline = "; ".join(one_line(h, 40) for h in tops)
    first = next((s["blocks"][0]["text"] for s in sections if s["blocks"]), "")
    parts = [one_line(title, 80)]
    if outline:
        parts.append(outline)
    if first and len(" — ".join(parts)) < PROFILE_CHARS - 40:
        parts.append(first_sentence(first, 90))
    return one_line(" — ".join(parts), PROFILE_CHARS)


def section_profile(section: dict, children: list[dict]) -> str:
    sub = "; ".join(one_line(c["heading"], 36) for c in children[:5])
    parts = [one_line(section["heading"], 80)]
    if sub:
        parts.append(sub)
    first = section["blocks"][0]["text"] if section["blocks"] else ""
    if first and len(" — ".join(parts)) < PROFILE_CHARS - 40:
        parts.append(first_sentence(first, 100))
    return one_line(" — ".join(parts), PROFILE_CHARS)


def folder_profile(name: str, titles: list[str]) -> str:
    return one_line(f"Folder {name}: " + "; ".join(one_line(t, 40) for t in titles[:6]), PROFILE_CHARS)
