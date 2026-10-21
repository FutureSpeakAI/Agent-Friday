"""Seed a realistic Friday data folder for the clean-machine restore leg.

CI only. FRIDAY_HOME must point at an empty folder. Writes expected.json (to
--expected) for check_restore.py: SHA-256 of every file that must travel, the
files that must not, the vault probe, the governance key's SHA-256 (never the
key) and the ledger's newest record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

if os.environ.get("GITHUB_ACTIONS") != "true":
    sys.exit("seed_home.py runs in CI only (it writes to this machine's keychain)")

ap = argparse.ArgumentParser()
ap.add_argument("--expected", required=True)
ap.add_argument("--over-fresh-install", action="store_true",
                help="the folder may hold what a fresh install left (no setup, no vault)")
args = ap.parse_args()

home = Path(os.environ["FRIDAY_HOME"])
home.mkdir(parents=True, exist_ok=True)
if args.over_fresh_install:
    assert not (home / "vault" / ".vault_config.json").exists() and not (home / ".setup_complete").exists(), \
        "FRIDAY_HOME holds a set-up Friday"
else:
    assert not any(home.iterdir()), "FRIDAY_HOME must be empty"
vault_pass = os.environ["SEED_VAULT_PASSPHRASE"]

from agent_friday.privacy import vault_crypto as vc  # noqa: E402

salt = os.urandom(16)
(home / "vault").mkdir(exist_ok=True)
(home / "vault" / ".vault_config.json").write_text(json.dumps({"salt_hex": salt.hex()}))
probe = b"clean-machine vault probe " + os.urandom(8).hex().encode()
(home / "vault" / "probe.enc").write_bytes(vc.encrypt(probe, vc.derive_key(vault_pass, salt)))

files = {
    "settings.json": json.dumps({"agent_name": "Friday", "voice_speaking_pace": "measured"}),
    ".setup_complete": "1",
    "core_memory/ME.md": "- the owner of this CI home likes tea\n",
    "core_memory/FRIDAY.md": "- answer briefly\n",
    "skills/ci-skill/SKILL.md": "---\nname: ci-skill\ndescription: a CI skill\n---\nSay hello.\n",
    "workspaces/ci-ws/manifest.json": json.dumps({"id": "ci-ws", "name": "CI workspace"}),
    "workspaces/ci-ws/index.html": "<!doctype html><p>CI</p>",
    "conversations/ci-conv.json": json.dumps({"id": "ci-conv", "turns": [{"role": "user", "text": "hi"}]}),
    "wiki/notes/ci.md": "# CI page\n",
    # must never travel
    "providers/keys/anthropic.key": "placeholder-provider-key-not-real",   # pragma: allowlist secret
    "google_accounts/tokens/ci.token.enc": "placeholder-token",
    "mcp_oauth/ci.oauth.enc": "placeholder-oauth",
    "security/vault-passphrase.dpapi": "placeholder-dpapi",
    "secret_key": "placeholder-session-secret",
    "runtime/models/ci.gguf": "x" * 4096,
    "logs/crashes.log": "nothing",
}
(home / "mcp_servers.json").write_text(json.dumps({"servers": {"ci-github": {
    "command": "npx", "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "placeholder-mcp-token-not-real",  # pragma: allowlist secret
                              "GITHUB_ORG": "ci-org"}}}}), encoding="utf-8")
for rel, text in files.items():
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

from agent_friday.services import vault_passphrase  # noqa: E402
stored = vault_passphrase.store(vault_pass)
print("vault passphrase stored in:", stored)

from agent_friday.services import action_ledger  # noqa: E402
action_ledger.migrate_legacy()
action_ledger.append("decision", {"tool": "ci_probe_send", "class": "outward", "decision": "allow",
                                  "surface": "ci"}, durable=True)
action_ledger.append("activity", {"kind": "tool_call", "tool": "ci_probe_tool", "ok": True})
rep = action_ledger.verify()
assert rep["ok"], rep

from agent_friday.governance.proof_of_integrity import get_governance_key  # noqa: E402
gk = get_governance_key()

never = [r for r in files if r.split("/")[0] in ("providers", "google_accounts", "mcp_oauth",
                                                 "security", "runtime", "logs") or r == "secret_key"]
travel = {r: hashlib.sha256((home / r).read_bytes()).hexdigest()
          for r in files if r not in never}
travel["vault/.vault_config.json"] = hashlib.sha256((home / "vault/.vault_config.json").read_bytes()).hexdigest()
travel["vault/probe.enc"] = hashlib.sha256((home / "vault/probe.enc").read_bytes()).hexdigest()
never_hashes = {r: hashlib.sha256((home / r).read_bytes()).hexdigest() for r in never}
expected = {"travel": travel, "never": never_hashes, "vault_probe": probe.decode(),
            "governance_key_sha256": hashlib.sha256(gk).hexdigest(),
            "ledger_last_seq": rep["last_seq"], "mcp_server": "ci-github", "private_words": ["likes tea", "ci-conv"]}
Path(args.expected).write_text(json.dumps(expected, indent=1), encoding="utf-8")
print("seeded", len(files), "files; ledger", rep["records"], "records")
