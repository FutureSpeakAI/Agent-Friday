"""The briefings folder: one door to write a briefing, one rule for which file is the latest.

Briefings are ``<wiki>/briefings/YYYY-MM-DD.md`` (older ones ``.html``). Two things went wrong when each caller did its
own thing:

* the folder's ``_index.md`` was rebuilt only when the server started, and every writer saved the file without
  touching it, so a server that ran for days listed none of the briefings written since; ``write`` saves the briefing
  and rebuilds the index in the same step;
* a reader that sorted the folder by name took ``_index.md`` (``_`` sorts after the digits) for the latest briefing,
  and another looked only for ``.html``, so a folder full of current ``.md`` briefings looked empty or stopped at the
  date the index was last built; ``dated`` names exactly the files that are briefings and ``latest`` picks the newest.

Guarded by tests/unit/test_briefing_files.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

#: A briefing is named for its date, with ``-pm`` for the afternoon edition. ``_index.md`` and
#: ``2026-11-21-supplement.md`` are not briefings.
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}(-pm)?\.(md|html)$")


def briefings_dir() -> Path:
    from agent_friday.paths import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "wiki" / "briefings"


def dated(directory: Optional[Path] = None) -> List[Path]:
    """The briefing files in the folder, newest first: by date, the afternoon edition after the morning one, ``.md``
    before ``.html`` for the same edition."""
    folder = Path(directory) if directory is not None else briefings_dir()
    try:
        files = [p for p in folder.iterdir() if p.is_file() and DATED.match(p.name)]
    except OSError:
        return []
    return sorted(files, key=lambda p: (p.stem[:10], p.stem.endswith("-pm"), p.suffix == ".md"), reverse=True)


def latest(directory: Optional[Path] = None) -> Optional[Path]:
    """The newest briefing file, or None."""
    files = dated(directory)
    return files[0] if files else None


def write(date_str: str, content: str, directory: Optional[Path] = None) -> Path:
    """Save the day's briefing and rebuild the folder's index. The save is atomic; an index that cannot be rebuilt does
    not fail the briefing (the next write or start-up rebuilds it)."""
    folder = Path(directory) if directory is not None else briefings_dir()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_str)):
        raise ValueError("a briefing is named for its date, YYYY-MM-DD")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("%s.md" % date_str)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    try:
        from agent_friday.services.wiki_engine import write_wiki_index
        write_wiki_index(folder)
    except Exception:
        pass
    return target
