"""Kimi K3 is listed honestly and can never be selected.

The engine (https://github.com/FareedKhan-dev/kimi-k3-in-c) runs a 2.78T-param
model CPU-only by streaming a 1.56 TB checkpoint from disk. Three facts decide
how it may appear in Friday, and all three come from the project README read on
2026-09-22:

  * "Version one is text-only: there are no tools, images, server, or context
    compaction." No tool calling and no HTTP server.
  * Storage: "~1.7 TB free | 1.56 TB checkpoint + 109 GB packed trunk".
  * 26.5 s/token at 8 GB RAM, 5.6 s/token at 128 GB+.

So it is a row you can read, never a seat anything can be routed to. A model
that cannot call tools and answers at seconds-per-token must not be reachable
as a fallback, because a fallback is chosen by Friday rather than by the user.
"""

import pytest

from agent_friday.services import kimi_k3 as k3
from agent_friday.services import model_catalog as mc


@pytest.fixture
def row():
    cat = mc.build_catalog()
    r = next((m for m in cat["models"] if m["id"] == k3.MODEL_ID), None)
    assert r is not None, "Kimi K3 is missing from the catalog entirely"
    return r


@pytest.fixture
def catalog():
    return mc.build_catalog()


# ── it is listed, and listed honestly ───────────────────────────────────────

def test_the_published_requirement_figures_are_what_we_report(row):
    """Quoted, not estimated. A wrong disk figure here is the difference
    between "cannot run on this machine" and a 1.7 TB download that stalls."""
    req = row["requirements"]
    assert req["disk_gb"] == pytest.approx(1700.0)
    assert req["checkpoint_gb"] == pytest.approx(1560.0)
    assert req["trunk_gb"] == pytest.approx(109.0)
    assert req["ram_gb_min"] == pytest.approx(8.2)
    assert req["seconds_per_token"]["8 GB"] == pytest.approx(26.5)
    assert req["seconds_per_token"]["128 GB+"] == pytest.approx(5.6)
    assert "AVX2" in req["cpu"] and "FMA" in req["cpu"]


def test_it_says_it_cannot_call_tools_or_serve(row):
    """Both are explicit in the README's own sentence. A row that implied
    either would invite exactly the use that cannot work."""
    caps = row["capabilities"]
    assert caps["tools"] is False
    assert caps["server"] is False
    assert caps["vision"] is False
    assert "tools" not in row["modalities"]
    assert row["modalities"] == ["text"]


def test_it_is_chat_capable_because_the_readme_says_so(row):
    """Worth pinning precisely because the obvious reading is wrong. The
    checkpoint is not base-only: "The official Kimi K3 checkpoint is also
    chat-capable", via --chat and the official XTML control tokens. Raw
    continuation is what you get WITHOUT --chat."""
    caps = row["capabilities"]
    assert caps["chat"] is True
    assert caps["raw_completion"] is True


def test_the_weights_licence_is_not_the_engine_licence(row):
    """The engine is Apache-2.0; the weights are Moonshot AI's own licence and
    the repo "grants no rights to them". Conflating the two is how a
    non-commercial weight ends up shipped."""
    note = (row["requirements"]["weights_license"] or "").lower()
    assert "moonshot" in note
    assert "apache" in note, "the row must say which licence covers what"


def test_it_is_marked_experimental_and_not_installed(row):
    assert row["experimental"] is True
    assert row["available"] is False, "nothing is installed; do not claim it is"
    assert row["hint"], "an unavailable row must say why"


def test_it_is_local_not_cloud(row):
    """It is a subprocess on this machine: no network transport exists at all.
    Badging it `cloud` would be backwards, and would imply an egress question
    that does not arise."""
    assert row["local"] is True
    assert row["classification"] == "local"
    assert row["cost_per_1k"] == 0.0


# ── it can never be selected ────────────────────────────────────────────────

def test_it_appears_in_no_role_picker(catalog):
    for role, entries in catalog["roles"].items():
        ids = [e["id"] for e in entries]
        assert k3.MODEL_ID not in ids, "Kimi K3 is offered as a %s seat" % role


def test_roles_is_empty_and_it_is_not_curated(row):
    """The two properties `build_catalog` actually keys on — it files an entry
    under a role only when the entry is curated AND names that role. Asserting
    the absence above without these would pass for the wrong reason the moment
    someone made it curated."""
    assert row["roles"] == []
    assert row["curated"] is False


def test_it_is_not_in_the_router_fallback_chain():
    """A fallback is Friday's own choice, not the user's. An engine that cannot
    call tools and answers in seconds per token must never be one."""
    from agent_friday.routing.model_router import (
        CLOUD_MODEL_FALLBACK_CHAIN, DEFAULT_CLOUD_MODEL, CLOUD_COST_PER_1K)
    assert k3.MODEL_ID not in CLOUD_MODEL_FALLBACK_CHAIN
    assert DEFAULT_CLOUD_MODEL != k3.MODEL_ID
    assert k3.MODEL_ID not in CLOUD_COST_PER_1K


def test_no_provider_lists_it_as_one_of_its_models():
    """It is not a provider descriptor and must not become one: a subprocess
    has no base_url, so `classification_of` would demote it to cloud."""
    from agent_friday.services.provider_registry import get_provider_registry
    for prov in get_provider_registry().list_providers():
        assert k3.MODEL_ID not in (prov.get("models") or []), \
            "%s lists Kimi K3 as a servable model" % prov.get("name")


def test_it_is_off_by_default():
    from agent_friday.core import DEFAULT_SETTINGS
    blk = DEFAULT_SETTINGS["kimi_k3"]
    assert blk["enabled"] is False
    assert blk["binary"] == "" and blk["model_dir"] == ""


# ── the adapter refuses until it is configured ──────────────────────────────

def test_complete_refuses_while_unconfigured():
    with pytest.raises(k3.KimiK3Unavailable) as ei:
        k3.complete("hello")
    msg = str(ei.value)
    assert msg and msg != "None", "the refusal must carry a readable reason"


def test_status_reason_is_none_exactly_when_available(monkeypatch):
    st = k3.status()
    assert (st["reason"] is None) == st["available"]

    monkeypatch.setattr(k3, "config", lambda: {
        "enabled": True, "binary": "C:/k3/bin/k3.exe", "model_dir": "C:/k3/model",
        "trunk_dir": "", "tokenizer_dir": "", "preset": "laptop"})
    monkeypatch.setattr(k3, "_exists", lambda p: True)
    st2 = k3.status()
    assert st2["available"] is True and st2["reason"] is None


def _configured(monkeypatch):
    monkeypatch.setattr(k3, "config", lambda: {
        "enabled": True, "binary": "C:/k3/bin/k3.exe", "model_dir": "C:/k3/model",
        "trunk_dir": "C:/k3/trunk", "tokenizer_dir": "", "preset": "laptop"})
    monkeypatch.setattr(k3, "_exists", lambda p: True)


def test_the_prompt_goes_in_a_file_never_on_argv(monkeypatch):
    """The README: --prompt-file is "preferred for anything non-ASCII: the shell
    re-encodes argv, whereas a file is read verbatim". An argv round-trip
    through a Windows code page is how a correct em dash becomes three
    characters."""
    _configured(monkeypatch)
    seen = {}

    class _P:
        returncode = 0
        stdout = b"Paris."
        stderr = b""

    def _run(argv, **kw):
        seen["argv"] = list(argv)
        seen["kw"] = dict(kw)
        return _P()

    monkeypatch.setattr(k3.subprocess, "run", _run)
    out = k3.complete("Where is the Eiffel Tower \u2014 briefly?")
    assert out == "Paris."
    assert "--prompt-file" in seen["argv"]
    assert "--prompt" not in seen["argv"], "the prompt was passed through argv"
    # --prompt/--prompt-file require --tok
    assert "--tok" in seen["argv"]


def test_output_is_decoded_as_utf8_not_the_locale_codepage(monkeypatch):
    """`text=True` on Windows decodes with the locale code page, which corrupts
    every multi-byte character. So the call must capture BYTES and decode
    explicitly -- a real em dash must survive the round trip."""
    _configured(monkeypatch)

    class _P:
        returncode = 0
        stdout = "an em dash \u2014 intact".encode("utf-8")
        stderr = b""

    captured = {}

    def _run(argv, **kw):
        captured.update(kw)
        return _P()

    monkeypatch.setattr(k3.subprocess, "run", _run)
    out = k3.complete("x")
    assert captured.get("text") is not True, "text=True re-introduces the bug"
    assert "\u2014" in out, "the em dash did not survive decoding"


def test_a_nonzero_exit_is_reported_not_swallowed(monkeypatch):
    _configured(monkeypatch)

    class _P:
        returncode = 2
        stdout = b""
        stderr = b"k3: could not open checkpoint"

    monkeypatch.setattr(k3.subprocess, "run", lambda argv, **kw: _P())
    with pytest.raises(k3.KimiK3Unavailable) as ei:
        k3.complete("x")
    assert "could not open checkpoint" in str(ei.value)


def test_an_unknown_preset_falls_back_rather_than_being_passed_through(monkeypatch):
    _configured(monkeypatch)
    seen = {}

    class _P:
        returncode = 0
        stdout = b"ok"
        stderr = b""

    monkeypatch.setattr(k3.subprocess, "run",
                        lambda argv, **kw: (seen.update(argv=list(argv)), _P())[1])
    k3.complete("x", preset="hyperspeed")
    i = seen["argv"].index("--preset")
    assert seen["argv"][i + 1] in k3.PRESETS
