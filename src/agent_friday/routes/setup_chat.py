"""The first-run setup chat, its connection checklist, and the profile it makes.

    GET    /api/setup-chat/state                  the chat as the window draws it
    POST   /api/setup-chat/begin                  consent screens done {routing_mode, vault_passphrase}
    POST   /api/setup-chat/answer                 {stage, value?, text?}
    POST   /api/setup-chat/skip-all               "Set up later": finish with defaults
    POST   /api/setup-chat/rerun                  run the chat again (from Settings)
    POST   /api/setup-chat/goto                   revisit a finished stage {stage}
    GET    /api/setup-chat/secret-shapes          the key shapes the input guard checks
    POST   /api/setup-chat/style/preview          sample reply for slider values
    GET    /api/setup-chat/research               the opt-in research job, for review
    POST   /api/setup-chat/research/start         start it from Settings {seeds}
    POST   /api/setup-chat/research/review        {decisions: [{id, action, text?}]}
    GET    /api/setup-chat/profile                answers and derived style
    PUT    /api/setup-chat/profile/answers        {answers: {id: text}, rebuild?}
    POST   /api/setup-chat/profile/style          {sliders, summary?}
    DELETE /api/setup-chat/profile                delete answers, transcript, style block
    GET    /api/setup/connections                 the checklist; never a secret value
    POST   /api/setup/connections/<id>/skip       {skipped}

LOCAL ONLY, on top of the app-wide login gate. The profile holds a person's
answers about themselves and the begin call carries the vault passphrase; both
are things the owner does at their own machine. A remote session, even an
authenticated one, is refused, and so is an observer credential. Writes need a
JSON body from the same origin.

No route here receives a provider key. The checklist's secure fields post
straight to the existing credential endpoints it names.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.services import setup_chat as sc

setup_chat_bp = Blueprint("setup_chat", __name__)


def _local_user_only():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "setup is only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "setup can only be done on Friday's own computer"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    if request.method in ("POST", "PUT", "DELETE"):
        if request.method != "DELETE" and not request.is_json:
            return jsonify({"error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"error": "cross-origin request refused"}), 403
    return None


@setup_chat_bp.before_request
def _gate():
    return _local_user_only()


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _refused(e: sc.Refused):
    return jsonify({"ok": False, **(e.payload or {"error": str(e)})}), 422


@setup_chat_bp.route("/api/setup-chat/state", methods=["GET"])
def setup_chat_state():
    return jsonify({"ok": True, **sc.view()})


@setup_chat_bp.route("/api/setup-chat/begin", methods=["POST"])
def setup_chat_begin():
    b = _body()
    return jsonify({"ok": True, **sc.begin(str(b.get("routing_mode") or ""),
                                           str(b.get("vault_passphrase") or ""))})


@setup_chat_bp.route("/api/setup-chat/answer", methods=["POST"])
def setup_chat_answer():
    b = _body()
    try:
        return jsonify({"ok": True, **sc.answer(str(b.get("stage") or ""),
                                                b.get("value"), str(b.get("text") or ""))})
    except sc.Refused as e:
        return _refused(e)
    except sc.Conflict as e:
        return jsonify({"ok": False, "error": "conflict", "message": str(e),
                        **sc.view()}), 409


@setup_chat_bp.route("/api/setup-chat/skip-all", methods=["POST"])
def setup_chat_skip_all():
    return jsonify({"ok": True, **sc.skip_all()})


@setup_chat_bp.route("/api/setup-chat/rerun", methods=["POST"])
def setup_chat_rerun():
    return jsonify({"ok": True, **sc.rerun()})


@setup_chat_bp.route("/api/setup-chat/goto", methods=["POST"])
def setup_chat_goto():
    try:
        return jsonify({"ok": True, **sc.goto(str(_body().get("stage") or ""))})
    except sc.Conflict as e:
        return jsonify({"ok": False, "error": "conflict", "message": str(e)}), 409


@setup_chat_bp.route("/api/setup-chat/secret-shapes", methods=["GET"])
def setup_chat_secret_shapes():
    from agent_friday.services import secret_shapes
    return jsonify({"ok": True, **secret_shapes.for_client()})


@setup_chat_bp.route("/api/setup-chat/style/preview", methods=["POST"])
def setup_chat_style_preview():
    return jsonify({"ok": True, **sc.style_preview(_body().get("sliders") or {})})


# ── research ─────────────────────────────────────────────────────────────────

@setup_chat_bp.route("/api/setup-chat/research", methods=["GET"])
def setup_chat_research():
    return jsonify({"ok": True, **sc.research_view()})


@setup_chat_bp.route("/api/setup-chat/research/start", methods=["POST"])
def setup_chat_research_start():
    from agent_friday.services import setup_reader, setup_research
    st = sc.load_state()
    reader = setup_reader.choose(st.get("routing_mode") or "",
                                 cloud_confirmed=bool(st.get("cloud_confirmed")))
    seeds = _body().get("seeds") or {}
    try:
        for v in seeds.values():
            for piece in (v if isinstance(v, list) else [v]):
                sc._guard_text(str(piece or ""))
        out = setup_research.start(seeds, reader, engine=sc.RESEARCH_ENGINE,
                                   spawn=sc.RESEARCH_SPAWN)
    except sc.Refused as e:
        return _refused(e)
    except setup_research.NoModel:
        return jsonify({"ok": False, "error": "no_model",
                        "message": "No model is available to read pages yet."}), 409
    except ValueError as e:
        return jsonify({"ok": False, "error": "no_seeds", "message": str(e)}), 400
    st = sc.load_state()
    st["reader"] = reader
    st["research"] = {"state": "running", "job_id": out["job_id"],
                      "task_id": out.get("task_id") or ""}
    sc.save_state(st)
    return jsonify({"ok": True, **out})


@setup_chat_bp.route("/api/setup-chat/research/review", methods=["POST"])
def setup_chat_research_review():
    from agent_friday.services import setup_research
    st = sc.load_state()
    job_id = (st.get("research") or {}).get("job_id") or ""
    if not job_id:
        return jsonify({"ok": False, "error": "no research to review"}), 404
    try:
        out = setup_research.review(job_id, _body().get("decisions") or [])
    except KeyError:
        return jsonify({"ok": False, "error": "no research to review"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": "refused", "message": str(e)}), 422
    return jsonify({"ok": True, **out, "research": sc.research_view()})


# ── profile ──────────────────────────────────────────────────────────────────

@setup_chat_bp.route("/api/setup-chat/profile", methods=["GET"])
def setup_chat_profile():
    from agent_friday.services import setup_profile
    return jsonify({"ok": True, **setup_profile.status()})


@setup_chat_bp.route("/api/setup-chat/profile/answers", methods=["PUT"])
def setup_chat_profile_answers():
    from agent_friday.services import setup_profile
    b = _body()
    try:
        for qid, text in (b.get("answers") or {}).items():
            t = sc._guard_text(str(text or ""))
            setup_profile.record_answer(str(qid), t, skipped=not t)
    except sc.Refused as e:
        return _refused(e)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if b.get("rebuild"):
        st = sc.load_state()
        p = setup_profile.load_profile()
        style = setup_profile.synthesize(p.get("answers") or {},
                                         st.get("reader") or {}, p.get("name") or "")
        p["style"] = {**style, "saved": True}
        setup_profile.save_profile(p)
        setup_profile.apply_style(p["style"], p.get("name") or "")
    return jsonify({"ok": True, **setup_profile.status()})


@setup_chat_bp.route("/api/setup-chat/profile/style", methods=["POST"])
def setup_chat_profile_style():
    from agent_friday.services import setup_profile, style_guard
    b = _body()
    p = setup_profile.load_profile()
    style = dict(p.get("style") or {})
    sliders = setup_profile.clamp(b.get("sliders") or style.get("sliders"))
    notes = style.get("notes") or []
    summary = b.get("summary")
    if summary is not None:
        summary, _ = style_guard.clean_model_prose(sc._guard_text(str(summary))[:900])
    else:
        summary = setup_profile.summary_for(sliders, notes, p.get("name") or "")
    style.update({"sliders": sliders, "notes": notes, "summary": summary,
                  "sample": setup_profile.sample_reply(sliders, notes, p.get("name") or ""),
                  "saved": True})
    style.setdefault("by", {"kind": "rules", "model": "", "provider": ""})
    p["style"] = style
    setup_profile.save_profile(p)
    applied = setup_profile.apply_style(style, p.get("name") or "")
    return jsonify({"ok": True, "applied": {k: applied[k] for k in
                                            ("ok", "communication_style", "response_length")},
                    **setup_profile.status()})


@setup_chat_bp.route("/api/setup-chat/profile", methods=["DELETE"])
def setup_chat_profile_delete():
    from agent_friday.services import setup_profile
    out = setup_profile.delete_profile()
    return jsonify({"ok": True, **out, **setup_profile.status()})


# ── the connection checklist ─────────────────────────────────────────────────

@setup_chat_bp.route("/api/setup/connections", methods=["GET"])
def setup_connections():
    from agent_friday.services import setup_connections as scx
    st = sc.load_state()
    return jsonify({"ok": True, **scx.checklist(st.get("skipped_connections") or [])})


@setup_chat_bp.route("/api/setup/connections/<path:item_id>/skip", methods=["POST"])
def setup_connections_skip(item_id):
    skipped = bool(_body().get("skipped", True))
    return jsonify({"ok": True, "skipped": sc.skip_connection(item_id, skipped)})
