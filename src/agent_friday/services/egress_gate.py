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


def register_public_text(text: str, origin: str = "") -> None:
    """Register externally-published text as gate-exempt, by provenance.

    Call ONLY from an ingest path, with text that came back from fetching a
    public source. Never from a send path, and never with anything the user
    wrote.
    """
    if not text or not isinstance(text, str):
        return
    t = text.strip()
    if not t or len(t) > 2000:      # a headline or a summary, not a document
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
    return (
        f"[EGRESS-GATE: {name} content withheld — did not leave your device. "
        f"Run this on a local seat to process it without redaction.]"
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

        # A favourable verdict earns a SCRUB ATTEMPT, not a send.
        try:
            from agent_friday.core import _scrub_pii
            scrubbed, _sub = _scrub_pii(para)
        except Exception:
            continue                                  # cannot scrub → cannot send

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
            _log(provider, field, Tier.PUBLIC, "allow",
                 f"third-party-published{(' origin=' + _origin) if _origin else ''}",
                 log_path)
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
                _p_trusted = _ps in _TRUSTED_PARAS or _ps in _PUBLIC_PARAS
            if _p_trusted:
                gated.append(p)
                trusted += 1
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
# report the withholding and move on.
_TOOL_RESULT_WITHHELD = ("[tool result withheld by egress gate — SENSITIVE "
                         "content stays on this device; use a local model to "
                         "work with it]")


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


def _gate_tools(tools: list, provider: str,
                log_path: Path | None = None) -> list:
    """Scan tool descriptions; redact any that carry sensitive context."""
    gated = []
    for tool in tools:
        if not isinstance(tool, dict):
            gated.append(tool)
            continue
        desc = tool.get("description", "")
        if desc and _classify_cloud(desc) > Tier.PUBLIC:
            _log(provider, "tool.description", Tier.PRIVATE,
                 "redact", "sensitive-tool-desc", log_path)
            gated.append({**tool, "description": "[description withheld by egress gate]"})
        else:
            gated.append(tool)
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
    # System prompt
    if "system" in sealed and isinstance(sealed["system"], str):
        sealed["system"] = _gate_text(
            sealed["system"], provider, "system", log_path
        )

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
