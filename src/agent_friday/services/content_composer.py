"""
Agent Friday — Content Composition Engine (spec §5)
FutureSpeak.AI · Asimov's Mind

One input, N platform-native outputs — the intelligence between "I made a
thing" and "it ships correctly everywhere."

Public API (§5.1 — envelopes out, never raises; suggest_hashtags returns a
plain list per the spec signature, [] on any failure)
----------------------------------------------------------------------
  adapt(post, platforms=None, *, formats=None, regenerate=False)
      → {ok, targets: [...], warnings: [...]}
  preview(body, assets, platform, format)
      → {ok, adapted_body, segments, hashtags, char_used, char_limit,
         asset_plan, warnings}
  suggest_hashtags(body, platform, limit=8) → list[str]
  convert_format(post, conversion) → {ok, conversion, targets, warnings}
  get_voice_card / set_voice_card       (§5.6 — seeded, user-editable)

Design notes
------------
· Capabilities come from services.platforms (being built in parallel) when
  importable, else the built-in §4.2 fallback table. Import is lazy and
  failure-tolerant.
· All LLM calls go through the module-level `_generate` indirection →
  model_router._generate_text (engine-direct, D5 — never the agent loop),
  gated first via `_gate_prompt` → egress_gate.gate_text(prompt, provider,
  "compose.prompt"). Tests monkeypatch `_generate` / `_gate_prompt` /
  `_friday_prompt`. A gated or failed rewrite falls back to deterministic
  adaptation — the composer never raises and never leaks.
· Asset transforms are PLAN objects only ({source, profile, out_path: None,
  platform_media_id: None, alt_text}) — the publisher executes them (§5.4).
· Grapheme-aware counting: X URLs weigh 23; Bluesky counts 300 graphemes.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR
from agent_friday.services import content_pipeline as _store

# ── Storage (§5.6) ───────────────────────────────────────────────────────────
VOICE_CARDS_DIR = FRIDAY_DIR / "content" / "voice_cards"
VOICE_CARD_MAX_CHARS = 2048           # cards stay small (≤2 KB, §5.6)

# The model router picks the concrete model; prompts are gated for the default
# cloud rail before they leave (§5.2 — same pattern creative_engine uses).
COMPOSE_PROVIDER = "anthropic"

URL_WEIGHT = 23                        # X counts every URL as 23 chars (§5.2)
_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+")

_THREAD_RESERVE = 9                    # room for the " (12/34)" numbering


# ─────────────────────────────────────────────────────────────────────────────
#  LLM / prompt indirection — module-level seams so tests monkeypatch them
# ─────────────────────────────────────────────────────────────────────────────

def _generate(messages, system=None, **kw) -> str:
    """All composition text-gen funnels through here (D5: engine-direct)."""
    from agent_friday.services.model_router import _generate_text
    return _generate_text(messages, system=system, **kw)


def _gate_prompt(prompt: str, provider: str = COMPOSE_PROVIDER) -> str:
    """Egress-gate a compose prompt before it leaves for a cloud model."""
    from agent_friday.services import egress_gate
    return egress_gate.gate_text(prompt, provider, "compose.prompt")


def _friday_prompt() -> str:
    """SOUL.md + user model + heuristics via the standard system prompt.

    Composition always rides the fixed cloud rail (COMPOSE_PROVIDER —
    there is no local leg here to route around), so the prompt is gated for
    that provider directly rather than predicted: TIER_2 vault/self-knowledge
    sections are redacted before `_generate` ever sees them, the same
    boundary `_gate_prompt` already applies to the user-authored post body.
    """
    from agent_friday.services.model_router import (
        _gated_vault_control, _get_friday_system_prompt)
    return _get_friday_system_prompt(
        workspace="content", provider=COMPOSE_PROVIDER,
        vault_control=_gated_vault_control())


# ─────────────────────────────────────────────────────────────────────────────
#  Grapheme-aware counting (§5.2 / §5.5)
# ─────────────────────────────────────────────────────────────────────────────

def grapheme_count(text: str) -> int:
    """Approximate user-perceived character count.

    Handles the cases platform counters care about: combining marks, ZWJ
    emoji sequences (family/profession emoji = 1), variation selectors,
    skin-tone modifiers, and regional-indicator pairs (flags = 1).
    """
    count = 0
    pending_join = False              # previous char was a ZWJ
    ri_run = 0                        # consecutive regional indicators
    for ch in (text or ""):
        cp = ord(ch)
        if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
            continue                  # variation selectors
        if 0x1F3FB <= cp <= 0x1F3FF:
            continue                  # skin-tone modifiers
        if unicodedata.combining(ch):
            continue                  # combining marks
        if cp == 0x200D:              # zero-width joiner
            pending_join = True
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:  # regional indicator — flags pair up
            ri_run += 1
            if ri_run % 2 == 0:
                continue
            count += 1
            pending_join = False
            continue
        ri_run = 0
        if pending_join:              # joined into the previous cluster
            pending_join = False
            continue
        count += 1
    return count


def count_chars(text: str, platform: Optional[str] = None) -> int:
    """Platform-correct length: grapheme count everywhere; on X every URL
    weighs URL_WEIGHT (23) regardless of its actual length."""
    text = text or ""
    if platform in ("twitter", "x"):
        total, last = 0, 0
        for m in _URL_RE.finditer(text):
            total += grapheme_count(text[last:m.start()]) + URL_WEIGHT
            last = m.end()
        return total + grapheme_count(text[last:])
    return grapheme_count(text)


def _take_prefix(text: str, budget: int, platform: Optional[str]) -> str:
    """Largest prefix whose platform count fits the budget (count is monotone
    non-decreasing in prefix length, so binary search is safe)."""
    if count_chars(text, platform) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_chars(text[:mid], platform) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _trim_to_count(text: str, limit: int, platform: Optional[str]) -> str:
    """Hard trim at a grapheme boundary with an ellipsis (validation step 6)."""
    if limit <= 0 or count_chars(text, platform) <= limit:
        return text
    prefix = _take_prefix(text, max(limit - 1, 1), platform).rstrip()
    return prefix + "…"


# ─────────────────────────────────────────────────────────────────────────────
#  Capabilities — live adapter when available, §4.2 fallback table otherwise
# ─────────────────────────────────────────────────────────────────────────────

_GENERIC_CAPS: Dict[str, Any] = {
    "formats": ["post"], "char_limit": 0, "title_limit": None,
    "media": {"images": {"max": 4}, "video": None, "alt_text": False},
    "thread": False, "native_schedule": False, "native_delete": False,
    "analytics": "none", "hashtags_max": None, "notes": [],
}

# Built-in mirror of the §4.2 capability matrix — used until the real
# services/platforms adapters (parallel build) are importable. char_limit 0
# means "no hard limit".
_FALLBACK_CAPS: Dict[str, Dict[str, Any]] = {
    "linkedin": {
        "formats": ["post", "image_post", "video_upload", "link_post", "article"],
        "char_limit": 3000, "title_limit": None,
        "media": {"images": {"max": 9}, "video": {"max_s": 1800}, "alt_text": True},
        "thread": False, "native_schedule": False, "native_delete": True,
        "analytics": "counts", "hashtags_max": 3,
        "notes": ["Articles are assisted handoff — no public write API."]},
    "twitter": {
        "formats": ["post", "thread", "image_post", "video_upload", "link_post"],
        "char_limit": 280, "title_limit": None,
        "media": {"images": {"max": 4}, "video": {"max_s": 140}, "alt_text": True},
        "thread": True, "native_schedule": False, "native_delete": True,
        "analytics": "full", "hashtags_max": 2,
        "notes": ["URLs count as 23 characters.", "Write access is paid-tier metered."]},
    "instagram": {
        "formats": ["post", "reel", "story", "image_post"],
        "char_limit": 2200, "title_limit": None,
        "media": {"images": {"max": 10, "formats": ["jpeg"], "aspect": (0.8, 1.91)},
                  "video": {"max_s": 900}, "alt_text": True},
        "thread": False, "native_schedule": False, "native_delete": True,
        "analytics": "full", "hashtags_max": 30,
        "notes": ["Feed images are JPEG only; media needs a public URL or resumable upload."]},
    "youtube": {
        "formats": ["video_upload", "short"],
        "char_limit": 5000, "title_limit": 100,
        "media": {"images": {"max": 1}, "video": {"max_s": 43200}, "alt_text": False},
        "thread": False, "native_schedule": True, "native_delete": True,
        "analytics": "full", "hashtags_max": 15,
        "notes": ["One upload costs 1,600 quota units (~6/day default)."]},
    "bluesky": {
        "formats": ["post", "thread", "image_post", "video_upload", "link_post"],
        "char_limit": 300, "title_limit": None,
        "media": {"images": {"max": 4, "max_bytes": 1_000_000}, "video": {"max_s": 180},
                  "alt_text": True},
        "thread": True, "native_schedule": False, "native_delete": True,
        "analytics": "counts", "hashtags_max": 3,
        "notes": ["300 graphemes, not bytes; facets are byte-range offsets."]},
    "mastodon": {
        "formats": ["post", "thread", "image_post", "video_upload", "link_post"],
        "char_limit": 500, "title_limit": None,
        "media": {"images": {"max": 4}, "video": {"max_s": 600}, "alt_text": True},
        "thread": True, "native_schedule": True, "native_delete": True,
        "analytics": "counts", "hashtags_max": 3,
        "notes": ["Instance-configurable limit — discover via /api/v2/instance."]},
    "reddit": {
        "formats": ["post", "link_post", "image_post", "video_upload"],
        "char_limit": 40000, "title_limit": 300,
        "media": {"images": {"max": 20}, "video": {"max_s": 900}, "alt_text": False},
        "thread": False, "native_schedule": False, "native_delete": True,
        "analytics": "counts", "hashtags_max": 0,
        "notes": ["Every subreddit is its own jurisdiction — check its rules."]},
    "substack": {
        "formats": ["article"],
        "char_limit": 0, "title_limit": None,
        "media": {"images": {"max": 50}, "video": None, "alt_text": False},
        "thread": False, "native_schedule": True, "native_delete": False,
        "analytics": "none", "hashtags_max": 0,
        "notes": ["Assisted handoff — no official API."]},
    "medium": {
        "formats": ["article"],
        "char_limit": 0, "title_limit": None,
        "media": {"images": {"max": 50}, "video": None, "alt_text": False},
        "thread": False, "native_schedule": False, "native_delete": False,
        "analytics": "none", "hashtags_max": 5,
        "notes": ["Legacy integration token only; set canonicalUrl for repurposed pieces."]},
    "tiktok": {
        "formats": ["video_upload", "post"],
        "char_limit": 2200, "title_limit": None,
        "media": {"images": {"max": 35}, "video": {"max_s": 600}, "alt_text": False},
        "thread": False, "native_schedule": False, "native_delete": True,
        "analytics": "counts", "hashtags_max": 5,
        "notes": ["Unaudited apps publish SELF_ONLY drafts."]},
    "federation": {
        "formats": ["listing"],
        "char_limit": 0, "title_limit": None,
        "media": {"images": {"max": 50}, "video": {"max_s": 0}, "alt_text": True},
        "thread": False, "native_schedule": True, "native_delete": True,
        "analytics": "full", "hashtags_max": 0,
        "notes": ["Full fidelity — never loses information."]},
    "discord": {
        "formats": ["announce"],
        "char_limit": 2000, "title_limit": None,
        "media": {"images": {"max": 10}, "video": None, "alt_text": False},
        "thread": False, "native_schedule": False, "native_delete": False,
        "analytics": "none", "hashtags_max": 0, "notes": []},
    "telegram": {
        "formats": ["announce"],
        "char_limit": 4096, "title_limit": None,
        "media": {"images": {"max": 10}, "video": None, "alt_text": False},
        "thread": False, "native_schedule": False, "native_delete": False,
        "analytics": "none", "hashtags_max": 0, "notes": []},
}


def _capabilities(platform: str) -> Dict[str, Any]:
    """Adapter capabilities when the platforms package exists, else fallback.
    The fallback row is the base so a partial adapter dict still resolves."""
    base = dict(_FALLBACK_CAPS.get(platform, _GENERIC_CAPS))
    try:
        from agent_friday.services import platforms as _plat  # parallel build
        adapter = _plat.get_adapter(platform)
        if adapter is not None:
            caps = adapter.capabilities()
            if isinstance(caps, dict) and caps:
                base.update(caps)
    except Exception:
        pass
    return base


# ─────────────────────────────────────────────────────────────────────────────
#  Hashtags (§5.3) — platform norms, not spray
# ─────────────────────────────────────────────────────────────────────────────

# Platforms whose adapters append the suggested hashtag block to the outbound
# body at prepare time (§5.3 — the preview's tags actually ship). The composer
# reserves char budget for that block so the adapter's own hard validation can
# never reject a body the composer just trimmed to the limit.
_BODY_HASHTAG_PLATFORMS = ("linkedin", "twitter", "bluesky", "mastodon")

# platform → (default count used by the pipeline, hard norm cap)
_HASHTAG_NORMS: Dict[str, Tuple[int, int]] = {
    "linkedin": (3, 3), "twitter": (0, 2), "instagram": (10, 30),
    "youtube": (3, 15), "bluesky": (2, 3), "mastodon": (2, 3),
    "reddit": (0, 0), "substack": (0, 0), "medium": (0, 5),
    "tiktok": (4, 5), "federation": (0, 0), "discord": (0, 0),
    "telegram": (0, 0),
}

_STOPWORDS = frozenset("""
a about after all also an and any are as at be because been before being but
by can could did do does for from had has have he her here his how i if in
into is it its just like me more most my no not now of on one only or other
our out over she so some than that the their them then there these they this
to under up very was we were what when where which while who why will with
would you your really things thing going want make made
""".split())


def _pinned_tags(platform: str) -> List[str]:
    """The user's pinned brand tags — sticky-first (§5.3). Settings shape:
    settings.content.pinned_hashtags = {"all": [...], "<platform>": [...]}."""
    try:
        cfg = ((core._load_settings() or {}).get("content") or {}).get("pinned_hashtags") or {}
        return list(cfg.get("all") or []) + list(cfg.get(platform) or [])
    except Exception:
        return []


def _fmt_tag(raw: str) -> str:
    """Normalize a candidate into #CamelCase (drops anything unusable —
    including absurdly long candidates no platform norm would tolerate)."""
    words = re.findall(r"[A-Za-z0-9]+", str(raw or ""))
    if not words:
        return ""
    joined = "".join(w if w[:1].isupper() or w.isdigit() else w.capitalize()
                     for w in words)
    return ("#" + joined) if 2 <= len(joined) <= 30 else ""


def _candidate_tags(body: str) -> List[str]:
    """Local candidate extraction — explicit #tags, capitalized phrases, then
    frequent keywords. Deterministic ordering; never invents trending tags."""
    text = body or ""
    seen, out = set(), []

    def _push(cand: str) -> None:
        tag = _fmt_tag(cand)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)

    for m in re.finditer(r"#(\w{2,})", text):           # tags already in body
        _push(m.group(1))
    for m in re.finditer(                                # capitalized phrases
            r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)+)\b", text):
        _push(m.group(1))
    counts: Dict[str, int] = {}
    order: Dict[str, int] = {}
    for i, w in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", text)):
        lw = w.lower()
        if lw in _STOPWORDS:
            continue
        counts[lw] = counts.get(lw, 0) + 1
        order.setdefault(lw, i)
    for lw in sorted(counts, key=lambda w: (-counts[w], order[w])):
        _push(lw)
    return out


def suggest_hashtags(body: str, platform: str, limit: int = 8) -> List[str]:
    """§5.3: pinned brand tags first, then extracted candidates, capped by the
    platform norm. Returns a plain list (spec signature); [] on any failure."""
    try:
        _default, cap = _HASHTAG_NORMS.get(platform, (2, 5))
        caps_max = _capabilities(platform).get("hashtags_max")
        if caps_max is not None:
            cap = min(cap, int(caps_max))
        n = max(0, min(int(limit), cap))
        if n <= 0:
            return []
        seen, out = set(), []
        for cand in list(_pinned_tags(platform)) + _candidate_tags(body):
            tag = _fmt_tag(cand)
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                out.append(tag)
            if len(out) >= n:
                break
        return out
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  Structural transforms — thread split, title extraction
# ─────────────────────────────────────────────────────────────────────────────

def split_thread(body: str, limit: int, platform: str = "twitter") -> List[str]:
    """Hook-first thread split (§5.2): sentence-packed segments, one idea per
    segment, each within the platform count after ' (i/n)' numbering."""
    limit = int(limit or 280)
    budget = max(limit - _THREAD_RESERVE, 16)
    text = (body or "").strip()
    if not text:
        return []
    units: List[str] = []
    for para in re.split(r"\n{2,}", text):
        para = " ".join(para.split())
        if para:
            units += [s for s in re.split(r"(?<=[.!?])\s+", para) if s]
    segs: List[str] = []
    cur = ""
    for u in units:
        if count_chars(u, platform) > budget:      # oversize sentence → wrap
            if cur:
                segs.append(cur)
                cur = ""
            segs += _hard_wrap(u, budget, platform)
            continue
        trial = f"{cur} {u}".strip() if cur else u
        if count_chars(trial, platform) <= budget:
            cur = trial
        else:
            segs.append(cur)
            cur = u
    if cur:
        segs.append(cur)
    if len(segs) > 1:
        n = len(segs)
        segs = [f"{s} ({i + 1}/{n})" for i, s in enumerate(segs)]
    return segs


def _hard_wrap(text: str, budget: int, platform: str) -> List[str]:
    """Wrap an oversize run at grapheme-safe cuts, preferring word breaks."""
    pieces: List[str] = []
    rest = text.strip()
    while rest:
        if count_chars(rest, platform) <= budget:
            pieces.append(rest)
            break
        prefix = _take_prefix(rest, budget, platform)
        sp = prefix.rfind(" ")
        if sp > len(prefix) // 2:                  # word break when reasonable
            prefix = prefix[:sp]
        pieces.append(prefix.strip())
        rest = rest[len(prefix):].strip()
    return [p for p in pieces if p]


_TITLE_LIMITS = {"youtube": 100, "reddit": 300}


def _extract_title(title: str, body: str, platform: str,
                   caps: Dict[str, Any]) -> str:
    """Title for platforms that carry one: explicit title → first markdown
    heading → first sentence; clipped to the platform title limit."""
    limit = int(caps.get("title_limit") or _TITLE_LIMITS.get(platform) or 0)
    text = (title or "").strip()
    if not text:
        m = re.search(r"^#{1,3}\s+(.+)$", body or "", re.MULTILINE)
        if m:
            text = m.group(1).strip()
    if not text:
        first = re.split(r"(?<=[.!?])\s+", (body or "").strip(), maxsplit=1)
        text = (first[0] if first else "").strip()
    if platform == "youtube":
        text = text.replace("<", "").replace(">", "")   # YT bans angle brackets
    return _trim_to_count(text, limit, platform) if limit else text


# ─────────────────────────────────────────────────────────────────────────────
#  Asset transform plans (§5.2 step 5) — plans only; publisher executes
# ─────────────────────────────────────────────────────────────────────────────

# platform → default timeline_engine video export profile
_PLATFORM_VIDEO_PROFILE = {
    "instagram": "instagram-reel", "tiktok": "tiktok-vertical",
    "youtube": "youtube-16x9", "twitter": "mp4-1080p", "bluesky": "mp4-1080p",
    "mastodon": "mp4-1080p", "linkedin": "mp4-1080p", "federation": None,
    "discord": None, "telegram": None,
}
_SHORT_PROFILE = "mp4-vertical-9x16"   # YouTube Shorts / vertical slices

# Still-image pseudo-profiles (PIL path in the publisher, §9.3)
_IMAGE_PROFILES = {"instagram": "jpeg-4x5", "bluesky": "img-1mb"}


def _asset_plan(assets: List[dict], platform: str, fmt: str,
                caps: Dict[str, Any], warnings: List[str],
                profile_override: Optional[str] = None) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    alt_supported = bool(((caps.get("media") or {}).get("alt_text")))
    for a in (assets or []):
        if not isinstance(a, dict):
            continue
        kind = a.get("kind") or "image"
        entry = {"source": a, "profile": None, "out_path": None,
                 "platform_media_id": None, "alt_text": a.get("alt_text") or ""}
        if kind == "video":
            if profile_override:
                entry["profile"] = profile_override
            elif platform == "youtube" and fmt == "short":
                entry["profile"] = _SHORT_PROFILE
            else:
                entry["profile"] = _PLATFORM_VIDEO_PROFILE.get(platform)
        elif kind == "image":
            entry["profile"] = profile_override or _IMAGE_PROFILES.get(platform)
            if alt_supported and not entry["alt_text"]:
                warnings.append(
                    f"{platform}: image '{a.get('filename') or '?'}' missing alt text")
        elif kind == "audio":
            entry["profile"] = profile_override or "audio-mp3"
        plan.append(entry)
    return plan


# ─────────────────────────────────────────────────────────────────────────────
#  Voice cards (§5.6) — seeded from DRAFT_MODE_PROMPTS, user-editable
# ─────────────────────────────────────────────────────────────────────────────

# Static fallbacks mirror misc_engine.DRAFT_MODE_PROMPTS should the import fail.
_LINKEDIN_SEED = (
    "You are a LinkedIn ghostwriter for a senior AI/engineering leader. "
    "Write a professional but personable post — 1-3 paragraphs, strong opening hook, "
    "no hashtag spam (2-3 max at the end if any). Conversational authority, not "
    "corporate fluff. The voice should feel like a seasoned journalist who pivoted to AI."
)
_TWEET_SEED = (
    "You are drafting a tweet. MUST be under 280 characters. Punchy, sharp, quotable. "
    "No hashtags unless they're genuinely clever. Think journalist, not influencer."
)

_VOICE_CARD_SEEDS: Dict[str, str] = {
    "linkedin": _LINKEDIN_SEED,
    "twitter": _TWEET_SEED,
    "instagram": (
        "Instagram caption voice: the first line must land before the ~125-character "
        "fold — hook first. Break lines for air. Warm and visual, never corporate. "
        "Hashtags live in a trailing block (8-12), not sprinkled through the text."),
    "youtube": (
        "YouTube register: keyword-front-loaded title under 100 characters, then a "
        "structured description — one-paragraph summary, chapters if timeline markers "
        "exist, links block last. Clear, searchable, no clickbait."),
    "bluesky": (
        "Bluesky voice: the tweet register without the hashtag spray — compressed to "
        "one sharp idea, conversational, 0-3 tags only when they genuinely help. "
        "300 graphemes hard limit."),
    "mastodon": (
        "Mastodon voice: conversational compression like Bluesky; respect instance "
        "culture — content warnings when the subject calls for one, 0-3 tags, "
        "no engagement bait."),
    "reddit": (
        "Reddit register: honest, community-first. Title states plainly what the post "
        "is (no clickbait). Body in clean markdown. Zero hashtags. Write like a "
        "knowledgeable member, not a marketer."),
    "tiktok": (
        "TikTok caption voice: hook line first, casual energy, 3-5 tags at the end. "
        "Under 2,200 characters including tags."),
    "substack": (
        "Newsletter register: long-form, personal, essayistic. A subtitle that earns "
        "the click, sections with room to breathe, links inline where they help."),
    "medium": (
        "Medium register: long-form essay voice — a clear thesis, subheads, and a "
        "canonical link back to the original when this is a repurposed piece."),
    "federation": (
        "Federation listing: full fidelity — complete title and body, license and "
        "price stated plainly. Nothing compressed, nothing lost."),
    "discord": (
        "Discord announce: one short, friendly community ping — what shipped and a "
        "link. No walls of text."),
    "telegram": (
        "Telegram announce: brief broadcast register — headline line, one-sentence "
        "context, link."),
    "_default": (
        "Adapt the content faithfully for the platform: native register, no generic "
        "AI phrasing, keep the author's voice."),
}


def _seed_text(platform: str) -> str:
    """Seed body for a platform card — live DRAFT_MODE_PROMPTS registers for
    the two proven voices (§5.6), static seeds otherwise."""
    mode = {"linkedin": "linkedin_post", "twitter": "tweet"}.get(platform)
    if mode:
        try:
            from agent_friday.services.misc_engine import DRAFT_MODE_PROMPTS
            text = DRAFT_MODE_PROMPTS.get(mode)
            if text:
                return text
        except Exception:
            pass
    return _VOICE_CARD_SEEDS.get(platform, _VOICE_CARD_SEEDS["_default"])


def get_voice_card(platform: str) -> Dict[str, Any]:
    """Read (seeding on first touch) the per-platform voice card."""
    try:
        platform = str(platform or "").strip().lower()
        if platform not in _store.PLATFORMS:
            return {"ok": False, "error": f"unknown platform: {platform}"}
        path = VOICE_CARDS_DIR / f"{platform}.md"
        seeded = False
        if not path.exists():
            VOICE_CARDS_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(_seed_text(platform), encoding="utf-8")
            seeded = True
        return {"ok": True, "platform": platform,
                "text": path.read_text(encoding="utf-8"),
                "path": str(path), "seeded": seeded}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_voice_card(platform: str, text: str) -> Dict[str, Any]:
    """Overwrite a voice card (clamped to 2 KB); previous version is appended
    to a history file — versioned like SOUL.md (§5.6)."""
    try:
        platform = str(platform or "").strip().lower()
        if platform not in _store.PLATFORMS:
            return {"ok": False, "error": f"unknown platform: {platform}"}
        clamped = str(text or "").strip()[:VOICE_CARD_MAX_CHARS]
        path = VOICE_CARDS_DIR / f"{platform}.md"
        VOICE_CARDS_DIR.mkdir(parents=True, exist_ok=True)
        if path.exists():
            old = path.read_text(encoding="utf-8")
            if old.strip() and old != clamped:
                hist = VOICE_CARDS_DIR / "history" / f"{platform}.md"
                hist.parent.mkdir(parents=True, exist_ok=True)
                with open(hist, "a", encoding="utf-8") as f:
                    f.write(f"\n\n---\n<!-- replaced {_store._now_iso()} -->\n{old}")
        path.write_text(clamped, encoding="utf-8")
        return {"ok": True, "platform": platform, "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _voice_card_text(platform: str) -> str:
    res = get_voice_card(platform)
    return (res.get("text") or "")[:VOICE_CARD_MAX_CHARS] if res.get("ok") else ""


def _compose_system_prompt(platform: str) -> str:
    """Friday system prompt (SOUL.md + user model, §5.6) + the platform voice
    card. Failure-tolerant: a missing rail degrades to the card alone."""
    base = ""
    try:
        base = _friday_prompt() or ""
    except Exception:
        pass
    card = _voice_card_text(platform)
    parts = [p for p in (base.strip(),) if p]
    if card:
        parts.append(f"== PLATFORM VOICE CARD ({platform}) ==\n{card}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
#  Voice rewrite (§5.2 step 2) — gated, engine-direct, deterministic fallback
# ─────────────────────────────────────────────────────────────────────────────

# Per-platform nativeness rules, folded into the rewrite prompt (§5.2).
_PLATFORM_RULES: Dict[str, str] = {
    "linkedin": ("Hook-first professional register: 1-3 paragraphs, strong opening, "
                 "conversational authority, no corporate fluff, 2-3 hashtags max."),
    "twitter": ("Compress to one sharp idea. Punchy and quotable. If the content is "
                "bigger than one post it becomes a thread — hook first, one idea per "
                "segment, closing link/CTA."),
    "instagram": ("Caption that survives the ~125-character above-the-fold cut. "
                  "Line-broken for air; hashtags go in a trailing block."),
    "youtube": ("Keyword-front-loaded title and a structured description: summary "
                "first, then chapters if timestamps exist, links last."),
    "bluesky": ("Tweet-register compression WITHOUT hashtag spray (0-3 tags). "
                "300 graphemes hard limit."),
    "mastodon": ("Conversational compression, 0-3 tags, add a content warning when "
                 "the subject calls for it."),
    "reddit": ("Honest community register: plain-spoken title (no clickbait), body "
               "in markdown, zero hashtags."),
    "tiktok": ("Hook line first, casual caption energy, 3-5 tags, under 2,200 chars."),
    "substack": ("Long-form newsletter register: expand context, essayistic voice."),
    "medium": ("Long-form essay register; keep the thesis clear."),
    "discord": ("One short friendly announce line plus the link."),
    "telegram": ("Brief broadcast register: headline, one sentence, link."),
}


def _compose_prompt(body: str, title: str, platform: str, fmt: str,
                    caps: Dict[str, Any]) -> str:
    limit = int(caps.get("char_limit") or 0)
    limit_line = (f"Hard limit: {limit} characters (grapheme-counted)."
                  if limit else "No hard length limit.")
    rules = _PLATFORM_RULES.get(platform, "Adapt naturally for the platform.")
    head = f"TITLE: {title.strip()}\n\n" if (title or "").strip() else ""
    return (f"Adapt the following content as a {platform} {fmt}.\n"
            f"{rules}\n{limit_line}\n"
            "Return ONLY the adapted text — no preamble, no quoting, no commentary.\n\n"
            f"{head}CONTENT:\n{body}")


def _rewrite(body: str, title: str, platform: str, fmt: str,
             caps: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Gated LLM rewrite. Any gate divergence/error or model failure falls
    back to the original body (deterministic adaptation still applies)."""
    if not (body or "").strip():
        return body, []
    prompt = _compose_prompt(body, title, platform, fmt, caps)
    try:
        gated = _gate_prompt(prompt)
    except Exception:
        return body, [f"{platform}: egress gate unavailable — "
                      "deterministic adaptation used"]
    if gated != prompt:
        # Classifier saw private content — nothing leaves; local adaptation only.
        return body, [f"{platform}: compose prompt withheld by egress gate — "
                      "deterministic adaptation used"]
    try:
        text = _generate(
            [{"role": "user", "content": gated}],
            system=_compose_system_prompt(platform),
            max_tokens=2048, temperature=0.4,
            orb_label=f"✍️ Composing — {platform}", workspace="content")
        text = (text or "").strip()
        if text:
            return text, []
    except Exception:
        pass
    return body, [f"{platform}: llm rewrite unavailable — "
                  "deterministic adaptation used"]


# ─────────────────────────────────────────────────────────────────────────────
#  The §5.2 adaptation pipeline (per target)
# ─────────────────────────────────────────────────────────────────────────────

_THREADABLE = ("twitter", "bluesky", "mastodon")


def _resolve_format(platform: str, fmt: Optional[str],
                    assets: List[dict]) -> str:
    """A stored/generic format only sticks when the platform supports it —
    e.g. a default 'post' target on federation resolves to 'listing'.
    'post' is the store's generic placeholder, so on federation it always
    re-resolves to the listing default even though the adapter can carry a
    literal 'post' (the marketplace pipeline composes listings, §4.13)."""
    if platform == "federation" and fmt == "post":
        return _default_format(platform, assets)
    if fmt and fmt in (_capabilities(platform).get("formats") or []):
        return fmt
    return _default_format(platform, assets)


def _default_format(platform: str, assets: List[dict]) -> str:
    has_video = any((a or {}).get("kind") == "video" for a in (assets or []))
    if platform == "youtube":
        return "video_upload"
    if platform == "federation":
        return "listing"
    if platform in ("discord", "telegram"):
        return "announce"
    if platform in ("substack", "medium"):
        return "article"
    if platform == "instagram" and has_video:
        return "reel"
    if platform == "tiktok" and has_video:
        return "video_upload"
    return "post"


def _platform_options(platform: str, tags: List[str]) -> Dict[str, Any]:
    """Taste isn't policed — sensitivity tags map to platform flags (§4, D4)."""
    opts: Dict[str, Any] = {}
    lowered = {str(t).lower() for t in (tags or [])}
    if "nsfw" in lowered:
        if platform == "mastodon":
            opts["sensitive"] = True
            opts["spoiler_text"] = "sensitive content"
        elif platform == "reddit":
            opts["nsfw"] = True
        elif platform == "twitter":
            opts["possibly_sensitive"] = True
    return opts


def _adapt_one(body: str, title: str, assets: List[dict], platform: str,
               fmt: str, tags: Optional[List[str]] = None,
               use_llm: bool = True,
               profile_override: Optional[str] = None
               ) -> Tuple[Dict[str, Any], List[str]]:
    """Run steps 1-6 of the §5.2 pipeline for one platform. Returns
    (target field dict, warnings). Step 7 (target write) is the caller's."""
    warnings: List[str] = []
    caps = _capabilities(platform)                        # 1 capabilities
    limit = int(caps.get("char_limit") or 0)

    adapted = body or ""
    if use_llm and platform != "federation":              # 2 voice + rewrite
        adapted, w = _rewrite(body or "", title or "", platform, fmt, caps)
        warnings += w
    # Federation is full fidelity — the only target that never loses info.

    opts = _platform_options(platform, tags or [])        # 4a platform flags

    default_n, _cap = _HASHTAG_NORMS.get(platform, (2, 5))  # 4 hashtags
    hashtags = suggest_hashtags(body or "", platform, limit=default_n) \
        if default_n else []

    # Char-budget reservations: the hashtag block some adapters append to the
    # outbound body (§5.3) and Mastodon's spoiler_text (which the server
    # counts against max_characters, §4.8) both eat into the limit — reserve
    # them here so a compose-time trim can never yield a payload the adapter
    # rejects at publish time ('body exceeds char_limit').
    reserve = 0
    if limit:
        if platform in _BODY_HASHTAG_PLATFORMS and hashtags:
            missing = [t for t in hashtags
                       if t.lower() not in (adapted or "").lower()]
            if missing:
                reserve += count_chars("\n\n" + " ".join(missing), platform)
        spoiler = str(opts.get("spoiler_text") or "")
        if platform == "mastodon" and spoiler:
            reserve += count_chars(spoiler, platform)
    eff_limit = max(limit - reserve, 16) if limit else 0

    segments: List[str] = []                              # 3 structural
    overflow = bool(eff_limit) and count_chars(adapted, platform) > eff_limit
    if fmt == "thread" or (overflow and caps.get("thread")
                           and platform in _THREADABLE):
        fmt = "thread"
        segments = split_thread(adapted, eff_limit or 280, platform)
    adapted_title = ""
    if caps.get("title_limit") or platform in _TITLE_LIMITS or \
            platform in ("substack", "medium", "federation"):
        adapted_title = _extract_title(title, body or "", platform, caps)

    plan = _asset_plan(assets or [], platform, fmt, caps,  # 5 asset plan
                       warnings, profile_override=profile_override)

    # 6 hard validation — grapheme counts, fold hook, trims
    if segments:
        for i, s in enumerate(segments):
            if eff_limit and count_chars(s, platform) > eff_limit:
                segments[i] = _trim_to_count(s, eff_limit, platform)
                warnings.append(f"{platform}: thread segment {i + 1} trimmed "
                                f"to {eff_limit}")
        adapted_body = segments[0] if segments else adapted
    else:
        adapted_body = adapted
        if eff_limit and count_chars(adapted_body, platform) > eff_limit:
            adapted_body = _trim_to_count(adapted_body, eff_limit, platform)
            warnings.append(f"{platform}: body trimmed to {eff_limit} chars")
    if platform == "instagram":
        first_line = (adapted_body or "").split("\n", 1)[0]
        if grapheme_count(first_line) > 125:
            warnings.append("instagram: first line exceeds the ~125-char "
                            "above-the-fold cut")

    fields = {
        "format": fmt,
        "adapted_title": adapted_title,
        "adapted_body": adapted_body,
        "segments": segments,
        "adapted_assets": plan,
        "char_limit": limit,
        "hashtags": hashtags,
        "mentions": [],
        "options": opts,
    }
    return fields, warnings


# ─────────────────────────────────────────────────────────────────────────────
#  Public API (§5.1)
# ─────────────────────────────────────────────────────────────────────────────

_UPDATE_KEYS = ("adapted_title", "adapted_body", "segments", "adapted_assets",
                "char_limit", "hashtags", "mentions", "options", "format")


def adapt(post: dict, platforms: Optional[List[str]] = None, *,
          formats: Optional[dict] = None, regenerate: bool = False
          ) -> Dict[str, Any]:
    """Produce/refresh PlatformTargets for a ContentPost (§5.1).

    Skips CONFIRMED targets; already-composed targets are kept unless
    regenerate=True (which rebuilds PENDING/HELD ones). When the post exists
    in the store the composed payloads are persisted (step 7); a bare dict
    round-trips in memory only. Returns {ok, targets, warnings}."""
    try:
        post = dict(post or {})
        body = post.get("body") or ""
        title = post.get("title") or ""
        assets = list(post.get("assets") or [])
        tags = list(post.get("tags") or [])
        existing = [t for t in (post.get("targets") or post.get("platforms") or [])
                    if isinstance(t, dict)]
        want = [str(p).strip().lower() for p in (platforms or []) if p]
        if not want:
            seen: List[str] = []
            for t in existing:
                p = t.get("platform")
                if p and p not in seen:
                    seen.append(p)
            want = seen
        if not want:
            return {"ok": False,
                    "error": "no platforms to adapt (pass platforms=[...] "
                             "or add targets to the post)"}
        post_id = post.get("id")
        warnings: List[str] = []
        out: List[Dict[str, Any]] = []
        new_targets: List[Dict[str, Any]] = []
        for plat in want:
            if plat not in _store.PLATFORMS:
                warnings.append(f"unknown platform: {plat} — skipped")
                continue
            tgt = next((t for t in existing if t.get("platform") == plat), None)
            if tgt and tgt.get("status") == "CONFIRMED":
                warnings.append(f"{plat}: target CONFIRMED — skipped")
                continue
            if tgt and (tgt.get("adapted_body") or "") and not regenerate:
                out.append(tgt)          # keep as-is; regenerate=True rebuilds
                continue
            fmt = ((formats or {}).get(plat)
                   or _resolve_format(plat, (tgt or {}).get("format"), assets))
            fields, w = _adapt_one(body, title, assets, plat, fmt, tags=tags)
            warnings += w
            if tgt:
                merged = dict(tgt)
                merged.update(fields)
                out.append(merged)
                if post_id and tgt.get("id"):     # 7 target write (best-effort)
                    _store.update_target(
                        tgt["id"], {k: fields[k] for k in _UPDATE_KEYS})
            else:
                nt = _store.new_platform_target(
                    plat, fields["format"],
                    **{k: fields[k] for k in _UPDATE_KEYS if k != "format"})
                out.append(nt)
                new_targets.append(nt)
        if post_id and new_targets:
            _store.add_targets(post_id, new_targets)   # no-op for bare dicts
        return {"ok": True, "targets": out, "warnings": warnings}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def preview(body: str, assets: list, platform: str, format: str) -> Dict[str, Any]:
    """Stateless single-platform adaptation for the Compose tab's live
    preview (§5.1). The preview IS the payload (D6) — same DETERMINISTIC
    pipeline, no persistence, and crucially use_llm=False: a live keystroke
    refresh must never fire a fresh temperature-0.4 cloud rewrite, both
    because a nondeterministic preview cannot be byte-what-ships (the stored
    target payload written by adapt()/compose is what publishes) and because
    drafts must not leave the machine on mere preview (§10.1 cost/egress)."""
    try:
        platform = str(platform or "").strip().lower()
        if platform not in _store.PLATFORMS:
            return {"ok": False, "error": f"unknown platform: {platform}"}
        fmt = format if format in _store.FORMATS else \
            _default_format(platform, list(assets or []))
        fields, warnings = _adapt_one(body or "", "", list(assets or []),
                                      platform, fmt, use_llm=False)
        segs = fields["segments"]
        if segs:
            used = max(count_chars(s, platform) for s in segs)
        else:
            used = count_chars(fields["adapted_body"], platform)
        return {"ok": True,
                "adapted_body": fields["adapted_body"],
                "segments": segs,
                "hashtags": fields["hashtags"],
                "char_used": used,
                "char_limit": fields["char_limit"],
                "asset_plan": fields["adapted_assets"],
                "warnings": warnings}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── §5.4 conversion matrix ───────────────────────────────────────────────────

_CONVERSION_ALIASES = {
    "thread": "tweet_thread", "x_thread": "tweet_thread",
    "blog_to_thread": "tweet_thread", "linkedin": "linkedin_post",
    "article_to_linkedin": "linkedin_post", "instagram_reel": "reel",
    "video_to_reel": "reel", "youtube_short": "short", "video_to_short": "short",
    "video_to_tiktok": "tiktok", "newsletter": "newsletter_section",
    "post_to_newsletter": "newsletter_section",
    "yt_description": "youtube_description", "image_carousel": "carousel",
}

# conversion → routing + timeline_engine profile (video paths). Plans only —
# the publisher executes transforms (§5.4).
_CONVERSION_MATRIX: Dict[str, Dict[str, Any]] = {
    "tweet_thread":        {"platform": "twitter",   "format": "thread"},
    "linkedin_post":       {"platform": "linkedin",  "format": "post"},
    "reel":                {"platform": "instagram", "format": "reel",
                            "profile": "instagram-reel", "kind": "video"},
    "short":               {"platform": "youtube",   "format": "short",
                            "profile": "mp4-vertical-9x16", "kind": "video"},
    "tiktok":              {"platform": "tiktok",    "format": "video_upload",
                            "profile": "tiktok-vertical", "kind": "video"},
    "clip":                {"platform": "twitter",   "format": "video_upload",
                            "profile": "mp4-vertical-9x16", "kind": "video",
                            "slice": True},
    "carousel":            {"platform": "instagram", "format": "image_post",
                            "kind": "image"},
    "newsletter_section":  {"platform": "substack",  "format": "article"},
    "youtube_description": {"platform": "youtube",   "format": "video_upload"},
    "audiogram":           {"platform": "instagram", "format": "reel",
                            "profile": "instagram-reel", "kind": "audio",
                            "stretch": True},
}

CONVERSIONS = tuple(_CONVERSION_MATRIX)


def _known_profile(profile: str) -> bool:
    """Validate a profile name against timeline_engine when importable."""
    try:
        from agent_friday.services.timeline_engine import EXPORT_PROFILES
        return profile in EXPORT_PROFILES
    except Exception:
        return True                     # can't check — trust the matrix


def convert_format(post: dict, conversion: str) -> Dict[str, Any]:
    """§5.4 conversion matrix. Returns {ok, conversion, targets, warnings} —
    targets are plan objects (asset transforms resolve to timeline_engine
    profiles / PIL pseudo-profiles and execute in the publisher)."""
    try:
        post = dict(post or {})
        name = str(conversion or "").strip().lower()
        name = _CONVERSION_ALIASES.get(name, name)
        spec = _CONVERSION_MATRIX.get(name)
        if not spec:
            return {"ok": False, "error": f"unknown conversion: {conversion}",
                    "conversions": list(CONVERSIONS)}
        body = post.get("body") or ""
        title = post.get("title") or ""
        tags = list(post.get("tags") or [])
        assets = list(post.get("assets") or [])
        kind = spec.get("kind")
        warnings: List[str] = []
        if kind:
            matched = [a for a in assets if (a or {}).get("kind") == kind]
            if not matched:
                warnings.append(f"{name}: post has no {kind} asset — "
                                "transform plan is empty")
            assets = matched
        profile = spec.get("profile")
        if profile and not _known_profile(profile):
            warnings.append(f"{name}: export profile '{profile}' not found "
                            "in timeline_engine")
        if spec.get("stretch"):
            warnings.append(f"{name}: audiogram composition is a Phase-5 "
                            "stretch — plan recorded, renderer pending")

        fields, w = _adapt_one(body, title, assets, spec["platform"],
                               spec["format"], tags=tags,
                               profile_override=profile)
        warnings += w
        if spec.get("slice"):
            # Long video → clip: slice bounds are chosen in the UI; the plan
            # records the intent for the publisher's timeline_engine call.
            for entry in fields["adapted_assets"]:
                entry["slice"] = {"start_s": None, "end_s": None}
        if name == "carousel":
            for i, entry in enumerate(fields["adapted_assets"]):
                entry["slide"] = i + 1
                entry["cover"] = (i == 0)
        if name == "youtube_description" and title:
            fields["adapted_title"] = _extract_title(
                title, body, "youtube", _capabilities("youtube"))
        target = _store.new_platform_target(
            spec["platform"], fields["format"],
            **{k: fields[k] for k in _UPDATE_KEYS if k != "format"})
        return {"ok": True, "conversion": name, "targets": [target],
                "warnings": warnings}
    except Exception as e:
        return {"ok": False, "error": str(e)}
