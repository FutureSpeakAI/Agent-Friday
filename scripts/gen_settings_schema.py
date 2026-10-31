"""Write (or check) src/agent_friday/resources/settings.schema.json.

    python scripts/gen_settings_schema.py           # rewrite it
    python scripts/gen_settings_schema.py --check   # exit 1 if it is stale

The schema is DEFAULT_SETTINGS' shape plus the choices and ranges
POST /api/settings enforces (services/settings_schema.py). Rewrite it on
purpose, after adding or changing a setting, and review the diff.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("FRIDAY_TESTING", "1")
# Importing the app writes into its home; never the person's own.
import tempfile  # noqa: E402
os.environ.setdefault("FRIDAY_HOME", tempfile.mkdtemp(prefix="friday-check-"))


def main(argv) -> int:
    from agent_friday.services import settings_schema as ss
    text = ss.dumps(ss.build())
    if "--check" in argv:
        current = ss.SCHEMA_FILE.read_text(encoding="utf-8") if ss.SCHEMA_FILE.exists() else ""
        if current != text:
            print("[settings-schema] STALE - run: python scripts/gen_settings_schema.py, then review the diff")
            return 1
        print("[settings-schema] OK - the schema matches DEFAULT_SETTINGS and the POST checks")
        return 0
    ss.SCHEMA_FILE.write_text(text, encoding="utf-8", newline="\n")
    print("[settings-schema] wrote %s" % ss.SCHEMA_FILE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
