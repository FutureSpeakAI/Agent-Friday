"""HTTP surface for cloud voice providers (cloud-voice-providers.md).

A SEPARATE blueprint on purpose. ``routes/voice.py`` is 3,100 lines and is being
edited concurrently; adding a cloud synthesis leg there would have meant a large
diff in a file under churn. Everything cloud-specific lives here, and
``routes/voice.py`` keeps one small change (the ``auto`` semantics fix), which is
the smallest edit that satisfies the decision.

Routes:
  GET  /api/voice/cloud/providers    - section 4.1/4.2 selection surface
  GET  /api/voice/cloud/disclosure   - section 4.3, shown BEFORE confirmation
  POST /api/voice/cloud/tts          - synthesis; gated, metered, attributed
  GET  /api/voice/indicator          - section 4.4, what actually served
"""
from __future__ import annotations

import logging

from flask import Blueprint, Response, jsonify, request

from agent_friday.services import cloud_voice, voice_indicator
from agent_friday.routes._errors import api_error

log = logging.getLogger(__name__)

cloud_voice_bp = Blueprint("cloud_voice", __name__)


@cloud_voice_bp.route("/api/voice/cloud/providers")
def cloud_voice_providers():
    """Every cloud provider, selectable or not, with the reason (section 4.2).

    Performs no network I/O - see the C3 note on
    ``cloud_voice.available_providers``.
    """
    return jsonify({"status": "ok",
                    "providers": cloud_voice.available_providers()})


@cloud_voice_bp.route("/api/voice/cloud/disclosure")
def cloud_voice_disclosure():
    """The four disclosures shown before a cloud provider is confirmed."""
    name = (request.args.get("provider") or "").strip().lower()
    if name not in cloud_voice.PROVIDERS:
        return jsonify({"status": "error",
                        "message": "unknown provider"}), 400
    return jsonify({"status": "ok", "disclosure": cloud_voice.disclosure(name)})


@cloud_voice_bp.route("/api/voice/indicator")
def cloud_voice_indicator():
    """What actually served the most recent interaction (section 4.4)."""
    return jsonify({"status": "ok", "indicator": voice_indicator.snapshot()})


@cloud_voice_bp.route("/api/voice/cloud/tts", methods=["POST"])
def cloud_voice_tts():
    """Synthesize with the selected cloud provider.

    THE C2 CONTRACT, and the reason this handler looks the way it does:

    On failure it returns HTTP 503 with ``surfaced: true``, the reason, and the
    offer - and it does **not** call the local synthesizer. Substituting local
    audio here would be the silent fallback the design forbids in the
    cloud-fails direction, and it would make the section 4.4 indicator lie about
    what served. The client shows the offer; the user takes it; the client then
    calls the existing ``/api/voice/tts``. Consent is a round trip, which is the
    whole point.

    The reverse direction (local fails -> cloud) is refused in the same shape by
    the local path, per section 6.3.
    """
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "message": "No text provided"}), 400
    provider = (body.get("provider") or "").strip().lower() or None
    try:
        result = cloud_voice.synthesize(text, provider=provider)
    except cloud_voice.CloudVoiceUnavailable as e:
        # Surface and offer. Never substitute.
        voice_indicator.record_degraded(
            requested=(e.requested or provider or "cloud"),
            serving=None, reason=e.user_message, offer=e.offer)
        return jsonify({
            "status": "unavailable",
            "surfaced": True,
            "substituted": False,
            "code": e.code,
            "message": e.user_message,
            "offer": e.offer,
            "requested": e.requested or provider,
        }), 503
    except cloud_voice.CloudVoiceError as e:
        return jsonify({"status": "error", "code": e.code,
                        "message": e.user_message}), 400
    except Exception as e:  # pragma: no cover - defensive
        log.exception("cloud voice synthesis failed")
        return api_error(e, "Couldn't speak with the cloud voice")

    # Written AFTER the response is served, FROM the served path.
    voice_indicator.record_served(
        provider=result.provider,
        label=cloud_voice.PROVIDERS[result.provider]["label"],
        model=result.model, is_cloud=True, cost_usd=result.cost_usd,
        chars=result.chars, priced=result.priced)

    resp = Response(result.audio, mimetype=result.mime)
    # Cost visibility in the moment (section 7.3 level 1), and the served
    # provider on the response itself so a client cannot render the indicator
    # from what it asked for.
    resp.headers["X-Friday-Voice-Provider"] = result.provider
    resp.headers["X-Friday-Voice-Model"] = result.model
    resp.headers["X-Friday-Voice-Chars"] = str(result.chars)
    resp.headers["X-Friday-Voice-Cost-USD"] = (
        "%.6f" % result.cost_usd if result.priced and result.cost_usd is not None
        else "unpriced")
    # Q3: Inworld audio is playback-only. Say so on the wire so a client or a
    # future caller cannot archive it without having been told.
    resp.headers["X-Friday-Voice-Durable"] = "1" if result.durable else "0"
    if not result.durable:
        # Q3. "Friday will not archive it" and "it is
        # not cached anywhere" are different promises, and only the second
        # is what non-durable should mean. Without this, the browser HTTP
        # cache and any intermediary would hold audio whose vendor terms
        # require deletion on termination -- a copy Friday never decided to
        # keep and cannot enumerate to delete.
        resp.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0")
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp
