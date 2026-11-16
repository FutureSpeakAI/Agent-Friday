"""The raw-key-reader guard (scripts/check_raw_key_readers.py): the allow-list can only shrink.

Each rule in the script's docstring has a case here that fails if the rule is removed.
"""
import datetime
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_raw_key_readers.py"
spec = importlib.util.spec_from_file_location("check_raw_key_readers", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

TODAY = datetime.date(2026, 11, 16)
ENTRY = {"reason": "moves to the proxy", "expires": "2027-03-31", "approved_by": "Dispatch"}


def tree(tmp_path, files):
    src = tmp_path / "src" / "agent_friday"
    for name, text in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(text, encoding="utf-8")
    return src


@pytest.mark.parametrize("code", [
    'import os\nkey = os.environ.get("ACME_API_KEY")\n',
    'import os\nkey = os.getenv("ACME_API_KEY", "")\n',
    'import os\nkey = os.environ["ACME_API_KEY"]\n',
    'import os\nkey = os.environ.get("ACME_ACCESS_TOKEN")\n',
    'import os\nkey = os.environ.get("VAULT_PASSPHRASE")\n',
    'from x import credential_store\nkey = credential_store.get_provider_key("acme")\n',
    'from x import core\nkey = core.ANTHROPIC_API_KEY\n',
    'from x import provider_api_key\nkey = provider_api_key(prov)\n',
    'from os import getenv\nkey = getenv("ACME_API_KEY")\n',
    'from x import core\nkey = getattr(core, "ACME_API_KEY", "")\n',
    'from x import firecrawl\nkey = firecrawl.api_key()\n',
    'import x\nkey = x.simple_secret(1)\n',
    'class A:\n    def f(self):\n        return self.token()\n',
    'from os import environ as e\nkey = e.get("ACME_API_KEY")\n',
    'import os as o\nkey = o.environ.get("ACME_API_KEY")\n',
    'from os import getenv as g\nkey = g("ACME_API_KEY")\n',
])
def test_each_way_of_reading_a_key_is_found(tmp_path, code):
    found = guard.readers_in(tree(tmp_path, {"m.py": code}))
    assert list(found) == ["src/agent_friday/m.py"]


@pytest.mark.parametrize("code", [
    'import os\nhome = os.environ.get("FRIDAY_HOME")\n',
    'import os\nos.environ["ACME_API_KEY"] = "x"\n',
    'ACME_API_KEY = "name only"\n',
    'import os\nos.environ.pop("ACME_API_KEY", None)\n',
    'import os\nos.environ.setdefault("ACME_API_KEY", "x")\n',
    'import x\nkey = x.token(1)\n',
    'from os import environ as e\nhome = e.get("FRIDAY_HOME")\n',
])
def test_reading_other_things_or_writing_a_key_is_not_a_read(tmp_path, code):
    assert guard.readers_in(tree(tmp_path, {"m.py": code})) == {}


def test_a_new_module_that_reads_a_key_is_refused_by_name(tmp_path):
    readers = guard.readers_in(tree(tmp_path, {"old.py": 'import os\nos.getenv("A_API_KEY")\n',
                                               "new.py": 'import os\nos.getenv("B_API_KEY")\n'}))
    found = guard.problems(readers, {"src/agent_friday/old.py": ENTRY}, TODAY)
    assert len(found) == 1 and found[0].startswith("src/agent_friday/new.py:2: new raw key reader")


def test_a_listed_module_that_stopped_reading_must_leave_the_list(tmp_path):
    readers = guard.readers_in(tree(tmp_path, {"old.py": "x = 1\n"}))
    found = guard.problems(readers, {"src/agent_friday/old.py": ENTRY}, TODAY)
    assert len(found) == 1 and "delete its entry" in found[0]


@pytest.mark.parametrize("entry,expect", [
    ({**ENTRY, "reason": ""}, "lacks reason"), ({**ENTRY, "approved_by": " "}, "lacks approved_by"),
    ({k: v for k, v in ENTRY.items() if k != "expires"}, "lacks expires"),
    ({**ENTRY, "expires": "soon"}, "not a date"), ({**ENTRY, "expires": "2026-11-15"}, "expired"),
])
def test_an_entry_without_a_reason_approver_or_a_live_expiry_is_refused(tmp_path, entry, expect):
    readers = guard.readers_in(tree(tmp_path, {"old.py": 'import os\nos.getenv("A_API_KEY")\n'}))
    found = guard.problems(readers, {"src/agent_friday/old.py": entry}, TODAY)
    assert len(found) == 1 and expect in found[0]


def test_the_real_tree_agrees_with_the_real_allow_list():
    """If this fails, a module began reading a raw key (or stopped and left a stale entry)."""
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_real_allow_list_is_well_formed():
    allow = json.loads(SCRIPT.with_name("raw_key_readers.allow.json").read_text(encoding="utf-8"))["readers"]
    assert allow and all(set(entry) >= {"reason", "expires", "approved_by"} for entry in allow.values())
