"""Friday's address on this PC: agent.<the agent's name> (services/local_address).

Workspace tabs live at https://agent.<the name picked at setup>, not at
localhost, once that address is proven to reach this Friday.

Pinned here:
  * the name -> address rule, including the names that must NOT become an
    address because they would hide a real website on this PC;
  * the page is told an address only once it is proven to reach this process;
  * the steps Windows asks about can never be run from a test.
"""
from __future__ import annotations

import pytest

from agent_friday.services import local_address as la


@pytest.mark.parametrize("name, slug", [
    ("AGENT FRIDAY", "friday"),
    ("Friday", "friday"),
    ("JARVIS", "jarvis"),
    ("Agent Smith", "smith"),
    ("agent-k", "k"),
    ("Mr. Robot", "mr-robot"),
    ("Ágata", "agata"),
    ("Agentur", "agentur"),          # "agent" only as a whole leading word
    ("  --  ", ""),
    ("エージェント", ""),
    ("AGENT", ""),
])
def test_the_address_comes_from_the_agents_name(name, slug):
    assert la.slug_from_name(name) == slug


@pytest.mark.parametrize("name, host", [
    ("AGENT FRIDAY", "agent.friday"),
    ("JARVIS", "agent.jarvis"),
    ("Mr. Robot", "agent.mr-robot"),
    ("エージェント", "agent.friday"),
])
def test_resolve_host_from_the_name(name, host):
    got, source, _note = la.resolve_host({"agent_name": name})
    assert (got, source) == (host, "agent name")


@pytest.mark.parametrize("name", ["COM", "AI", "Agent Dev", "APP", "Google", "UK"])
def test_a_name_that_is_a_real_internet_ending_falls_back_and_says_why(name):
    host, source, note = la.resolve_host({"agent_name": name})
    assert host == "agent.friday"
    assert "real internet domain ending" in note


@pytest.mark.parametrize("name", ["local", "Onion", "LOCALHOST"])
def test_a_name_that_is_spoken_for_falls_back_and_says_why(name):
    host, source, note = la.resolve_host({"agent_name": name})
    assert host == "agent.friday"
    assert "would not work reliably" in note


@pytest.mark.parametrize("host, ok", [
    ("agent.friday", True),
    ("agent.jarvis-2", True),
    ("agent.internal", True),
    ("agent.com", False),
    ("agent.io", False),
    ("agent.dev", False),
    ("friday", False),
    ("agent.friday.com", False),
    ("x.agent.friday", False),
    ("agent.-bad", False),
    ("agent.bad-", False),
    ("agent.123", False),
    ("agent.fri day", False),
    ("agent.friday'; rm", False),
    ("agent." + "a" * 64, False),
])
def test_only_agent_dot_one_word_is_an_address(host, ok):
    assert (la.host_problem(host) == "") is ok


def test_a_custom_address_wins_and_a_bad_one_is_ignored_with_a_note():
    assert la.resolve_host({"agent_name": "JARVIS", "local_address": {"host": "agent.home"}})[:2] \
        == ("agent.home", "custom")
    host, source, note = la.resolve_host({"agent_name": "JARVIS",
                                          "local_address": {"host": "agent.com"}})
    assert (host, source) == ("agent.jarvis", "agent name")
    assert "not usable" in note


def test_origin_leaves_out_default_ports():
    assert la.origin("https", "agent.friday", 443) == "https://agent.friday"
    assert la.origin("http", "agent.friday", 80) == "http://agent.friday"
    assert la.origin("https", "agent.pwtest", 3243) == "https://agent.pwtest:3243"


def test_the_page_is_never_given_an_address_that_was_not_proven(monkeypatch):
    """No check has run yet: the page keeps its own address (origin None)."""
    monkeypatch.setattr(la, "refresh_in_background", lambda *a, **k: None)
    monkeypatch.setitem(la._STATUS, "value", None)
    info = la.page_info()
    assert info["origin"] is None and info["secure"] is False


def test_the_page_gets_the_proven_address(monkeypatch):
    import time
    monkeypatch.setattr(la, "refresh_in_background", lambda *a, **k: None)
    monkeypatch.setitem(la._STATUS, "value", {"preferred_origin": "https://agent.friday", "secure": True})
    monkeypatch.setitem(la._STATUS, "ts", time.time())
    assert la.page_info()["origin"] == "https://agent.friday"


def test_an_address_serving_another_friday_is_not_this_ones(monkeypatch):
    """A test server must never send its tabs to the real Friday: the answer on
    agent.<name> has to come from THIS process."""
    monkeypatch.setattr(la, "_settings", lambda: {"agent_name": "AGENT FRIDAY"})
    monkeypatch.setattr(la, "resolves_to_loopback", lambda h: True)
    other = {"answers": True, "friday": True, "instance": "someone-else", "trusted": True,
             "redirect": None, "error": ""}
    monkeypatch.setattr(la, "probe", lambda base, timeout=2.5: dict(other, origin=base))
    monkeypatch.setattr(la, "oauth_info", lambda *a, **k: {})
    st = la.compute_status()
    assert st["preferred_origin"] is None
    assert st["secure"] is False
    assert st["https"]["served_by"] == "another program"


def test_this_friday_through_a_trusted_certificate_is_secure(monkeypatch):
    monkeypatch.setattr(la, "_settings", lambda: {"agent_name": "AGENT FRIDAY"})
    monkeypatch.setattr(la, "resolves_to_loopback", lambda h: True)

    def probe(base, timeout=2.5):
        if base.startswith("https://"):
            return {"origin": base, "answers": True, "friday": True, "instance": la.INSTANCE_ID,
                    "trusted": True, "redirect": None, "error": ""}
        return {"origin": base, "answers": True, "friday": False, "instance": None,
                "trusted": None, "redirect": "https://agent.friday/api/local-address/ping", "error": ""}

    monkeypatch.setattr(la, "probe", probe)
    monkeypatch.setattr(la, "oauth_info", lambda *a, **k: {})
    st = la.compute_status()
    assert st["secure"] is True
    assert st["preferred_origin"] == "https://agent.friday"
    assert st["https"]["served_by"] == "this Friday, through another program"


def test_an_untrusted_certificate_falls_back_to_plain_http(monkeypatch):
    monkeypatch.setattr(la, "_settings", lambda: {"agent_name": "JARVIS"})
    monkeypatch.setattr(la, "resolves_to_loopback", lambda h: True)

    def probe(base, timeout=2.5):
        https = base.startswith("https://")
        return {"origin": base, "answers": True, "friday": True, "instance": la.INSTANCE_ID,
                "trusted": False if https else None, "redirect": None,
                "error": "the certificate is not trusted by Windows" if https else ""}

    monkeypatch.setattr(la, "probe", probe)
    monkeypatch.setattr(la, "oauth_info", lambda *a, **k: {})
    st = la.compute_status()
    assert st["secure"] is False
    assert st["preferred_origin"] == "http://agent.jarvis"


def test_a_name_this_pc_does_not_know_is_not_used(monkeypatch):
    monkeypatch.setattr(la, "_settings", lambda: {"agent_name": "JARVIS"})
    monkeypatch.setattr(la, "resolves_to_loopback", lambda h: False)
    monkeypatch.setattr(la, "probe", lambda *a, **k: pytest.fail("probed a name that does not resolve"))
    monkeypatch.setattr(la, "oauth_info", lambda *a, **k: {})
    st = la.compute_status()
    assert st["resolves"] is False and st["preferred_origin"] is None


def test_the_tray_opens_the_named_address_only_when_it_reaches_the_same_friday(monkeypatch):
    monkeypatch.setattr(la, "_settings", lambda: {"agent_name": "AGENT FRIDAY"})
    monkeypatch.setattr(la, "resolves_to_loopback", lambda h: True)
    answers = {
        "http://localhost:3000": {"instance": "A"},
        "https://agent.friday": {"instance": "A", "trusted": True},
        "http://agent.friday": {"instance": "A"},
    }
    monkeypatch.setattr(la, "probe", lambda base, timeout=1.5: answers.get(base, {}))
    assert la.open_url("http://localhost:3000") == "https://agent.friday/"
    answers["https://agent.friday"] = {"instance": "A", "trusted": False}
    assert la.open_url("http://localhost:3000") == "http://agent.friday/"
    answers["http://agent.friday"] = {"instance": "B"}
    assert la.open_url("http://localhost:3000") == "http://localhost:3000"


def test_the_tray_keeps_localhost_when_the_name_is_unknown(monkeypatch):
    monkeypatch.setattr(la, "_settings", lambda: {"agent_name": "JARVIS"})
    monkeypatch.setattr(la, "resolves_to_loopback", lambda h: False)
    assert la.open_url("http://localhost:3000") == "http://localhost:3000"


# ── the steps Windows asks about ───────────────────────────────────────────
def test_no_system_change_can_ever_run_from_a_test():
    with pytest.raises(RuntimeError, match="never changes"):
        la._run_system_command(["certutil", "-user", "-addstore", "Root", "x.pem"])


def test_the_hosts_step_only_ever_writes_the_validated_name():
    script = la.hosts_script("agent.jarvis")
    assert "$name = 'agent.jarvis'" in script
    assert la.MARK_BEGIN in script and la.MARK_END in script
    assert "IsReadOnly" in script and ".agent-friday.bak" in script
    for bad in ("agent.com", "agent.x'; Remove-Item C:\\ -Recurse; '", "evil.example"):
        with pytest.raises(ValueError):
            la.hosts_script(bad)


def test_the_hosts_step_asks_windows_for_permission():
    argv = la.hosts_command("agent.jarvis")
    joined = " ".join(argv)
    assert "-Verb RunAs" in joined          # the UAC prompt
    assert "-EncodedCommand" in joined
    assert argv[0].lower().endswith("powershell.exe")


def test_trust_uses_certutil_for_the_current_user_only(monkeypatch, tmp_path):
    """-user: only this Windows account's roots, and Windows shows its Security
    Warning for that store. Recorded, not run."""
    from agent_friday.services import local_ca
    monkeypatch.setattr(la, "state_dir", lambda: tmp_path)
    local_ca.ensure(tmp_path, "agent.pwtest")
    ran = []
    monkeypatch.setattr(la, "_run_system_command", lambda argv, timeout=300: ran.append(argv) or (1223, ""))
    monkeypatch.setattr(la, "refresh_in_background", lambda *a, **k: None)
    ok, _ = la.start_trust_job()
    assert ok
    import time
    for _ in range(100):
        if la.job_status()["state"] != "waiting":
            break
        time.sleep(0.05)
    assert ran and ran[0][1:4] == ["-user", "-addstore", "Root"]
    assert ran[0][0].lower().endswith("certutil.exe")
    assert la.job_status()["state"] == "cancelled"


def test_a_real_trust_step_under_test_fails_without_touching_windows(monkeypatch, tmp_path):
    from agent_friday.services import local_ca
    monkeypatch.setattr(la, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(la, "refresh_in_background", lambda *a, **k: None)
    local_ca.ensure(tmp_path, "agent.pwtest")
    ok, _ = la.start_trust_job()
    assert ok
    import time
    for _ in range(100):
        if la.job_status()["state"] != "waiting":
            break
        time.sleep(0.05)
    st = la.job_status()
    assert st["state"] == "failed" and "never changes" in st["message"]


def test_hosts_entry_state_reads_lines_with_several_names(monkeypatch, tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\tlocalhost other agent.jarvis # note\n", encoding="utf-8")
    monkeypatch.setattr(la, "_hosts_path", lambda: hosts)
    assert la.hosts_entry_state("agent.jarvis") == {"readable": True, "listed": True, "ours": False}
    assert la.hosts_entry_state("agent.jarv")["listed"] is False
    hosts.write_text(f"x\n{la.MARK_BEGIN}\n127.0.0.1\tagent.jarvis\n::1\t\tagent.jarvis\n{la.MARK_END}\n",
                     encoding="utf-8")
    assert la.hosts_entry_state("agent.jarvis")["ours"] is True


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows PowerShell")
def test_the_hosts_step_adds_and_removes_only_its_own_block(tmp_path):
    """The exact script the elevated step runs, pointed at a COPY of a hosts
    file and run unelevated: the real hosts file is never involved."""
    import subprocess
    hosts = tmp_path / "hosts"
    original = ("# Copyright (c) Microsoft Corp.\r\n127.0.0.1\tlocalhost\r\n"
                "# >>> agent.friday (Friday demo proxy) >>>\r\n127.0.0.1\tagent.friday\r\n"
                "# <<< agent.friday (Friday demo proxy) <<<\r\n")
    hosts.write_bytes(original.encode("utf-8"))

    def run(add):
        script = la.hosts_script("agent.jarvis", add=add)
        real = r"$hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'"
        assert real in script
        script = script.replace(real, "$hosts = '%s'" % str(hosts).replace("'", "''"))
        script = script.replace("ipconfig /flushdns | Out-Null", "")
        assert "System32" not in script and "ipconfig" not in script
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return hosts.read_bytes().decode("utf-8")

    added = run(True)
    assert added.startswith(original)                       # nothing before it touched
    assert "127.0.0.1\tagent.jarvis" in added and "::1\t\tagent.jarvis" in added
    assert added.count(la.MARK_BEGIN) == 1
    again = run(True)                                        # idempotent
    assert again.count(la.MARK_BEGIN) == 1
    removed = run(False)
    assert removed == original                               # the demo proxy's lines survive
    assert (tmp_path / "hosts.agent-friday.bak").read_bytes().decode("utf-8") == original
