"""
judgment_gate — telling Stephen's affairs apart from the world's.

WHY (deep-research.md §5). The existing gate classifies by pattern: regex,
keyword lists, NER, embeddings. Those layers are good at what a span LOOKS
like and structurally blind to WHOSE it is. The keyword rules exist to keep
Stephen's legal, medical and financial affairs on this machine — and they
cannot tell his affairs from a story about someone else's. Measured: 9 of 120
public news headlines classified TIER_3 because they contained "court" or
"raised a Series B" (ca4ee8c). For a journalist whose beat IS courts and
money, the rule shreds exactly the payloads that need frontier intelligence.

Stephen's instruction, verbatim: "keywording is insufficient; we need a
judgement call and a classification system to protect sensitive materials, and
it should work with the PII scrubber so we don't lock cloud models out
completely (we'll need frontier intelligence sometimes)."

THE SAFETY PROPERTY, which everything here is arranged to protect:

    The judgment model may only RESCUE material the keyword rules
    over-blocked. It can NEVER authorize sending something unscrubbed.

Mechanically, that is three facts:

  1. Judgment is an appeals court. It is consulted ONLY on spans the
     deterministic layers already decided to withhold. A span the
     deterministic layers would have sent is never shown to it, so judgment
     cannot make anything travel that was not already travelling.
  2. A verdict is permission to ATTEMPT a rescue, not permission to send.
     Every rescued span is scrubbed and then re-checked by deterministic code
     (`verify_outgoing`). If a hard identifier, a watchlist token or a
     never-send token survives, the send is BLOCKED — not degraded, not
     logged-and-sent.
  3. The judge never sees a decision it can skip. When the seat is
     unreachable, times out, or returns malformed JSON, the deterministic gate
     governs that payload unchanged — fail toward redaction, never toward open.

Everything here composes with what already exists and replaces none of it:
core._scrub_pii is the scrubber, sensitivity_classifier is the classifier, and
egress_gate.seal_outbound remains the single choke point.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from agent_friday.services.sensitivity_classifier import Tier

_log = logging.getLogger("friday.judgment")

# ── Verdicts (§5.2) ───────────────────────────────────────────────────────────
ABOUT_THE_WORLD = "ABOUT_THE_WORLD"      # third-party published material
STEPHEN_SUBSTANCE = "STEPHEN_SUBSTANCE"  # his, but identity is separable
NEVER_SEND = "NEVER_SEND"                # identity and substance inseparable
_VALID_VERDICTS = {ABOUT_THE_WORLD, STEPHEN_SUBSTANCE, NEVER_SEND}

# Judging is itself a model call. If that call ever routed cloudward it would
# re-enter the gate and recurse forever, so a thread-local flag makes the gate
# behave deterministically for anything sent while a judgment is in flight.
_local = threading.local()

JUDGE_TIMEOUT_S = 25.0
MAX_SPANS_PER_CALL = 24
MAX_SPAN_CHARS = 1200


# ── Kill switch (§5.6.4) ──────────────────────────────────────────────────────

def enabled() -> bool:
    """settings.judgment_gate.enabled — default OFF.

    Default-off is deliberate and is not timidity: the layer is strictly
    additive and strictly removable, so the safe default is the behaviour that
    has been running. Step 4 of the build turns it on after the probe battery
    passes on this machine.
    """
    if getattr(_local, "judging", False):
        return False
    if _PROBE_DISABLED:
        return False        # the battery caught a leak; the layer stays off
    try:
        from agent_friday.core import _load_settings
        cfg = (_load_settings() or {}).get("judgment_gate") or {}
        return bool(cfg.get("enabled", False))
    except Exception:
        return False


def _cfg(key: str, default):
    try:
        from agent_friday.core import _load_settings
        return ((_load_settings() or {}).get("judgment_gate") or {}).get(key, default)
    except Exception:
        return default


# ── §5.3 The never-list — what no verdict can override ────────────────────────

_HARD_ID_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"),
    "routing": re.compile(r"\b\d{9}\b"),
    "api_key": re.compile(r"\b(?:sk-ant-|sk-|AQ\.|AIza)[A-Za-z0-9_\-]{16,}\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}

# Raw vault documents never travel as documents, under any verdict (§5.3).
_VAULT_DOC_MARKERS = (
    "vault/legal/", "vault/finances/", "vault/family/",
    "vault\\legal\\", "vault\\finances\\", "vault\\family\\",
)

_NEVER_CACHE: dict[str, Any] = {"mtime": -1.0, "items": []}
# Tokens injected in-memory for the duration of the probe battery. A separate
# list rather than a poke at _NEVER_CACHE: the cache is mtime-driven, so a
# planted token was silently discarded by the next disk reload — which made the
# watchlist probe pass by never being tested. Found while building the battery,
# which is the battery working.
_PROBE_EXTRA_NEVER: list[str] = []


def never_send_tokens() -> list[str]:
    """Stephen's never-send watchlist — an extension of the existing privacy
    watchlist with a stronger meaning: not "scrub this" but "block any payload
    containing this" (§5.3).

    Read from ~/.friday/privacy_shield.json under the "never_send" key, beside
    the existing "watchlist". The dial is his; Friday builds it and never
    repoints it.
    """
    try:
        from agent_friday.core import FRIDAY_DIR
        path = Path(FRIDAY_DIR) / "privacy_shield.json"
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if mtime != _NEVER_CACHE["mtime"]:
            items: list[str] = []
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                raw = data.get("never_send") if isinstance(data, dict) else None
                if isinstance(raw, list):
                    items = [str(x).strip() for x in raw
                             if isinstance(x, (str, int, float)) and str(x).strip()]
            _NEVER_CACHE.update({"mtime": mtime, "items": items})
    except Exception:
        pass
    return list(_NEVER_CACHE["items"]) + _PROBE_EXTRA_NEVER


def _token_pattern(token: str) -> re.Pattern:
    esc = re.escape(token)
    left = r"\b" if token[:1].isalnum() else ""
    right = r"\b" if token[-1:].isalnum() else ""
    return re.compile(left + esc + right, re.IGNORECASE)


def never_send_hits(text: str) -> list[str]:
    """Which never-send tokens appear in `text`. Non-empty means BLOCK."""
    if not text:
        return []
    hits = [t for t in never_send_tokens() if _token_pattern(t).search(text)]
    low = text.lower()
    hits += [m for m in _VAULT_DOC_MARKERS if m in low]
    return hits


def hard_identifier_hits(text: str) -> list[str]:
    """Hard identifiers surviving in outgoing text. Non-empty means BLOCK.

    Card numbers are Luhn-checked so that order numbers and long digit runs do
    not manufacture a block — a gate that cries wolf gets switched off.
    """
    if not text:
        return []
    out = []
    for kind, pat in _HARD_ID_PATTERNS.items():
        for m in pat.finditer(text):
            if kind == "card":
                digits = re.sub(r"\D", "", m.group(0))
                if not (13 <= len(digits) <= 19 and _luhn(digits)):
                    continue
            out.append(kind)
            break
    return out


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# ── §5.5 step 4: verification of the scrub ────────────────────────────────────

class ScrubVerdict:
    """Result of the deterministic post-scrub check.

    `blocked` is the important field: it is what makes a WRONG JUDGMENT
    survivable. The model can be wrong about whether a span is Stephen's; it
    cannot make an unscrubbed identifier travel, because this runs after it
    and does not consult it.
    """

    def __init__(self, ok: bool, reason: str = "", hits: list[str] | None = None):
        self.ok = ok
        self.reason = reason
        self.hits = hits or []

    def __repr__(self):
        return f"<ScrubVerdict ok={self.ok} reason={self.reason!r}>"


def verify_outgoing(text: str, *, reclassify: bool = True) -> ScrubVerdict:
    """§5.5 step 4 — deterministic re-check of text that is about to leave.

      (a) hard identifiers or never-send tokens surviving → BLOCK
      (b) still classifying SENSITIVE after the scrub → treat as NEVER_SEND

    Runs regardless of what any model said, and never asks one.
    """
    if not text or not isinstance(text, str):
        return ScrubVerdict(True, "empty")

    never = never_send_hits(text)
    if never:
        return ScrubVerdict(False, "never-send material survived the scrub",
                            never)
    hard = hard_identifier_hits(text)
    if hard:
        return ScrubVerdict(False, "hard identifier survived the scrub", hard)

    if reclassify:
        try:
            from agent_friday.services.sensitivity_classifier import classify
            if classify(text, default=Tier.PUBLIC, egress=True) >= Tier.SENSITIVE:
                return ScrubVerdict(False,
                                    "still classifies SENSITIVE after scrubbing",
                                    ["reclassify"])
        except Exception as e:
            # Cannot verify → cannot send. Fail-closed, same contract as the
            # gate this composes with.
            return ScrubVerdict(False, f"verification failed to run ({e})",
                                ["verifier_error"])
    return ScrubVerdict(True, "ok")


# ── §5.4 Who judges ───────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """You classify text spans for a privacy gate. You are the appeals court: every span you see was already blocked by keyword rules, and your job is to say which ones were blocked by mistake.

The user is Stephen, a working journalist. His beat is courts, government, money and health policy. Keyword rules block anything mentioning "court", "legal", "medical", "income" — which blocks his ordinary professional reading along with his actual private affairs. You exist to tell those apart.

For each span, answer the single question: is this Stephen's OWN private material, or material about the world?

Verdicts:
- ABOUT_THE_WORLD — third-party material: published facts, news, other people's public actions, court records of other parties, quotes from public sources, general reference material, technical or product text.
- STEPHEN_SUBSTANCE — Stephen's own material where the substance matters but his identity can be separated from it: "my client in the housing case", "my doctor said", his own finances or family discussed in a way that survives having names and numbers removed.
- NEVER_SEND — Stephen's material where identity and substance cannot be separated: the span IS the private fact and removing identifiers would leave nothing meaningful, or it contains credentials, account numbers or medical/legal records of his own.

Rules:
- When uncertain, answer STEPHEN_SUBSTANCE. Never guess ABOUT_THE_WORLD to be helpful.
- First person about his own affairs ("my", "I owe", "our custody") is NEVER ABOUT_THE_WORLD.
- Third person about named public figures or organizations is normally ABOUT_THE_WORLD even when the topic is legal, medical or financial.
- Judge the span, not the topic. A sensitive TOPIC is not a private FACT.
- Give a one-sentence reason for every verdict. A judgment that cannot explain itself cannot be audited.

Reply with STRICT JSON only, no prose, no code fence:
{"verdicts":[{"i":0,"verdict":"ABOUT_THE_WORLD","reason":"..."}]}
One entry per span, in order, using the given index."""


# The sidekick, not the brain. Measured 2026-08-17 on a 12-case fixture
# (6 public / 6 first-person private), both seats warm:
#
#   gemma4:e2b   3 spans 1.2s | 6 spans 1.7s   12/12 correct, 0 false rescues
#   gemma4:12b   3 spans 49.6s | 6 spans 40.2s  12/12 correct, 0 false rescues
#
# Same verdicts, ~30x the latency. The design INFERRED 2-4s on the brain and
# the real figure was 40-60s, which would have put a minute onto every cloud
# call that today loses content. The e2b also survives every Arbiter lease
# (R10), so judgment stays available while the heavy seat is writing — the
# brain does not.
#
# Honest limit on that evidence: both seats scored 100%, so this measures "no
# difference detected on clear-cut cases", not "identical judgment". A harder
# fixture could separate them; the false-rescue count (the dangerous error) was
# zero for both, which is the number that matters most.
DEFAULT_JUDGE_MODEL = "gemma4:e2b"


def _judge_model() -> str | None:
    """The seat that judges. Settings override; sidekick by default."""
    explicit = _cfg("model", None)
    if explicit:
        return str(explicit)
    return DEFAULT_JUDGE_MODEL


def judge_spans(spans: list[str]) -> tuple[list[dict] | None, str]:
    """Ask the local seat for a verdict on each span.

    Returns (verdicts, detail). `verdicts is None` means judgment could not run
    — the caller must then apply the deterministic outcome unchanged (§5.4).
    Never raises.
    """
    if not spans:
        return [], "no spans"
    if getattr(_local, "judging", False):
        return None, "re-entrant judgment call"

    trimmed = [s[:MAX_SPAN_CHARS] for s in spans[:MAX_SPANS_PER_CALL]]
    payload = json.dumps(
        [{"i": i, "text": t} for i, t in enumerate(trimmed)], ensure_ascii=False)

    _local.judging = True
    t0 = time.time()
    try:
        # Direct, tool-free, JSON-constrained. NOT model_router._call_ollama:
        # that runs an agentic tool loop even with no tools passed, and a
        # 3-span batch came back after 48.9s as "[Agent hit max tool iterations
        # without completing.]" — see services/local_call.py. Single spans
        # escaped it, which is exactly why this failure hid until the first
        # batched call.
        from agent_friday.services import local_call
        model = _judge_model()
        raw = local_call.call(
            _JUDGE_SYSTEM,
            f"Classify these {len(trimmed)} spans:\n{payload}",
            model, json_mode=True, max_tokens=2048,
            timeout=int(_cfg("timeout_s", JUDGE_TIMEOUT_S * 4)))
    except Exception as e:
        _log.warning("judgment call failed: %s", e)
        return None, f"{type(e).__name__}: {e}"
    finally:
        _local.judging = False

    took = time.time() - t0
    verdicts = _parse_verdicts(raw, len(trimmed))
    if verdicts is None:
        return None, f"malformed verdict JSON after {took:.1f}s"
    # Spans beyond the per-call cap were never judged; they keep the
    # deterministic outcome rather than inheriting a neighbour's verdict.
    while len(verdicts) < len(spans):
        verdicts.append({"verdict": NEVER_SEND,
                         "reason": "not judged (batch cap) — deterministic outcome kept"})
    return verdicts, f"judged {len(trimmed)} spans in {took:.1f}s"


def _parse_verdicts(raw: str, n: int) -> list[dict] | None:
    """Strict-ish JSON extraction. Anything we cannot read cleanly is a
    malformed verdict, which means judgment did not run."""
    if not raw or not isinstance(raw, str):
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?", "", txt)
        txt = re.sub(r"\n?```\s*$", "", txt).strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(txt[start:end + 1])
    except Exception:
        return None
    items = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    out: list[dict] = [None] * n  # type: ignore[list-item]
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("i"))
        except (TypeError, ValueError):
            continue
        v = str(it.get("verdict", "")).strip().upper()
        if not (0 <= i < n) or v not in _VALID_VERDICTS:
            continue
        out[i] = {"verdict": v,
                  "reason": str(it.get("reason", ""))[:300] or "no reason given"}
    # A span the model skipped or mislabelled keeps the deterministic outcome.
    for i in range(n):
        if out[i] is None:
            out[i] = {"verdict": NEVER_SEND,
                      "reason": "no usable verdict returned — deterministic outcome kept"}
    return out


# ── §5.6.2 The overturn ledger ────────────────────────────────────────────────

def _ledger_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "vault" / "judgment-overturns.jsonl"


_LEDGER_LOCK = threading.Lock()


def record_overturn(span: str, verdict: str, reason: str, provider: str,
                    *, field: str = "", origin: str = "") -> None:
    """Every time judgment lets a span through that the deterministic layers
    would have withheld, that is an overturn. Overturns are the ENTIRE risk
    surface of this design, so they are first-class data, not log noise.

    The span itself is never written — only its hash, length and shape. A
    privacy ledger that stores the private text would be self-defeating.
    """
    import hashlib
    entry = {
        "ts": time.time(),
        "span_hash": hashlib.blake2b(span.encode("utf-8", "replace"),
                                     digest_size=8).hexdigest(),
        "span_chars": len(span),
        "verdict": verdict,
        "reason": reason,
        "provider": provider,
        "field": field,
        "origin": origin,
    }
    try:
        p = _ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LEDGER_LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
    _log.info("OVERTURN provider=%s verdict=%s reason=%s", provider, verdict,
              reason[:120])


def read_overturns(limit: int = 200) -> list[dict]:
    try:
        lines = _ledger_path().read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def overturn_digest(days: int = 7) -> dict:
    """§5.6.3 — the five sentences Stephen reads each week."""
    cutoff = time.time() - days * 86400
    rows = [r for r in read_overturns(5000) if r.get("ts", 0) >= cutoff]
    by_provider: dict[str, int] = {}
    for r in rows:
        by_provider[r.get("provider", "?")] = by_provider.get(r.get("provider", "?"), 0) + 1
    return {
        "days": days,
        "overturns": len(rows),
        "by_provider": by_provider,
        "sample_reasons": [r.get("reason", "") for r in rows[-5:]],
    }


# ── §5.6.1 The probe battery ──────────────────────────────────────────────────
#
# A classifier that makes judgments is a model that can be wrong, and a
# scrubber that silently succeeds is the failure mode this codebase
# specialises in. So the layer proves itself at every boot against planted
# material, and ANY private-fixture failure disables it — loudly. Losing the
# capability honestly beats keeping it dishonestly.
#
# Every value below is synthetic. The SSN is the canonical invalid-by-design
# 000-… form, the card is a published test number, the names are invented.

_PROBE_WATCHLIST_TOKEN = "Zyrellian Hollowbrook"  # pragma: allowlist secret — invented name, no real person

_PRIVATE_PROBES: list[tuple[str, str]] = [
    ("ssn", "My social security number is 123-45-6789 and I need help filing."),  # pragma: allowlist secret
    ("card", "Put it on my card 4111 1111 1111 1111, expires next year."),  # pragma: allowlist secret
    ("routing", "Wire it to routing number 021000021 at my bank."),
    ("api_key", "Use my key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA to test."),  # pragma: allowlist secret
    ("first_person_legal",
     "My custody hearing is on the 14th and my lawyer says my ex will contest "
     "the settlement figure I proposed for our daughter."),
    ("first_person_medical",
     "My A1C came back at 8.2 and my doctor wants to change my prescription "
     "before my next appointment."),
    ("vault_doc", "Here is the file at vault/finances/2025-tax-return.pdf"),
    ("watchlist", f"I discussed this privately with {_PROBE_WATCHLIST_TOKEN} last week."),
]

# Known-public fixtures that MUST survive. These are the value the layer
# exists to recover; if they die, the layer is pointless even when it is safe.
_PUBLIC_PROBES: list[tuple[str, str]] = [
    ("headline_legal",
     "Trump asks US Supreme Court to allow ballroom construction work to continue"),
    ("headline_finance",
     "Point2 Technology raised a $136M Series B led by an undisclosed investor."),
    ("third_party_medical",
     "The FDA approved a new diabetes medication after a trial of 4,000 patients "
     "showed improved outcomes."),
    ("neutral", "What is on my schedule for today?"),
]

_PROBE_RESULT: dict | None = None
_PROBE_DISABLED = False


def probe_disabled() -> bool:
    return _PROBE_DISABLED


def probe_battery(*, log_path: Path | None = None) -> dict:
    """Run the battery. Private fixtures must not survive; public should.

    Called from egress_gate.startup_self_test at boot and after any change to
    the judgment prompt. Returns a dict; also sets the module kill flag on a
    private-fixture failure.
    """
    global _PROBE_RESULT, _PROBE_DISABLED
    import os as _os
    from agent_friday.services import egress_gate as _eg

    log_path = log_path or Path(_os.devnull)
    # The watchlist probe only means something if the token is actually on a
    # list. Install it for the duration of the battery, in memory only.
    _PROBE_EXTRA_NEVER.append(_PROBE_WATCHLIST_TOKEN)

    leaked: list[dict] = []
    checked = 0
    try:
        for name, probe in _PRIVATE_PROBES:
            checked += 1
            try:
                sealed = _eg.seal_outbound(
                    {"messages": [{"role": "user", "content": probe}]},
                    "probe-cloud", log_path)
                msgs = sealed.get("messages") or [{}]
                out = str(msgs[0].get("content", "")) if isinstance(msgs[0], dict) else ""
            except Exception as e:
                # A gate that RAISES has blocked the send — that is a pass,
                # not a failure. Fail-closed is the contract.
                out = ""
                _log.info("probe %s blocked by exception (pass): %s", name, e)

            survived = _survivors(name, probe, out)
            if survived:
                leaked.append({"probe": name, "survived": survived,
                               "output_sample": out[:160]})

        public_lost = []
        for name, probe in _PUBLIC_PROBES:
            try:
                sealed = _eg.seal_outbound(
                    {"messages": [{"role": "user", "content": probe}]},
                    "probe-cloud", log_path)
                msgs = sealed.get("messages") or [{}]
                out = str(msgs[0].get("content", "")) if isinstance(msgs[0], dict) else ""
            except Exception:
                out = ""
            if probe.strip() not in out:
                public_lost.append(name)
    finally:
        try:
            _PROBE_EXTRA_NEVER.remove(_PROBE_WATCHLIST_TOKEN)
        except ValueError:
            pass

    ok = not leaked
    _PROBE_RESULT = {
        "ok": ok,
        "private_probes": checked,
        "leaked": leaked,
        "public_probes": len(_PUBLIC_PROBES),
        "public_lost": public_lost,
        "judgment_enabled_at_probe_time": enabled(),
        "ts": time.time(),
    }

    if not ok:
        _PROBE_DISABLED = True
        _log.error(
            "JUDGMENT GATE DISABLED — %d planted private probe(s) survived "
            "sealing: %s. The deterministic gate is now governing alone.",
            len(leaked), ", ".join(p["probe"] for p in leaked))
        _notify_probe_failure(leaked)
    else:
        _PROBE_DISABLED = False
        if public_lost and _PROBE_RESULT["judgment_enabled_at_probe_time"]:
            # Not a safety failure — a capability failure. Loud, not fatal.
            _log.warning(
                "judgment probe battery: %d public fixture(s) were withheld "
                "(%s) — the layer is safe but is not recovering the value it "
                "exists for", len(public_lost), ", ".join(public_lost))
        elif public_lost:
            # Judgment is off, so these SHOULD be withheld — that is the
            # over-blocking the layer exists to fix, measured rather than
            # assumed. Recorded as the baseline, not reported as a fault.
            _log.info(
                "judgment probe battery: %d public fixture(s) withheld with "
                "judgment OFF (%s) — this is the over-blocking baseline",
                len(public_lost), ", ".join(public_lost))
    return _PROBE_RESULT


def _survivors(name: str, probe: str, out: str) -> list[str]:
    """What identifying material from `probe` is still present in `out`.

    Checks the actual planted tokens, not just "is the string different" — a
    scrub that mangles whitespace while leaving the SSN intact must fail.
    """
    checks = {
        "ssn": ["123-45-6789"],  # pragma: allowlist secret
        "card": ["4111 1111 1111 1111", "4111111111111111"],  # pragma: allowlist secret
        "routing": ["021000021"],
        "api_key": ["sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA"],  # pragma: allowlist secret
        "vault_doc": ["vault/finances/2025-tax-return.pdf"],
        "watchlist": [_PROBE_WATCHLIST_TOKEN],
        "first_person_legal": ["custody hearing", "contest the settlement"],
        "first_person_medical": ["A1C came back at 8.2", "change my prescription"],
    }
    return [tok for tok in checks.get(name, [probe]) if tok in out]


def _notify_probe_failure(leaked: list[dict]) -> None:
    try:
        # Package root, not services/ — see _report_task_completion.
        import agent_friday.notifications_engine as notifications_engine
        notifications_engine.push(
            title="Privacy judgment layer disabled",
            body=("Planted private test material survived the egress gate "
                  f"({', '.join(p['probe'] for p in leaked)}). The judgment "
                  "layer is off and the original keyword gate is governing "
                  "alone. Cloud calls still work; they will over-block again."),
            proactive_chat=True,
            chat_message=(
                "I disabled my own privacy judgment layer just now. At startup I "
                "test it with planted private material that must never survive — "
                f"and {len(leaked)} of those tests leaked ({', '.join(p['probe'] for p in leaked)}). "
                "I've fallen back to the older keyword-based rules, which are "
                "clumsier but proven. Nothing of yours went anywhere; the test "
                "material is synthetic. This needs a look before I turn it back on."),
        )
    except Exception:
        pass


def probe_result() -> dict | None:
    return _PROBE_RESULT


# ── Dry run — what WOULD happen to this text (§3.2 / RS2) ─────────────────────

def dry_run(text: str) -> dict:
    """Compute the protection plan for `text` without sending anything.

    Research needs to tell Stephen, BEFORE the work starts, whether Claude will
    see his question and what would be removed from it (Q5). That means asking
    the gate a hypothetical, and a hypothetical must not be answerable by
    "send it and see".

    Returns:
      cloud_allowed  bool   — False when never-send material is load-bearing
      question_sent  str    — the protected text Claude would actually receive
      scrub_tags     [kind] — KINDS ONLY. Never values; this dict gets shown.
      scrub_count    int
      reason         str

    Note the asymmetry with the live gate: here a NEVER_SEND verdict or a
    surviving identifier means `cloud_allowed=False` for the whole commission,
    rather than redacting one span. A research question with a hole in it is
    not a research question.
    """
    from agent_friday.core import _scrub_pii
    from agent_friday.services.sensitivity_classifier import classify

    raw = (text or "").strip()
    if not raw:
        return {"cloud_allowed": True, "question_sent": "", "scrub_tags": [],
                "scrub_count": 0, "reason": "nothing to protect"}

    never = never_send_hits(raw)
    if never:
        return {"cloud_allowed": False, "question_sent": None,
                "scrub_tags": [], "scrub_count": 0,
                "reason": (f"The question contains material on your never-send "
                           f"list ({len(never)} match"
                           f"{'es' if len(never) != 1 else ''}).")}

    scrubbed, lookup = _scrub_pii(raw)
    kinds = sorted({t.split(":")[1] for t in lookup if t.count(":") >= 2})

    check = verify_outgoing(scrubbed)
    if not check.ok:
        return {"cloud_allowed": False, "question_sent": None,
                "scrub_tags": kinds, "scrub_count": len(lookup),
                "reason": (f"After scrubbing, the question still cannot be "
                           f"protected: {check.reason}.")}

    tier = classify(scrubbed, default=Tier.PUBLIC, egress=True)
    if tier <= Tier.PUBLIC:
        return {"cloud_allowed": True, "question_sent": scrubbed,
                "scrub_tags": kinds, "scrub_count": len(lookup),
                "reason": "Nothing in the question needs to stay here."}

    # Keywords object. This is exactly the appeals-court case, so ask.
    if not enabled():
        # Judgment off: the deterministic verdict stands, and a question the
        # keyword rules dislike does not go to Claude. Say WHY, so the answer
        # reads as a setting rather than a mystery.
        return {"cloud_allowed": False, "question_sent": None,
                "scrub_tags": kinds, "scrub_count": len(lookup),
                "reason": ("The keyword rules flag this question and the "
                           "judgment layer is switched off, so it stays here.")}

    verdicts, detail = judge_spans([scrubbed])
    if verdicts is None:
        return {"cloud_allowed": False, "question_sent": None,
                "scrub_tags": kinds, "scrub_count": len(lookup),
                "reason": f"I could not get a judgment on it ({detail}), so it stays here."}
    v = verdicts[0]
    if v.get("verdict") == NEVER_SEND:
        return {"cloud_allowed": False, "question_sent": None,
                "scrub_tags": kinds, "scrub_count": len(lookup),
                "reason": v.get("reason", "judged private")}
    return {"cloud_allowed": True, "question_sent": scrubbed,
            "scrub_tags": kinds, "scrub_count": len(lookup),
            "reason": v.get("reason", "")}


# ── Is this layer actually running? (visibility) ──────────────────────────────

def state() -> dict:
    """Everything needed to answer "is my privacy judgment on?" without asking.

    A privacy layer that exists and is not running is the "green but doing
    nothing" pattern, so its state is reportable rather than inferable.
    """
    setting = False
    try:
        from agent_friday.core import _load_settings
        setting = bool(((_load_settings() or {}).get("judgment_gate") or {})
                       .get("enabled", False))
    except Exception:
        pass
    probe = probe_result()
    if _PROBE_DISABLED:
        summary = ("OFF — disabled automatically because planted private test "
                   "material survived the startup probes.")
    elif not setting:
        summary = ("OFF — not enabled. The original keyword rules are "
                   "governing alone, which over-blocks public material on "
                   "legal, medical and financial topics.")
    else:
        summary = "ON — judging spans the keyword rules would otherwise withhold."
    return {
        "enabled_setting": setting,
        "disabled_by_probe": _PROBE_DISABLED,
        "effective": enabled(),
        "summary": summary,
        "model": _judge_model(),
        "never_send_tokens": len(never_send_tokens()),
        "probe": {"ok": (probe or {}).get("ok"),
                  "private_probes": (probe or {}).get("private_probes"),
                  "leaked": [p["probe"] for p in (probe or {}).get("leaked", [])],
                  "public_lost": (probe or {}).get("public_lost", []),
                  "ran": probe is not None},
        "overturns_7d": overturn_digest(7),
    }
