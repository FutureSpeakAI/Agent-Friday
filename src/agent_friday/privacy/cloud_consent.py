"""The ONE place cloud-consent posture is resolved.

Why this module exists
-----------------------
`model_routing.mode` defaults to ``"cloud_only"`` on every fresh install —
that is the factory setting, not a choice anyone made. Until 2026-09-06,
``services/egress_gate.py``'s ``is_unrestricted_cloud()`` treated that
factory default (plus a separate, easily-missed ``unrestricted_cloud``
flag) as license to bypass every privacy safeguard in the codebase: tier
classification, redaction, the PII scrub, the never-send list, all of it.
A stranger who never opened Settings inherited "no safeguards" the moment
they picked cloud-only as their provider — which most people do, since it
needs no local model and no setup.

Stephen's ruling: unrestricted cloud access is earned by an explicit
decision, never inherited from a default. And the decision is only real on
hardware that can actually deliver the alternative — offering "private
local" as an option on a machine that cannot run local models well enough
to do the work (reasoning, voice, image, video, and the arbiter juggling
between them) is a promise the hardware will break, which is the exact
failure mode this whole week's work has been about surfacing rather than
hiding.

So there are two shapes, and which one a user sees depends on their own
hardware, verified against the same verdict machinery the model picker and
the onboarding starter-set already use — never a second, independent
guess at what the machine can do:

  * **Capable hardware** — a genuine choice between "local_private" and
    "cloud_unrestricted". Either answer is durable once given.
  * **Insufficient hardware** — no local option is offered at all. The
    machine's actual limits are stated plainly, and unrestricted cloud is
    still something the user consents to, not something they land in by
    silence.

What ``cloud_consent`` means
-----------------------------
``model_routing.cloud_consent`` is a record, not a flag:
``{"answered": bool, "choice": "local_private" | "cloud_unrestricted" | None,
"at": iso-str | None, "capability_snapshot": dict | None}``.
``is_unrestricted_cloud()`` (below) is True iff ``answered`` is True AND
``choice == "cloud_unrestricted"``. Everything else — unanswered, answered
"local_private", or any read failure — is gated. Choosing "local_private"
does not mean local-only routing is force-enabled elsewhere; it means the
one flag capable of turning every safeguard off at once stays off.

Migration for existing installs
--------------------------------
An install that had already set the old standalone ``unrestricted_cloud``
flag to True is treated as having already made this choice — that flag's
whole meaning under the pre-2026-09-06 design WAS "give the cloud full
access", so re-asking would be asking the same question the user already
answered under a different name. Everyone else, regardless of what
``mode`` happens to be, is unanswered until they see one of the two
screens above. No settings.json migration script runs for this; `resolve()`
computes the grandfather-in live, exactly the way `vault_policy.resolve()`
computes its own posture live rather than trusting a one-time migration
that could silently go stale.

The write path
---------------
``record_consent()`` is the only legitimate way to set ``cloud_consent``.
It re-derives the capability assessment itself — it does not trust
whatever the caller claims the hardware can do — so a request for
"local_private" on hardware `assess_local_capability()` calls insufficient
is refused outright, not silently downgraded. It writes through
``core._save_settings(..., _internal_cloud_consent_write=True)``, the one
call in the codebase permitted to carry that keyword. Every other write
path to settings — the generic ``POST /api/settings`` above all, which a
model's own HTTP tool can already reach — has this key stripped from its
delta before anything is merged. See ``core._save_settings``'s own
docstring for the mechanics, and the note there about why this mirrors
`enterprise_consent_grant`'s removal rather than a softer warning.

Honest limit, stated rather than hidden: this defends every write path
*this application* exposes. It cannot stop something with raw filesystem
access to ``settings.json`` from writing the key directly — no in-process
guard can, on a local desktop app, and no guard elsewhere in this codebase
claims otherwise either.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, NamedTuple

_log = logging.getLogger("friday.privacy.cloud_consent")

CHOICE_LOCAL = "local_private"
CHOICE_CLOUD = "cloud_unrestricted"
VALID_CHOICES = (CHOICE_LOCAL, CHOICE_CLOUD)

#: The bar for "this machine can actually run local models for the work in
#: question", not merely "this machine can load the weights". `fits` (HR1/
#: HR2, residency_policy.py) answers "does it load" and has a real `refused`
#: state (HR18: a MEASURED shortfall refuses). `runs_well` answers "is it
#: usable" and has NO `refused` state at all — its worst answer is
#: `degraded` (RAM shortfall or observed thrashing; §5.2's own table). A
#: `degraded` local model is not the genuine alternative this gate exists to
#: offer, so the bar here is the strict one: `fits` not refused/unknown, AND
#: `runs_well` == "ready" specifically, not merely "not refused".
_BAD_FITS_STATUSES = ("refused", "unknown", None)


class ConsentStatus(NamedTuple):
    answered: bool
    choice: str | None
    at: str | None
    capability_snapshot: dict | None
    #: "settings" | "legacy_unrestricted_cloud_flag" | "unanswered" | "error-default"
    source: str

    @property
    def unrestricted(self) -> bool:
        """The one thing `egress_gate.is_unrestricted_cloud()` needs."""
        return self.answered and self.choice == CHOICE_CLOUD


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve(config: dict | None = None) -> ConsentStatus:
    """Resolve cloud-consent posture from ONE place. Never raises.

    `config` is the `model_routing` mapping when the caller already holds
    one; otherwise the live settings are read. Every failure resolves to
    the protective answer (unanswered → gated), matching every other
    privacy resolver in this package.
    """
    try:
        if config is None:
            from agent_friday.core import _load_settings
            cfg = (_load_settings() or {}).get("model_routing") or {}
        else:
            cfg = config or {}
        if not isinstance(cfg, dict):
            cfg = {}

        cc = cfg.get("cloud_consent")
        if isinstance(cc, dict) and cc.get("answered") is True:
            choice = cc.get("choice")
            if choice not in VALID_CHOICES:
                choice = None
            return ConsentStatus(answered=choice is not None, choice=choice,
                                 at=cc.get("at"),
                                 capability_snapshot=cc.get("capability_snapshot"),
                                 source="settings")

        # Grandfather-in: the pre-2026-09-06 standalone flag WAS this same
        # decision under a different name.
        if bool(cfg.get("unrestricted_cloud", False)):
            return ConsentStatus(answered=True, choice=CHOICE_CLOUD, at=None,
                                 capability_snapshot=None,
                                 source="legacy_unrestricted_cloud_flag")

        return ConsentStatus(answered=False, choice=None, at=None,
                             capability_snapshot=None, source="unanswered")
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("cloud consent could not be resolved (%s); failing SAFE "
                   "to gated (unanswered).", exc)
        return ConsentStatus(answered=False, choice=None, at=None,
                             capability_snapshot=None, source="error-default")


def is_unrestricted_cloud() -> bool:
    """`services/egress_gate.py`'s only remaining call for this decision."""
    return resolve().unrestricted


def _row_ok(row: dict) -> bool:
    v = (row or {}).get("verdicts") or {}
    fits = (v.get("fits") or {}).get("status")
    runs_well = (v.get("runs_well") or {}).get("status")
    return fits not in _BAD_FITS_STATUSES and runs_well == "ready"


def _role_capable(rows: list) -> tuple:
    """(ok, reason). `rows` are LocalModelRow-shaped dicts from
    `routes.intelligence.local_models_catalog()` — the same rows the model
    picker renders, so this can never disagree with what a user sees there.
    """
    if not rows:
        return False, "no local model is catalogued for this role at all"
    for row in rows:
        if _row_ok(row):
            return True, None
    v = (rows[0] or {}).get("verdicts") or {}
    explanation = ((v.get("runs_well") or {}).get("explanation")
                  or (v.get("fits") or {}).get("explanation")
                  or "no candidate for this role runs well on this machine")
    return False, explanation


def _text_capable(profile: dict, ladder_rows: list) -> tuple:
    """(ok, reason) for reasoning specifically. Unlike voice/image/video —
    each served by exactly one runner Friday ships (CPU faster-whisper/
    Piper or GPU NeMo for voice; ComfyUI for image and video) — text can be
    served by Ollama, Friday's own runtime store (a fetched GGUF under
    `~/.friday/runtime/models/`), or an already-resident llama-server seat,
    and `local_models_catalog()`'s "text" rows only ever cover the curated
    `model_plan.BRAIN_MODELS` ladder. Checking only those rows answers
    "capable" or not based on which of four specific tags happen to be
    resident, blind to anything else Friday can actually serve — Stephen's
    own machine has a real 3.19 GiB GGUF in Friday's runtime store that is
    not one of the four ladder rungs, and a curated-ladder-only check would
    call that machine incapable while it is actively running local
    reasoning.

    `local_seats.installed()` is already the correct, runner-agnostic
    signal for "what can Friday actually serve" — Friday's own store UNION
    the Ollama daemon, built specifically because trusting the daemon alone
    moved Stephen's real reasoning seat off its runtime on 2026-08-18 (see
    that module's own docstring). Every name it returns is checked directly
    against `residency_policy.verdicts()`, the same function that produces
    the curated ladder's own rows — never a second, different bar for a
    model just because it did not come from the ladder.
    """
    from agent_friday.services import residency_policy as rp
    try:
        from agent_friday.services import local_seats
        conversational = [n for n, _ in local_seats.installed()]
    except Exception:
        conversational = []
    for model_id in conversational:
        v = rp.verdicts({"model_id": model_id}, profile)
        if _row_ok({"verdicts": v}):
            return True, None
    # Nothing actually resident runs well — fall back to the curated
    # ladder's OWN verdicts, so a capable-but-empty machine (real headroom,
    # nothing downloaded yet) still reads as capable, matching what the
    # model picker itself would recommend fetching.
    return _role_capable(ladder_rows)


def assess_local_capability(profile: dict | None = None) -> dict:
    """Can this machine genuinely do private-local for reasoning, voice
    (stt+tts), image, and video, including the arbiter juggling between
    them? Reuses the exact verdict machinery the model picker and the
    onboarding starter set already compute — this is deliberately not a
    second, independent guess at hardware capability.

    Returns `{"capable": bool, "roles": {role: {"ok": bool, "why": str|None}},
    "chain_ok": bool, "chain_why": str|None}`.
    """
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.routes.intelligence import (local_models_catalog,
                                                       build_starter_set)
        prof = profile if profile is not None else hwp.get()
        lm = local_models_catalog(prof, {})

        roles = {}
        text_ok, text_why = _text_capable(prof, lm.get("text") or [])
        roles["text"] = {"ok": text_ok, "why": text_why}
        voice_rows = lm.get("voice") or []
        stt_ok, stt_why = _role_capable(
            [r for r in voice_rows if r.get("modality") == "stt"])
        tts_ok, tts_why = _role_capable(
            [r for r in voice_rows if r.get("modality") == "tts"])
        # Both directions are needed for a real conversation — a machine
        # that can hear but not speak (or the reverse) cannot honour "voice"
        # as a private-local capability, even though one row individually
        # runs well.
        voice_ok = stt_ok and tts_ok
        voice_why = None if voice_ok else (stt_why or tts_why)
        roles["voice"] = {"ok": voice_ok, "why": voice_why}
        image_ok, image_why = _role_capable(lm.get("image") or [])
        roles["image"] = {"ok": image_ok, "why": image_why}
        video_ok, video_why = _role_capable(lm.get("video") or [])
        roles["video"] = {"ok": video_ok, "why": video_why}

        # `plan_chain()`'s real shape (residency_policy.py): a normal return
        # is {"stages": [{"where": "leased"|"cloud"|"refused", ...}, ...],
        # "contract_ok": bool|None, ...} -- there is no top-level "error" or
        # "status" key on a genuine result. `build_starter_set()` wraps a
        # raised exception as {"error": str(e)} itself, which is the one
        # shape that IS an error here.
        chain_ok, chain_why = True, None
        try:
            starter = build_starter_set(prof)
            chain = starter.get("chain") or {}
            for name, result in chain.items():
                if not isinstance(result, dict) or result.get("error"):
                    chain_ok = False
                    chain_why = ("the planner could not price a %s turn on "
                                "this hardware (%s)" % (
                                    name, (result or {}).get("error")
                                    if isinstance(result, dict) else "no result"))
                    break
                stages = result.get("stages") or []
                cloud_or_refused = [st for st in stages
                                    if st.get("where") in ("cloud", "refused")]
                if cloud_or_refused:
                    chain_ok = False
                    st = cloud_or_refused[0]
                    chain_why = ("a %s turn's own resource plan sends its %s "
                                "stage to %s on this hardware, not a local "
                                "seat" % (name, st.get("role"), st.get("where")))
                    break
                if result.get("contract_ok") is False:
                    chain_ok = False
                    chain_why = ("a %s turn's resource plan reports "
                                "contract_ok=False on this hardware" % name)
                    break
        except Exception as exc:
            chain_ok, chain_why = False, "chain planning failed (%s)" % exc

        capable = all(r["ok"] for r in roles.values()) and chain_ok
        return {"capable": capable, "roles": roles,
                "chain_ok": chain_ok, "chain_why": chain_why}
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("local capability could not be assessed (%s); treating "
                   "as insufficient.", exc)
        return {"capable": False,
                "roles": {r: {"ok": False, "why": "capability check failed"}
                          for r in ("text", "voice", "image", "video")},
                "chain_ok": False, "chain_why": "capability check failed"}


class ConsentRejected(RuntimeError):
    """`record_consent()` refused the write — the client asked for a
    private-local choice on hardware `assess_local_capability()` calls
    insufficient. Never silently downgraded to cloud instead."""


def record_consent(choice: str, *, profile: dict | None = None) -> dict:
    """The ONLY legitimate way to set `cloud_consent`. Re-derives capability
    server-side rather than trusting the caller. Raises `ConsentRejected`
    or `ValueError` rather than writing an inconsistent record.
    """
    if choice not in VALID_CHOICES:
        raise ValueError("choice must be one of %r, got %r" % (VALID_CHOICES, choice))

    snapshot = assess_local_capability(profile)
    if choice == CHOICE_LOCAL and not snapshot["capable"]:
        raise ConsentRejected(
            "local_private was requested but this machine's own capability "
            "check says it cannot run local models well enough for the "
            "full job (reasoning, voice, image, video, and the arbiter "
            "juggling between them). Offering that choice here would be a "
            "promise the hardware breaks.")

    record = {"answered": True, "choice": choice, "at": _now_iso(),
              "capability_snapshot": snapshot}

    from agent_friday.core import _save_settings
    _save_settings({"model_routing": {"cloud_consent": record}},
                   _internal_cloud_consent_write=True)
    _log.warning("cloud consent recorded: choice=%s capable=%s", choice,
                snapshot["capable"])
    return record


def status(profile: dict | None = None) -> dict:
    """Serialisable posture for the onboarding/Settings UI and /api/health."""
    cs = resolve()
    needs_prompt = not cs.answered
    payload = {
        "answered": cs.answered,
        "choice": cs.choice,
        "at": cs.at,
        "source": cs.source,
        "unrestricted": cs.unrestricted,
        "needs_prompt": needs_prompt,
    }
    if needs_prompt:
        payload["capability"] = assess_local_capability(profile)
    return payload
