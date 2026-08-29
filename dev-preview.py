"""Launch Friday against a THROWAWAY home, for looking at the UI.

Never point a dev preview at the real ~/.friday. Booting the server runs the
vault passphrase migration, the plaintext-vault migration and the wiki merge;
none of those belong in a UI review, and two of them touch credentials.

This redirects the profile to a temp directory and asserts the redirect took
before importing anything from the package -- the same shape, and for the same
reason, as the isolation assertion in the upgrade rehearsal.

Not part of the shipped product; it is a developer tool that lives beside the
launch.json that invokes it.
"""
import os
import sys
import tempfile
from pathlib import Path

HOME = Path(os.environ.get("FRIDAY_DEV_HOME")
            or tempfile.mkdtemp(prefix="friday_preview_"))
HOME.mkdir(parents=True, exist_ok=True)

os.environ["USERPROFILE"] = str(HOME)
os.environ["HOME"] = str(HOME)
os.environ["HOMEDRIVE"] = HOME.drive or "C:"
os.environ["HOMEPATH"] = str(HOME)[len(HOME.drive):] or "\\"
os.environ["FRIDAY_PORT"] = os.environ.get("FRIDAY_PORT", "3077")
os.environ["FRIDAY_SKIP_MODEL"] = "1"
# No passphrase: the consent flow is what sets one, and this is the flow under
# review. Clear anything inherited from the developer's shell.
os.environ.pop("FRIDAY_PASSWORD", None)
os.environ.pop("FRIDAY_VAULT_PASSPHRASE", None)

seen = Path.home()
if str(seen).lower() != str(HOME).lower():
    sys.exit("ISOLATION FAILED: Path.home() is %s, not %s. Refusing to start."
             % (seen, HOME))

print("[dev-preview] home     : %s" % HOME)
print("[dev-preview] port     : %s" % os.environ["FRIDAY_PORT"])

sys.path.insert(0, str(Path(__file__).parent / "src"))
import runpy  # noqa: E402

runpy.run_path(str(Path(__file__).parent / "server.py"), run_name="__main__")
