"""Guard: no vendored or bundled dependency may phone home.

WHY THIS EXISTS. Until v3.1.1 this repo vendored `tools/browser-use`, which
carried upstream's own PostHog project key and shipped telemetry that was ON by
default. It was not anonymous counters: `AgentTelemetryEvent` carried `task`
(the user's literal prompt), `urls_visited`, `final_result_response` and
`error_message`, with `enable_exception_autocapture=True` and geo-IP left
enabled. For a product whose entire pitch is that the user's data does not
leave the machine, that is the worst possible dependency to carry silently.

It was removed in v3.1.1 as part of a repo cleanup — incidentally, not because
anyone had noticed the telemetry. That is the failure this file is aimed at.
The key survives in eight public tags (v1.2.0 .. v3.1.0) and nothing structural
stopped an equivalent package being re-vendored tomorrow.

WHY A TEST AND NOT AN ENVIRONMENT VARIABLE. `ANONYMIZED_TELEMETRY=False` would
have silenced browser-use, and that is precisely the shape of defect this
codebase spent 24-25 August removing: a protection that holds only while
someone remembers to set it, on every launch path, in every future version.
Configuration can be unset, missed by one launcher, or ignored by an upstream
release. A test that fails on the *presence of the key or endpoint* cannot be
unset, and it fails at commit time rather than on a user's machine.

This is the same discipline as scripts/check_gated_prompt_callers.py: make the
unsafe thing impossible to add quietly, rather than trusting a future reader to
remember the rule.

SCOPE. Tracked files only (`git ls-files`) — that is what a re-vendor would
add, and it keeps the test independent of whatever is lying around a working
tree or installed in a venv. Third-party telemetry inside *installed* packages
is a different problem with a different fix; see docs/audits/ for the
dependency sweep.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent

# Each entry: (label, compiled pattern, what it would mean if it appeared).
# Patterns match the CREDENTIAL or the ENDPOINT, not the word "telemetry" —
# a vendored library that merely mentions telemetry in a docstring is not a
# finding, and a test that fired on the word would be turned off within a week.
TELEMETRY_SIGNATURES = [
    (
        "posthog_project_key",
        re.compile(r"phc_[A-Za-z0-9]{20,}"),
        "a PostHog project write key — this is what browser-use carried",
    ),
    (
        "posthog_endpoint",
        re.compile(r"\b(?:eu|us)\.i\.posthog\.com|\bapp\.posthog\.com"),
        "a PostHog ingest host",
    ),
    (
        "sentry_dsn",
        re.compile(r"https://[0-9a-f]{16,}@[\w.\-]*ingest[\w.\-]*\.sentry\.io"),
        "a Sentry DSN — crash reports carry stack traces and local variables",
    ),
    (
        "segment_endpoint",
        re.compile(r"\bapi\.segment\.io\b"),
        "a Segment analytics ingest host",
    ),
    (
        "mixpanel_endpoint",
        re.compile(r"\bapi\.mixpanel\.com\b"),
        "a Mixpanel analytics ingest host",
    ),
    (
        "amplitude_endpoint",
        re.compile(r"\bapi(?:2)?\.amplitude\.com\b"),
        "an Amplitude analytics ingest host",
    ),
    (
        "google_analytics",
        re.compile(r"\b(?:www\.)?google-analytics\.com\b|\banalytics\.google\.com\b"),
        "a Google Analytics endpoint",
    ),
    (
        "wandb_endpoint",
        re.compile(r"\bapi\.wandb\.ai\b"),
        "a Weights & Biases ingest host",
    ),
]

# Files permitted to contain the literals above, with the reason. Keep this
# list SHORT and justified: every entry is a place the guard cannot see.
#
# It is EMPTY, and that is the point. The obvious way to write this test was to
# paste the real browser-use key in as a sample and allowlist this file -- but
# then the guard cannot scan itself, and the tree still contains a telemetry
# key. The repo's own pre-commit secret scanner rejected that first draft,
# correctly. The sample is therefore assembled at runtime from fragments that
# match nothing, so no telemetry literal exists anywhere in the tree and this
# file is scanned like every other.
ALLOWED: set[str] = set()

# Binary-ish things we cannot meaningfully grep.
SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".ogg", ".webm",
    ".zip", ".gz", ".exe", ".dll", ".pyd", ".so", ".bin",
    ".gguf", ".safetensors", ".onnx", ".pdf",
)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode("utf-8", "replace")
    return [p for p in out.split("\0") if p]


def scan_text(text: str) -> list[tuple[str, str]]:
    """Return [(label, matched_fragment)] for every telemetry signature found.

    Factored out so the guard itself can be tested against a known-bad sample.
    A scanner nobody has ever seen return a positive is indistinguishable from
    a scanner that returns nothing.
    """
    found = []
    for label, pattern, _meaning in TELEMETRY_SIGNATURES:
        m = pattern.search(text)
        if m:
            found.append((label, m.group(0)))
    return found


def test_scanner_actually_detects_the_thing_it_looks_for():
    """The guard must be shown to fire. This is the whole point.

    The sample has the exact SHAPE of what shipped in this repo until v3.1.1 --
    `git show v3.1.0:tools/browser-use/browser_use/telemetry/service.py` is the
    real thing -- but is assembled from fragments at runtime, so this file does
    not itself become the telemetry literal it forbids.
    """
    fake_key = "phc_" + "A1b2C3d4E5f6G7h8J9k0" * 2      # shape, not the key
    fake_host = "eu.i." + "posthog" + ".com"
    sample = (
        f"PROJECT_API_KEY = '{fake_key}'\n"
        f"HOST = 'https://{fake_host}'\n"
    )
    hits = dict(scan_text(sample))
    assert "posthog_project_key" in hits, "scanner missed a live PostHog key"
    assert "posthog_endpoint" in hits, "scanner missed a PostHog ingest host"

    # ...and does not fire on innocuous prose, or it would be disabled by
    # whoever next has to read its output.
    assert scan_text("We deliberately ship no telemetry. See THREAT_MODEL.md.") == []


def test_no_telemetry_keys_or_endpoints_in_tracked_files():
    """No tracked file may carry an analytics key or ingest endpoint."""
    offenders: list[str] = []

    for rel in _tracked_files():
        if rel in ALLOWED or rel.lower().endswith(SKIP_SUFFIXES):
            continue
        path = REPO / rel
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for label, fragment in scan_text(text):
            meaning = next(m for lb, _p, m in TELEMETRY_SIGNATURES if lb == label)
            offenders.append(f"{rel}: {label} ({meaning})\n      -> {fragment}")

    assert not offenders, (
        "Telemetry credential or endpoint found in tracked files.\n\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\n\nA dependency that phones home does not belong in a sovereignty\n"
          "product. Strip it, or — if it is genuinely required and genuinely\n"
          "inert — add the path to ALLOWED in this file with a reason, so the\n"
          "exception is visible in review instead of silent."
    )


def test_shipped_requirement_tiers_declare_no_telemetry_packages():
    """The Windows installer's tiers must not pull a telemetry SDK.

    Catches the other direction: not a vendored copy, but a line added to a
    requirements file that drags an analytics client onto a user's machine.
    `wandb` is here because it appeared in this project's own dev venv (it
    arrives with the Tier-2 NeMo voice extra) and pulls `sentry-sdk` with it —
    dev-only today, one requirements edit away from shipping.
    """
    banned = {
        "posthog": "PostHog analytics client",
        "sentry-sdk": "Sentry crash reporting",
        "analytics-python": "Segment analytics client",
        "mixpanel": "Mixpanel analytics client",
        "amplitude-analytics": "Amplitude analytics client",
        "wandb": "Weights & Biases — phones home, and pulls sentry-sdk",
    }

    req_dir = REPO / "packaging" / "windows" / "requirements"
    if not req_dir.is_dir():
        pytest.skip("windows installer requirement tiers not present")

    offenders = []
    for tier in sorted(req_dir.glob("*.txt")):
        for lineno, raw in enumerate(
            tier.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!\[;\s]", line)[0].strip().lower()
            if name in banned:
                offenders.append(
                    f"{tier.name}:{lineno}: {name} — {banned[name]}"
                )

    assert not offenders, (
        "A shipped installer tier declares a telemetry package:\n\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
