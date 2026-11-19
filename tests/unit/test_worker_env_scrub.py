"""Friday's own worker processes (voice, media conversion, podcast render, g2p helper) start without secrets.

Rule (Compressor eval K2, A1): a child process never receives a provider key, a vault passphrase or a token, whatever
it is called. These workers need the rest of the environment (CUDA, model cache folders, thread counts), so the
guard here is a pattern on the NAME, not an allow-list. Each case fails if its module goes back to ``dict(os.environ)``.
"""
import io

import pytest

from agent_friday.services import extension_security as es
from agent_friday.services import g2p_fallback, media_convert, podcast_render, voice_workers

SECRETS = {"ANTHROPIC_API_KEY": "sk-1", "OPENAI_API_KEY": "sk-2", "ELEVENLABS_APIKEY": "sk-3",
           "FRIDAY_VAULT_PASSPHRASE": "pw", "GITHUB_TOKEN": "tok", "SOME_FUTURE_SECRET": "s", "DB_PASSWORD": "p"}
KEPT = {"PATH": "p", "HF_HOME": "h", "CUDA_PATH": "c", "OMP_NUM_THREADS": "4", "PYTHONPATH": "x", "KEYBOARD_LAYOUT": "us"}


@pytest.fixture
def environment(monkeypatch):
    for name, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(name, value)


def assert_clean(env):
    assert [name for name in SECRETS if name in env] == []
    assert not any(value.startswith("sk-") for value in env.values())
    assert env["PATH"] == "p" and env["HF_HOME"] == "h" and env["CUDA_PATH"] == "c"


def test_the_helper_drops_secret_names_and_keeps_the_rest(environment):
    env = es.without_secret_names(dict(__import__("os").environ))
    assert_clean(env)
    assert env["KEYBOARD_LAYOUT"] == "us"


def test_a_voice_worker_gets_no_secret(environment):
    assert_clean(voice_workers._worker_env("whisper"))


def test_the_media_converter_gets_no_secret(environment):
    assert_clean(media_convert._font_env())


def test_the_podcast_renderer_gets_no_secret(environment):
    assert_clean(podcast_render.ProcessKokoro()._env())


def test_the_g2p_helper_gets_no_secret(environment, monkeypatch):
    seen = {}

    class _Child:
        stdout = io.StringIO('{"ready": true}\n')
        stdin = io.StringIO()

        def kill(self):
            pass

        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        seen["env"] = dict(kwargs["env"])
        return _Child()

    monkeypatch.setattr(g2p_fallback.subprocess, "Popen", fake_popen)
    helper = g2p_fallback._Helper()
    try:
        helper._start()
    except Exception:
        pass
    assert "env" in seen, "the helper never reached Popen"
    assert_clean(seen["env"])
