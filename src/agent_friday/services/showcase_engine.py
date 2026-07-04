"""Showcase engine — presentation decks and websites as gallery creations.

Two generators with the same shape:

  generate_presentation(topic, ...)  → friday-deck-<ts>.html   (keyboard deck)
  generate_website(brief, ...)       → friday-site-<ts>.html   (hash-routed site)

Both run the SAME two-stage pipeline:

  1. The user's routed text model (via ``_generate_text`` — Anthropic, OpenAI
     or local Ollama, exactly like briefings) produces a strict JSON spec:
     an outline, not markup.
  2. Python renders that spec through a fixed, hand-written HTML template.

The LLM never writes HTML. That split is deliberate: a model asked for raw
markup produces something different (and occasionally broken) every run, while
a JSON outline through a deterministic template gives the same polished,
self-contained, offline-safe artifact every time — which is what a live demo
needs. Output lands in ~/Desktop/friday-creations/ so the Studio gallery and
the /creation/<file> framed view pick it up with zero extra wiring.
"""

import html
import json
import logging
import re
from datetime import datetime

from agent_friday.core import CREATIONS_DIR

_log = logging.getLogger("friday.showcase")

# Palette shared by both templates — Friday's dark-neon Studio look.
_BG = "#07080d"
_PANEL = "#10121c"
_TEXT = "#e8e8f0"
_MUTED = "#a8a8b4"
_ACCENT = "#00d4ff"
_ACCENT2 = "#a78bfa"


# ── Spec parsing ───────────────────────────────────────────────────────────

def _parse_json_spec(text):
    """Extract the first JSON object from an LLM reply (fences tolerated)."""
    if not text:
        return None
    t = text.strip()
    if "```" in t:
        # Take the largest fenced block if any fence is present.
        blocks = re.findall(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
        if blocks:
            t = max(blocks, key=len).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(t[start:end + 1])
    except Exception:
        return None


def _spec_from_llm(prompt, orb_label, workspace=None):
    """One-shot JSON spec from the routed text provider; one strict retry."""
    from agent_friday.services.model_router import _generate_text
    raw = _generate_text(prompt, orb_label=orb_label, workspace=workspace)
    spec = _parse_json_spec(raw)
    if spec is None:
        raw = _generate_text(
            prompt + "\n\nIMPORTANT: your previous reply was not parseable. "
                     "Respond with ONLY the raw JSON object — no prose, no "
                     "markdown fences.",
            orb_label=orb_label, workspace=workspace)
        spec = _parse_json_spec(raw)
    return spec


def _e(s):
    return html.escape(str(s or ""), quote=True)


def _save_creation(filename, html_text, orb):
    from agent_friday.services.creations import _notify_creation
    CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = CREATIONS_DIR / filename
    path.write_text(html_text, encoding="utf-8")
    try:
        _notify_creation(filename, orb)
    except Exception:
        pass
    return {"filename": filename, "url": f"/api/creations/{filename}",
            "path": str(path)}


def _routed_model_label():
    try:
        from agent_friday.core import _load_settings
        return (_load_settings() or {}).get("orchestrator_model") or "routed-text"
    except Exception:
        return "routed-text"


# ── Presentations ──────────────────────────────────────────────────────────

_DECK_PROMPT = """You are building the content outline for a presentation deck.

Topic / brief:
{topic}
{style_line}
Return ONLY a raw JSON object (no markdown fences, no commentary) with EXACTLY
this shape:

{{"title": "deck title, <= 8 words",
  "subtitle": "one-line subtitle",
  "slides": [
    {{"kicker": "2-4 word section label",
      "title": "slide headline, <= 10 words",
      "bullets": ["3 to 5 bullets, each <= 14 words, concrete and punchy"],
      "notes": "1-3 sentence speaker notes for this slide"}}
  ],
  "closing": "one memorable closing line"}}

Produce exactly {n} content slides. No bullet may repeat another slide's
point. Write for a spoken presentation: verbs first, no filler."""


def generate_presentation(topic, slides=None, style=None, workspace=None):
    """Generate a self-contained HTML slide deck into the creations gallery.

    Returns the creative-engine style envelope:
    {"status": "ok"|"error", "files": [...], "model": ..., "message": ...}
    """
    from agent_friday.services.creations import _creation_orb_start
    topic = (topic or "").strip()
    if not topic:
        return {"status": "error", "message": "A topic is required."}
    try:
        n = max(3, min(int(slides or 8), 16))
    except (TypeError, ValueError):
        n = 8
    orb = None
    try:
        orb = _creation_orb_start("Slide deck")
    except Exception:
        pass
    style_line = f"\nStyle hints: {style.strip()}\n" if (style or "").strip() else ""
    try:
        spec = _spec_from_llm(
            _DECK_PROMPT.format(topic=topic, n=n, style_line=style_line),
            orb_label="Slide deck", workspace=workspace)
    except Exception as e:
        _log.warning("presentation spec generation failed: %s", e, exc_info=True)
        return {"status": "error",
                "message": f"Could not generate the deck outline: {e}"}
    if not spec or not isinstance(spec.get("slides"), list) or not spec["slides"]:
        return {"status": "error",
                "message": "The text model did not return a usable deck outline."}

    page = _render_deck_html(spec)
    fn = f"friday-deck-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    finfo = _save_creation(fn, page, orb)
    return {"status": "ok", "kind": "presentation", "model": _routed_model_label(),
            "files": [finfo],
            "message": (f"Deck '{spec.get('title', topic)}' created with "
                        f"{len(spec['slides'])} content slides. Arrow keys / space "
                        f"navigate; N toggles speaker notes; printing exports PDF.")}


def _render_deck_html(spec):
    title = _e(spec.get("title") or "Untitled Deck")
    subtitle = _e(spec.get("subtitle") or "")
    closing = _e(spec.get("closing") or "Thank you.")
    slides_html = [
        f'<section class="slide title-slide">'
        f'<div class="kicker">Agent Friday · Presentation</div>'
        f'<h1>{title}</h1>'
        f'<p class="subtitle">{subtitle}</p>'
        f'</section>'
    ]
    for s in spec.get("slides", []):
        if not isinstance(s, dict):
            continue
        bullets = "".join(f"<li>{_e(b)}</li>" for b in (s.get("bullets") or [])[:6])
        notes = _e(s.get("notes") or "")
        slides_html.append(
            f'<section class="slide">'
            f'<div class="kicker">{_e(s.get("kicker") or "")}</div>'
            f'<h2>{_e(s.get("title") or "")}</h2>'
            f'<ul>{bullets}</ul>'
            + (f'<div class="notes">🗒 {notes}</div>' if notes else "")
            + f'</section>'
        )
    slides_html.append(
        f'<section class="slide closing-slide">'
        f'<h1>{closing}</h1>'
        f'<p class="subtitle">Built by Agent Friday</p>'
        f'</section>'
    )
    body = "\n".join(slides_html)
    count = len(slides_html)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  html,body{{margin:0;height:100%;background:{_BG};color:{_TEXT};
    font-family:'Segoe UI',system-ui,-apple-system,sans-serif;overflow:hidden}}
  .slide{{display:none;position:fixed;inset:0;padding:8vh 10vw;
    flex-direction:column;justify-content:center;box-sizing:border-box;
    background:radial-gradient(1200px 600px at 85% -10%,rgba(0,212,255,.10),transparent),
               radial-gradient(1000px 500px at -10% 110%,rgba(167,139,250,.10),transparent),{_BG}}}
  .slide.active{{display:flex}}
  .kicker{{color:{_ACCENT};letter-spacing:.28em;text-transform:uppercase;
    font-size:clamp(11px,1.1vw,15px);margin-bottom:2.2vh;font-weight:600}}
  h1{{font-size:clamp(34px,5.2vw,72px);line-height:1.08;margin:0 0 2vh;
    background:linear-gradient(92deg,{_ACCENT},{_ACCENT2});
    -webkit-background-clip:text;background-clip:text;color:transparent}}
  h2{{font-size:clamp(26px,3.6vw,52px);line-height:1.12;margin:0 0 3.5vh}}
  .subtitle{{color:{_MUTED};font-size:clamp(15px,1.6vw,22px);margin:0}}
  ul{{margin:0;padding-left:1.2em}}
  li{{font-size:clamp(16px,1.9vw,26px);line-height:1.45;margin:1.4vh 0;opacity:.94}}
  li::marker{{color:{_ACCENT}}}
  .notes{{display:none;margin-top:4vh;padding:14px 18px;border-left:3px solid {_ACCENT2};
    background:{_PANEL};color:{_MUTED};font-size:clamp(12px,1.1vw,15px);border-radius:0 10px 10px 0}}
  body.show-notes .notes{{display:block}}
  .hud{{position:fixed;bottom:3.2vh;left:0;right:0;display:flex;
    justify-content:center;gap:8px;z-index:5}}
  .dot{{width:8px;height:8px;border-radius:50%;background:#2a2e40;cursor:pointer}}
  .dot.on{{background:{_ACCENT}}}
  .counter{{position:fixed;bottom:2.8vh;right:3vw;color:{_MUTED};
    font-size:12px;font-variant-numeric:tabular-nums}}
  .brand{{position:fixed;bottom:2.8vh;left:3vw;color:{_MUTED};font-size:12px}}
  @media print{{
    html,body{{overflow:visible;background:{_BG}}}
    .slide{{display:flex;position:relative;inset:auto;height:100vh;page-break-after:always}}
    .hud,.counter,.brand{{display:none}}
  }}
</style></head><body>
{body}
<div class="hud" id="hud"></div>
<div class="counter" id="ctr"></div>
<div class="brand">⚡ Agent Friday</div>
<script>
(function(){{
  var slides=[].slice.call(document.querySelectorAll('.slide')),i=0;
  var hud=document.getElementById('hud'),ctr=document.getElementById('ctr');
  slides.forEach(function(_,k){{var d=document.createElement('div');
    d.className='dot';d.onclick=function(){{go(k)}};hud.appendChild(d)}});
  function go(k){{
    i=Math.max(0,Math.min(k,slides.length-1));
    slides.forEach(function(s,j){{s.classList.toggle('active',j===i)}});
    [].slice.call(hud.children).forEach(function(d,j){{d.classList.toggle('on',j===i)}});
    ctr.textContent=(i+1)+' / '+{count};
  }}
  document.addEventListener('keydown',function(e){{
    if(e.key==='ArrowRight'||e.key===' '||e.key==='PageDown')go(i+1);
    else if(e.key==='ArrowLeft'||e.key==='PageUp')go(i-1);
    else if(e.key==='Home')go(0);
    else if(e.key==='End')go(slides.length-1);
    else if(e.key==='n'||e.key==='N')document.body.classList.toggle('show-notes');
  }});
  document.addEventListener('click',function(e){{
    if(e.target.closest('.hud'))return;
    go(e.clientX>window.innerWidth/2?i+1:i-1);
  }});
  go(0);
}})();
</script>
</body></html>"""


# ── Websites ───────────────────────────────────────────────────────────────

_SITE_PROMPT = """You are producing the content spec for a small marketing /
info website.

Brief:
{brief}
{style_line}
Return ONLY a raw JSON object (no markdown fences, no commentary) with EXACTLY
this shape:

{{"site_title": "short site name",
  "tagline": "one-line tagline",
  "pages": [
    {{"slug": "url-safe-slug",
      "nav": "short nav label",
      "title": "page headline",
      "hero": "1-2 sentence hero paragraph",
      "sections": [
        {{"heading": "section heading",
          "body": "1-3 sentence paragraph",
          "bullets": ["optional list items, <= 12 words each"],
          "cards": [{{"title": "card title", "text": "1-2 sentences"}}]
        }}
      ]}}
  ],
  "footer": "one-line footer"}}

Produce exactly {n} pages; the FIRST page is the landing page. Each page gets
2-4 sections. Use "bullets" OR "cards" per section only when they genuinely
fit — plain body text is fine. Concrete, confident copy; no lorem ipsum."""


def generate_website(brief, pages=None, style=None, workspace=None):
    """Generate a self-contained multi-page (hash-routed) website into the gallery."""
    from agent_friday.services.creations import _creation_orb_start
    brief = (brief or "").strip()
    if not brief:
        return {"status": "error", "message": "A site brief is required."}
    try:
        n = max(1, min(int(pages or 4), 6))
    except (TypeError, ValueError):
        n = 4
    orb = None
    try:
        orb = _creation_orb_start("Website")
    except Exception:
        pass
    style_line = f"\nStyle hints: {style.strip()}\n" if (style or "").strip() else ""
    try:
        spec = _spec_from_llm(
            _SITE_PROMPT.format(brief=brief, n=n, style_line=style_line),
            orb_label="Website", workspace=workspace)
    except Exception as e:
        _log.warning("website spec generation failed: %s", e, exc_info=True)
        return {"status": "error",
                "message": f"Could not generate the site spec: {e}"}
    if not spec or not isinstance(spec.get("pages"), list) or not spec["pages"]:
        return {"status": "error",
                "message": "The text model did not return a usable site spec."}

    page = _render_site_html(spec)
    fn = f"friday-site-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    finfo = _save_creation(fn, page, orb)
    return {"status": "ok", "kind": "website", "model": _routed_model_label(),
            "files": [finfo],
            "message": (f"Website '{spec.get('site_title', brief[:40])}' created "
                        f"with {len(spec['pages'])} pages (hash navigation, single "
                        f"self-contained file — works offline and deploys anywhere).")}


def _slugify(s, fallback):
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")
    return s or fallback


def _render_site_html(spec):
    site_title = _e(spec.get("site_title") or "Untitled Site")
    tagline = _e(spec.get("tagline") or "")
    footer = _e(spec.get("footer") or f"{site_title} — built by Agent Friday")
    pages = [p for p in spec.get("pages", []) if isinstance(p, dict)]
    navs, page_html = [], []
    for idx, p in enumerate(pages):
        slug = _slugify(p.get("slug"), f"page-{idx + 1}")
        navs.append(f'<a href="#/{slug}" data-slug="{slug}">{_e(p.get("nav") or p.get("title") or slug)}</a>')
        sections = []
        for sec in (p.get("sections") or []):
            if not isinstance(sec, dict):
                continue
            inner = ""
            if sec.get("body"):
                inner += f'<p>{_e(sec["body"])}</p>'
            if sec.get("bullets"):
                inner += "<ul>" + "".join(f"<li>{_e(b)}</li>" for b in sec["bullets"][:8]) + "</ul>"
            if sec.get("cards"):
                cards = "".join(
                    f'<div class="card"><h4>{_e(c.get("title") or "")}</h4>'
                    f'<p>{_e(c.get("text") or "")}</p></div>'
                    for c in sec["cards"][:6] if isinstance(c, dict))
                inner += f'<div class="cards">{cards}</div>'
            sections.append(
                f'<section><h3>{_e(sec.get("heading") or "")}</h3>{inner}</section>')
        page_html.append(
            f'<main class="page" id="page-{slug}" data-slug="{slug}">'
            f'<header class="hero"><h2>{_e(p.get("title") or "")}</h2>'
            f'<p class="hero-p">{_e(p.get("hero") or "")}</p></header>'
            + "".join(sections) + '</main>')
    first_slug = _slugify(pages[0].get("slug"), "page-1") if pages else "home"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{site_title}</title>
<style>
  :root{{--accent:{_ACCENT};--accent2:{_ACCENT2}}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:{_BG};color:{_TEXT};
    font-family:'Segoe UI',system-ui,-apple-system,sans-serif;line-height:1.6}}
  .topbar{{position:sticky;top:0;display:flex;align-items:center;gap:26px;
    padding:16px 6vw;background:rgba(7,8,13,.88);backdrop-filter:blur(8px);
    border-bottom:1px solid #1b1e2d;z-index:10;flex-wrap:wrap}}
  .logo{{font-weight:700;font-size:18px;
    background:linear-gradient(92deg,var(--accent),var(--accent2));
    -webkit-background-clip:text;background-clip:text;color:transparent}}
  nav{{display:flex;gap:20px;flex-wrap:wrap}}
  nav a{{color:{_MUTED};text-decoration:none;font-size:14px;padding:4px 2px;
    border-bottom:2px solid transparent}}
  nav a.on{{color:{_TEXT};border-bottom-color:var(--accent)}}
  nav a:hover{{color:{_TEXT}}}
  .page{{display:none;max-width:980px;margin:0 auto;padding:8vh 6vw 10vh}}
  .page.active{{display:block}}
  .hero h2{{font-size:clamp(30px,4.6vw,54px);line-height:1.1;margin:0 0 14px;
    background:linear-gradient(92deg,{_TEXT},var(--accent));
    -webkit-background-clip:text;background-clip:text;color:transparent}}
  .hero-p{{color:{_MUTED};font-size:clamp(15px,1.6vw,19px);max-width:64ch;margin:0 0 4vh}}
  section{{margin:6vh 0}}
  h3{{font-size:clamp(20px,2.2vw,28px);margin:0 0 10px;color:var(--accent)}}
  p{{color:#c9c9d6;max-width:70ch}}
  ul{{padding-left:1.2em}} li{{margin:6px 0;color:#c9c9d6}}
  li::marker{{color:var(--accent)}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
    gap:16px;margin-top:14px}}
  .card{{background:{_PANEL};border:1px solid #1c2030;border-radius:14px;
    padding:18px 18px 8px}}
  .card h4{{margin:0 0 6px;color:{_TEXT}}}
  .card p{{font-size:14px;color:{_MUTED}}}
  footer{{border-top:1px solid #1b1e2d;color:{_MUTED};font-size:13px;
    text-align:center;padding:26px 6vw}}
</style></head><body>
<div class="topbar"><span class="logo">{site_title}</span><nav id="nav">{''.join(navs)}</nav></div>
{''.join(page_html)}
<footer>{footer} · <span style="opacity:.7">⚡ Built by Agent Friday</span></footer>
<script>
(function(){{
  var pages=[].slice.call(document.querySelectorAll('.page'));
  var links=[].slice.call(document.querySelectorAll('#nav a'));
  function show(){{
    var slug=(location.hash||'#/{first_slug}').replace(/^#\\//,'');
    var hit=false;
    pages.forEach(function(p){{var on=p.dataset.slug===slug;p.classList.toggle('active',on);hit=hit||on}});
    if(!hit&&pages[0]){{pages[0].classList.add('active');slug=pages[0].dataset.slug}}
    links.forEach(function(a){{a.classList.toggle('on',a.dataset.slug===slug)}});
    window.scrollTo(0,0);
  }}
  window.addEventListener('hashchange',show);show();
}})();
</script>
</body></html>"""
