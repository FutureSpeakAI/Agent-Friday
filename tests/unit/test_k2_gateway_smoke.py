"""The live smoke script (scripts/k2_gateway_smoke.py) against fake providers: it can be trusted when run for real.

Rules guarded here: a configured provider that answers 200 is PASS; one that answers 401 is FAIL and the exit code is 1;
a provider with no key is SKIP and does not fail the run; no key ever appears in the output.
"""
import http.server
import json
import importlib.util
import pathlib
import threading

import pytest

from agent_friday.services import key_gateway as gw
from agent_friday.services import model_router as smr

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "k2_gateway_smoke.py"
REAL = "sk-smoke-real-key-for-tests"  # pragma: allowlist secret


def load_script():
    spec = importlib.util.spec_from_file_location("k2_gateway_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Provider:
    def __init__(self, status=200):
        self.status = status
        self.seen = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.seen.append((self.path, dict(self.headers)))
                # the body echoes every request header, as a hostile or careless provider might
                body = json.dumps({"data": [], "echo": dict(self.headers)}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.seen.append((self.path, dict(self.headers)))
                events = [{"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1}}]
                if body.get("stream"):
                    data = "".join("data: %s\n\n" % json.dumps(e) for e in events) + "data: [DONE]\n\n"
                    kind = "text/event-stream"
                else:
                    data = json.dumps({"model": "m", "choices": [{"message": {"role": "assistant", "content": "ok"},
                                                                  "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
                    kind = "application/json"
                payload = data.encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = "http://127.0.0.1:%d" % self.server.server_address[1]


@pytest.fixture
def world(monkeypatch):
    """Every gateway row points at one fake provider; keys come from the environment only."""
    fake = Provider()
    for name, row in list(gw.PROVIDERS.items()):
        monkeypatch.setitem(gw.PROVIDERS, name, gw.Provider(**{**row.__dict__, "upstream": fake.origin}))
    for var in ("BRAVE_SEARCH_API_KEY", "FIRECRAWL_API_KEY", "ELEVENLABS_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(gw, "_from_core", lambda row: "")
    monkeypatch.setattr(gw, "_from_settings", lambda row: "")
    monkeypatch.setattr(gw, "_is_cloud_endpoint", lambda address: True)
    monkeypatch.setattr(smr, "_load_settings", lambda: {"model_routing": {"openai_base_url": fake.origin + "/v1"}})
    monkeypatch.setattr("agent_friday.services.credential_store.get_provider_key", lambda *a, **k: "", raising=False)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def run(monkeypatch, capsys, *arguments):
    monkeypatch.setattr("sys.argv", ["k2_gateway_smoke.py", *arguments])
    code = load_script().main()
    return code, capsys.readouterr().out


def test_configured_providers_that_answer_200_pass(world, monkeypatch, capsys):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", REAL)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_" + REAL)
    monkeypatch.setenv("OPENAI_API_KEY", REAL)
    code, out = run(monkeypatch, capsys)
    assert code == 0 and out.count(" PASS ") == 3 and "gateway firecrawl" in out and " SKIP " in out
    assert REAL not in out
    sent = " ".join(str(headers) for _, headers in world.seen)
    assert REAL in sent, "the provider must really have received the key through the gateway"


def test_a_provider_that_refuses_the_key_fails_the_run(world, monkeypatch, capsys):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", REAL)
    world.status = 401
    code, out = run(monkeypatch, capsys)
    assert code == 1 and " FAIL " in out and "HTTP 401" in out and REAL not in out


def test_with_no_keys_nothing_was_tested_and_that_is_not_a_pass(world, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys)
    assert code == 2 and " PASS " not in out and out.count(" SKIP ") >= 4 and "NOTHING WAS TESTED" in out
    assert "ALL CONFIGURED PROVIDERS PASSED" not in out


def test_an_openrouter_key_is_checked_on_its_authenticated_endpoint_not_the_public_one(world, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", REAL)
    monkeypatch.setattr(smr, "_load_settings", lambda: {"model_routing": {"openai_base_url": world.origin + "/openrouter.ai/v1"}})
    code, out = run(monkeypatch, capsys)
    assert code == 0 and "gateway key check (legacy-openrouter)" in out
    assert world.seen[-1][0] == "/openrouter.ai/v1/key"


def test_a_provider_that_echoes_the_key_back_in_an_error_never_gets_it_printed(world, monkeypatch, capsys):
    monkeypatch.setenv("FIRECRAWL_API_KEY", REAL)
    world.status = 403
    code, out = run(monkeypatch, capsys)
    assert code == 1 and "HTTP 403" in out and REAL not in out and "Bearer" not in out


def test_an_exception_whose_message_holds_the_key_is_reported_by_type_only(world, monkeypatch, capsys):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", REAL)

    def boom(*args, **kwargs):
        raise RuntimeError("the key was " + REAL)
    monkeypatch.setattr("requests.get", boom)
    code, out = run(monkeypatch, capsys)
    assert code == 1 and "RuntimeError" in out and REAL not in out


def test_the_chat_flag_makes_one_streamed_call_through_the_gateway_and_names_the_model(world, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", REAL)
    monkeypatch.setattr(smr, "_seal_or_block", lambda payload, provider_name, model="": payload)
    monkeypatch.setattr(smr, "_load_settings", lambda: {"model_routing": {"openai_base_url": world.origin + "/v1",
                                                                          "openai_model": "tiny-model"}})
    code, out = run(monkeypatch, capsys, "--chat")
    assert code == 0 and "chat through gateway (stream)" in out and "PASS" in out and "model tiny-model" in out
    posts = [headers for path, headers in world.seen if path.endswith("/chat/completions")]
    assert posts and posts[0].get("Authorization") == "Bearer " + REAL and REAL not in out


def test_without_the_chat_flag_no_chat_call_is_made(world, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", REAL)
    run(monkeypatch, capsys)
    assert not [path for path, _ in world.seen if path.endswith("/chat/completions")]
