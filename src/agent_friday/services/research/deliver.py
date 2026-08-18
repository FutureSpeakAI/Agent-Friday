"""
Delivery — land, style, surface, and say so (§3.7 / RS9).

Four things, and none of them is optional:

  1. LAND. The report writes through the wiki engine, never `write_file`.
     write_file bypasses the Drive mirror, the encryption check, and the
     knowledge-graph dirty-marking — a report written that way is invisible to
     GraphRAG, so the NEXT commission cannot cite it and research does not
     compound. Every citation lands as a real clickable link.
  2. STYLE. The markdown is rendered into a standalone HTML page by a fixed
     template. Deterministic — a model writes the content, a template writes
     the page. Same discipline the showcase engine uses for decks and sites.
  3. SURFACE. The styled page is a file the UI can open in a new tab, and the
     commission is listed.
  4. SAY SO. A proactive chat message with the lede. A commission that
     completes without the user hearing about it is a failure even when it
     exits zero — that is the whole reason P4 existed.

The colophon names the models that ACTUALLY served each stage, including
fallbacks and promotions. Never a vendor, never a model that did not serve.
"""
from __future__ import annotations

import html
import logging
import re
import time
from pathlib import Path

_log = logging.getLogger("friday.research.deliver")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", (text or "report")).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    return (s[:70] or "report").strip("-")


# ── markdown ──────────────────────────────────────────────────────────────────

def build_markdown(c, verified: dict) -> str:
    """The canonical report. Citations are real links, per Q1."""
    findings = {f.id: f for f in c.findings()}
    keep = set(verified.get("verified_ids") or [])

    lines: list[str] = []
    title = (c.plan.working_title if c.plan else c.question) or c.question
    lines.append(f"# {title}\n")
    lines.append(f"*Research commission `{c.id}` — "
                 f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(c.created_at))}*\n")
    lines.append(f"**Question asked:** {c.question}\n")

    if verified.get("answer"):
        lines.append("## Answer\n")
        lines.append(verified["answer"] + "\n")

    for sec in verified.get("sections", []):
        body = sec.get("body", "")
        if not body.strip():
            continue
        lines.append(f"## {sec.get('heading', 'Findings')}\n")
        lines.append(_link_citations(body, findings, keep) + "\n")

    # Mandatory section. May be empty, is never absent (§3.0).
    # A model he NAMED that we could not reach is disclosed in the report,
    # not left in a log line. His original bug report was "I asked for
    # Opus 5 but it ran on Gemma4" — a silent fallback here is that same
    # defect wearing a different hat.
    subs = list(getattr(c, "substitutions", []) or [])
    if subs:
        lines.append("## Where I could not use the model you asked for" + chr(10))
        for s in subs:
            lines.append("- " + s)
        lines.append("")

    lines.append("## What I could not confirm\n")
    unc = verified.get("unconfirmed") or []
    if not unc:
        lines.append("*Every claim in this report resolved to a quote in a "
                     "page I actually fetched.*\n")
    else:
        for u in unc:
            lines.append(f"- **{u.get('claim','')}** — {u.get('what_was_tried','')}")
        lines.append("")

    # Sources, deduped, every one a real href.
    srcs, seen = [], set()
    for fid in keep:
        f = findings.get(fid)
        if f and f.url and f.url not in seen:
            seen.add(f.url)
            srcs.append(f)
    if srcs:
        lines.append("## Sources\n")
        for i, f in enumerate(srcs, 1):
            lines.append(f"{i}. [{f.url}]({f.url})")
        lines.append("")

    lines.append("---\n")
    lines.append(_colophon_line(c, verified) + "\n")
    return "\n".join(lines)


def _link_citations(body: str, findings: dict, keep: set) -> str:
    """Turn [F:id] markers into real clickable links to the source."""
    def sub(m):
        fid = m.group(1)
        f = findings.get(fid)
        if not f or fid not in keep:
            return ""
        return f" ([source]({f.url}))" if f.url else ""
    return re.sub(r"\s*\[F:([a-z0-9]+)\]", sub, body)


def _colophon_line(c, verified: dict) -> str:
    col = c.colophon or {}
    bits = []
    if col.get("scoped_by"):
        prot = c.protection.sentence()
        bits.append(f"Scoped by {col['scoped_by']}"
                    + (f" ({prot.rstrip('.')})" if c.protection.scrub_count else ""))
    if col.get("ground_by"):
        bits.append("ground by " + " + ".join(col["ground_by"]))
    if verified.get("synthesized_by"):
        note = verified.get("seat_note") or ""
        bits.append(f"synthesized by {verified['synthesized_by']}"
                    + (f" ({note})" if note else ""))
    bits.append(f"{col.get('fetches', 0)} fetches")
    bits.append(f"{verified.get('claims_verified', 0)} claims confirmed, "
                f"{verified.get('claims_struck', 0)} struck in verification")
    if col.get("wall_clock_s"):
        bits.append(f"{col['wall_clock_s'] / 60:.0f} min")
    return "*" + " · ".join(bits) + "*"


# ── landing ───────────────────────────────────────────────────────────────────

def land(c, markdown: str) -> str | None:
    """Write into the wiki so GraphRAG indexes it and research compounds."""
    try:
        from agent_friday.services.wiki_engine import WIKI_DIR, wiki_write_text
        rel = f"Research/{_slug(c.plan.working_title if c.plan else c.question)}.md"
        path = Path(WIKI_DIR) / rel
        wiki_write_text(path, markdown)
        c.report_path = rel
        c.save()
        c.log(f"landed in the wiki at {rel}")
        return rel
    except Exception as e:
        _log.error("could not land the report: %s", e)
        c.log(f"could not land the report in the wiki: {e}")
        return None


# ── styling ───────────────────────────────────────────────────────────────────

_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root {{ --bg:#fbfaf8; --fg:#1b1a18; --mut:#6b6560; --line:#e3ded7; --acc:#7a4b2a; }}
 @media (prefers-color-scheme: dark) {{
   :root {{ --bg:#16151a; --fg:#eceaf0; --mut:#a09aa8; --line:#2f2d36; --acc:#d8a06a; }} }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:var(--bg); color:var(--fg);
   font:16px/1.65 ui-serif,Georgia,"Iowan Old Style",serif; }}
 main {{ max-width:44rem; margin:0 auto; padding:3rem 1.4rem 6rem; }}
 h1 {{ font-size:2.1rem; line-height:1.2; margin:0 0 .4rem; letter-spacing:-.01em; }}
 h2 {{ font-size:1.25rem; margin:2.4rem 0 .7rem; padding-bottom:.3rem;
   border-bottom:1px solid var(--line); }}
 a {{ color:var(--acc); }}
 .meta {{ color:var(--mut); font-size:.86rem; margin-bottom:2rem;
   font-family:ui-sans-serif,system-ui,sans-serif; }}
 .colophon {{ margin-top:3rem; padding-top:1rem; border-top:1px solid var(--line);
   color:var(--mut); font-size:.82rem; font-family:ui-sans-serif,system-ui,sans-serif; }}
 .unconfirmed {{ background:color-mix(in srgb,var(--acc) 8%,transparent);
   border-left:3px solid var(--acc); padding:.8rem 1rem; border-radius:0 4px 4px 0; }}
 ol,ul {{ padding-left:1.3rem; }}
 li {{ margin:.3rem 0; word-break:break-word; }}
 code {{ font-size:.9em; }}
 table {{ display:block; overflow-x:auto; }}
</style>
<main>{body}</main>
"""


def _md_to_html(md: str) -> str:
    """Small, deterministic markdown -> HTML. No model in this path.

    Deliberately not a full markdown engine: the input is generated by our own
    template above, so the subset it uses is the subset that needs support.
    """
    out, in_list = [], False
    for raw in md.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if line.startswith("---"):
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", line)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        cls = ' class="meta"' if line.startswith("*") and line.endswith("*") else ""
        out.append(f"<p{cls}>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"(?<!href=\")(?<!>)(https?://[^\s<)]+)",
               r'<a href="\1" target="_blank" rel="noopener">\1</a>', s)
    return s


def style(c, markdown: str) -> str | None:
    """Render the landed markdown into Friday's page style."""
    try:
        title = (c.plan.working_title if c.plan else c.question) or "Research"
        page = _PAGE.format(title=html.escape(title), body=_md_to_html(markdown))
        out = c.dir / "report.html"
        out.write_text(page, encoding="utf-8")
        c.styled_path = str(out)
        c.save()
        c.log(f"styled page written to {out}")
        return str(out)
    except Exception as e:
        _log.error("could not style the report: %s", e)
        c.log(f"could not style the report: {e}")
        return None


# ── surfacing ─────────────────────────────────────────────────────────────────

def announce(c, verified: dict) -> bool:
    """The unprompted report into the conversation (RS9). Returns whether it
    actually went out — a delivery step that silently no-ops is the bug."""
    conf = verified.get("claims_verified", 0)
    struck = verified.get("claims_struck", 0)
    unc = len(verified.get("unconfirmed") or [])
    where = c.report_path or "(not landed)"
    msg = (f"Research finished: *\"{c.question}\"* — "
           f"{conf} claim{'s' if conf != 1 else ''} confirmed"
           + (f" across {len(set(f.url for f in c.findings() if f.url))} sources"
              if conf else "")
           + (f", {struck} struck in verification" if struck else "")
           + (f", {unc} thing{'s' if unc != 1 else ''} I couldn't confirm "
              f"(flagged in the report)" if unc else "")
           + f".\n\nFull report: {where}"
           + (" (opens in a new tab)" if c.styled_path else "")
           + "\n\n" + _colophon_line(c, verified))
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=f"Research finished: {c.question[:60]}",
                body=f"{conf} confirmed, {struck} struck, {unc} unconfirmed.",
                proactive_chat=True, chat_message=msg,
                target={"kind": "research", "id": c.id,
                        "open": c.styled_path or ""})
        c.log("announced into the conversation")
        return True
    except Exception as e:
        _log.error("could not announce the finished commission: %s", e)
        c.log(f"COULD NOT ANNOUNCE the finished commission: {e}")
        return False


def announce_failure(c) -> bool:
    """A failed commission reports too — silence reads exactly like success."""
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=f"Research failed: {c.question[:60]}",
                body=c.failure or "No report was produced.",
                proactive_chat=True,
                chat_message=(f"Research **failed**: *\"{c.question}\"*\n\n"
                              f"{c.failure or 'No report was produced.'}\n\n"
                              f"I am not giving you a report, because I do not "
                              f"have one."),
                target={"kind": "research", "id": c.id})
        c.log("announced the failure")
        return True
    except Exception as e:
        c.log(f"could not announce the failure: {e}")
        return False
