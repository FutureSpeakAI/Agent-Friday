"""
Egress Gate — the security boundary for all outbound cloud API calls.

seal_outbound(payload, provider) runs immediately before EVERY cloud HTTP call,
after payload assembly and before the network request. It is the enforcement
boundary; the model router is an optimization that happens before it.

Architecture:
  Router     — decides WHICH provider a request goes to (routing optimization)
  EgressGate — decides WHAT that provider is allowed to see (security boundary)

These are separate by design. The router can be wrong or bypassed; the gate is
the last line of defense and cannot be bypassed without modifying this module.

ENVELOPE COVERAGE (audited 2026-08-24). The gate's promise is "nothing
sensitive leaves this device", which only equals "scan the messages" if every
prose-bearing field of the payload is gated. Currently gated:

  system (string AND block-list form)      messages[].content (string)
  content[].text                           content[].tool_result
  content[].tool_use.input (values)        tools[].description   (mcp_* only)
  tools[].input_schema / .parameters prose (mcp_* only, description/title)

both in the Anthropic top-level shape and the OpenAI ``function``-nested shape.

NOT gated, and deliberately so: image and document blocks (binary — a text
classifier cannot judge them; record_binary_egress accounts for them instead),
and schema machinery — types, ``required``, property names, ``enum`` values —
because redacting an enum member makes a tool uncallable rather than private.

WHERE A REAL PII LIBRARY WOULD HELP (Presidio and friends are under separate
evaluation; nothing here presumes that decision). The classifier is keyword-
and pattern-driven, and the deterministic identifier scrub (core._scrub_pii,
run at §5.5 step 1) covers only ``system``/``messages``/``prompt``. The tool
paths added on 2026-08-24 therefore get the TIER gate but not the scrub, so
they fail closed — an argument containing one email address is withheld whole
rather than having the address masked and the rest preserved. That is safe but
blunt. A real entity recogniser would let all these paths mask spans instead of
withholding fields, which is a capability win, not a safety one; the safety
property does not depend on it. If one is adopted, the seams are _gate_text
(span decisions) and _scrub_all (identifier masking) — not new call sites.

Default: REDACT on uncertainty — fail-closed, not fail-open.
Local providers bypass this gate; data stays on-device. "Local" is decided by
the provider REGISTRY's egress classification (classification: "local" + a
local-capable adapter + a loopback/RFC1918 base_url verified at call time —
see is_local_provider), with the legacy {"ollama", "local"} family names kept
only as a fallback for non-registry provider strings.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from agent_friday.services.sensitivity_classifier import classify as _classify_impl, Tier

# ── Provider classification ────────────────────────────────────────────────────
# Legacy family names that historically bypassed the gate. Kept ONLY as the
# belt-and-braces fallback for provider strings that are not registry names
# (the executor's family enums). The primary check is registry-driven:
# is_local_provider() below (GAP-9 fix, spec §9.2).
_LOCAL_PROVIDERS = {"ollama", "local"}


def is_local_provider(name: str) -> bool:
    """Registry-driven local classification (spec §9.2) — the gate bypass test.

    A provider earns the local bypass only when ALL of:
      1. its registry descriptor says `classification: "local"` (effective —
         normalize demotes unearned claims at load),
      2. its adapter is a local-capable transport (ollama / local-voice /
         nemo-local / openai-compatible), and
      3. its base_url resolves to a loopback / RFC1918 / *.local host —
         re-checked HERE at call time, so a settings edit between registry
         load and the call cannot open a hole.

    A descriptor typed "ollama" but pointing at a REMOTE url therefore
    classifies CLOUD and gets sealed — the inverse of GAP-9. Names not in the
    registry fall back to the legacy family set (defense in depth), so the
    executor's "ollama"/"local" enums keep bypassing and everything else —
    including unknown/empty names — is cloud (fail-closed).
    """
    n = (name or "").lower().strip()
    if not n:
        return False  # unknown provenance → cloud → gated
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        from agent_friday.routing.provider_descriptors import (
            classification_of, adapter_of, is_private_host,
            LOCAL_CAPABLE_ADAPTERS,
        )
        p = get_provider_registry().get_provider(n)
    except Exception:
        # Registry unavailable (early boot, tests importing the gate alone):
        # the conservative legacy set is the only authority.
        return n in _LOCAL_PROVIDERS
    if p is None:
        return n in _LOCAL_PROVIDERS  # legacy belt-and-braces
    if classification_of(p) != "local":
        return False
    if adapter_of(p) not in LOCAL_CAPABLE_ADAPTERS:
        return False
    # Call-time re-verification of the actual host (DNS re-resolved, cached
    # briefly inside is_private_host).
    return is_private_host(p.get("base_url"))

_LOG_LOCK = threading.Lock()
_DEFAULT_LOG = Path.home() / ".friday" / "vault" / "egress-log.jsonl"

# Structured logger for the single most security-sensitive line in the codebase —
# every cloud egress allow/block decision. Using `print()` here (the prior state)
# meant that under pythonw.exe / a detached tray launch (no console) these decisions
# were silently discarded, creating an invisible audit gap. Routing through the
# friday.* hierarchy sends them to ~/.friday/friday.log alongside everything else.
_dlog = logging.getLogger("friday.egress")


# ── Classifier rate limiting ──────────────────────────────────────────────────
# The classifier's heavy layers (embeddings, NER) are expensive. Under a load
# spike — many concurrent channel messages, a burst of parallel subagents —
# unbounded concurrent classification can exhaust memory and make the classifier
# raise, which trips the fail-closed path and blocks traffic that a paced
# classifier would have passed. So excess callers QUEUE instead of crashing:
# a token bucket admits at most FRIDAY_EGRESS_CLASSIFY_RATE calls per second
# and briefly sleeps the rest. Pacing is best-effort (a bounded wait, then
# proceed) — the CONTENT gate itself always still runs.
try:
    _RATE_MAX_PER_SEC = max(1, int(os.environ.get("FRIDAY_EGRESS_CLASSIFY_RATE", "40")))
except ValueError:
    _RATE_MAX_PER_SEC = 40
_RATE_MAX_WAIT_S = 10.0
_RATE_LOCK = threading.Lock()
_rate_window_start = 0.0
_rate_count = 0


def _rate_limit() -> None:
    """Pace classifier calls: queue (sleep) instead of crash under load spikes.

    Never raises and never skips classification — after _RATE_MAX_WAIT_S the
    caller proceeds anyway (all work is local; only the pacing is waived), so a
    pathological burst degrades to slower classification, not a blocked gate.
    """
    global _rate_window_start, _rate_count
    deadline = time.monotonic() + _RATE_MAX_WAIT_S
    while True:
        with _RATE_LOCK:
            now = time.monotonic()
            if now - _rate_window_start >= 1.0:
                _rate_window_start = now
                _rate_count = 0
            if _rate_count < _RATE_MAX_PER_SEC:
                _rate_count += 1
                return
            wait = 1.0 - (now - _rate_window_start)
        if time.monotonic() >= deadline:
            return  # never block a request indefinitely — pacing is best-effort
        time.sleep(min(max(wait, 0.01), 0.25))


def _is_cloud(provider: str) -> bool:
    """True when the provider's payloads leave the device (must be gated).

    Registry-first via is_local_provider(): the registry's egress
    classification decides, with the hardcoded family set only as fallback
    for non-registry names. Default on uncertainty: CLOUD (gated).
    """
    return not is_local_provider(provider)


def _classify_cloud(text: str) -> int:
    """Classify content for cloud egress.

    Uses PUBLIC as the base default — content with no signals from any layer is
    treated as public and allowed through. Fail-closed behaviour is provided by
    the embedding layer: text semantically close to sensitive exemplars (sim >=
    0.50) is conservatively classified as PRIVATE before any keyword/regex match
    is required. This catches contextual PII ("my son lives with me on weekends")
    that keyword lists miss, while not blocking genuinely neutral conversations.

    Calls are paced by _rate_limit() so a load spike queues briefly instead of
    OOM-crashing the classifier (which would trip fail-closed and block traffic).
    """
    _rate_limit()
    # egress=True: precision keyword mode — this call classifies an actual
    # cloud payload, not a routing decision (see sensitivity_classifier).
    return _classify_impl(text, default=Tier.PUBLIC, egress=True)


# ── Trusted self-authored constants ───────────────────────────────────────────
# Friday-authored compile-time strings (the shipped system prompt, tool
# blurbs) that must never be redacted: they contain no user data by
# construction, and redacting them strips the model of its identity and tool
# instructions. Exact-match only — whole string or verbatim paragraph — so any
# interpolation of user content produces a different string and gates normally.
_TRUSTED_TEXTS: set = set()
_TRUSTED_PARAS: set = set()
_TRUSTED_LOCK = threading.Lock()


# ── Third-party published text ────────────────────────────────────────────────
#
# A SEPARATE registry from the self-authored one above, deliberately, because
# the contracts differ and merging them would weaken the statement made there.
#
# The problem this solves: 9 of 120 public news headlines classify TIER_3 —
# "Trump asks US Supreme Court to allow ballroom work to continue",
# "Point2 Technology ... raised a $136M Series B". Those are the legal and
# financial keyword rules doing their job on the wrong material: they exist to
# keep STEPHEN's legal and financial affairs on the machine, and a headline the
# BBC published is neither. One tainted paragraph made the whole weekly story
# block tier-3, the gate withheld it, and Friday received a folder of redaction
# notices and honestly refused to write an editorial from them.
#
# What earns the exemption is PROVENANCE, established at INGEST:
#
#   * `news_engine` registers each article's title/summary at the moment it
#     comes back from an external feed fetch. The text is on this list because
#     Friday retrieved it from a public source, not because anybody said so.
#   * Exact match only — whole string or verbatim paragraph. Interpolating any
#     user content produces a different string, which gates normally.
#   * There is no send-time API. Nothing accepts "treat this as news" from a
#     caller, so the exemption cannot be claimed by asserting it; a would-be
#     spoofer would have to get their text into a news feed Friday subscribes
#     to first, and even then it would only exempt that exact text.
#
# What it deliberately does NOT cover, even though all of it also "arrives from
# outside": email bodies, calendar entries, documents, anything a tool returned
# that was not a registered feed fetch, and Friday's own analysis ABOUT the
# news — her synthesis may weave in private context and is classified normally.
_PUBLIC_PARAS: set = set()
_PUBLIC_MAX = 20000          # bounded; oldest-wins eviction is not worth it
# P7(b): `origin` was accepted and thrown away, so the egress log could not
# attribute an exemption to the page it came from — an audit surface that
# says "this was exempt" without being able to say "because it came from
# here" is not an audit surface. Bounded alongside _PUBLIC_PARAS.
_PUBLIC_ORIGINS: dict = {}


# ── WO-17: user-granted file content ──────────────────────────────────────────
#
# A SECOND FEEDER of the registry above, not a new mechanism. `file_grants.py`
# calls register_public_text(paragraph, origin="user-grant:<id>") at READ TIME
# — when read_file/search_files actually reads a file the user has granted —
# so a granted file's paragraphs pass through exactly the pipe news does.
# There is still no send-time exemption API here: nothing accepts a flag on a
# call, only exact-match lookups against text that was actually read.
#
# _OVERRIDE_PARAS is separate and narrower: a file grant may (with an explicit,
# separately-acknowledged checkbox) override Stephen's own never-send
# watchlist for THAT file's exact paragraphs only. It is consulted before the
# never-send floor raises (see _gate_text_span), never before the classifier.
_OVERRIDE_PARAS: set = set()
_OVERRIDE_MAX = 20000


def register_override_text(text: str, origin: str = "", max_len: int = 2000) -> None:
    """Register a never-send-watchlist OVERRIDE for one paragraph's exact text.

    Call ONLY from file_grants.py, and ONLY for a paragraph belonging to a
    file grant whose never_send_override was explicitly checked (with the
    specific matches shown in the dialog before the button). Exact-match only
    — this cannot be claimed for text that was not itself read from the
    granted file.

    max_len defaults to headline-length (2000) for callers that never pass
    it; file_grants.py passes a document-sized cap (a granted file's
    paragraphs are a page of prose, not a headline — see the sibling note on
    register_public_text for the bug this parameter fixes).
    """
    if not text or not isinstance(text, str):
        return
    t = text.strip()
    if not t or len(t) > max_len:
        return
    with _TRUSTED_LOCK:
        if len(_OVERRIDE_PARAS) < _OVERRIDE_MAX:
            _OVERRIDE_PARAS.add(t)
            if origin:
                _PUBLIC_ORIGINS[t] = str(origin)[:500]


# ── WO-17: provider-echo ───────────────────────────────────────────────────────
#
# The companion rule that keeps a granted file's OWN analysis, written by a
# cloud provider, from being redacted on its way back to that same provider in
# a later turn's replayed history. Nothing new ever leaves the device by
# registering this — it only permits sending a provider text that provider
# itself already produced. Scoped per-provider: text is not "public", it is
# "already seen by this one provider".
_PROVIDER_ECHO: dict = {}   # stripped text -> set of provider names
_PROVIDER_ECHO_MAX = 20000


def register_provider_echo(text: str, provider: str) -> None:
    """Register a cloud completion as replayable back to the SAME provider.

    Call ONLY with text a provider's own API actually returned, immediately
    after that call, naming that same provider.
    """
    if not text or not isinstance(text, str) or not provider:
        return
    t = text.strip()
    if not t or len(t) > 20000:      # a turn of prose, not an open-ended document
        return
    with _TRUSTED_LOCK:
        if t not in _PROVIDER_ECHO and len(_PROVIDER_ECHO) >= _PROVIDER_ECHO_MAX:
            return
        _PROVIDER_ECHO.setdefault(t, set()).add(provider)


def _never_send_covered_by_override(text: str) -> bool:
    """True when EVERY paragraph that trips the never-send floor is covered
    by a registered file-grant override (WO-17 §3).

    A lookup, not a flag: the caller supplies only `text`, and this checks it
    against paragraphs that were actually registered by file_grants.py at
    read time. A payload containing never-send material the override does
    NOT cover still fails closed below.
    """
    try:
        from agent_friday.services import judgment_gate as _jg
    except Exception:
        return False
    sep = "\n\n" if "\n\n" in text else ("\n" if "\n" in text else None)
    paras = text.split(sep) if sep else [text]
    with _TRUSTED_LOCK:
        for p in paras:
            ps = p.strip()
            if not ps:
                continue
            if _jg.never_send_hits(p) and ps not in _OVERRIDE_PARAS:
                return False
    return True


def _origin_reason(origin: str) -> str:
    """Render an origin string into the egress-log reason, distinguishing WO-17
    grant/echo provenance from ordinary third-party-published news — the
    acceptance bar that every grant-passed span be attributable to its grant id."""
    if origin.startswith("user-grant:"):
        return f"user-granted origin={origin}"
    if origin.startswith("provider-echo:"):
        return f"provider-echo origin={origin}"
    return f"third-party-published{(' origin=' + origin) if origin else ''}"


def register_public_text(text: str, origin: str = "", max_len: int = 2000) -> None:
    """Register externally-published text as gate-exempt, by provenance.

    Call ONLY from an ingest path, with text that came back from fetching a
    public source. Never from a send path, and never with anything the user
    wrote.

    max_len defaults to 2000 (a headline or a summary, not a document) for
    every existing caller (news, web_fetch). WO-17 (2026-08-25) found this
    cap silently swallowing 3 of a 4-page granted CV's paragraphs — the gate
    splits a tool result on the SAME page-sized "\\n\\n" boundaries this
    registers, and a resume page routinely runs well past 2000 characters,
    so the grant looked like it worked (no error, ledger entry created,
    check_grant returned 'active') while most of the document silently kept
    gating normally. file_grants.py passes a document-sized max_len; the
    default is unchanged for every other caller.
    """
    if not text or not isinstance(text, str):
        return
    t = text.strip()
    if not t or len(t) > max_len:
        return
    with _TRUSTED_LOCK:
        if len(_PUBLIC_PARAS) < _PUBLIC_MAX:
            _PUBLIC_PARAS.add(t)
            if origin:
                _PUBLIC_ORIGINS[t] = str(origin)[:500]


def public_origin_of(text: str) -> str:
    """Which source a registered public string came from ("" if unknown)."""
    if not text:
        return ""
    with _TRUSTED_LOCK:
        return _PUBLIC_ORIGINS.get(text.strip(), "")


def public_text_count() -> int:
    with _TRUSTED_LOCK:
        return len(_PUBLIC_PARAS)


def register_trusted_text(text: str) -> None:
    """Register a Friday-authored constant as gate-exempt.

    ONLY call this with compile-time constants that contain no user data —
    the docstring above is the contract.
    """
    if not text or not isinstance(text, str):
        return
    with _TRUSTED_LOCK:
        _TRUSTED_TEXTS.add(text.strip())
        for sep in ("\n\n", "\n"):
            for p in text.split(sep):
                if p.strip():
                    _TRUSTED_PARAS.add(p.strip())


class NeverSendBlocked(RuntimeError):
    """Raised when a payload contains never-send material (§5.3).

    Deliberately an exception rather than a redaction: the never-list means
    "block any payload containing this", not "strip this and carry on". It
    propagates through model_router._seal_or_block, which already converts a
    raising gate into a blocked send — so the strongest verdict reuses the
    strongest existing path instead of inventing a second one.
    """


def _redact_placeholder(tier: int) -> str:
    name = Tier.NAMES.get(tier, f"TIER_{tier}")
    # WO-1 (2026-08-25): the placeholder used to state only WHAT happened
    # (withheld) and never WHAT TO DO about it, so a model with no honesty
    # directive (or one that had just been redacted for the same reason —
    # see REFUSAL_HONESTY_DIRECTIVE above) filled the gap with a plausible
    # invented answer instead of reporting the withholding. The behavioral
    # instruction is now IN the placeholder itself, so it survives even when
    # nothing else in the prompt tells the model how to react.
    return (
        f"[EGRESS-GATE: {name} content withheld — did not leave your device. "
        f"Do not retry the call, invent the content, or describe what it "
        f"might have said. Tell the user this specific item was withheld by "
        f"the privacy gate and can be read on a local seat.]"
    )


def _log(provider: str, field: str, tier: int, action: str, reason: str,
         log_path: Path | None = None):
    entry = {
        "ts": time.time(),
        "provider": provider,
        "field": field,
        "tier": Tier.NAMES.get(tier, str(tier)),
        "action": action,
        "reason": reason,
    }
    verdict = "ALLOW" if action == "allow" else "BLOCK"
    # Structured log (survives pythonw / no-console launches). INFO for allow,
    # WARNING for a block so a withheld leak is visible even at a raised log level.
    _dlog.log(
        logging.INFO if action == "allow" else logging.WARNING,
        "%s provider=%s field=%s tier=%s (%s)",
        verdict, provider, field, Tier.NAMES.get(tier, tier), reason,
    )
    dest = log_path or _DEFAULT_LOG
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with open(dest, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def record_binary_egress(provider: str, field: str, *, action: str,
                         reason: str, byte_len: int = 0,
                         log_path: Path | None = None) -> dict:
    """Record a send whose PAYLOAD this gate cannot classify.

    Images, audio and other binary leave without passing `_gate_text`, because
    there is no text to tier. `routes/chat.py` drew the wrong conclusion from
    that true premise for a long time:

        "image/camera BYTES cannot be text-classified by the egress gate ...
         so there is nothing to gate here"

    The bytes are not classifiable. **The decision to send them is**, and it is
    the decision that belongs in the ledger. Without this, a screenshot of the
    user's desktop went to Gemini leaving no trace in the one file that is
    supposed to enumerate everything that left — while a four-word prompt to
    the same provider was logged in full.

    This does not inspect, redact or block. It states, in the same record and
    the same format as every text decision, that a binary payload left (or was
    withheld) and why. Tier is reported as UNCLASSIFIABLE rather than borrowing
    a text tier it did not earn.
    """
    entry = {
        "ts": time.time(),
        "provider": provider,
        "field": field,
        "tier": "UNCLASSIFIABLE",
        "action": action,
        "reason": reason,
        "bytes": int(byte_len or 0),
    }
    _dlog.log(
        logging.INFO if action == "allow" else logging.WARNING,
        "%s provider=%s field=%s tier=UNCLASSIFIABLE bytes=%d (%s)",
        "ALLOW" if action == "allow" else "BLOCK",
        provider, field, entry["bytes"], reason,
    )
    dest = log_path or _DEFAULT_LOG
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with open(dest, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
    return entry


# ── The judgment appeal (§5.3–§5.5) ───────────────────────────────────────────

def _run_appeals(appeals: list, gated: list, provider: str, field: str,
                 log_path) -> int:
    """Give the judgment layer a chance to rescue over-blocked paragraphs.

    THE SAFETY PROPERTY, enforced here and stated in judgment_gate's docstring:
    the model may only rescue what the keyword rules over-blocked; it can never
    authorize sending something unscrubbed. Three things make that true:

      * every paragraph in `appeals` was ALREADY going to be withheld — this
        function can only turn withheld into sent, never the reverse, and it is
        never shown a paragraph that was already passing;
      * a favourable verdict does not send the paragraph. It sends the paragraph
        through the scrubber, and then through `verify_outgoing`, which is
        deterministic code that does not consult the verdict. If a hard
        identifier, watchlist token, or never-send token survives — or the text
        still classifies SENSITIVE — the rescue is refused and the redaction
        placeholder stays;
      * if judgment cannot run at all, nothing is rescued and every paragraph
        keeps the deterministic outcome (§5.4).

    Returns how many paragraphs were actually rescued.
    """
    try:
        from agent_friday.services import judgment_gate as jg
    except Exception:
        return 0
    if not jg.enabled():
        return 0

    # §5.3: never-list material is not judged at all. No verdict overrides it.
    judgeable, blocked_outright = [], 0
    for idx, para, det_tier in appeals:
        if jg.never_send_hits(para) or jg.hard_identifier_hits(para):
            blocked_outright += 1
            continue
        judgeable.append((idx, para, det_tier))
    if not judgeable:
        return 0

    verdicts, detail = jg.judge_spans([p for _, p, _ in judgeable])
    if verdicts is None:
        # §5.4: judgment could not run → the deterministic gate governs this
        # payload unchanged. Never held hostage waiting for a judgment.
        _log(provider, field, Tier.PRIVATE, "redact",
             f"judgment unavailable ({detail}) — deterministic outcome kept",
             log_path)
        return 0

    rescued = 0
    for (idx, para, det_tier), v in zip(judgeable, verdicts):
        verdict, reason = v.get("verdict"), v.get("reason", "")
        if verdict == jg.NEVER_SEND:
            continue                                  # floor does not move

        # A first-person span cannot be third-party material, whatever the
        # model says. Downgraded rather than dropped: STEPHEN_SUBSTANCE still
        # allows a send once the scrub has actually separated something, so
        # this costs capability only where identity is inseparable anyway.
        if verdict == jg.ABOUT_THE_WORLD and jg.looks_first_person(para):
            _log(provider, field, det_tier, "redact",
                 "judgment said ABOUT_THE_WORLD on a FIRST-PERSON span — "
                 "overruled to STEPHEN_SUBSTANCE; a span about the user's own "
                 "affairs is never somebody else's", log_path)
            verdict = jg.STEPHEN_SUBSTANCE

        # A favourable verdict earns a SCRUB ATTEMPT, not a send.
        try:
            from agent_friday.core import _scrub_pii
            scrubbed, _sub = _scrub_pii(para)
        except Exception:
            continue                                  # cannot scrub → cannot send

        # ── STEPHEN_SUBSTANCE requires that the scrub ACTUALLY separated
        # something (found live, 2026-08-17, with the gate switched on) ──
        #
        # §5.2 defines STEPHEN_SUBSTANCE as "his material, where the SUBSTANCE
        # matters and the IDENTITY can be separated", treated by "scrubbed —
        # identifying spans replaced by placeholders — then re-verified, then
        # sent". The leak: for
        #
        #   "My custody hearing is on the 14th and my lawyer says my ex will
        #    contest it."
        #
        # the judge correctly answered STEPHEN_SUBSTANCE — and the scrubber
        # found NOTHING to replace, because the sentence carries no name,
        # number or address. It is pure first-person substance. verify_outgoing
        # then passed it, since it blocks at SENSITIVE and this classifies
        # PRIVATE. So a span the judge had just identified as Stephen's own
        # private material travelled verbatim.
        #
        # A scrub that replaced nothing did not separate identity from
        # substance; it confirmed they cannot be separated. That is the
        # definition of NEVER_SEND, so the span is withheld. ABOUT_THE_WORLD is
        # unaffected — third-party material has no identity of his to separate,
        # which is the whole point of the verdict.
        if verdict == jg.STEPHEN_SUBSTANCE and not _sub:
            _log(provider, field, det_tier, "redact",
                 "judgment=STEPHEN_SUBSTANCE but the scrub replaced nothing — "
                 "identity could not be separated from substance, so the span "
                 "is withheld", log_path)
            continue

        check = jg.verify_outgoing(scrubbed)
        if not check.ok:
            # The step that makes a wrong judgment survivable.
            _log(provider, field, det_tier, "redact",
                 f"judgment said {verdict} but the scrub did not verify "
                 f"({check.reason}; {','.join(check.hits)}) — withheld",
                 log_path)
            continue

        gated[idx] = scrubbed
        rescued += 1
        _log(provider, field, det_tier, "allow",
             f"judgment={verdict} overturned {Tier.NAMES.get(det_tier)} "
             f"after verified scrub: {reason[:120]}", log_path)
        jg.record_overturn(para, verdict, reason, provider, field=field,
                           origin=public_origin_of(para))
    if blocked_outright:
        _log(provider, field, Tier.SENSITIVE, "drop",
             f"{blocked_outright} paragraph(s) on the never-list — not judged",
             log_path)
    return rescued


# ── Field-level gating ────────────────────────────────────────────────────────

def _gate_text(text: str, provider: str, field: str,
               log_path: Path | None = None) -> str:
    """Gate a text string for a cloud provider, rescuing JSON field-wise.

    This is the wrapper every caller reaches. It runs the span-wise gate below
    and, if that withheld something from a JSON payload, descends into the
    structure instead of surrendering the whole result.

    THE BUG THIS FIXES (found 2026-08-24). The span-wise rescue splits on blank
    lines and newlines. `json.dumps` emits ONE LINE with no separators, so for
    every JSON-returning tool the rescue never engaged: a single incidental
    phrase anywhere in the result replaced the ENTIRE payload with a
    125-character notice. Measured — a 636-character news result came back as
    125 characters, which is why voice could call search_news and never read
    the answer back.

    Deliberately placed HERE rather than at the tool-result call site, because
    the callers that need it do not share one. The voice leg gates its tool
    results by calling this function directly (routes/voice.py), the text-chat
    leg arrives via _gate_tool_result, and workers via gate_worker_payload.
    Fixing the wrapper fixes all three without touching any of them.

    Whole-value first, descend second: a payload that passes as a whole costs
    exactly one classification, so nothing on the common path slows down. The
    field-wise walk is paid only by results that would otherwise have been
    destroyed outright — the same bargain the span pass already makes.

    Policy is unchanged and fail-closed: every field is judged by exactly the
    rules the whole value would have been, a field that cannot be salvaged is
    still withheld, and non-JSON text keeps its previous behaviour exactly.
    """
    whole = _gate_text_span(text, provider, field, log_path)
    if whole == text:
        return whole                      # nothing withheld — no need to descend
    parsed = _try_json(text)
    if parsed is None:
        return whole                      # not JSON; the span pass already ran
    gated = _gate_json_value(parsed, provider, field, log_path)
    try:
        return json.dumps(gated, default=str)
    except (TypeError, ValueError):
        return whole                      # unserialisable → keep the safe answer


def _gate_text_span(text: str, provider: str, field: str,
                    log_path: Path | None = None) -> str:
    """Gate a single text string for a cloud provider.

    Multi-paragraph fields are gated SPAN-WISE: only the offending paragraphs
    are withheld, the rest survives. Whole-field drops destroyed every cloud
    call that merely mentioned a trigger word — the assembled system prompt
    (which the router already tier-gates section-by-section) came back as ""
    and the Anthropic API rejected the empty message. Fail-closed is preserved:
    if no paragraph individually explains the whole-field signal (or every
    paragraph trips), we fall back to withholding the entire field.
    """
    if not text or not isinstance(text, str):
        return text

    # ── §5.3 the never-list: the floor, and it moves for nothing ──
    # Found by the probe battery on 2026-08-17, before this layer ever shipped:
    # the never-send check originally lived inside the judgment appeal, so with
    # judgment disabled — the DEFAULT — a planted never-send token sailed
    # straight through. A floor that only exists when an optional layer is
    # switched on is not a floor. It runs here: unconditionally, ahead of the
    # trusted and public registries (no provenance exemption may override it),
    # ahead of classification, and independent of judgment.
    try:
        from agent_friday.services import judgment_gate as _jg
        _never = _jg.never_send_hits(text)
    except Exception:
        _never = []
    if _never and not _never_send_covered_by_override(text):
        _log(provider, field, Tier.SENSITIVE, "block",
             f"never-send material present ({len(_never)} token(s)) — payload blocked",
             log_path)
        raise NeverSendBlocked(
            f"This payload contains material on your never-send list "
            f"({len(_never)} match(es) in {field}), so I did not send it to "
            f"{provider}. Nothing was redacted and sent — the whole call was "
            f"stopped. Use a local model to work with this."
        )
    elif _never:
        # WO-17 §3: every never-send-tripping paragraph in this text is
        # individually covered by a file grant's explicit override — the
        # floor still applied, it just found consent already on file for the
        # exact paragraphs it flagged. Fall through to normal gating below,
        # which still classifies/redacts anything the override does NOT cover.
        _log(provider, field, Tier.SENSITIVE, "allow",
             f"never-send material present ({len(_never)} token(s)) but every "
             f"matching paragraph is covered by a file-grant override",
             log_path)

    with _TRUSTED_LOCK:
        _t_stripped = text.strip()
        # P7(a): the whole-field trusted check consulted _TRUSTED_TEXTS but NOT
        # _PUBLIC_PARAS, so a registered public string sent as a single
        # paragraph skipped the span loop entirely and was redacted whole —
        # the exemption worked only for text that happened to arrive beside
        # other paragraphs. Both registries are consulted here now.
        if _t_stripped in _TRUSTED_TEXTS:
            _log(provider, field, Tier.PUBLIC, "allow", "trusted-self-authored", log_path)
            return text
        if _t_stripped in _PUBLIC_PARAS:
            _origin = _PUBLIC_ORIGINS.get(_t_stripped, "")
            _log(provider, field, Tier.PUBLIC, "allow", _origin_reason(_origin), log_path)
            return text
        if provider in _PROVIDER_ECHO.get(_t_stripped, ()):
            _log(provider, field, Tier.PUBLIC, "allow",
                 _origin_reason(f"provider-echo:{provider}"), log_path)
            return text
    tier = _classify_cloud(text)
    if tier == Tier.PUBLIC:
        _log(provider, field, tier, "allow", "public-content", log_path)
        return text
    # Span-level pass for multi-paragraph text.
    sep = "\n\n" if "\n\n" in text else ("\n" if "\n" in text else None)
    if sep is not None:
        paras = text.split(sep)
        gated, withheld, trusted = [], 0, 0
        # §5.4: judgment is an APPEALS COURT. Paragraphs the deterministic
        # layers would withhold are collected here and judged in ONE batched
        # call; paragraphs that already pass are never shown to the judge, so
        # judgment cannot make anything travel that was not already travelling.
        appeals: list[tuple[int, str, int]] = []   # (index, text, det_tier)
        for p in paras:
            if not p.strip():
                gated.append(p)
                continue
            with _TRUSTED_LOCK:
                _ps = p.strip()
                _p_origin = _PUBLIC_ORIGINS.get(_ps, "")
                _p_echo = provider in _PROVIDER_ECHO.get(_ps, ())
                _p_trusted = _ps in _TRUSTED_PARAS or _ps in _PUBLIC_PARAS or _p_echo
            if _p_trusted:
                gated.append(p)
                trusted += 1
                # WO-17 acceptance: every grant-passed span must be
                # attributable to its grant id in the egress log, even when
                # this is a partial (span-level) rescue that never reaches
                # the whole-field log line below.
                if _p_echo:
                    _log(provider, field, Tier.PUBLIC, "allow",
                         _origin_reason(f"provider-echo:{provider}"), log_path)
                elif _p_origin.startswith("user-grant:"):
                    _log(provider, field, Tier.PUBLIC, "allow",
                         _origin_reason(_p_origin), log_path)
                continue
            pt = _classify_cloud(p)
            if pt == Tier.PUBLIC:
                gated.append(p)
            else:
                appeals.append((len(gated), p, pt))
                gated.append(_redact_placeholder(pt))
                withheld += 1
        if appeals:
            rescued = _run_appeals(appeals, gated, provider, field, log_path)
            withheld -= rescued
        nonempty = sum(1 for p in paras if p.strip())
        if (0 < withheld < nonempty) or (withheld == 0 and trusted > 0) or \
                (withheld == 0 and appeals):
            # Either the split localized the signal (partial withholding), or
            # the whole-field signal came entirely from trusted self-authored
            # paragraphs. withheld == 0 with NO trusted paragraphs means the
            # signal spans paragraphs — distrust the split and fall through.
            _log(provider, field, tier, "redact" if withheld else "allow",
                 f"tier={Tier.NAMES[tier]} span-level: withheld {withheld}/{nonempty} "
                 f"paragraphs ({trusted} trusted)",
                 log_path)
            return sep.join(gated)
        # Fall through to the whole-field action (all paragraphs tripped, or
        # the signal could not be localized).

    # Whole-field appeal. This case is NOT an edge case: a single-paragraph
    # message has no separator, so it never reaches the span loop — and single
    # paragraphs are what ordinary chat turns and research questions are made
    # of. Without this, judgment would have covered only multi-paragraph text
    # and quietly done nothing for the traffic it was built for.
    _whole = [(0, text, tier)]
    _slot = [None]
    if _run_appeals(_whole, _slot, provider, field, log_path) and _slot[0] is not None:
        return _slot[0]

    if tier == Tier.SENSITIVE:
        _log(provider, field, tier, "drop", f"tier={Tier.NAMES[tier]}", log_path)
        return ""  # cloud gets nothing for SENSITIVE
    # TIER_2 / PRIVATE → redacted placeholder
    _log(provider, field, tier, "redact", f"tier={Tier.NAMES[tier]}", log_path)
    return _redact_placeholder(tier)


def _gate_messages(messages: list, provider: str,
                   log_path: Path | None = None) -> list:
    """Gate a list of message dicts. Returns a new list; never mutates input."""
    gated = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            gated.append(msg)
            continue
        if "content" not in msg:
            gated.append(msg)
            continue
        content = msg["content"]
        if isinstance(content, str):
            _g = _gate_text(content, provider, f"message[{i}].content", log_path)
            if content and not _g:
                # The Anthropic API rejects empty message content ("all messages
                # must have non-empty content"), turning a withheld turn into a
                # hard 400 for the whole call. Substitute a marker the model can
                # act on — it still sees NONE of the withheld content.
                _g = _MESSAGE_WITHHELD
            gated.append({**msg, "content": _g})
        elif isinstance(content, list):
            new_parts = []
            for j, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "text":
                    new_parts.append({
                        **part,
                        "text": _gate_text(
                            part.get("text", ""), provider,
                            f"message[{i}].content[{j}].text", log_path,
                        ),
                    })
                elif isinstance(part, dict) and part.get("type") == "tool_result":
                    new_parts.append(_gate_tool_result(
                        part, provider, f"message[{i}].content[{j}]", log_path))
                elif isinstance(part, dict) and part.get("type") == "tool_use":
                    new_parts.append(_gate_tool_use(
                        part, provider, f"message[{i}].content[{j}]", log_path))
                else:
                    new_parts.append(part)
            gated.append({**msg, "content": new_parts})
        else:
            gated.append(msg)
    return gated


# What the model sees in place of a fully withheld user/assistant message.
# Phrased so the model's natural response tells the user what happened and
# how to proceed — important for VOICE turns, where this reply gets spoken.
_MESSAGE_WITHHELD = ("[EGRESS-GATE: message withheld — it stayed on this "
                     "device. Tell the user their last message contained "
                     "sensitive content that is only processed locally, and "
                     "that they can switch to the local model to discuss it.]")

# What the model sees in place of a SENSITIVE tool result. An empty string
# (what _gate_text returns for SENSITIVE) would read as "the tool returned
# nothing" and send the agent loop into pointless retries; this marker lets it
# report the withholding and move on. WO-1 (2026-08-25): states the behavioral
# instruction explicitly, same reasoning as _redact_placeholder above — a
# model with no other honesty context still knows not to invent the result.
_TOOL_RESULT_WITHHELD = ("[tool result withheld by egress gate — SENSITIVE "
                         "content stays on this device; use a local model to "
                         "work with it. Do not retry the call, invent the "
                         "content, or describe what it might have said. Tell "
                         "the user this specific item was withheld by the "
                         "privacy gate and can be read on a local seat.]")


# What the model sees in place of a single withheld JSON field. Short on
# purpose: a result with many private fields should stay readable, not turn
# into a wall of identical notices.
_FIELD_WITHHELD = "[withheld by egress gate]"


def _try_json(text: str):
    """Parse a tool result as JSON, or return None if it is not JSON.

    Only containers count. A bare JSON scalar ("42", or a quoted string) has no
    fields to descend into, so treating it as structured buys nothing and would
    only re-serialise it into a different shape than it arrived in.
    """
    t = (text or "").lstrip()
    if not t or t[0] not in "{[":
        return None            # cheap reject before paying for a parse
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _gate_json_value(node, provider: str, field: str,
                     log_path: Path | None = None):
    """Gate every string VALUE in a parsed JSON tool result; keep every key.

    Keys are structure, not content — gating them would corrupt the result into
    something the model cannot read. Numbers, booleans and null carry no prose
    for a classifier to judge and pass through untouched.

    Calls the SPAN gate, not the wrapper: descending is already happening here,
    structurally, over the parsed object. Routing back through the wrapper
    would let a string field that happens to look like JSON start a second
    descent, and reentrancy is not something the last security boundary in the
    codebase should have to reason about.
    """
    if isinstance(node, str):
        if not node:
            return node
        g = _gate_text_span(node, provider, field, log_path)
        return g if g else _FIELD_WITHHELD
    if isinstance(node, dict):
        return {k: _gate_json_value(v, provider, f"{field}.{k}", log_path)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_gate_json_value(v, provider, f"{field}[{i}]", log_path)
                for i, v in enumerate(node)]
    return node


def _gate_tool_result(part: dict, provider: str, field: str,
                      log_path: Path | None = None) -> dict:
    """Gate the text inside a tool_result block (file reads, command output —
    whatever a tool pulled mid-loop). Anthropic allows `content` as a plain
    string or a list of typed blocks; non-text blocks (images) pass through
    untouched because a text classifier can't judge them (documented caveat)."""
    def _gate_one(text: str, subfield: str) -> str:
        out = _gate_text(text, provider, subfield, log_path)
        return _TOOL_RESULT_WITHHELD if (text and out == "") else out

    inner = part.get("content")
    if isinstance(inner, str):
        return {**part, "content": _gate_one(inner, f"{field}.tool_result")}
    if isinstance(inner, list):
        new_inner = []
        for k, block in enumerate(inner):
            if isinstance(block, dict) and block.get("type") == "text":
                new_inner.append({
                    **block,
                    "text": _gate_one(block.get("text", ""),
                                      f"{field}.tool_result[{k}]"),
                })
            else:
                new_inner.append(block)
        return {**part, "content": new_inner}
    return part


# What the model sees in place of a withheld tool ARGUMENT value.
_ARG_WITHHELD = "[withheld by egress gate]"


# ── Tool-definition classification cache ──────────────────────────────────────
# Tool DEFINITIONS are static: first-party descriptions ship in the binary, and
# an MCP server's description + schema are fixed at registration and re-sent,
# byte-identical, on every single cloud call. Classifying them per call is pure
# repeat work, and it is not free. Measured 2026-08-24, 112 MCP tools with six
# schema properties each, steady state (classifier models already warm, so this
# excludes the ~25s one-time lazy load that dominates any first measurement):
#
#     before this cache, gating descriptions only          2,930 ms per call
#     with this cache, gating descriptions AND schema        700 ms per call
#
# — 4.2x faster while classifying 7x more text (784 strings vs 112). Without
# the cache the same widened coverage would have cost ~19s per call, which is
# what makes this a prerequisite for the schema gating rather than a nicety.
#
# Scoped deliberately to tool-definition text and NOTHING else. User content —
# messages, tool arguments, tool results — is never cached: it is unbounded,
# it is the sensitive material, and holding it in a module-level dict for the
# life of the process is exactly the sort of quiet copy this gate exists to
# prevent. The never-send check is also left OUTSIDE the cache (it runs on
# every call, in _gate_text) because that list can change at runtime and a
# floor that a stale cache can hold open is not a floor.
_TOOL_TIER_CACHE: dict[str, int] = {}
_TOOL_TIER_LOCK = threading.Lock()
_TOOL_TIER_MAX = 8192


def _classify_tool_text(text: str) -> int:
    """_classify_cloud for static tool-definition prose, memoised by exact text."""
    with _TOOL_TIER_LOCK:
        hit = _TOOL_TIER_CACHE.get(text)
    if hit is not None:
        return hit
    tier = _classify_cloud(text)
    with _TOOL_TIER_LOCK:
        if len(_TOOL_TIER_CACHE) >= _TOOL_TIER_MAX:
            _TOOL_TIER_CACHE.clear()  # bounded; a rebuild costs one slow call
        _TOOL_TIER_CACHE[text] = tier
    return tier


def _gate_tool_prose(text: str, provider: str, field: str,
                     log_path: Path | None = None) -> str:
    """Gate one static tool-definition string (a description or schema title).

    Tool prose is short, single-paragraph, and author-written, so the span-wise
    paragraph logic in _gate_text buys nothing here — but the never-send floor
    still applies, and it is re-checked on every call rather than cached.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        from agent_friday.services import judgment_gate as _jg
        _never = _jg.never_send_hits(text)
    except Exception:
        _never = []
    if _never:
        _log(provider, field, Tier.SENSITIVE, "block",
             f"never-send material present ({len(_never)} token(s)) — payload blocked",
             log_path)
        raise NeverSendBlocked(
            f"This payload contains material on your never-send list "
            f"({len(_never)} match(es) in {field}), so I did not send it to "
            f"{provider}. Nothing was redacted and sent — the whole call was "
            f"stopped. Use a local model to work with this."
        )
    if _classify_tool_text(text) > Tier.PUBLIC:
        _log(provider, field, Tier.PRIVATE, "redact", "sensitive-tool-prose",
             log_path)
        return ""
    _log(provider, field, Tier.PUBLIC, "allow", "public-tool-prose", log_path)
    return text


def _tool_view(tool: dict) -> tuple[dict, str]:
    """Return (the dict actually holding name/description, the schema key).

    Two wire shapes carry the same tool. Anthropic puts name/description/
    input_schema at the top level; the OpenAI function shape nests
    name/description/parameters under ``function`` (built by
    routing.model_router.anthropic_to_openai_tools).

    The gate used to read the top level only, so on every openai-compatible
    CLOUD provider — openai, openrouter, any openai-shaped cloud seat — the
    name came back "" , failed the ``mcp_`` prefix test, and the description
    travelled ungated. The Anthropic path withheld the very same string. That
    is not a policy difference, it is the gate reading the wrong envelope
    (found 2026-08-24).
    """
    inner = tool.get("function")
    if isinstance(inner, dict) and ("name" in inner or "description" in inner):
        return inner, "parameters"
    return tool, "input_schema"


def _gate_schema_prose(node, provider: str, field: str,
                       log_path: Path | None = None):
    """Gate the PROSE inside a third-party tool schema, and only the prose.

    An MCP server authors its input schema as well as its description, and the
    schema is prose-bearing: ``description`` and ``title`` on every property,
    nested arbitrarily deep. Only the top-level description was ever replaced,
    so everything a server wrote into its schema reached the cloud verbatim.

    Deliberately narrow: types, ``required`` lists, property NAMES and ``enum``
    VALUES are machinery, not prose. Redacting an enum member does not make a
    tool private, it makes it uncallable — the model must send the exact string
    back. A withheld description degrades capability; a withheld enum destroys
    it. So structure is copied through untouched and only the two prose keys
    are gated.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("description", "title") and isinstance(v, str) and v:
                g = _gate_tool_prose(v, provider, f"{field}.{k}", log_path)
                out[k] = g if g else _ARG_WITHHELD
            else:
                out[k] = _gate_schema_prose(v, provider, f"{field}.{k}", log_path)
        return out
    if isinstance(node, list):
        return [_gate_schema_prose(v, provider, f"{field}[{i}]", log_path)
                for i, v in enumerate(node)]
    return node


def _gate_arg_values(node, provider: str, field: str,
                     log_path: Path | None = None):
    """Gate every STRING VALUE inside a tool_use argument object.

    Argument names are chosen by whoever wrote the tool, so there is no key
    whitelist to gate against — which is exactly how this was missed. The gate
    walked dict keys named content/text/system/prompt; a server whose argument
    is called ``q`` or ``body`` fell straight through. Here every string value
    at any depth is gated and every KEY is preserved, so the object keeps the
    shape the provider expects.
    """
    if isinstance(node, str):
        if not node:
            return node
        g = _gate_text(node, provider, field, log_path)
        return g if g else _ARG_WITHHELD
    if isinstance(node, dict):
        return {k: _gate_arg_values(v, provider, f"{field}.{k}", log_path)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_gate_arg_values(v, provider, f"{field}[{i}]", log_path)
                for i, v in enumerate(node)]
    return node  # ints, bools, None — nothing for a text classifier to judge


def _gate_tool_use(part: dict, provider: str, field: str,
                   log_path: Path | None = None) -> dict:
    """Gate the arguments of an assistant tool_use block replayed in history.

    The agent loop echoes each assistant tool_use back into the conversation
    (services/agent.py) so the next turn has the context. Those arguments are
    real user data — what was written to the vault, who a message was
    addressed to — and they were neither tier-gated (``tool_use`` is not one of
    the block types _gate_messages handled) nor PII-scrubbed (``input`` is not
    one of the keys _scrub_all recurses into).

    On a same-provider turn this re-sends what that provider itself authored.
    The leak is a history assembled against one seat and replayed to another:
    a local seat that falls back to cloud carries its own tool calls with it.

    ``id``/``name``/``type`` are left alone. Anthropic pairs tool_use to
    tool_result by id, so touching them would turn a redaction into a 400.
    """
    inp = part.get("input")
    if not isinstance(inp, (dict, list, str)):
        return part
    return {**part, "input": _gate_arg_values(
        inp, provider, f"{field}.tool_use.input", log_path)}


def _gate_tools(tools: list, provider: str,
                log_path: Path | None = None) -> list:
    """Redact tool descriptions that could carry third-party context.

    Scoped deliberately to MCP-registered tools (``mcp_<server>_<tool>``).

    First-party tool descriptions are static text authored in this repository
    and shipped in the binary. They cannot leak the vault because they were
    never in the vault — they are documentation, not user data. Running them
    through a content classifier meant any description containing an ordinary
    word like "contact", "family" or "calendar" was blanked, and the model was
    handed a list of tools it could not read. That does not protect anything;
    it just makes Friday look incapable, and it did so on every cloud-fallback
    turn for an unknown length of time (found 2026-08-21).

    MCP tool descriptions are still gated: they arrive at runtime from a
    third-party server, so unlike first-party text they are not something this
    repository vouched for. When one is withheld the replacement keeps the
    tool's name and origin so the model can still reason about whether to call
    it, rather than receiving an anonymous entry — a withheld description
    should degrade capability, not erase it.
    """
    gated = []
    for tool in tools:
        if not isinstance(tool, dict):
            gated.append(tool)
            continue
        # Read whichever envelope this tool arrived in (Anthropic top-level or
        # OpenAI function-nested) so the same policy applies to both.
        holder, schema_key = _tool_view(tool)
        name = holder.get("name") or ""
        if not name.startswith("mcp_"):
            gated.append(tool)
            continue

        new_holder = dict(holder)
        changed = False

        desc = holder.get("description", "")
        if desc and _classify_tool_text(desc) > Tier.PUBLIC:
            _log(provider, "tool.description", Tier.PRIVATE,
                 "redact", "sensitive-tool-desc", log_path)
            new_holder["description"] = (
                f"{name}: description withheld by the egress gate because it "
                f"contained content classified as private. The tool is still "
                f"callable; its parameters are unchanged.")
            changed = True

        schema = holder.get(schema_key)
        if isinstance(schema, dict):
            gated_schema = _gate_schema_prose(
                schema, provider, f"tool[{name}].{schema_key}", log_path)
            if gated_schema != schema:
                new_holder[schema_key] = gated_schema
                changed = True

        if not changed:
            gated.append(tool)
        elif holder is tool:
            gated.append(new_holder)
        else:
            gated.append({**tool, "function": new_holder})
    return gated


# ── Public API ────────────────────────────────────────────────────────────────

def gate_text(text: str, provider: str, field: str = "prompt",
              log_path: Path | None = None) -> str:
    """Gate a single user-supplied text string for a cloud provider.

    Public wrapper around the field-level gate so call sites that do NOT go
    through the Anthropic/OpenAI payload path — most importantly the Gemini
    ``models.generate_content`` sites, which build their own ``contents`` and
    never hit ``seal_outbound`` — can still enforce the boundary on the text
    they send. Local providers pass through unchanged. Same fail-closed
    semantics as ``seal_outbound``: SENSITIVE → "" (dropped), PRIVATE →
    redacted placeholder, PUBLIC → unchanged.
    """
    if not _is_cloud(provider):
        return text
    return _gate_text(text, provider, field, log_path)


def gate_worker_payload(
    payload: dict[str, Any],
    *,
    base_url: str,
    provider: str = "ollama",
    log_path: Path | None = None,
) -> dict[str, Any]:
    """The orchestrator worker adapters' single egress entry point (decision D2).

    Every other provider call site funnels through ``model_router._seal_or_block``.
    ``worker_adapters/ollama_adapter.py`` funnelled through nothing at all: it
    POSTed a hand-built payload straight to a hardcoded ``localhost:11434``,
    skipping the gate, the PII scrub, health recording and cost metering. The
    bypass was undocumented — unlike the Gemini dispatch gap, which carries an
    explaining comment.

    The fix is not "call seal_outbound from the adapter" but "stop letting the
    CALL SITE decide whether gating applies". This function makes that decision
    itself, from the destination:

      * destination verified on-device  → returned unchanged. Cheap by
        construction: no classifier runs, no payload copy. This is D2's "make
        the gate cheap for local traffic rather than optional" — the traffic is
        still gated, the gate just has nothing to do.
      * anything else                   → fully sealed, fail-closed. A gate that
        cannot verify itself raises rather than letting the payload out.

    Handles the Ollama-native ``/api/generate`` shape (a bare ``prompt`` string),
    which ``seal_outbound`` does not cover — it only knows the Anthropic
    ``system``/``messages``/``tools`` keys, so an ungated ``prompt`` would have
    sailed straight through even if the adapter had called it.
    """
    try:
        from agent_friday.routing.provider_descriptors import is_private_host
        on_device = bool(is_private_host(base_url))
    except Exception:
        on_device = False  # cannot verify the host → treat as cloud

    if on_device and is_local_provider(provider):
        return payload

    # Cloud-bound (or unverifiable): fail-closed, same contract as every other
    # provider call site.
    if not gate_operational():
        raise RuntimeError(
            "Egress gate is non-functional (startup self-test failed); worker "
            f"send to {base_url!r} blocked. Fix the gate or use a local model."
        )

    sealed = seal_outbound(dict(payload), provider, log_path)
    if isinstance(sealed.get("prompt"), str):
        sealed["prompt"] = gate_text(sealed["prompt"], provider, "prompt",
                                     log_path)
    return sealed


def _scrub_all(obj, lookup: dict):
    """§5.5 step 1 — run core._scrub_pii over every string in the payload.

    WHY THIS MOVED HERE. The design doc lists the scrub as step 1 "inside" the
    gate, "existing, unconditional". It was neither. The scrubber ran at the
    CHAT ROUTE (routes/chat.py) and as a tool post-hook, while the gate ran a
    layer below at the model router — two systems that never met, with cloud
    paths that got one and not the other (channels, workers, compute_client,
    subagent dispatch). Putting the scrub at the choke point makes the
    documented order of operations real, and means a call site cannot forget it.

    Idempotent by construction: the placeholders it writes ([PII:kind:hash])
    match none of the PII patterns, so paths that already scrubbed (chat) find
    nothing left to do and pay only the regex pass.
    """
    from agent_friday.core import _scrub_pii
    if isinstance(obj, str):
        out, sub = _scrub_pii(obj)
        lookup.update(sub)
        return out
    if isinstance(obj, list):
        return [_scrub_all(x, lookup) for x in obj]
    if isinstance(obj, dict):
        # Only content-bearing keys. Scrubbing ids/roles/model names would
        # corrupt the request shape for no privacy gain.
        return {k: (_scrub_all(v, lookup)
                    if k in ("content", "text", "system", "prompt") else v)
                for k, v in obj.items()}
    return obj


def seal_outbound(
    payload: dict[str, Any],
    provider: str,
    log_path: Path | None = None,
    pii_lookup: dict | None = None,
) -> dict[str, Any]:
    """Gate the assembled call payload before it leaves to a cloud provider.

    Parameters
    ----------
    payload    : the fully assembled call payload (system, messages, tools, …)
    provider   : provider name — "anthropic", "openai", "gemini", "ollama", etc.
    log_path   : optional path to the egress log file
    pii_lookup : optional dict the gate FILLS with tag -> real value, for the
                 caller to pass to core._rehydrate_pii on the response.

    On pii_lookup: seal_outbound returns a payload and has nowhere to hand back
    a rehydration table, so callers that want their user's real data restored
    in the reply pass a dict in. A caller that passes nothing still gets the
    scrub — it just sees "[PII:addr:1a2b3c4d]" in the response instead of the
    address. That default is deliberate: forgetting the parameter costs
    legibility, never privacy.

    Returns a new payload dict with sensitive content redacted or dropped.
    Local providers (Ollama / 'local') are returned unchanged.

    Default on uncertainty: REDACT — fail-closed, not fail-open.
    """
    if not _is_cloud(provider):
        return payload  # stays on-device, no gating needed

    sealed = dict(payload)

    # ── §5.5 step 1: deterministic identifier scrub, unconditional ──
    _lk = pii_lookup if isinstance(pii_lookup, dict) else {}
    try:
        for key in ("system", "messages", "prompt"):
            if key in sealed:
                sealed[key] = _scrub_all(sealed[key], _lk)
    except Exception as e:
        # A scrub that cannot run must not become a send that skips it.
        _dlog.error("PII scrub failed inside the gate — BLOCKING: %s", e)
        raise RuntimeError(
            f"Egress scrub failed; cloud send blocked rather than sent "
            f"unscrubbed: {e}"
        ) from e

    # ── §5.5 steps 2-4: classify, judge, verify ──
    # System prompt. Anthropic accepts it as a plain string OR as a list of
    # text blocks — the shape prompt caching requires, because cache_control
    # rides on the block. Gating only the string form meant adopting block-form
    # caching would have silently un-gated the system prompt: the scrub still
    # ran (it recurses "text" keys) so identifiers were masked, but the TIER
    # gate never saw it and prose like a custody discussion travelled whole.
    # Nothing builds it that way today; this covers it before anything does.
    if "system" in sealed:
        _sys = sealed["system"]
        if isinstance(_sys, str):
            sealed["system"] = _gate_text(_sys, provider, "system", log_path)
        elif isinstance(_sys, list):
            _blocks = []
            for _k, _b in enumerate(_sys):
                if isinstance(_b, dict) and _b.get("type") == "text":
                    _blocks.append({**_b, "text": _gate_text(
                        _b.get("text", ""), provider, f"system[{_k}].text",
                        log_path)})
                else:
                    _blocks.append(_b)
            sealed["system"] = _blocks

    # Message history (Anthropic format: list of dicts)
    if "messages" in sealed and isinstance(sealed["messages"], list):
        sealed["messages"] = _gate_messages(
            sealed["messages"], provider, log_path
        )

    # Tool definitions
    if "tools" in sealed and isinstance(sealed["tools"], list):
        sealed["tools"] = _gate_tools(sealed["tools"], provider, log_path)

    return sealed


# ── Startup self-test (R2, Fable 5 adversarial review) ────────────────────────
_SELF_TEST_RESULT = None  # None = not yet run; dict {"ok": bool, ...} once run


def startup_self_test() -> dict:
    """Seal a known-SENSITIVE probe once and record whether the gate withheld it.

    Called at server boot: if the gate cannot positively withhold a probe that
    is unambiguously Tier-3 (SSN + bank-account keywords) — a layer failed to
    import, the classifier crashed, or gating silently passed the text through —
    cloud routing is refused (model_router._seal_or_block consults
    gate_operational()) so the failure mode is caught at boot, not at first leak.
    """
    global _SELF_TEST_RESULT
    probe = "My SSN is 123-45-6789 and my bank account number is 987654321."  # pragma: allowlist secret
    try:
        sealed = seal_outbound(
            {"messages": [{"role": "user", "content": probe}]},
            "selftest-cloud",
            log_path=Path(os.devnull),
        )
        msgs = sealed.get("messages") or [{}]
        out = str(msgs[0].get("content", "")) if isinstance(msgs[0], dict) else ""
        ok = probe not in out and "123-45-6789" not in out  # pragma: allowlist secret
        _SELF_TEST_RESULT = {"ok": ok}
        if not ok:
            _SELF_TEST_RESULT["error"] = "sensitive probe survived the gate"
        # False-positive leg: benign, self-authored-style content must SURVIVE
        # sealing. An over-aggressive classifier once emptied every system
        # prompt (it mentions the Sovereign Vault) and everyday user turns,
        # 400-ing all cloud calls — invisible to the leak-only probe above.
        # A false-positive failure is logged loudly but does NOT flip ok:
        # refusing all cloud routing over over-redaction would be a worse
        # failure mode than the over-redaction itself.
        benign_msg = "Good morning! What is on my schedule for today?"
        benign_sys = ("You are Agent Friday, a helpful assistant. You know your "
                      "user's life context through the Sovereign Vault, wiki, and "
                      "trust graph.\n\nRespond conversationally.")
        fp = seal_outbound(
            {"system": benign_sys,
             "messages": [{"role": "user", "content": benign_msg}]},
            "selftest-cloud", log_path=Path(os.devnull),
        )
        fp_msg = str((fp.get("messages") or [{}])[0].get("content", ""))
        fp_sys = str(fp.get("system", ""))
        fp_ok = (benign_msg == fp_msg) and ("Agent Friday" in fp_sys)
        _SELF_TEST_RESULT["false_positive_ok"] = fp_ok
        if not fp_ok:
            _dlog.warning(
                "egress gate FALSE-POSITIVE self-test failed: benign content "
                "was redacted (system survived=%s, message survived=%s) — "
                "cloud calls will degrade or 400",
                "Agent Friday" in fp_sys, benign_msg == fp_msg,
            )
    except Exception as e:
        _SELF_TEST_RESULT = {"ok": False, "error": str(e)}

    # ── §5.6.1: the probe battery ──
    # The two-probe self-test above proves the gate withholds an SSN. It does
    # not prove the judgment layer cannot be talked into rescuing something it
    # should not, so the battery runs a fixture set of planted private material
    # that must NEVER survive. A private-fixture failure disables the judgment
    # layer and notifies loudly (in judgment_gate.probe_battery); it does not
    # flip `ok`, because falling back to the deterministic gate is the SAFE
    # state and refusing all cloud routing over it would be the worse failure.
    try:
        from agent_friday.services import judgment_gate as _jg
        battery = _jg.probe_battery()
        _SELF_TEST_RESULT["judgment_battery"] = {
            "ok": battery.get("ok"),
            "private_probes": battery.get("private_probes"),
            "leaked": [p["probe"] for p in battery.get("leaked", [])],
            "public_lost": battery.get("public_lost", []),
        }
        if not battery.get("ok"):
            _dlog.error(
                "judgment probe battery FAILED (%s leaked) — judgment layer "
                "disabled, deterministic gate governing alone",
                ",".join(p["probe"] for p in battery.get("leaked", [])))
    except Exception as e:
        _SELF_TEST_RESULT["judgment_battery"] = {"ok": None, "error": str(e)}
        _dlog.warning("judgment probe battery could not run: %s", e)

    return _SELF_TEST_RESULT


def gate_operational() -> bool:
    """False only when the startup self-test ran AND failed.

    When the self-test has not run (unit tests, library embedding) the per-call
    fail-closed wrapper in model_router remains the enforcement point, so the
    default is True.
    """
    return _SELF_TEST_RESULT is None or bool(_SELF_TEST_RESULT.get("ok"))
