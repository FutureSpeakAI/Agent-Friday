"""A route that fails still tells the user WHAT failed, with an error id, and
never HOW: no exception text, no file paths, no traceback in the body.

Three paths are covered:

* A route that catches an exception answers through `api_error`. Each case
  makes the call the route makes raise RuntimeError(INTERNAL) and checks the
  status code and envelope are the route's own, the key the UI reads holds a
  non-empty message and the error id, and neither the exception text nor a
  traceback is in the body.
* A route that returns a service's result dict passes it through
  `public_result`: exception text the service marked (it stays real for the
  model, which reads the same dict through tools) is swapped for an id.
* An exception written for the user (`UserFacingError`) is still shown as is.
"""
from __future__ import annotations

import importlib

import pytest

from agent_friday.user_errors import UserFacingValueError, exception_text

INTERNAL = "SECRET-INTERNAL-/path/detail"  # pragma: allowlist secret


def _raiser(exc_type=RuntimeError):
    def _boom(*_a, **_k):
        raise exc_type(INTERNAL)
    return _boom


def _patch(monkeypatch, target, replacement):
    """target = "module:attr.attr" -- patch the name where the route looks it up."""
    mod_name, chain = target.split(":")
    owner = importlib.import_module(mod_name)
    parts = chain.split(".")
    for p in parts[:-1]:
        owner = getattr(owner, p)
    monkeypatch.setattr(owner, parts[-1], replacement)


def _call(client, method, url, body):
    if method == "GET":
        return client.get(url)
    return client.open(url, method=method, json=body if body is not None else {})


def _assert_hidden(resp, url):
    raw = resp.get_data(as_text=True)
    assert "SECRET-INTERNAL" not in raw, "%s leaked the exception text" % url
    assert "Traceback" not in raw, "%s leaked a traceback" % url


R = "agent_friday.routes."
S = "agent_friday.services."

# (id, method, url, json body, patch target, exception type, expected status, key the UI reads)
CASES = [
    ("costs", "GET", "/api/costs/summary", None, R + "costs:_cm.summary", RuntimeError, 500, "message"),
    ("workspace_studio", "GET", "/api/workspace/customizations", None,
     R + "workspace_studio:all_customizations", RuntimeError, 500, "message"),
    ("hooks", "GET", "/api/hooks", None, R + "hooks:_hooks.list_hooks", RuntimeError, 500, "message"),
    ("updates", "GET", "/api/updates/status", None, R + "updates:_uc.status", RuntimeError, 500, "message"),
    ("scheduler", "GET", "/api/schedules", None, R + "scheduler:_sched.list_schedules",
     RuntimeError, 500, "message"),
    ("memory_proposals", "GET", "/api/memory/proposals/pending", None,
     R + "memory_proposals:mp.pending", RuntimeError, 500, "reason"),
    ("work_log", "GET", "/api/work-log", None, R + "work_log:wl.get_log", RuntimeError, 500, "error"),
    ("budget_policy", "GET", "/api/budget/policies", None,
     R + "budget_policy:be.get_all_policies", RuntimeError, 500, "error"),
    ("ambient", "GET", "/api/ambient/state", None, R + "ambient:get_ambient_state",
     RuntimeError, 500, "message"),
    ("arbiter", "GET", "/api/arbiter/leases", None, R + "arbiter:_arb.held", RuntimeError, 500, "message"),
    ("compute", "GET", "/api/compute/jobs", None, R + "compute:prov.get_active_jobs",
     RuntimeError, 500, "error"),
    ("connectors", "GET", "/api/connectors", None, R + "connectors:list_connectors",
     RuntimeError, 500, "message"),
    ("contacts", "GET", "/api/people/forgotten", None, S + "forget_person:list_forgotten",
     RuntimeError, 500, "message"),
    ("calendar", "POST", "/api/calendar/enrich", {"event_id": "e1", "research": "notes"},
     R + "calendar:_enrich_calendar_event", RuntimeError, 500, "message"),
    ("creative_pipeline", "GET", "/api/pipelines/templates", None,
     R + "creative_pipeline:cp.list_templates", RuntimeError, 500, "message"),
    ("projects", "GET", "/api/creative/projects", None, R + "projects:cm.list_projects",
     RuntimeError, 500, "message"),
    ("core_routes", "GET", "/api/capabilities/state", None, S + "capability_state:as_dicts",
     RuntimeError, 500, "error"),
    ("google_accounts", "GET", "/api/google/accounts", None, R + "google_accounts:ga.list_accounts",
     RuntimeError, 500, "message"),
    ("insights", "GET", "/api/security/risk-score", None, R + "insights:get_behavioral_monitor",
     RuntimeError, 500, "message"),
    ("news", "GET", "/api/source-trust", None, R + "news:get_source_trust_graph",
     RuntimeError, 500, "message"),
    ("orchestrator", "GET", "/api/orchestrator/workers", None, R + "orchestrator:_orch",
     RuntimeError, 500, "error"),
    ("privacy_consent", "GET", "/api/privacy/cloud-consent", None,
     R + "privacy_consent:cloud_consent.status", RuntimeError, 500, "message"),
    ("residency", "POST", "/api/residency/preview", {}, S + "residency_arbiter:get_arbiter",
     RuntimeError, 500, "message"),
    ("skills", "GET", "/api/skills", None, "agent_friday.skill_registry:list_skills",
     RuntimeError, 500, "error"),
    ("workflows", "GET", "/api/workflows/overview", None, S + "workflow_overview:overview",
     RuntimeError, 500, "message"),
    ("research", "GET", "/api/privacy/gate", None, S + "judgment_gate:state", RuntimeError, 500, "error"),
    ("work_plan", "POST", "/api/work/proposals", {"tasks": [{"title": "x"}]},
     R + "work_plan:wp.build", RuntimeError, 400, "error"),
    ("goals", "POST", "/api/goals", {"title": "a goal"}, R + "goals:_goals.create_goal",
     RuntimeError, 400, "error"),
    ("creations", "POST", "/api/create/text", {"prompt": "a line"},
     S + "model_router:_generate_text", RuntimeError, 200, "message"),
    # A plain ValueError is not trusted, even on a 400/404 path.
    ("platform", "GET", "/api/distros/anything", None, R + "platform:distributions.load_distro",
     ValueError, 404, "error"),
    ("control", "POST", "/api/control/app-grants", {"app": "notepad.exe", "tier": "act"},
     S + "desktop_grants:set_grant", ValueError, 400, "error"),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_a_failing_route_says_what_failed_and_hides_how(client, monkeypatch, case):
    _id, method, url, body, target, exc_type, status, key = case
    if _id == "creations":
        from agent_friday.services import demo_mode
        monkeypatch.setattr(demo_mode, "is_demo", lambda: False)
    _patch(monkeypatch, target, _raiser(exc_type))
    resp = _call(client, method, url, body)
    _assert_hidden(resp, url)
    assert resp.status_code == status
    data = resp.get_json()
    assert isinstance(data.get(key), str) and data[key].strip(), "%s lost its %r message" % (url, key)
    assert data.get("error_id") and data["error_id"] in data[key]


# ── Service results: marked exception text is swapped at the boundary ─────────

def _marked():
    try:
        raise RuntimeError(INTERNAL)
    except RuntimeError as e:
        return exception_text(e)


# (id, method, url, body, patch target, service result, expected status, key path)
RESULT_CASES = [
    ("content_pipeline", "GET", "/api/content/posts/p1", None, R + "content_pipeline:store.get_post",
     lambda: {"ok": False, "error": _marked()}, 200, ("error",)),
    ("channels", "GET", "/api/channels", None, R + "channels:manager.status",
     lambda: {"channels": {"telegram": {"last_error": _marked()}}}, 200,
     ("channels", "telegram", "last_error")),
    ("learning", "GET", "/api/learning/state", None, R + "learning:learning_loop.state",
     lambda: {"last_error": _marked()}, 200, ("state", "last_error")),
    ("user_model", "POST", "/api/user-model/forget", {"category": "x"}, R + "user_model:user_model.forget",
     lambda: {"ok": False, "error": _marked()}, 200, ("error",)),
    ("dreaming", "POST", "/api/memory/dream", {}, R + "dreaming:memory_dreaming.dream",
     lambda: {"ok": False, "error": _marked()}, 200, ("error",)),
]


@pytest.mark.parametrize("case", RESULT_CASES, ids=[c[0] for c in RESULT_CASES])
def test_a_service_result_reaches_the_browser_without_its_exception_text(client, monkeypatch, case):
    _id, method, url, body, target, result, status, path = case
    _patch(monkeypatch, target, lambda *_a, **_k: result())
    resp = _call(client, method, url, body)
    _assert_hidden(resp, url)
    assert resp.status_code == status
    data = resp.get_json()
    value = data
    for k in path:
        value = value[k]
    assert data.get("error_id") and data["error_id"] in value


def test_take_comparison_keeps_the_real_error_for_the_model_and_hides_it_from_the_browser(
        client, monkeypatch):
    from agent_friday.services import model_router, take_comparison

    def _gen_fails(i):
        raise RuntimeError(INTERNAL)

    # The service result the model path reads: the real text.
    res = take_comparison.compare_text("intent", _gen_fails, n=1)
    assert res["takes"][0]["message"] == INTERNAL

    # The same failure through the route: an id, not the text.
    monkeypatch.setattr(model_router, "_generate_text", _raiser())
    resp = client.post("/api/takes/text", json={"prompt": "write a line", "n": 1})
    _assert_hidden(resp, "/api/takes/text")
    assert resp.status_code == 200
    assert resp.get_json()["error_id"]


def test_federation_inbox_does_not_hand_a_peer_the_decrypt_error(client, monkeypatch):
    _patch(monkeypatch, R + "federation:transport.decrypt_message",
           lambda *_a, **_k: {"ok": False, "error": _marked()})
    resp = client.post("/api/federation/inbox", json={"ciphertext": "x"})
    _assert_hidden(resp, "/api/federation/inbox")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "decryption failed"


# ── Messages written for the user still reach the user ───────────────────────

def test_a_user_facing_refusal_is_shown_as_written(client, monkeypatch):
    _patch(monkeypatch, S + "desktop_grants:set_grant",
           _raiser_user("tier must be one of none, observe, act"))
    resp = client.post("/api/control/app-grants", json={"app": "notepad.exe", "tier": "bogus"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "tier must be one of none, observe, act"


def test_an_unknown_distribution_still_says_which(client):
    resp = client.get("/api/distros/no-such-distro-xyz")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Unknown distribution: no-such-distro-xyz"


def _raiser_user(msg):
    def _boom(*_a, **_k):
        raise UserFacingValueError(msg)
    return _boom
