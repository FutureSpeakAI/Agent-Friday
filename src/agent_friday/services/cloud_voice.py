"""Cloud voice providers - ElevenLabs and Inworld as Tier-3 siblings.

Implements ``docs/design/active/cloud-voice-providers.md`` for the **TTS**
modality only. STT stays local: section 3.3 refuses cloud-STT-with-local-TTS
outright and offers "Local listening, cloud voice" as the one permitted
asymmetry, so this module synthesizes and never transcribes.

THE THREE CONSTRAINTS, which nothing in this file may weaken
------------------------------------------------------------
C1  Both local and cloud paths are always available and the user chooses.
    Nothing here is ever a default. ``resolve_provider()`` returns ``None``
    unless the user explicitly named a cloud provider.
C2  No silent fallback in either direction. This module raises
    :class:`CloudVoiceUnavailable` - which carries a *reason* and an *offer* -
    rather than substituting a different provider. Callers surface the offer;
    they do not act on it.
C3  The offline path is never load-bearing on a network call. Every network
    import in this file is lazy and inside a call site, availability checks do
    no I/O, and no module-level work touches a socket. Section 6.4 step 6 is the
    regression this discipline exists to prevent.

THE EGRESS GATE IS A PREREQUISITE, NOT A FOLLOW-UP
--------------------------------------------------
Section 8.3: routing Friday's *conversational* output through cloud synthesis
makes every spoken sentence ungated text leaving the machine. :func:`synthesize`
therefore routes synthesis input through ``egress_gate`` **before** any byte
reaches a vendor, and treats a withheld result as a hard refusal - never as a
reason to send the redacted remainder quietly. It uses ``_gate_text`` rather
than the public ``gate_text`` deliberately: the public wrapper short-circuits on
``_is_cloud(provider)``, and a provider missing from that list would sail
straight through. Fail closed, mirroring ``elevenlabs_tools.py:162``.

NO KEYS IN THIS FILE. Keys resolve through the existing mechanism
(:func:`_api_key`): module constant -> env -> ``settings.json``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from agent_friday.user_errors import UserFacingError

log = logging.getLogger(__name__)

_TIMEOUT = 120
#: Hard ceiling on a single synthesis request. Both vendors accept far more;
#: this bounds a single metered call so a runaway string cannot quietly become
#: a large bill. Callers that need more should chunk deliberately.
MAX_CHARS = 5000


# -- Model allowlists --------------------------------------------------------
#
# Q2 (settled): ship only models documented as generally available.
# ElevenLabs' Beta Services Addendum removes Beta Services from the definition
# of "Services" and forbids commercial or production use of them, and NO
# first-party ElevenLabs page labels which models are Beta. Verified directly
# 2026-09-09: elevenlabs.io/docs/overview/models lists every model id with
# languages and latency but carries no GA/Beta column, and elevenlabs.io
# /terms-of-use references a separate Beta Services Addendum without
# enumerating its members.
#
# The rule applied, per the decision: where GA cannot be DETERMINED, exclude.
# A model is admitted here only on POSITIVE first-party commercial-commitment
# evidence - never on documentation tone. The three signals accepted:
#   (a) a per-character API price published on elevenlabs.io/pricing/api;
#   (b) a dedicated per-plan concurrency column in the models page's paid-plan
#       table (ElevenLabs does not publish paid-plan SLAs for Beta Services);
#   (c) designation as the REPLACEMENT for a formally deprecated model - a
#       vendor does not migrate deprecations onto something it may withdraw.
#
# Admitted, with the evidence each rests on:
#   eleven_flash_v2_5      (a) + (b) "Flash" concurrency column + (c) named
#                          replacement for deprecated eleven_turbo_v2_5, and
#                          documented as powering the Agents Platform.
#   eleven_multilingual_v2 (a) + (b) "Multilingual v2" concurrency column.
#
# Excluded, and why - each is a model this spec's section 5.3 originally named:
#   eleven_v3, eleven_v3_conversational - no dedicated concurrency column, no
#       deprecation-replacement role, no independent GA marker. Section 5.3
#       listed eleven_v3_conversational as the "Best quality" option; under Q2
#       it cannot ship until ElevenLabs confirms its status in writing. This is
#       the decision's "exclude rather than risk it" applied to a model the
#       spec wanted.
#   eleven_turbo_v2_5, eleven_turbo_v2, scribe_v1 - formally deprecated.
#   eleven_multilingual_v3 - NOT A REAL MODEL ID. It does not appear in
#       ElevenLabs' model table (verified 2026-09-09); not shippable.
#   scribe_v2, scribe_v2_realtime - STT, refused by section 3.3, out of scope.
#
# Consequence, stated plainly: ElevenLabs ships with ONE quality tier, not two.
# Q2's answer is still required from ElevenLabs directly before eleven_v3_* can
# be offered.
ELEVENLABS_GA_MODELS = {
    "eleven_flash_v2_5": {
        "label": "Responsive",
        "detail": "Flash v2.5 - ~75 ms vendor-claimed, unverified",
        "usd_per_1k_chars": 0.05,
        "ga_evidence": ("published API price; dedicated Flash concurrency "
                        "column per paid plan; named replacement for the "
                        "deprecated eleven_turbo_v2_5"),
    },
    "eleven_multilingual_v2": {
        "label": "Best quality",
        "detail": "Multilingual v2 - higher latency, most stable long-form",
        "usd_per_1k_chars": 0.10,
        "ga_evidence": ("published API price; dedicated Multilingual v2 "
                        "concurrency column per paid plan"),
    },
}

# Q8, settled: APPLY THE SAME STANDARD, and accept the consequence.
#
# The Q2 rule is "where GA cannot be determined, exclude." Applying it to
# ElevenLabs and not to Inworld would mean the rule is a preference, not a
# standard - one standard for both vendors, or neither standard means anything.
#
# Inworld's GA status CANNOT be determined: Inworld's product docs present
# TTS-2 as production, while Inworld's OWN comparison article and Artificial
# Analysis both label it "Research Preview". A vendor contradicting itself is
# not weaker evidence than silence, it is worse - there is a first-party
# statement on each side. No amount of further reading settles it.
#
# Therefore INWORLD SHIPS NOTHING. It is registered, visible, and unselectable,
# with the reason stated. Better one honest provider than two where one carries
# an unresolved commercial-use question papered over because we wanted the
# feature.
#
# The integration code stays. Enabling Inworld later is a CONFIGURATION CHANGE,
# not a rebuild: flip ``GA_ESTABLISHED["inworld"]`` to True. Everything else -
# the HTTP client, the egress gate, the metering rows, the plan-tier handling,
# the disclosure surface, the non-durability enforcement - is built and tested.
#
# WHAT WOULD CHANGE THE ANSWER. Any one of these, in writing from Inworld:
#   1. A first-party page or support response stating TTS-2 is generally
#      available and not a research preview, dated and attributable.
#   2. Removal of the "Research Preview" label from Inworld's own comparison
#      material, which is currently the strongest evidence against.
#   3. An explicit statement that research-preview status does not restrict
#      commercial or production use.
# NOTE these resolve Q8 ONLY. Q3 (the section 4 / section 13 Outputs
# contradiction) is a SEPARATE blocker: resolving Q8 would let Inworld be
# selected, and would NOT make its audio durable. Both must be answered before
# Inworld audio can be archived or shipped.
GA_ESTABLISHED = {
    # ElevenLabs: established for the two models in ELEVENLABS_GA_MODELS, on
    # the positive commercial-commitment evidence recorded against each.
    "elevenlabs": True,
    # Inworld: NOT established. See the block above for what would change it.
    "inworld": False,
}

#: Human reason shown wherever a provider is unselectable for this cause.
GA_UNESTABLISHED_REASON = {
    "inworld": ("Inworld's general-availability status is unresolved - its own "
                "documentation and its own comparison material disagree on "
                "whether TTS-2 is production or a research preview. Friday "
                "does not ship providers whose commercial-use terms it cannot "
                "establish. See Q8 in the cloud voice spec."),
}


def ga_established(provider: str) -> bool:
    """May this provider be selected at all? Q8's enforcement point.

    Separate from :func:`key_problem` on purpose: a user with a perfectly valid
    Inworld key still cannot select Inworld, and the reason they are given must
    be the real one rather than a misleading key error.
    """
    return bool(GA_ESTABLISHED.get(provider, False))

INWORLD_MODELS = {
    "inworld-tts-2-flash": {
        "label": "Responsive",
        "detail": "TTS-2 Flash - 20 ms p90 vendor-claimed, unverified",
        "usd_per_1k_chars": 0.015,
        "ga_evidence": "UNRESOLVED (Q8) - vendor docs and third parties disagree",
    },
    "inworld-tts-2": {
        "label": "Best quality",
        "detail": "TTS-2 - 100 ms p90 vendor-claimed, unverified",
        "usd_per_1k_chars": 0.025,
        "ga_evidence": "UNRESOLVED (Q8) - vendor docs and third parties disagree",
    },
}

#: Q4: Inworld's rate is tier-dependent (On-Demand -> Growth -> Enterprise)
#: while ElevenLabs' is flat. A single PRICING row is therefore only correct for
#: On-Demand and would silently OVER-report for anyone else. Resolved by making
#: plan tier a configured input (the spec's second option): on any tier other
#: than on-demand the call is metered under an id in
#: ``cost_meter.UNPRICED_MODELS``, which stores SQL NULL and renders as "not
#: priced" - never as a number that looks like a fact.
INWORLD_PLAN_TIERS = ("on_demand", "growth", "enterprise")


PROVIDERS: dict[str, dict[str, Any]] = {
    "elevenlabs": {
        "label": "ElevenLabs (cloud)",
        "base_url": "https://api.elevenlabs.io/v1",
        "env_key": "ELEVENLABS_API_KEY",
        "settings_key": "elevenlabs_api_key",
        "core_attr": "ELEVENLABS_API_KEY",
        "models": ELEVENLABS_GA_MODELS,
        "default_model": "eleven_flash_v2_5",
        "default_voice": "21m00Tcm4TlvDq8ikWAM",  # "Rachel", stock
        # Section 2.5. Stated, not linked - this is what the user agrees to.
        "retention": ("ElevenLabs may use your content to train its models by "
                      "default on consumer plans; an opt-out toggle exists in "
                      "your ElevenLabs account settings."),
        # Q1 (settled): NO cloned Friday voice. ElevenLabs clones
        # cannot be exported or downloaded and are reachable only through their
        # API with your key, which would make Friday's IDENTITY a permanent
        # vendor dependency - in direct conflict with the offline-voice
        # commitment. Cloud voices are alternates a user may select; they are
        # never Friday's identity. No cloning flow exists in this module and
        # none should be added without a decision record superseding this.
        "cloning": False,
        "durable_output": True,
    },
    "inworld": {
        "label": "Inworld (cloud)",
        "base_url": "https://api.inworld.ai/tts/v1",
        "env_key": "INWORLD_API_KEY",
        "settings_key": "inworld_api_key",
        "core_attr": "INWORLD_API_KEY",
        "models": INWORLD_MODELS,
        "default_model": "inworld-tts-2-flash",
        "default_voice": "Ashley",
        "retention": ("Inworld's retention and training posture for API audio "
                      "is not stated per-tier in its public terms."),
        "cloning": False,
        # Q3 (settled): Inworld's Terms section 4 assigns Outputs to
        # the user while section 13 requires deleting all Outputs on
        # termination. Those cannot both hold for a product that ships
        # generated audio. TREAT INWORLD AUDIO AS NON-DURABLE: fine for
        # interactive playback, never written into archives, saved artifacts,
        # or anything shipped. This is NOT resolved in code - it is flagged as
        # requiring legal review before commercial release.
        # :func:`audio_is_durable` is the enforcement point.
        "durable_output": False,
    },
}


class CloudVoiceError(UserFacingError, RuntimeError):
    """A cloud voice call failed. Carries a classified reason code.

    The message is written for the user. Where a provider's or library's own
    text explains the failure it rides in `detail`, which is what str()
    returns (the model and the log see it; a route shows only the message)."""

    def __init__(self, message: str, *, code: str = "cloud_voice_failed",
                 detail: str | None = None):
        super().__init__(message, detail=detail)
        self.code = code


class CloudVoiceUnavailable(CloudVoiceError):
    """Cloud voice cannot serve, and another provider must not be substituted.

    Sections 6.1/6.3. This is the type that makes "surfaces and offers, never
    substitutes" mechanical rather than aspirational: it carries the human
    ``reason`` and an explicit ``offer`` describing the action available to the
    user. A caller that catches this and quietly calls another synthesizer has
    violated C2, and the accompanying tests assert that no caller does.
    """

    def __init__(self, message: str, *, code: str, offer: str | None = None,
                 requested: str = "", detail: str | None = None):
        super().__init__(message, code=code, detail=detail)
        self.offer = offer
        self.requested = requested


@dataclass
class CloudVoiceResult:
    """What actually happened. The section 4.4 indicator is written FROM this.

    Never from configuration and never from intent - ``provider`` here is the
    provider that served the bytes, which is C2's final clause made a data
    dependency rather than a convention.
    """
    audio: bytes
    mime: str
    provider: str
    model: str
    chars: int
    duration_ms: int
    cost_usd: float | None
    priced: bool
    durable: bool
    notes: list = field(default_factory=list)


# -- Keys --------------------------------------------------------------------

def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _stored_key(provider: str) -> str:
    """The key saved through Settings or the setup checklist: the credential
    store, under the provider's own name (``POST /api/providers/<name>/key``)."""
    try:
        from agent_friday.services import credential_store as cs
        return (cs.get_provider_key(provider) or "").strip()
    except Exception:
        return ""


def _migrate_plaintext_key(provider: str, key: str) -> None:
    """Move a key found in plaintext settings.json into the credential store.

    Older builds read the key from settings.json and nothing wrote it anywhere
    safer. Found there, it is encrypted into the store and the plaintext copy
    is blanked, so the next read takes the store path. Best-effort: a failed
    migration leaves the key where it was and still usable.
    """
    spec = PROVIDERS.get(provider) or {}
    try:
        from agent_friday.services import credential_store as cs
        cs.set_provider_key(provider, key)
        if (cs.get_provider_key(provider) or "") != key:
            return
        from agent_friday.core import _save_settings
        _save_settings({spec.get("settings_key"): ""})
    except Exception:
        pass


def _api_key(provider: str) -> str:
    """Module constant -> live env -> credential store -> settings.json.

    The credential store is where a key typed into Friday is kept. The
    plaintext settings.json field is read last, as a fallback for installs
    that predate the store, and a key found there is migrated into the store
    on the way out. ``elevenlabs_tools._api_key()`` delegates here so there is
    one way keys resolve, not two.
    """
    spec = PROVIDERS.get(provider) or {}
    key = ""
    try:
        from agent_friday import core
        key = getattr(core, spec.get("core_attr") or "", "") or ""
    except Exception:
        key = ""
    if not key:
        import os
        key = os.environ.get(spec.get("env_key") or "", "") or ""
    if not key:
        key = _stored_key(provider)
    if not key:
        key = str(_settings().get(spec.get("settings_key") or "", "") or "").strip()
        if key:
            _migrate_plaintext_key(provider, key)
    return str(key).strip()


def key_problem(provider: str, key: str) -> str | None:
    """A human reason the key looks wrong, or None.

    Section 5.4 asks that the ElevenLabs key-ID trap be retained and surfaced.
    It is: the short hex string beside a key in the ElevenLabs dashboard is the
    key *id*, and the API rejects it with ``api_key_id_used_as_api_key``. That
    confusion cost elevenlabs-voice.md section 1 an entire unverifiable section.

    Q5 - whether Inworld has an analogous ID/secret trap - is UNVERIFIED, so no
    Inworld-specific claim is made here rather than inventing one.
    """
    if not key:
        return "needs an API key"
    if provider == "elevenlabs" and not key.startswith("sk_"):
        return ("this looks like an ElevenLabs key *id*, not a key - real keys "
                "begin with 'sk_'. The short hex string beside a key in the "
                "dashboard is its id and the API will reject it.")
    return None


# -- Selection (C1: explicit, persisted, never inferred) ---------------------

def resolve_provider(settings: dict | None = None) -> str | None:
    """The cloud provider the user explicitly selected, or None.

    Returns None for every value that is not a registered cloud provider -
    including ``"auto"``. Q5 of the local-voice spec is settled: ``auto`` means
    **local only**, labelled "Automatic (local only)" in the interface, and
    never reaches cloud. A mode that can silently send a user's voice to a third
    party is the exact failure the transparency commitment exists to prevent, so
    ``auto`` resolving to a cloud provider here would be that failure with a
    friendly name.
    """
    s = settings if settings is not None else _settings()
    if _local_only(s):
        return None
    pref = str(s.get("voice_engine") or "local").strip().lower()
    if pref.startswith("cloud:"):
        name = pref.split(":", 1)[1].strip()
        if name not in PROVIDERS:
            return None
        return name if ga_established(name) else None
    if pref not in PROVIDERS:
        return None
    # Q8: a provider whose GA status Friday cannot establish is not selectable,
    # even if the user persisted it (e.g. from a build where it was enabled).
    return pref if ga_established(pref) else None


def _local_only(settings: dict) -> bool:
    """Airplane / Sovereign mode. An absolute override - section 6.3, D-AC3.

    Cloud voice providers join the unreachable set. No new bypass.
    """
    try:
        return str(((settings.get("model_routing") or {}).get("mode")) or ""
                   ).strip().lower() == "local_only"
    except Exception:
        return False


def selected_model(provider: str, settings: dict | None = None) -> str:
    s = settings if settings is not None else _settings()
    spec = PROVIDERS[provider]
    want = str(s.get("%s_model" % provider) or "").strip()
    if want in spec["models"]:
        return want
    return spec["default_model"]


def selected_voice(provider: str, settings: dict | None = None) -> str:
    """Per-provider voice id, so switching providers and back does not lose it."""
    s = settings if settings is not None else _settings()
    return (str(s.get("%s_voice_id" % provider) or "").strip()
            or PROVIDERS[provider]["default_voice"])


def available_providers(settings: dict | None = None) -> list:
    """Every cloud provider, with whether it is selectable and why not (4.2).

    A provider without a key is VISIBLE but unselectable, with the reason - the
    ``nvidia-nemo`` pattern. It is not hidden (that would make the capability
    undiscoverable) and it is not selectable (that would be the dead control
    docs/decisions/2026-09-04-five-dead-settings.md names).

    C3: this function performs NO network I/O. Availability is a key check and a
    settings read. A reachability probe here would put a DNS timeout on a path
    the local voice can block on, which is section 6.4 step 6's named
    regression.
    """
    s = settings if settings is not None else _settings()
    locked = _local_only(s)
    out = []
    for name, spec in PROVIDERS.items():
        key = _api_key(name)
        reason = key_problem(name, key)
        if not ga_established(name):
            # Precedence matters: a user with a perfectly valid key must be
            # told the truth (GA unresolved), not a misleading "needs a key".
            reason = GA_UNESTABLISHED_REASON.get(
                name, "this provider is not available in this build")
        if locked:
            reason = ("local-only mode is on - cloud voice is disabled and "
                      "cannot be selected")
        out.append({
            "name": name,
            "label": spec["label"],
            "selectable": (reason is None),
            "reason": reason,
            "models": [
                {"id": mid, "label": m["label"], "detail": m["detail"],
                 "usd_per_1k_chars": m["usd_per_1k_chars"],
                 "ga_evidence": m["ga_evidence"]}
                for mid, m in spec["models"].items()
            ],
            "durable_output": spec["durable_output"],
            "cloning_supported": spec["cloning"],
        })
    return out


def audio_is_durable(provider: str) -> bool:
    """May audio from this provider be written somewhere permanent?

    Q3, enforced. Inworld returns False: its audio is for interactive playback
    only and must not be archived, saved as an artifact, or shipped. Callers
    that persist audio MUST consult this before writing. See
    :func:`legal_review_required` for what is not resolved in code.
    """
    return bool((PROVIDERS.get(provider) or {}).get("durable_output", False))


def legal_review_required(provider: str) -> str | None:
    """Unresolved legal questions blocking COMMERCIAL release, not development.

    Deliberately returns prose rather than raising: these are questions for a
    lawyer, and this module has no business pretending it resolved them by
    picking a code path. Surfaced at the disclosure point.
    """
    if provider == "inworld":
        return ("Inworld Terms section 4 assigns Outputs to you while section "
                "13 requires deleting all Outputs on termination; these cannot "
                "both hold for shipped audio (Q3). Separately, whether TTS-2 is "
                "GA or a research preview is unresolved (Q8). Friday therefore "
                "treats Inworld audio as NON-DURABLE - interactive playback "
                "only. Written clarification from Inworld is required before "
                "commercial release.")
    if provider == "elevenlabs":
        return ("ElevenLabs publishes no first-party GA/Beta model list and its "
                "Beta Services Addendum forbids commercial and production use "
                "of Beta Services (Q2). Friday ships only the two models with "
                "positive commercial-commitment evidence; eleven_v3 and "
                "eleven_v3_conversational are withheld pending written "
                "confirmation from ElevenLabs.")
    return None


# -- Disclosure (section 4.3) ------------------------------------------------

def disclosure(provider: str, settings: dict | None = None) -> dict:
    """The four things shown BEFORE a cloud provider is confirmed.

    This is where section 2's cascade objection and section 1.3's cost objection
    are enforced as UI rather than buried as prose.
    """
    s = settings if settings is not None else _settings()
    spec = PROVIDERS[provider]
    model = selected_model(provider, s)
    meta = spec["models"][model]
    per_reply = round(meta["usd_per_1k_chars"] * 0.15, 4)  # ~150 chars/reply
    return {
        "provider": provider,
        "label": spec["label"],
        # 1. Mode.
        "mode": "Synthesis-only",
        "mode_detail": (
            "This replaces one speech-to-speech hop with speech-to-text, "
            "model, text-to-speech. Barge-in and model-native prosody are "
            "lost. Friday keeps listening locally - your microphone audio "
            "does not leave this machine."),
        # 2. What leaves the machine. Named exactly.
        "egress": ("The text Friday is about to speak, after it passes the "
                   "privacy gate. Not your microphone audio."),
        # 3. Retention and training.
        "retention": spec["retention"],
        # 4. Cost - at the user's own volume, from their own history.
        "cost_per_reply_usd": per_reply,
        "cost_projection": _projected_monthly(meta["usd_per_1k_chars"]),
        "priced": _is_priced(provider, model, s),
        "legal_review": legal_review_required(provider),
        "cloning_note": (
            "Friday does not clone voices. Cloud voices are alternates you may "
            "select; they are never Friday's own voice, which stays on this "
            "machine and works offline."),
    }


def _projected_monthly(usd_per_1k_chars: float) -> dict:
    """USD/month at the user's ACTUAL recent speech volume, not a generic table.

    Falls back to an explicitly-labelled estimate when there is no history, so
    the number is never presented as measured when it is assumed.
    """
    try:
        from agent_friday.services import cost_meter
        summary = cost_meter.summary(rng="month") or {}
        chars = 0
        for row in (summary.get("by_kind") or []):
            if str(row.get("kind") or "") == "voice":
                chars = int(row.get("input_tokens") or 0)
        if chars > 0:
            return {"usd": round((chars / 1000.0) * usd_per_1k_chars, 2),
                    "basis": "your voice usage over the last month"}
    except Exception:
        pass
    return {"usd": round((30 * 40 * 150 / 1000.0) * usd_per_1k_chars, 2),
            "basis": "estimate - 40 spoken replies a day; no voice history yet"}


def _is_priced(provider: str, model: str, settings: dict) -> bool:
    return meter_model_id(provider, model, settings) == model


def meter_model_id(provider: str, model: str,
                   settings: dict | None = None) -> str:
    """The id this call is METERED under - Q4's resolution.

    On-Demand Inworld meters under the plain id, which ``cost_meter.PRICING``
    prices. Any other Inworld plan tier meters under ``<model>:<tier>``, which
    lives in ``cost_meter.UNPRICED_MODELS`` and stores SQL NULL. That renders as
    "not priced" instead of a number that would silently over-report by the
    difference between the on-demand ceiling and the user's actual rate.
    """
    if provider != "inworld":
        return model
    s = settings if settings is not None else _settings()
    tier = str(s.get("inworld_plan_tier") or "on_demand").strip().lower()
    if tier == "on_demand":
        return model
    return "%s:%s" % (model, tier)


# -- The gate (section 8.3 - a PREREQUISITE) ---------------------------------

def gate_synthesis_input(text: str, provider: str) -> str:
    """Pass synthesis input through the egress gate. Fail closed.

    Raises :class:`CloudVoiceUnavailable` when the gate withholds anything or
    cannot be reached. Two properties worth stating because both were choices:

    1. It uses ``egress_gate._gate_text``, not the public ``gate_text``. The
       public wrapper returns text unchanged when ``_is_cloud(provider)`` is
       false, so a provider absent from that list would bypass the gate
       entirely. This is the same reasoning that made
       ``elevenlabs_tools.py:162`` use the underscore form.
    2. A PARTIAL redaction is refused, not sent. If the gate returns anything
       other than the input verbatim, some part of what Friday was about to say
       is not allowed off this machine - and speaking the remainder would both
       leak the fact of the redaction and produce a sentence the user did not
       write. The user is told and offered the local voice instead.
    """
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception as e:
        raise CloudVoiceUnavailable(
            "the privacy gate could not be reached, so nothing was sent to %s"
            % provider,
            detail="the privacy gate could not be reached, so nothing was sent to "
                   "%s (%s)" % (provider, e),
            code="cloud_voice_gate_unavailable",
            offer="Speak this with Friday's local voice instead.",
            requested=provider)
    _never_send = getattr(_eg, "NeverSendBlocked", None)
    try:
        gated = _eg._gate_text(text, provider, "%s.tts" % provider)
    except Exception as e:
        if _never_send is not None and isinstance(e, _never_send):
            raise CloudVoiceUnavailable(
                "this text contains something that never leaves this device",
                detail="this text contains something that never leaves this device "
                       "(%s)" % e,
                code="cloud_voice_never_send",
                offer="Speak this with Friday's local voice instead.",
                requested=provider)
        raise CloudVoiceUnavailable(
            "the privacy gate failed, so nothing was sent to %s" % provider,
            detail="the privacy gate failed, so nothing was sent to %s (%s)"
                   % (provider, e),
            code="cloud_voice_gate_failed",
            offer="Speak this with Friday's local voice instead.",
            requested=provider)
    if gated != text:
        raise CloudVoiceUnavailable(
            "part of this reply stays on this device, so it was not sent to "
            "%s." % PROVIDERS.get(provider, {}).get("label", provider),
            code="cloud_voice_withheld",
            offer=("Speak this with Friday's local voice, which can say all "
                   "of it."),
            requested=provider)
    return gated


# -- Synthesis ---------------------------------------------------------------

def synthesize(text: str, *, provider: str | None = None,
               settings: dict | None = None,
               session_ctx=None) -> CloudVoiceResult:
    """Synthesize `text` with the user's selected cloud provider.

    Raises :class:`CloudVoiceUnavailable` rather than falling back. The caller
    decides what to surface and what to offer; this function never substitutes a
    provider, never quietly downgrades a model, and never returns audio from a
    provider other than the one named in the result.
    """
    s = settings if settings is not None else _settings()
    name = provider or resolve_provider(s)
    if not name:
        raise CloudVoiceUnavailable(
            "no cloud voice provider is selected",
            code="cloud_voice_not_selected",
            offer="Choose a provider in Settings then Voice.")
    if name not in PROVIDERS:
        raise CloudVoiceUnavailable(
            "%r is not a known cloud voice provider" % name,
            code="cloud_voice_unknown_provider", requested=str(name))
    if not ga_established(name):
        raise CloudVoiceUnavailable(
            "%s cannot be used: %s" % (
                PROVIDERS[name]["label"],
                GA_UNESTABLISHED_REASON.get(
                    name, "not available in this build")),
            code="cloud_voice_ga_unestablished",
            offer=("Use Friday's local voice, or ElevenLabs if you have "
                   "a key."),
            requested=name)
    if _local_only(s):
        # Section 6.3: the absolute override. Reached only if a caller passed an
        # explicit provider; resolve_provider() already returns None here.
        raise CloudVoiceUnavailable(
            "local-only mode is on - Friday will not send spoken text to "
            "%s." % PROVIDERS[name]["label"],
            code="cloud_voice_local_only",
            offer=("Turn off local-only mode in Settings then Privacy to use "
                   "cloud voice."),
            requested=name)

    text = (text or "").strip()
    if not text:
        raise CloudVoiceError("no text to speak", code="cloud_voice_empty")
    if len(text) > MAX_CHARS:
        raise CloudVoiceError(
            "text is %d characters; the per-request limit is %d"
            % (len(text), MAX_CHARS), code="cloud_voice_too_long")

    key = _api_key(name)
    problem = key_problem(name, key)
    if problem:
        raise CloudVoiceUnavailable(
            "%s cannot be used: %s" % (PROVIDERS[name]["label"], problem),
            code="cloud_voice_no_key",
            offer=("Add a key in Settings then Voice, or use Friday's local "
                   "voice."),
            requested=name)

    # THE GATE. Before any byte leaves. Raises on withhold.
    text = gate_synthesis_input(text, name)

    model = selected_model(name, s)
    voice = selected_voice(name, s)
    started = time.time()
    if name == "elevenlabs":
        audio, mime = _synth_elevenlabs(text, key, model, voice)
    else:
        audio, mime = _synth_inworld(text, key, model, voice)
    elapsed_ms = int((time.time() - started) * 1000)

    cost = _meter(name, model, text, elapsed_ms, s, session_ctx)
    metered_id = meter_model_id(name, model, s)
    notes = []
    if not audio_is_durable(name):
        notes.append(
            "Inworld audio is for playback only - Friday will not archive it. "
            "See Q3 in the cloud voice spec.")
    return CloudVoiceResult(
        audio=audio, mime=mime, provider=name, model=model,
        chars=len(text), duration_ms=elapsed_ms, cost_usd=cost,
        priced=(metered_id == model), durable=audio_is_durable(name),
        notes=notes)


def _meter(provider: str, model: str, text: str, elapsed_ms: int,
           settings: dict, session_ctx) -> float | None:
    """Record the call on the existing per-character convention (7.1).

    ``cost_meter.PRICING`` already reuses the "in"-per-1K slot as USD per 1K
    CHARACTERS, with the call site passing ``len(text)`` as ``input_tokens`` and
    ``0`` as ``output_tokens``. Nothing new is built here; Inworld's rows were
    added to that table in the same shape. Metering never raises.
    """
    metered_id = meter_model_id(provider, model, settings)
    try:
        from agent_friday.services import cost_meter
        cost_meter.record(provider, metered_id,
                          input_tokens=len(text), output_tokens=0,
                          duration_ms=elapsed_ms, kind="voice",
                          session_ctx=session_ctx)
        return cost_meter.cost_for(metered_id, len(text), 0)
    except Exception:
        log.debug("cloud voice metering failed", exc_info=True)
        return None


def _synth_elevenlabs(text: str, key: str, model: str, voice: str):
    """POST /text-to-speech/{voice_id}. HTTP client imported lazily (C3)."""
    import requests
    url = "%s/text-to-speech/%s" % (PROVIDERS["elevenlabs"]["base_url"], voice)
    try:
        resp = requests.post(
            url, headers={"xi-api-key": key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": model}, timeout=_TIMEOUT)
    except Exception as e:
        raise CloudVoiceUnavailable(
            "could not reach ElevenLabs",
            detail="could not reach ElevenLabs (%s)" % e,
            code="cloud_voice_network",
            offer="Use Friday's local voice for now.",
            requested="elevenlabs")
    _raise_for_status(resp, "elevenlabs", "ElevenLabs")
    return resp.content, "audio/mpeg"


def _synth_inworld(text: str, key: str, model: str, voice: str):
    """POST /voice. Inworld authenticates with ``Authorization: Basic <key>``.

    Q5 - whether Inworld has an ElevenLabs-style key-id trap - is unverified, so
    no format assertion is made about the key here.
    """
    import base64
    import requests
    url = "%s/voice" % PROVIDERS["inworld"]["base_url"]
    try:
        resp = requests.post(
            url, headers={"Authorization": "Basic %s" % key,
                          "Content-Type": "application/json"},
            json={"text": text, "voiceId": voice, "modelId": model},
            timeout=_TIMEOUT)
    except Exception as e:
        raise CloudVoiceUnavailable(
            "could not reach Inworld",
            detail="could not reach Inworld (%s)" % e,
            code="cloud_voice_network",
            offer="Use Friday's local voice for now.",
            requested="inworld")
    _raise_for_status(resp, "inworld", "Inworld")
    try:
        payload = resp.json()
        raw = payload.get("audioContent") or payload.get("audio")
        if raw:
            return base64.b64decode(raw), "audio/wav"
    except Exception:
        pass
    return resp.content, "audio/wav"


def _raise_for_status(resp, provider: str, label: str) -> None:
    """Classify metered-provider failures separately (section 6.5).

    Quota exhaustion, budget breach and auth failure are NOT network failures: a
    user out of credits needs a different action than a user with a dead
    connection, so they get different codes and different offers.
    """
    if resp.status_code < 400:
        return
    detail = ""
    try:
        body = resp.json().get("detail") or resp.json().get("error")
        detail = (body.get("message") if isinstance(body, dict)
                  else str(body)) or ""
    except Exception:
        detail = (getattr(resp, "text", "") or "")[:300]
    if resp.status_code in (401, 403):
        raise CloudVoiceUnavailable(
            "%s rejected the API key (HTTP %d)" % (label, resp.status_code),
            detail="%s rejected the API key (%s)" % (label, detail or resp.status_code),
            code="cloud_voice_auth",
            offer=("Check the key in Settings then Voice, or use Friday's "
                   "local voice."),
            requested=provider)
    if resp.status_code == 429 or "quota" in detail.lower():
        raise CloudVoiceUnavailable(
            "%s is out of credits or rate-limited (HTTP %d)" % (label, resp.status_code),
            detail="%s is out of credits or rate-limited (%s)"
                   % (label, detail or resp.status_code),
            code="cloud_voice_quota",
            offer="Top up your %s plan, or use Friday's local voice." % label,
            requested=provider)
    raise CloudVoiceUnavailable(
        "%s returned HTTP %d" % (label, resp.status_code),
        detail="%s returned HTTP %d: %s" % (label, resp.status_code,
                                            detail or "(no detail)"),
        code="cloud_voice_http",
        offer="Use Friday's local voice for now.",
        requested=provider)
