"""The first backup on a freshly installed, freshly set up Friday (CI only,
north star §34.4-9).

FRIDAY_HOME points at the new data folder (setup complete, nothing scheduled)
and FRIDAY_BUNDLE_PASSPHRASE holds the bundle passphrase. With the installed
Python and the installed CLI:

  offered     the first backup is due and names nothing in its way
  turned_on   `friday bundle schedule on --out <folder> --every daily`
  made        `friday bundle schedule run`: one scheduled bundle, verified
  no_offer    the offer is no longer due: scheduled bundles are the yes
  doctor      Doctor's backup check reports healthy, scheduled
  turned_off  `friday bundle schedule off` removes the kept passphrase

Exits non-zero on any failure and prints one JSON result.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if os.environ.get("GITHUB_ACTIONS") != "true":
    sys.exit("check_first_backup.py runs in CI only")

ap = argparse.ArgumentParser()
ap.add_argument("--folder", required=True, help="where the scheduled bundles go (outside the data folder)")
args = ap.parse_args()
folder = Path(args.folder)
folder.mkdir(parents=True, exist_ok=True)
fails, result = [], {}


def check(name, ok, detail=""):
    result[name] = {"ok": bool(ok), "detail": str(detail)[:600]}
    if not ok:
        fails.append(name)


def cli(*words):
    p = subprocess.run([sys.executable, "-m", "agent_friday.cli", "bundle", "schedule", *words],
                       capture_output=True, text=True, timeout=900)
    return p.returncode, (p.stdout + p.stderr).strip()


from agent_friday.services import backup_schedule, doctor, first_backup, friday_bundle  # noqa: E402

try:
    s = first_backup.status()
    check("offered", s["due"] and s["setup_done"] and not s["scheduled"], s)
    code, out = cli("on", "--out", str(folder), "--every", "daily", "--keep", "2")
    check("turned_on", code == 0 and backup_schedule.state().get("enabled"), out)
    code, out = cli("run")
    made = sorted(folder.glob("friday-scheduled-*.fridaybundle"))
    verified = False
    if made:
        try:
            verified = bool(friday_bundle.verify(made[-1], os.environ["FRIDAY_BUNDLE_PASSPHRASE"]).get("ok"))
        except Exception as e:  # noqa: BLE001
            out += " | verify: %s" % e
    check("made", code == 0 and len(made) == 1 and verified, "%s | %s" % (out, [m.name for m in made]))
    s = first_backup.status()
    check("no_offer", not s["due"] and s["why"] == "scheduled", s)
    backup = next((c for c in doctor.build()["checks"] if c.get("id") == "backup"), {})
    check("doctor", backup.get("state") == "healthy" and "scheduled" in str(backup.get("code")), backup)
except Exception as e:  # noqa: BLE001
    check("script", False, repr(e))
finally:
    # always: the kept passphrase leaves the keychain whatever happened above
    code, out = cli("off")
    try:
        off = not backup_schedule.state().get("enabled")
    except Exception as e:  # noqa: BLE001
        off, out = False, "%s | %r" % (out, e)
    check("turned_off", code == 0 and off, out)

print(json.dumps({"ok": not fails, "failed": fails, "checks": result}, indent=1, default=str))
sys.exit(1 if fails else 0)
