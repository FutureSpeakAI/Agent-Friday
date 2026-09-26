"""Which local images may be sent to a cloud service as a seed or for a
vision check.

`generate_video`, `generate_music` and the vision QA gate read an image from
disk and upload its bytes to a cloud model. A path the model names can point
anywhere on the owner's disk, and uploading a private photo is an outward act.
So a seed image is used without a decision only when it is:

  * inside Friday's own output folders (the creations folders and the office
    documents folder, `action_gate.output_dirs`), which is where the Studio's
    seed picker and every pipeline stage take their seeds from; or
  * a file the owner named in this conversation: the path appears in what the
    owner typed (the trusted side of the provenance ledger,
    `services/taint.py`). A chat attachment is sent as bytes and never becomes
    a path, so it does not come through here.

Anything else waits for the owner: the governance checkpoint classifies the
call as outward (a yes in chat, or an approval card), and the engines refuse
to read the file unless the call is running on that decision
(`action_gate.owner_decision`). Containment is checked with
`paths.contained` on the resolved path, so `..`, symlinks and junctions that
lead out of an output folder do not count as inside it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


def resolve(image_path) -> Optional[Path]:
    """The file a seed argument names, resolved, or None.

    An absolute or home-relative path is taken as written. Anything else is a
    creation: a name (or relative path) inside CREATIONS_DIR, and failing
    that its bare file name there.
    """
    from agent_friday.core import CREATIONS_DIR
    from agent_friday.paths import contained, safe_name
    s = str(image_path or "").strip()
    if not s or "\x00" in s:
        return None
    expanded = os.path.expanduser(s)
    if os.path.isabs(expanded) or (len(expanded) > 1 and expanded[1] == ":"):
        try:
            return Path(os.path.realpath(expanded))
        except Exception:
            return None
    for rel in (s, Path(s).name):
        try:
            cand = contained(CREATIONS_DIR, safe_name(rel) if rel != s else rel)
        except ValueError:
            continue
        if cand.exists():
            return cand
    return None


def in_output_dir(p: Path) -> bool:
    from agent_friday.governance.action_gate import output_dirs
    from agent_friday.paths import contained
    for root in output_dirs():
        try:
            contained(root, str(p))
            return True
        except ValueError:
            continue
    return False


def _named_by_owner(image_path, ledger_key: Optional[str]) -> bool:
    if not ledger_key:
        return False
    try:
        from agent_friday.services import taint
        return taint.origin_of(ledger_key, str(image_path)).kind == "user"
    except Exception:
        return False


def check(image_path, *, ledger_key: Optional[str] = None,
          decided: Optional[str] = None) -> Tuple[bool, str]:
    """(may_be_sent_without_asking, why) for one seed image argument."""
    p = resolve(image_path)
    if p is None or not p.is_file():
        # Nothing to read; the engine reports it as not found. The engine
        # checks again when it reads, so a file that appears later is judged.
        return True, "it names no readable file"
    if in_output_dir(p):
        return True, "it is in Friday's own creations"
    if decided:
        return True, "the owner approved it"
    if _named_by_owner(image_path, ledger_key):
        return True, "the owner named this file in the conversation"
    return False, (f"it would upload {p.name}, a file outside Friday's "
                   f"creations that the owner did not name, to a cloud service")


def seed_args(tool_name: str, args) -> List[str]:
    a = args or {}
    if tool_name == "generate_video":
        keys: Iterable[str] = ("image_path",)
    elif tool_name == "generate_music":
        keys = ("seed_image_path", "seed_image_paths")
    else:
        return []
    out: List[str] = []
    for k in keys:
        v = a.get(k)
        if isinstance(v, (list, tuple)):
            out.extend(str(x) for x in v if x)
        elif v:
            out.append(str(v))
    return out


def classify(tool_name: str, args, ctx: Optional[dict] = None) -> Tuple[str, str]:
    """(class, why) for a generation call, for the governance checkpoint."""
    from agent_friday.governance.action_gate import INTERNAL, OUTWARD
    key = None
    if ctx:
        from agent_friday.services import taint
        key = taint.ledger_key(ctx)
    for s in seed_args(tool_name, args):
        ok, why = check(s, ledger_key=key)
        if not ok:
            return OUTWARD, why
    return INTERNAL, "internal"


def check_running_call(image_path) -> Tuple[bool, str]:
    """`check` for the tool call running now, from inside an engine."""
    from agent_friday.governance import action_gate
    from agent_friday.services import taint
    return check(image_path, ledger_key=taint.CURRENT_KEY.get(),
                 decided=action_gate.owner_decision())
