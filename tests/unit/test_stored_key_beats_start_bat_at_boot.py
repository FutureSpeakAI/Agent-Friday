"""The key you saved must be the key that boots — not just the key that probes.

Follow-up to `baf1a77`, which flipped `provider_api_key()` to read the
credential store before the environment. That fixed the probe and the
openai-compatible dispatch and left the most important reader untouched:

    core.get_anthropic_client()  ->  os.environ, then settings.json
                                     (never the credential store)

So the actual Anthropic chat call still ran on whatever was in the
environment, and the environment comes from `start.bat` via
`_bootstrap_env_from_launch_scripts()`.

`bootstrap_provider_env()` was supposed to close that, and its own docstring
says why it could not:

    Called at server boot, after the launch-script bootstrap. Never overrides
    a key already set in the environment.

start.bat wins the race, so the stored key was skipped every boot. A key
swapped in Settings worked until restart — `hot_reload_provider_key` sets
`os.environ` and `core.ANTHROPIC_API_KEY` live — and was then quietly
replaced by the dead one from start.bat, with the panel still saying
"connected".

That made the previous fix worse than incomplete: after `0734377`, a
cloud_only user with no working key is now TOLD to add one in Settings —
which is exactly where she already added it.

The distinction that fixes it without breaking anyone: a value this process
put into the environment from a launch script is not the same thing as a
variable the user set in Windows. `_bootstrap_env_from_launch_scripts`
already tracks what it set; it just never told anyone. A launch-script value
loses to a key saved through the product. A genuine system environment
variable still wins, because that is a deliberate act by someone who knows
what an environment variable is.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday import core  # noqa: E402
from agent_friday.services import credential_store as cs  # noqa: E402

ENV = "ANTHROPIC_API_KEY"


@pytest.fixture
def stored(monkeypatch):
    """One key in the encrypted store, named as anthropic's."""
    monkeypatch.setattr(cs, "list_provider_keys", lambda: ["anthropic"],
                        raising=False)
    monkeypatch.setattr(cs, "get_provider_key",
                        lambda p: "fake-key-SAVED-in-settings", raising=False)
    monkeypatch.setattr(cs, "_env_key_for_provider", lambda p: ENV,
                        raising=False)


def test_a_saved_key_replaces_the_one_start_bat_put_there(monkeypatch, stored):
    """The whole bug, in one assertion."""
    monkeypatch.setenv(ENV, "fake-key-DEAD-from-start-bat")
    monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", {ENV}, raising=False)

    assert cs.bootstrap_provider_env() == 1
    import os
    assert os.environ[ENV] == "fake-key-SAVED-in-settings"


def test_a_real_system_env_var_still_wins(monkeypatch, stored):
    """Not everything in the environment came from us.

    Someone who sets ANTHROPIC_API_KEY in Windows has done a deliberate
    thing and knows what it means. Overriding that from a file they cannot
    see would be the same disease pointed the other way.
    """
    monkeypatch.setenv(ENV, "fake-key-set-by-the-user-in-windows")
    monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", set(), raising=False)

    assert cs.bootstrap_provider_env() == 0
    import os
    assert os.environ[ENV] == "fake-key-set-by-the-user-in-windows"


def test_an_empty_environment_is_filled_as_before(monkeypatch, stored):
    """No regression on the path that already worked."""
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", set(), raising=False)

    assert cs.bootstrap_provider_env() == 1
    import os
    assert os.environ[ENV] == "fake-key-SAVED-in-settings"


def test_the_launch_bootstrap_publishes_what_it_set():
    """The set has to actually be exported, or the rule above is inert —
    a value read without being obeyed, which is this codebase's signature
    defect (services/role_consumers.py)."""
    assert hasattr(core, "ENV_FROM_LAUNCH_SCRIPTS")
    assert isinstance(core.ENV_FROM_LAUNCH_SCRIPTS, set)


def test_missing_core_attribute_fails_closed(monkeypatch, stored):
    """If the export ever disappears, fall back to the old conservative
    behaviour rather than trampling the environment."""
    monkeypatch.setenv(ENV, "fake-key-from-somewhere")
    monkeypatch.delattr(core, "ENV_FROM_LAUNCH_SCRIPTS", raising=False)

    assert cs.bootstrap_provider_env() == 0
    import os
    assert os.environ[ENV] == "fake-key-from-somewhere"
