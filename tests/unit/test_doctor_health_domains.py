"""The Doctor checks every health domain (north star §28.2-1): the graphics
card, local voice, the document engine, the browser lane and the remote
channels join the checks it already had. Each reads only: nothing is started,
loaded or downloaded, and what is off on purpose never reads as a failure."""
from __future__ import annotations

import importlib.util

import pytest

NEW = ("gpu", "local_voice", "document_engine", "browser_lane", "channels", "phone")


def _checks():
    from agent_friday.services import doctor
    return {c["id"]: c for c in doctor.build()["checks"]}


def test_every_domain_has_a_check_that_runs_here():
    checks = _checks()
    missing = [c for c in NEW if c not in checks]
    assert not missing, "the Doctor has no check for: %s" % ", ".join(missing)
    for cid in NEW:
        assert not str(checks[cid]["code"]).startswith("check_failed"), checks[cid]


@pytest.fixture
def fakes(monkeypatch):
    from agent_friday.phone import config as phone_config
    from agent_friday.services import (browser_session, gpu_probe, local_voice, office_engine, safe_mode)
    from agent_friday.services.channels import manager
    f = {"gpu": {"cuda": True, "devices": 1, "source": "nvidia-smi", "free_mib": 9000, "detail": ""},
         "cublas": {"ok": True}, "voice": True, "office": True, "office_ok": (True, "sha256 matches"),
         "playwright": True, "browser": None, "safe": False,
         "channels": {"enabled": False, "channels": {}}, "phone": {"enabled": False}}
    monkeypatch.setattr(gpu_probe, "probe", lambda refresh=False: f["gpu"])
    monkeypatch.setattr(gpu_probe, "cublas_status", lambda env=None: f["cublas"])
    monkeypatch.setattr(local_voice, "deps_installed", lambda: f["voice"])
    monkeypatch.setattr(office_engine, "available", lambda: f["office"])
    monkeypatch.setattr(office_engine, "verify_binary", lambda force=False: f["office_ok"])
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: (object() if f["playwright"] else None) if name == "playwright"
                        else real_find(name, *a))
    monkeypatch.setattr(browser_session, "current", lambda: f["browser"])
    monkeypatch.setattr(safe_mode, "allows", lambda part: not f["safe"])
    monkeypatch.setattr(manager, "status", lambda: f["channels"])
    monkeypatch.setattr(phone_config, "status", lambda: f["phone"])
    return f


def _state(cid):
    c = _checks()[cid]
    return c["state"], c["code"]


def test_the_graphics_card(fakes):
    assert _state("gpu") == ("healthy", "cuda_gpus:1")
    fakes["gpu"] = dict(fakes["gpu"], devices=0, cuda=False, source="CUDA_VISIBLE_DEVICES")
    assert _state("gpu")[0] == "unavailable_by_configuration"
    fakes["gpu"] = dict(fakes["gpu"], source="nvidia-smi")
    assert _state("gpu")[0] == "unavailable_by_hardware"
    fakes["gpu"] = dict(fakes["gpu"], devices=1, cuda=True)
    fakes["cublas"] = {"ok": False, "how": "missing"}
    assert _state("gpu") == ("misconfigured", "cublas_missing")


def test_local_voice(fakes):
    assert _state("local_voice")[0] == "unverified"         # installed; its models are not loaded here
    fakes["voice"] = False
    assert _state("local_voice")[0] == "unavailable_by_configuration"


def test_the_document_engine(fakes):
    assert _state("document_engine")[0] == "healthy"
    fakes["office_ok"] = (False, "officecli.exe does not match its pinned checksum")
    assert _state("document_engine") == ("broken", "checksum_mismatch")
    fakes["office_ok"] = (False, "no pinned sha256 in INSTALL.json")
    assert _state("document_engine")[0] == "misconfigured"
    fakes["office"] = False
    assert _state("document_engine")[0] == "unavailable_by_configuration"


def test_the_browser_lane(fakes):
    assert _state("browser_lane") == ("healthy", "ready_closed")
    fakes["playwright"] = False
    assert _state("browser_lane")[0] == "unavailable_by_configuration"


def _ch(state):
    return {"enabled": True, "health": {"source_state": state}}


def test_remote_channels(fakes):
    assert _state("channels")[0] == "unavailable_by_configuration"
    fakes["channels"] = {"enabled": True, "channels": {"telegram": _ch("running"), "discord": {"enabled": False}}}
    assert _state("channels") == ("healthy", "running:1")
    fakes["channels"] = {"enabled": True, "channels": {"telegram": _ch("missing")}}
    assert _state("channels")[0] == "misconfigured"
    fakes["channels"] = {"enabled": True, "channels": {"telegram": _ch("not-running")}}
    assert _state("channels")[0] == "degraded"
    fakes["safe"] = True
    assert _state("channels")[0] == "blocked_by_policy"


def test_the_phone_line(fakes):
    assert _state("phone")[0] == "unavailable_by_configuration"
    fakes["phone"] = {"enabled": True, "auth_token": "stored", "api_key_secret": "stored"}
    assert _state("phone")[0] == "healthy"
    fakes["phone"] = {"enabled": True, "auth_token": "missing", "api_key_secret": "stored"}
    assert _state("phone")[0] == "misconfigured"
    fakes["phone"] = {"enabled": True, "auth_token": "unreadable", "api_key_secret": "stored"}
    assert _state("phone")[0] == "broken"
