"""Check a restore on a machine that never saw the original (CI only).

FRIDAY_HOME points at the restored folder. Exits non-zero on any failure and
prints one JSON result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

if os.environ.get("GITHUB_ACTIONS") != "true":
    sys.exit("check_restore.py runs in CI only")

ap = argparse.ArgumentParser()
ap.add_argument("--expected", required=True)
ap.add_argument("--vault-passphrase-from-env", action="store_true",
                help="this OS has no keychain backend: take the vault passphrase from the env")
args = ap.parse_args()
exp = json.loads(Path(args.expected).read_text(encoding="utf-8"))
home = Path(os.environ["FRIDAY_HOME"])
fails, result = [], {}


def check(name, ok, detail=""):
    result[name] = bool(ok)
    if not ok:
        fails.append(f"{name}: {detail}")


for rel, digest in exp["travel"].items():
    p = home / rel
    check("travelled:" + rel, p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest,
          "missing or different")
for rel, digest in exp["never"].items():
    p = home / rel
    # absent, or a file this machine made for itself (a new session secret)
    check("not_carried:" + rel, not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest,
          "a file that must not travel was restored")

mcp = json.loads((home / "mcp_servers.json").read_text(encoding="utf-8"))
env = mcp["servers"][exp["mcp_server"]]["env"]
check("mcp_token_not_carried", env.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "" and env.get("GITHUB_ORG") == "ci-org",
      json.dumps(env))

from agent_friday.governance.proof_of_integrity import get_governance_key  # noqa: E402
gk = get_governance_key()
check("governance_key_installed", hashlib.sha256(gk).hexdigest() == exp["governance_key_sha256"],
      "the governance key on this machine is not the one the bundle carried")

from agent_friday.privacy import vault_crypto as vc  # noqa: E402
from agent_friday.services import vault_passphrase  # noqa: E402
if args.vault_passphrase_from_env:
    vp = os.environ.get("SEED_VAULT_PASSPHRASE", "")
else:
    os.environ.pop("FRIDAY_VAULT_PASSPHRASE", None)
    os.environ.pop("FRIDAY_PASSWORD", None)
    vault_passphrase.reset_cache()
    vp, src = vault_passphrase.resolve(use_cache=False)
    result["vault_passphrase_from"] = src
salt = vc.load_salt(home / "vault" / ".vault_config.json")
try:
    got = vc.decrypt((home / "vault" / "probe.enc").read_bytes(), vc.derive_key(vp, salt)).decode()
except Exception as e:  # noqa: BLE001
    got = "error: %s" % type(e).__name__
check("vault_opens", got == exp["vault_probe"], got)

from agent_friday.services import action_ledger  # noqa: E402
rep = action_ledger.verify(force=True)
check("ledger_verifies", rep["ok"] and rep["intact"], json.dumps(rep.get("problems", [])[:3]))
kinds = [e["kind"] for e in action_ledger.entries(newest_first=False)]
check("ledger_has_old_receipts", "decision" in kinds, kinds[:10])
check("ledger_records_the_restore", "restore" in kinds, kinds[-5:])

from agent_friday.governance import action_gate  # noqa: E402
why = action_gate.outward_hold() or ""
check("outward_held_after_restore", "restored" in why, why)
from agent_friday.services import friday_bundle  # noqa: E402
check("resume_works", friday_bundle.resume_after_restore("ci") and not action_gate.outward_hold(),
      action_gate.outward_hold() or "")

from agent_friday.services import doctor  # noqa: E402
blob = json.dumps(doctor.build())
check("doctor_has_no_content", not any(w in blob for w in exp["private_words"]), "a private word in the report")
check("doctor_sees_a_backup_or_none", '"backup"' in blob, "")

result["failures"] = fails
print(json.dumps(result, indent=1))
sys.exit(1 if fails else 0)
