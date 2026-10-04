"""Claude's agent as an engine (salon spec §4.7 `engine: claude_agent`, §10
row 3): it runs in the codebase's folder as a process on this PC (B1, with
the disclosure), pointed at the salon proxy with a dummy key, and everything
it changed becomes one step whose receipt names the hosts it reached.

The engine binary is a stub here: a script that edits a file, calls the
"API" through the proxy with the dummy key, and prints a result. No token is
spent and no real key exists in this test.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import sys
import threading

import pytest

from agent_friday.services import claude_engine as ce
from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs

REAL = "sk-ant-real-engine-000000000"  # pragma: allowlist secret

STUB = r'''
import json, os, sys, urllib.request, pathlib
task = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else ""
base = os.environ.get("ANTHROPIC_BASE_URL", "")
key = os.environ.get("ANTHROPIC_API_KEY", "")
pathlib.Path("index.html").write_text("<h1>%s</h1>\n" % task, encoding="utf-8")
pathlib.Path("notes.txt").write_text("edited by the engine stub\n", encoding="utf-8")
req = urllib.request.Request(base + "/v1/messages", data=b"{}", method="POST", headers={"x-api-key": key, "Content-Type": "application/json"})
status = urllib.request.urlopen(req, timeout=10).status
if "--also-forbidden" in sys.argv:
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/somewhere/else", data=b"{}", method="POST", headers={"x-api-key": key}), timeout=10)
    except Exception:
        pass
print(json.dumps({"result": "done: " + task, "key_seen": key, "api_status": status, "env_has_real": "REAL" in " ".join(os.environ.values())}))
'''


class _Upstream(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        _Upstream.seen.append({"path": self.path, "key": self.headers.get("x-api-key")})
        out = b'{"ok": true}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 0.0, "by_key_profile": {}, "calls": 0})
    _Upstream.seen = []
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Upstream)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(ce, "UPSTREAM", "http://127.0.0.1:%d" % srv.server_address[1])
    monkeypatch.setattr(ce, "_owner_key", lambda: REAL)
    stub = tmp_path / "claude_stub.py"; stub.write_text(STUB, encoding="utf-8")
    monkeypatch.setattr(ce, "_engine_command", lambda: [sys.executable, str(stub)])
    yield
    srv.shutdown()


def test_the_engine_edits_through_the_proxy_and_its_changes_become_one_step():
    conv = convs.create("Rent tracker")
    rec = cb.create("Rent tracker", conversation_id=conv["id"])
    cb.set_seat(rec["id"], "heavy", "claude-opus-5-5")
    out = ce.run_task(rec["id"], "Say hello", key_profile="mine", timeout_s=60)
    assert out["status"] == "ok", out
    st = out["step"]
    assert st["who"].startswith("claude-agent via mine")
    assert set(f["path"] for f in st["receipt"]["files"]) == {"index.html", "notes.txt"}
    assert st["receipt"]["engine"] == "claude_agent"
    assert st["receipt"]["hosts_contacted"] == [{"host": ce.UPSTREAM.split("//")[1], "path": "/v1/messages", "count": 1, "refused": 0}]
    # The key never entered the process: the stub saw the dummy and the upstream saw the real one.
    assert out["engine"]["key_seen"] == "friday-proxy-dummy" and out["engine"]["env_has_real"] is False
    assert _Upstream.seen and _Upstream.seen[0]["key"] == REAL
    # The disclosure is part of the result, and B1 is named.
    assert "this PC" in out["disclosure"] and "read" in out["disclosure"].lower() and out["tier"] == "B1"
    assert cb.read(rec["id"], "index.html").startswith("<h1>Say hello</h1>")
    # No proxy is left listening.
    assert out["proxy"]["stopped"] is True


def test_a_request_the_proxy_refused_is_on_the_receipt():
    rec = cb.create("Rent tracker", conversation_id=convs.create("x")["id"])
    out = ce.run_task(rec["id"], "Try the wrong door", key_profile="mine", timeout_s=60, extra_args=["--also-forbidden"])
    hosts = {h["path"]: h for h in out["step"]["receipt"]["hosts_contacted"]}
    assert hosts["/somewhere/else"]["refused"] == 1
    assert out["proxy"]["refused"] == 1


def test_no_engine_installed_is_a_typed_blocker_not_a_promise(monkeypatch):
    rec = cb.create("Rent tracker", conversation_id=convs.create("x")["id"])
    monkeypatch.setattr(ce, "_engine_command", lambda: None)
    out = ce.run_task(rec["id"], "anything", key_profile="mine")
    assert out["status"] == "refused" and out["blocker"] == "missing_dependency" and "claude" in out["say"].lower()


def test_a_guest_key_is_what_the_proxy_injects(monkeypatch, tmp_path):
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", tmp_path / "keys")
    rec = cb.create("Rent tracker", conversation_id=convs.create("x")["id"])
    cb.add_guest_key(rec["id"], "Alex", "anthropic", "sk-ant-guest-engine-000000")  # pragma: allowlist secret
    out = ce.run_task(rec["id"], "hello", key_profile="Alex", timeout_s=60)
    assert out["status"] == "ok" and out["step"]["who"].startswith("claude-agent via Alex")
    assert _Upstream.seen[0]["key"] == "sk-ant-guest-engine-000000"  # pragma: allowlist secret


def test_nothing_changed_is_said_plainly(monkeypatch, tmp_path):
    rec = cb.create("Rent tracker", conversation_id=convs.create("x")["id"])
    noop = tmp_path / "noop.py"; noop.write_text("print('{\"result\": \"nothing to do\"}')", encoding="utf-8")
    monkeypatch.setattr(ce, "_engine_command", lambda: [sys.executable, str(noop)])
    out = ce.run_task(rec["id"], "do nothing", key_profile="mine", timeout_s=60)
    assert out["status"] == "no_change" and out["step"] is None
