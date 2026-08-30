"""Weekly "is there a newer Agent Friday?" check. Notify only.

WHAT THIS IS
    One unauthenticated HTTPS GET of a PUBLIC GitHub endpoint, at most once a
    week, whose entire result is a notification the user can dismiss. If a
    newer stable release exists, Friday says so and links to it. The user
    downloads it, or does not.

WHAT THIS IS NOT
    It is not telemetry. Nothing about this install goes out: no install id, no
    usage data, no version string in a query parameter, and no User-Agent
    beyond whatever `requests` sends by existing. The request is
    indistinguishable from any other client asking GitHub what it published.
    That is a product value with a test behind it — see
    tests/unit/test_update_check.py::test_outbound_request_carries_nothing_identifying,
    which reconstructs the whole outbound request and scans it for anything
    identifying, and the test directly below it which proves that scanner fires.

    It is also not a downloader. It never fetches an asset, never unpacks
    anything, never runs an installer. The notification carries a LINK.

    And it is not a fourth update path. `install.ps1` is how Friday updates;
    `friday status` is where the version truth lives. This module consumes
    `services.app_version` — the same function `friday status` consumes — and
    adds no version detection of its own.

WHY IT READS DISK AND NOT THE MANIFEST
    See services/app_version.py. `install-manifest.json` claimed 5.6.4 on
    installs whose files were 5.6.3. A checker that trusted it would tell those
    users they were current, which is worse than no checker at all.

WHY IT FAILS SILENTLY
    Offline, DNS down, GitHub 503, rate-limited: none of these are the user's
    problem and none of them are news. They are logged and dropped. A
    sovereignty tool that nags because the network is down is a bad houseguest.

    This matters more than it looks. `scheduler.dispatch` notifies on EVERY
    failure regardless of the schedule's notify mode ("Failures always notify"),
    so a task that RAISES here would put "Update check failed" on screen every
    week for anyone whose wifi was off. `run_update_check` therefore swallows
    everything and always returns normally.

CADENCE
    The schedule is a six-hour ticker; the WEEK is enforced in this module, and
    only against checks that actually SUCCEEDED. A weekly-triggered schedule
    would fire at one instant per week and a laptop that was asleep — or whose
    network was not up yet at boot — would silently skip the whole week. So a
    failed check simply retries at the next tick.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Optional

import requests

from agent_friday.core import FRIDAY_DIR
from agent_friday.services.app_version import running_version, version_truth

_log = logging.getLogger("friday.update_check")

# ── The endpoint ─────────────────────────────────────────────────────────────
# One constant for the repository, matching cli.REPO_URL. `friday update` used
# to print `FutureSpeakAI/friday-desktop`, which 404s.
REPO_OWNER = "FutureSpeakAI"
REPO_NAME = "Agent-Friday"
RELEASES_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
RELEASES_PAGE = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/latest"

# The LIST endpoint, not /releases/latest, and deliberately with NO query
# string. /releases/latest would have GitHub apply the "newest stable" rule
# server-side, which is convenient and invisible: nothing local could be tested,
# and a change at GitHub would change what desktops offer. Reading the list and
# filtering here means the draft/pre-release rule is code in this repo with
# tests against it. The default page size is fine; `?per_page=` would put a
# query string on a request whose emptiness is the point.

REQUEST_TIMEOUT = 10        # seconds; a slow GitHub must not hold a worker

# Accept only. No User-Agent (requests' own default is what an ordinary HTTP
# client sends — anything we add is a fingerprint we chose to transmit), no
# Authorization, no cookies, no custom X- headers.
_HEADERS = {"Accept": "application/vnd.github+json"}

# ── State ────────────────────────────────────────────────────────────────────
STATE_FILE = FRIDAY_DIR / "update_check.json"
CHECK_INTERVAL_SECONDS = 7 * 24 * 3600      # the actual weekly cadence
_STATE_LOCK = threading.Lock()

# ── Scheduler wiring ─────────────────────────────────────────────────────────
SCHEDULE_REF = "update_check"
SCHEDULE_ID = f"sch_{SCHEDULE_REF}"         # scheduler._seed_and_reconcile: sch_<ref>
SCHEDULE_LABEL = "Update check (weekly)"
TICK_MINUTES = 360                          # 6h ticker; the week is enforced here


# ═════════════════════════════════════════════════════════════════════════════
#  Version comparison
# ═════════════════════════════════════════════════════════════════════════════

_PRERELEASE_TAG = re.compile(
    r"[-_.]?(rc|alpha|beta|dev|pre|preview|nightly|snapshot)", re.IGNORECASE
)


def _parse_version(raw: str) -> Optional[tuple]:
    """`v5.7.0` / `5.7.0` -> (5, 7, 0). None when it is not a version at all."""
    if not raw:
        return None
    text = str(raw).strip().lstrip("vV")
    text = re.split(r"[-+]", text, maxsplit=1)[0]
    parts = text.split(".")
    if not parts or not parts[0]:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _is_newer(candidate: str, current: str) -> bool:
    a, b = _parse_version(candidate), _parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _looks_like_a_prerelease(release: dict) -> bool:
    """Belt and braces.

    `prerelease` is a checkbox somebody ticks at publish time. `v6.0.0-rc1` is a
    release candidate whether or not anybody remembered. Trusting only the flag
    makes one careless click a shipped RC on every desktop, so the tag shape is
    checked too.
    """
    if release.get("prerelease") or release.get("draft"):
        return True
    tag = str(release.get("tag_name") or "")
    suffix = re.split(r"[-+]", tag.lstrip("vV"), maxsplit=1)
    return len(suffix) > 1 and bool(_PRERELEASE_TAG.match("-" + suffix[1]))


def _newest_stable(releases: list) -> Optional[dict]:
    best, best_v = None, None
    for r in releases:
        if not isinstance(r, dict) or _looks_like_a_prerelease(r):
            continue
        v = _parse_version(r.get("tag_name") or "")
        if v is None:
            continue
        if best_v is None or v > best_v:
            best, best_v = r, v
    return best


# ═════════════════════════════════════════════════════════════════════════════
#  State
# ═════════════════════════════════════════════════════════════════════════════

def read_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        _log.info("update check: could not persist state (%s)", e)


def _force_next_check() -> None:
    """Testing/manual seam: forget that we checked recently."""
    with _STATE_LOCK:
        state = read_state()
        state.pop("last_success_ts", None)
        _write_state(state)


def _due(state: dict, now: float) -> bool:
    last = state.get("last_success_ts") or 0
    try:
        last = float(last)
    except (TypeError, ValueError):
        last = 0
    return (now - last) >= CHECK_INTERVAL_SECONDS


# ═════════════════════════════════════════════════════════════════════════════
#  The check
# ═════════════════════════════════════════════════════════════════════════════

def check_for_update() -> dict:
    """Ask GitHub what the newest stable release is. Never raises for network.

    Returns a dict with `ok`, `update_available`, `latest_version`,
    `latest_url`, `current_version`, `reason`.
    """
    current = running_version()
    result = {
        "ok": False,
        "update_available": False,
        "current_version": current,
        "latest_version": None,
        "latest_url": None,
        "reason": "",
    }

    # Settings are deliberately NOT consulted here, and the absence is the
    # point. `model_routing.mode` (local_only / cloud_only)
    # decides where TOKENS go; it is enforced in model_router's provider ladder
    # (_filter_attempts_for_mode) and has nothing to say about reading a public
    # web page that contains none of the user's text. Someone who routes every
    # token locally has not asked to be kept ignorant of a security fix.
    # (Tested: test_routing_mode_does_not_gate_the_update_check, which writes a
    # real settings.json rather than stubbing the reader.)

    try:
        resp = requests.get(RELEASES_API, timeout=REQUEST_TIMEOUT, headers=_HEADERS)
    except Exception as e:                                  # noqa: BLE001
        # Offline, DNS failure, TLS problem, timeout. Not news.
        _log.info("update check: GitHub unreachable (%s: %s)", type(e).__name__, e)
        result["reason"] = "unreachable"
        return result

    status = getattr(resp, "status_code", 0)
    if status != 200:
        # 403/429 is the rate limit; 5xx is GitHub's bad day. Same treatment.
        _log.info("update check: GitHub answered HTTP %s", status)
        result["reason"] = "unreachable"
        return result

    try:
        releases = resp.json()
    except Exception as e:                                  # noqa: BLE001
        _log.info("update check: unparseable response (%s)", e)
        result["reason"] = "unreachable"
        return result

    if not isinstance(releases, list):
        _log.info("update check: unexpected response shape %s", type(releases).__name__)
        result["reason"] = "unreachable"
        return result

    newest = _newest_stable(releases)
    if newest is None:
        result["ok"] = True
        result["reason"] = "no_stable_release"
        return result

    latest = str(newest.get("tag_name") or "").lstrip("vV")
    result["ok"] = True
    result["latest_version"] = latest
    result["latest_url"] = newest.get("html_url") or RELEASES_PAGE

    if not current:
        # We could not read our own version off disk. Do not guess we are
        # behind, and do not guess we are current — guessing is the manifest
        # bug. Say so and let `friday status` explain.
        result["reason"] = "local_version_unknown"
        return result

    if _is_newer(latest, current):
        result["update_available"] = True
        result["reason"] = "update_available"
    else:
        result["reason"] = "up_to_date"
    return result


# ═════════════════════════════════════════════════════════════════════════════
#  Notification
# ═════════════════════════════════════════════════════════════════════════════

def _push_notification(**kwargs) -> None:
    """Seam over notifications_engine.push so tests can watch it."""
    try:
        from agent_friday import notifications_engine as _ne
        _ne.push(**kwargs)
    except Exception as e:                                  # noqa: BLE001
        _log.info("update check: could not push notification (%s)", e)


def _announce(result: dict) -> None:
    version = result["latest_version"]
    truth = version_truth()
    body = (
        f"You are running {result['current_version']}. "
        f"Agent Friday {version} is available on GitHub.\n\n"
        "Friday will not download or install anything by itself — open the "
        "release page when you are ready, unzip it, and run "
        "“Install Agent Friday.cmd”. Your notes, settings and "
        "connected accounts are kept."
    )
    if truth.get("disagreement"):
        body += "\n\nAlso worth knowing: " + truth["disagreement"]

    _push_notification(
        title=f"Agent Friday {version} is available",
        body=body,
        priority="low",
        source="update_check",
        kind="update",
        # One per release. The engine drops a duplicate while the previous one
        # is unread; `notified_version` below covers the dismissed case.
        dedupe_key=f"update-available-{version}",
        target={"url": result["latest_url"]},
        meta={
            "current_version": result["current_version"],
            "latest_version": version,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
#  The scheduled task
# ═════════════════════════════════════════════════════════════════════════════

def run_update_check(force: bool = False) -> dict:
    """The scheduler's entry point. NEVER raises, NEVER downloads.

    Nothing in here is allowed to escape. `scheduler.dispatch` pushes a
    notification on every failed run regardless of notify mode, so an exception
    from this function is a weekly popup for every user with flaky wifi.
    """
    try:
        now = time.time()
        with _STATE_LOCK:
            state = read_state()
            if not force and not _due(state, now):
                return {"ok": True, "skipped": True,
                        "reason": "checked_within_the_week"}

        result = check_for_update()

        with _STATE_LOCK:
            state = read_state()
            state["last_checked_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            state["last_result"] = "ok" if result["ok"] else result["reason"]
            state["current_version"] = result["current_version"]
            if result["ok"]:
                # Only a SUCCESSFUL check moves the week forward. A failed one
                # retries at the next six-hour tick, so a laptop that was
                # offline at boot does not lose the whole week.
                state["last_success_ts"] = now
                state["latest_version"] = result["latest_version"]
                state["latest_url"] = result["latest_url"]
                state["update_available"] = result["update_available"]

            already = state.get("notified_version")
            should_announce = bool(
                result["update_available"]
                and result["latest_version"]
                and result["latest_version"] != already
            )
            if should_announce:
                # Recorded BEFORE the push, and inside the lock: two ticks
                # racing must not produce two notifications for one release.
                state["notified_version"] = result["latest_version"]
            _write_state(state)

        if should_announce:
            _announce(result)

        return {"ok": result["ok"], "skipped": False,
                "reason": result["reason"],
                "update_available": result["update_available"],
                "latest_version": result["latest_version"],
                "notified": should_announce}

    except Exception as e:                                  # noqa: BLE001
        # The last line of defence. Whatever went wrong, the user hears nothing.
        _log.info("update check: giving up quietly (%s: %s)", type(e).__name__, e)
        return {"ok": False, "skipped": False, "reason": "error"}


def status() -> dict[str, Any]:
    """What the Settings panel and `friday status` show. Reads state only —
    never touches the network, so opening Settings is not a GitHub request."""
    state = read_state()
    truth = version_truth()
    return {
        "current_version": truth["running"],
        "version_truth": truth,
        "last_checked_at": state.get("last_checked_at"),
        "last_result": state.get("last_result"),
        "latest_version": state.get("latest_version"),
        "latest_url": state.get("latest_url") or RELEASES_PAGE,
        "update_available": bool(state.get("update_available")),
        "releases_page": RELEASES_PAGE,
        "schedule_id": SCHEDULE_ID,
        "enabled": is_enabled(),
    }


def is_enabled() -> bool:
    """Whether the weekly check is switched on.

    Read from the SCHEDULE record, which is the only place the answer lives.
    A missing record means the roster has not been seeded yet on this install;
    the default is on, so say on rather than reporting a switch the user never
    touched as off.
    """
    try:
        from agent_friday.services.scheduler import get_schedule
        rec = get_schedule(SCHEDULE_ID)
        if rec is None:
            return True
        return bool(rec.get("enabled", True))
    except Exception:
        return True
