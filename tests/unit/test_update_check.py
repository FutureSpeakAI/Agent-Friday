"""The weekly "is there a newer Friday?" check, and the promises it makes.

Friday ships no telemetry. That is a product value with a test behind it
(tests/unit/test_no_vendored_telemetry.py), and it constrains this feature
completely: the update check is a client-initiated GET of a PUBLIC GitHub
endpoint and nothing else. It sends no install identifier, no usage data, no
version string, and no User-Agent that says more than an ordinary HTTP client
already says by existing.

The load-bearing test in this file is
`test_outbound_request_carries_nothing_identifying`. It reconstructs the whole
outbound request — method, URL, query string, headers, body — and scans it for
anything that could tie the request to this machine or this install. Directly
below it, `test_the_identity_scanner_actually_fires` feeds that same scanner a
request that DOES leak, and fails if the scanner shrugs. A guard nobody has
seen return a positive is indistinguishable from a guard that returns nothing.
"""
from __future__ import annotations

import getpass
import json
import platform
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agent_friday.services import update_check as uc  # noqa: E402


# ── Fixtures / helpers ───────────────────────────────────────────────────────

class _Resp:
    """Enough of requests.Response for this module."""

    def __init__(self, payload, status=200, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _release(tag, *, draft=False, prerelease=False, published="2026-08-01T00:00:00Z"):
    return {
        "tag_name": tag,
        "name": f"Agent Friday {tag}",
        "draft": draft,
        "prerelease": prerelease,
        "published_at": published,
        "html_url": f"https://github.com/FutureSpeakAI/Agent-Friday/releases/tag/{tag}",
        "body": "release notes",
    }


@pytest.fixture
def captured(monkeypatch):
    """Capture every outbound GET this module makes, and serve a canned reply."""
    calls = []

    def _fake_get(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return _fake_get.reply

    _fake_get.reply = _Resp([_release("v5.7.0")])
    monkeypatch.setattr(uc.requests, "get", _fake_get)
    return calls, _fake_get


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Every test gets its own update_check.json."""
    monkeypatch.setattr(uc, "STATE_FILE", tmp_path / "update_check.json")
    return tmp_path


@pytest.fixture
def pushed(monkeypatch):
    """Capture notifications instead of writing to ~/.friday/notifications.json."""
    seen = []
    monkeypatch.setattr(uc, "_push_notification", lambda **kw: seen.append(kw))
    return seen


# ═════════════════════════════════════════════════════════════════════════════
#  THE PRIVACY PROMISE
# ═════════════════════════════════════════════════════════════════════════════

def _flatten_request(call) -> str:
    """Everything that would go on the wire, as one searchable blob."""
    kw = call["kwargs"]
    parts = [call["url"]]
    for k in ("params", "headers", "data", "json", "cookies", "auth"):
        v = kw.get(k)
        if v:
            parts.append(f"{k}={v!r}")
    return "\n".join(parts)


def _identity_leaks(blob: str, *, our_version: str) -> list[str]:
    """Return every identifying fragment found in an outbound request blob.

    Each needle is something that would let the far end distinguish THIS
    install from any other, or learn something about the person running it.
    """
    leaks = []
    needles = [
        ("hostname", socket.gethostname()),
        ("username", getpass.getuser()),
        ("home directory", str(Path.home())),
        ("machine node", platform.node()),
        ("our version", our_version),
    ]
    low = blob.lower()
    for label, needle in needles:
        if needle and len(str(needle)) >= 3 and str(needle).lower() in low:
            leaks.append(f"{label}: {needle}")

    # Anything that smells like an install id / auth material.
    for token in ("install_id", "installid", "machine_id", "machineid", "uuid",
                  "client_id", "clientid", "authorization", "cookie",
                  "x-friday", "telemetry", "anonymous_id", "distinct_id"):
        if token in low:
            leaks.append(f"identifier-shaped key: {token}")
    return leaks


def test_outbound_request_carries_nothing_identifying(captured, monkeypatch):
    """The whole point. If this fails, the feature must not ship."""
    calls, _ = captured
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    uc.check_for_update()

    assert len(calls) == 1, "the check must make exactly one request"
    call = calls[0]

    # Public, unauthenticated, no query string at all.
    split = urlsplit(call["url"])
    assert split.scheme == "https"
    assert split.netloc == "api.github.com"
    assert split.query == "", f"the URL carries a query string: {split.query!r}"
    assert not call["kwargs"].get("params"), "no query parameters, ever"

    # No request body, no credentials, no cookies.
    assert not call["kwargs"].get("data")
    assert not call["kwargs"].get("json")
    assert not call["kwargs"].get("auth")
    assert not call["kwargs"].get("cookies")

    # No custom User-Agent. requests' own default ("python-requests/x.y.z") is
    # what an ordinary HTTP client sends; anything we add is a fingerprint we
    # chose to transmit.
    headers = {k.lower(): v for k, v in (call["kwargs"].get("headers") or {}).items()}
    assert "user-agent" not in headers, (
        "a custom User-Agent was set: " + repr(headers.get("user-agent"))
        + "\nA UA carrying our version or a build id is telemetry with extra steps."
    )
    assert "authorization" not in headers
    assert "cookie" not in headers

    leaks = _identity_leaks(_flatten_request(call), our_version="5.7.0")
    assert not leaks, (
        "The update check transmitted something identifying:\n"
        + "\n".join(f"  - {leak}" for leak in leaks)
        + "\n\nThis product's pitch is that the user's data does not leave the\n"
          "machine. A version string in a header is still a version string."
    )


def test_the_identity_scanner_actually_fires():
    """Show the guard above catching a real leak, so a green run means something."""
    leaking = "\n".join([
        "https://api.github.com/repos/x/y/releases?v=5.7.0&install_id=abc123",
        "headers={'User-Agent': 'AgentFriday/5.7.0 (%s)'}" % socket.gethostname(),
    ])
    leaks = _identity_leaks(leaking, our_version="5.7.0")

    assert any("our version" in leak for leak in leaks), "missed the version"
    assert any("hostname" in leak for leak in leaks), "missed the hostname"
    assert any("install_id" in leak for leak in leaks), "missed the install id"

    # ...and stays quiet on a clean request, or it would be switched off.
    clean = "https://api.github.com/repos/FutureSpeakAI/Agent-Friday/releases\nheaders={'Accept': 'application/vnd.github+json'}"
    assert _identity_leaks(clean, our_version="5.7.0") == []


def test_check_never_downloads_or_installs_anything(captured, monkeypatch):
    """Notify only. The user chooses."""
    calls, fake = captured
    fake.reply = _Resp([_release("v9.9.9")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    result = uc.check_for_update()

    assert result["update_available"] is True
    # Exactly one request — the metadata read. No asset fetch followed it.
    assert len(calls) == 1
    assert all("releases" in c["url"] for c in calls)
    assert not any(c["url"].endswith(".zip") for c in calls)

    src = Path(uc.__file__).read_text(encoding="utf-8")
    for forbidden in ("subprocess", "shutil.unpack", "zipfile", "os.startfile",
                      "urlretrieve", "os.execv"):
        assert forbidden not in src, (
            f"update_check.py references {forbidden!r} — this module notifies, "
            "it does not install."
        )


# ═════════════════════════════════════════════════════════════════════════════
#  WHICH RELEASE COUNTS
# ═════════════════════════════════════════════════════════════════════════════

def test_newer_stable_release_is_offered(captured, monkeypatch):
    calls, fake = captured
    fake.reply = _Resp([_release("v5.8.0"), _release("v5.7.0")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    result = uc.check_for_update()

    assert result["update_available"] is True
    assert result["latest_version"] == "5.8.0"
    assert result["latest_url"].endswith("/tag/v5.8.0")


def test_same_version_is_not_an_update(captured, monkeypatch):
    calls, fake = captured
    fake.reply = _Resp([_release("v5.7.0")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    assert uc.check_for_update()["update_available"] is False


def test_older_release_is_not_an_update(captured, monkeypatch):
    """A user running a dev build ahead of the last tag is not "behind"."""
    calls, fake = captured
    fake.reply = _Resp([_release("v5.6.0")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    assert uc.check_for_update()["update_available"] is False


def test_prerelease_is_ignored(captured, monkeypatch):
    """A newer release flagged `prerelease` must not be offered."""
    calls, fake = captured
    fake.reply = _Resp([
        _release("v5.9.0", prerelease=True),
        _release("v5.7.0"),
    ])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    result = uc.check_for_update()

    assert result["update_available"] is False, "a pre-release was offered as stable"
    assert result["latest_version"] == "5.7.0"


def test_prerelease_shaped_tag_is_ignored_even_if_github_says_stable(captured, monkeypatch):
    """Belt and braces: `v6.0.0-rc1` is a pre-release whatever the flag says.

    The flag is set by whoever clicked Publish. The tag is set by the release
    process. Trusting only the flag makes one careless checkbox a shipped
    release-candidate on every desktop.
    """
    calls, fake = captured
    fake.reply = _Resp([
        _release("v6.0.0-rc1", prerelease=False),
        _release("v5.7.0"),
    ])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    assert uc.check_for_update()["update_available"] is False


def test_draft_is_ignored(captured, monkeypatch):
    calls, fake = captured
    fake.reply = _Resp([_release("v5.9.0", draft=True), _release("v5.7.0")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    assert uc.check_for_update()["update_available"] is False


def test_unknown_local_version_offers_nothing(captured, monkeypatch):
    """If we cannot read our own version off disk we do not know we are behind.

    Guessing here is how the manifest bug told people they were current.
    """
    calls, fake = captured
    fake.reply = _Resp([_release("v9.9.9")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: None)

    result = uc.check_for_update()

    assert result["update_available"] is False
    assert result["reason"] == "local_version_unknown"


# ═════════════════════════════════════════════════════════════════════════════
#  FAILING QUIETLY
# ═════════════════════════════════════════════════════════════════════════════

def test_offline_fails_silently(monkeypatch, pushed):
    """A sovereignty tool that nags because the network is down is a bad
    houseguest. No exception, no notification."""
    def _boom(url, **kwargs):
        raise OSError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(uc.requests, "get", _boom)
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    result = uc.run_update_check()          # must not raise

    assert result["ok"] is False
    assert result["reason"] == "unreachable"
    assert pushed == [], "an offline machine was nagged"


def test_rate_limited_fails_silently(monkeypatch, pushed):
    """GitHub answers 403 with a rate-limit body. Not the user's problem."""
    monkeypatch.setattr(
        uc.requests, "get",
        lambda url, **kw: _Resp({"message": "API rate limit exceeded"}, status=403),
    )
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    result = uc.run_update_check()

    assert result["ok"] is False
    assert result["reason"] == "unreachable"
    assert pushed == []


def test_garbage_response_fails_silently(monkeypatch, pushed):
    monkeypatch.setattr(uc.requests, "get", lambda url, **kw: _Resp(None, status=200))
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    assert uc.run_update_check()["ok"] is False
    assert pushed == []


def test_scheduled_task_never_raises(monkeypatch, pushed):
    """The scheduler notifies on EVERY failure regardless of notify mode
    (scheduler.dispatch: "Failures always notify"). So a raising task is a
    weekly "update check failed" popup for anyone whose wifi was off — the
    exact nagging this feature promised not to do. It must swallow everything.
    """
    def _explode(*a, **k):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr(uc, "check_for_update", _explode)

    result = uc.run_update_check()          # must not raise

    assert result["ok"] is False
    assert pushed == []


# ═════════════════════════════════════════════════════════════════════════════
#  NOTIFYING ONCE
# ═════════════════════════════════════════════════════════════════════════════

def test_update_notification_is_pushed_once_per_version(captured, monkeypatch, pushed):
    calls, fake = captured
    fake.reply = _Resp([_release("v5.8.0")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    uc.run_update_check()
    assert len(pushed) == 1
    assert "5.8.0" in pushed[0]["title"]
    # The user chooses: the notification carries a link, not an installer.
    assert pushed[0]["target"]["url"].endswith("/tag/v5.8.0")

    # A second (and third) weekly run must stay quiet about the same release,
    # including after the user dismissed it.
    uc._force_next_check()
    uc.run_update_check()
    uc._force_next_check()
    uc.run_update_check()
    assert len(pushed) == 1, "the same version was announced more than once"


def test_a_newer_version_notifies_again(captured, monkeypatch, pushed):
    calls, fake = captured
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    fake.reply = _Resp([_release("v5.8.0")])
    uc.run_update_check()

    fake.reply = _Resp([_release("v5.9.0")])
    uc._force_next_check()
    uc.run_update_check()

    assert len(pushed) == 2
    assert "5.9.0" in pushed[1]["title"]


def test_up_to_date_pushes_nothing(captured, monkeypatch, pushed):
    calls, fake = captured
    fake.reply = _Resp([_release("v5.7.0")])
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    uc.run_update_check()

    assert pushed == [], "being up to date is not news"


# ═════════════════════════════════════════════════════════════════════════════
#  CADENCE
# ═════════════════════════════════════════════════════════════════════════════

def test_a_successful_check_is_not_repeated_within_the_week(captured, monkeypatch):
    calls, fake = captured
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    uc.run_update_check()
    uc.run_update_check()
    uc.run_update_check()

    assert len(calls) == 1, "the ticker hit the network more than once a week"


def test_a_failed_check_retries_at_the_next_tick(monkeypatch):
    """A laptop whose wifi was not up at boot must not wait seven days.

    The schedule is a ticker; the WEEK is enforced here, and only for checks
    that actually succeeded.
    """
    attempts = []

    def _boom(url, **kwargs):
        attempts.append(url)
        raise OSError("offline")

    monkeypatch.setattr(uc.requests, "get", _boom)
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    uc.run_update_check()
    uc.run_update_check()

    assert len(attempts) == 2, "a failed check blocked the retry for a week"


def test_state_survives_a_restart(captured, monkeypatch):
    calls, fake = captured
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    uc.run_update_check()
    state = uc.read_state()

    assert state["last_checked_at"]
    assert state["last_result"] == "ok"
    assert json.loads(uc.STATE_FILE.read_text(encoding="utf-8"))["last_result"] == "ok"


# ═════════════════════════════════════════════════════════════════════════════
#  ROUTING MODES
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode", ["cloud_only", "local_only", "hybrid", ""])
def test_routing_mode_does_not_gate_the_update_check(captured, monkeypatch, mode):
    """This is an HTTP GET of a public page, not a model call.

    `model_router._filter_attempts_for_mode` bans CLOUD legs under local_only
    and LOCAL legs under cloud_only. That machinery is about provider ladders
    and must not reach a GitHub metadata read — a user who routes every TOKEN
    locally has not asked to be kept ignorant of a security fix.

    The mode is written to the REAL settings.json (conftest redirects home to a
    temp dir) rather than stubbed, so this exercises whatever the settings layer
    actually does — including the offline overlay, which forces local routing.
    """
    from agent_friday import core

    calls, fake = captured
    monkeypatch.setattr(uc, "running_version", lambda *a, **k: "5.7.0")

    settings_file = core.SETTINGS_FILE
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    original = settings_file.read_text(encoding="utf-8") if settings_file.exists() else None
    try:
        existing = json.loads(original) if original else {}
        existing["model_routing"] = {"mode": mode}
        settings_file.write_text(json.dumps(existing), encoding="utf-8")
        core._invalidate_settings_cache()

        assert (core._load_settings().get("model_routing") or {}).get("mode") == mode, (
            "the test did not actually put the app into this routing mode"
        )

        result = uc.check_for_update()
    finally:
        if original is None:
            settings_file.unlink(missing_ok=True)
        else:
            settings_file.write_text(original, encoding="utf-8")
        core._invalidate_settings_cache()

    assert len(calls) == 1, f"mode={mode!r} suppressed the check"
    assert result["reason"] != "blocked"


def test_update_check_never_reads_the_routing_mode_at_all():
    """Structural companion to the test above.

    The behavioural test proves the check still fires today. This one prevents
    someone adding a well-meaning `if mode == "local_only": return` later — the
    kind of change that looks like privacy and is actually just a user who
    stops being told about security fixes.
    """
    src = Path(uc.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )
    for token in ("local_only", "cloud_only", "model_routing", "_load_settings"):
        assert token not in code, (
            f"update_check.py consults {token!r} outside a comment"
        )


def test_update_check_does_not_touch_the_model_egress_gate(monkeypatch):
    """seal_outbound()/gate_text() exist to redact USER text bound for a model
    provider. Routing a request that contains no user text through them would
    be theatre, and would couple this feature to the vault's state."""
    src = Path(uc.__file__).read_text(encoding="utf-8")
    for gated in ("seal_outbound", "gate_text", "gate_worker_payload"):
        assert gated not in src


# ═════════════════════════════════════════════════════════════════════════════
#  ON BY DEFAULT, AND VISIBLE
# ═════════════════════════════════════════════════════════════════════════════

def test_scheduler_registers_the_check_enabled_by_default():
    from agent_friday.services import scheduler as sched

    sched._register_default_builtin_tasks()

    assert uc.SCHEDULE_REF in sched.BUILTIN_TASKS, (
        "the update check is not in the built-in roster, so no install gets it"
    )
    meta = sched.BUILTIN_TASKS[uc.SCHEDULE_REF]
    assert meta["default_enabled"] is True, "Stephen asked for on by default"
    assert meta["notify"] == "silent", (
        "a non-silent schedule pushes 'Update check — complete' every week, and "
        "pushes a failure notification every time the network is down"
    )
    assert meta["default_trigger"] == "interval"


def test_seeding_gives_the_schedule_a_stable_id():
    """The Settings toggle addresses the schedule by id. If the id moves, the
    toggle silently stops controlling anything."""
    from agent_friday.services import scheduler as sched

    assert uc.SCHEDULE_ID == f"sch_{uc.SCHEDULE_REF}"
    sched._register_default_builtin_tasks()
    assert uc.SCHEDULE_REF in sched.BUILTIN_TASKS


def test_a_fresh_install_actually_gets_an_enabled_schedule(tmp_path, monkeypatch):
    """Registration is not delivery.

    `BUILTIN_TASKS` only says the task EXISTS. What reaches a user's machine is
    whatever `_seed_and_reconcile` writes into schedules.json, and that is what
    the Settings toggle reads and what the tick loop dispatches. Asserting the
    roster and stopping there would pass while every install got nothing.
    """
    from agent_friday.services import scheduler as sched

    store = tmp_path / "schedules.json"
    monkeypatch.setattr(sched, "SCHEDULES_FILE", store)

    sched._register_default_builtin_tasks()
    sched._seed_and_reconcile()

    seeded = {r["id"]: r for r in json.loads(store.read_text(encoding="utf-8"))}
    assert uc.SCHEDULE_ID in seeded, (
        "a fresh install seeds no update-check schedule, so the weekly check "
        "never runs and the Settings toggle controls nothing"
    )
    rec = seeded[uc.SCHEDULE_ID]
    assert rec["enabled"] is True, "Stephen asked for on by default"
    assert rec["trigger"] == "interval"
    assert rec["spec"]["every_minutes"] == uc.TICK_MINUTES
    assert rec["notify"] == "silent"
    assert rec["task"] == {"kind": "builtin", "ref": uc.SCHEDULE_REF}


def test_an_existing_install_is_opted_in_on_upgrade(tmp_path, monkeypatch):
    """THE DECISION MADE ON STEPHEN'S BEHALF, pinned so it cannot drift silently.

    Stephen asked for on-by-default for NEW installations. `_seed_and_reconcile`
    also adds newly-registered built-ins to installs that predate them, so
    people who already installed get it switched on too. Nobody asked for that.

    It is documented in CHANGELOG.md under a heading inviting him to overrule
    it. If the answer becomes "off for existing installs", this test is where
    that decision gets recorded — flip the assertion and pass
    default_enabled=False through a seeding path that can tell the two apart.
    """
    from agent_friday.services import scheduler as sched

    store = tmp_path / "schedules.json"
    monkeypatch.setattr(sched, "SCHEDULES_FILE", store)

    # An install that predates the update check: it already has other builtins.
    store.write_text(json.dumps([{
        "id": "sch_daily_creation",
        "name": "Daily creation",
        "trigger": "daily",
        "spec": {"hour": 8, "minute": 0},
        "task": {"kind": "builtin", "ref": "daily_creation"},
        "enabled": True,
        "notify": "on_complete",
    }]), encoding="utf-8")

    sched._register_default_builtin_tasks()
    sched._seed_and_reconcile()

    seeded = {r["id"]: r for r in json.loads(store.read_text(encoding="utf-8"))}
    assert "sch_daily_creation" in seeded, "reconcile clobbered an existing schedule"
    assert uc.SCHEDULE_ID in seeded
    assert seeded[uc.SCHEDULE_ID]["enabled"] is True, (
        "This is the flagged decision. If it is reversed, reverse it HERE and "
        "in CHANGELOG.md, not by quietly editing the seeding default."
    )

if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
