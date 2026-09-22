"""C3: the offline path is never load-bearing on a network call.

Section 6.4 of cloud-voice-providers.md calls this "the load-bearing artifact"
and names step 6 as the one that will actually fail one day: a cloud health
check or a voice-catalogue refresh blocking a local path on a DNS timeout. Steps
1-5 fail loudly; step 6 fails as "voice got slow," which is how C3 erodes
without anyone deciding to erode it.

This file covers the parts of that gate the cloud provider layer is responsible
for. The full end-to-end round trip (mic -> transcription -> model -> audio with
the network black-holed) belongs in the CI matrix and needs a real machine; what
is asserted here is the property the cloud layer can break on its own, which is
the one this change introduces the risk of.
"""
from __future__ import annotations

import socket
import time

import pytest

from agent_friday.services import cloud_voice


@pytest.fixture
def _no_network(monkeypatch):
    """Black-hole the network. Any socket attempt becomes a loud failure.

    Deliberately raises rather than hanging: a test that reproduces the DNS
    timeout faithfully would itself take 30 seconds, and the property under test
    is "does not touch the network at all", which an exception proves faster and
    more precisely than a stopwatch.
    """
    def _blocked(*a, **k):
        raise AssertionError(
            "a network call was made on a path the local voice can block on - "
            "this is the section 6.4 step 6 regression")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    return monkeypatch


@pytest.fixture
def _no_keys(monkeypatch):
    for var in ("GEMINI_API_KEY", "ELEVENLABS_API_KEY", "INWORLD_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cloud_voice, "_settings", dict)
    import agent_friday.core as core
    for attr in ("GEMINI_API_KEY", "ELEVENLABS_API_KEY", "INWORLD_API_KEY"):
        monkeypatch.setattr(core, attr, "", raising=False)
    return monkeypatch


class TestOfflineNoCloud:

    def test_provider_listing_does_no_network_io(self, _no_network, _no_keys):
        """Section 4.2's selection surface must not probe reachability.

        The tempting implementation pings each provider so the UI can grey out
        the unreachable ones. That puts a DNS timeout in front of a settings
        screen the offline user still needs.
        """
        rows = cloud_voice.available_providers()
        assert rows
        assert all(not r["selectable"] for r in rows)

    def test_provider_listing_is_fast(self, _no_keys):
        """The stopwatch half of step 6, as a coarse backstop.

        A generous ceiling on purpose - this catches a 30 s DNS stall, not a
        slow machine.
        """
        started = time.time()
        cloud_voice.available_providers()
        assert time.time() - started < 1.0

    def test_resolution_does_no_network_io(self, _no_network, _no_keys):
        assert cloud_voice.resolve_provider({"voice_engine": "auto"}) is None
        assert cloud_voice.resolve_provider({"voice_engine": "local"}) is None

    def test_importing_the_module_opens_no_socket(self, _no_network):
        """C3: no cloud module does network work at import time.

        `requests` is imported lazily INSIDE each call site, which is the
        discipline elevenlabs_tools.py established so the module stays
        import-safe with no network libs and no key.
        """
        import importlib
        importlib.reload(cloud_voice)
        assert cloud_voice.PROVIDERS

    def test_no_key_refuses_without_reaching_the_network(
            self, _no_network, _no_keys):
        """The refusal is decided locally, before any connection attempt."""
        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize("hello", provider="elevenlabs", settings={})
        assert exc.value.code == "cloud_voice_no_key"
        assert exc.value.offer

    def test_module_source_has_no_module_level_network_import(self):
        """Pins the lazy-import discipline in the source itself.

        A future edit that hoists `import requests` to the top of the file would
        pass every behavioural test above while making the module import pull in
        a network stack on a machine that may not have one.
        """
        from pathlib import Path
        src = Path(cloud_voice.__file__).read_text(encoding="utf-8")
        head = src.split("PROVIDERS", 1)[0]
        for banned in ("\nimport requests", "\nimport httpx",
                       "\nfrom requests"):
            assert banned not in head, (
                "a network library is imported at module scope in "
                "cloud_voice.py; it must stay lazy inside call sites (C3)")
