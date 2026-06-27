"""
Agent Friday — Hint System
Inspired by Goose's .goosehints (Apache-2.0). All code is original.

.fridayhints files customize Friday's behavior per directory/workspace.
"""
import yaml, os
from pathlib import Path
from functools import lru_cache

HINTS_FILENAME = ".fridayhints"

class FridayHints:
    def __init__(self, data: dict = None, source: str = None):
        data = data or {}
        self.system_prompt_additions = data.get("system_prompt_additions", "")
        self.preferred_model = data.get("preferred_model", None)
        self.tools_enabled = data.get("tools_enabled", None)
        self.tools_disabled = data.get("tools_disabled", [])
        self.personality_overrides = data.get("personality_overrides", {})
        self.workspace_config = data.get("workspace_config", {})
        self.context_notes = data.get("context_notes", "")
        self.source = source

    def merge(self, other: "FridayHints") -> "FridayHints":
        merged = FridayHints()
        merged.system_prompt_additions = "\n".join(filter(None, [
            self.system_prompt_additions, other.system_prompt_additions
        ]))
        merged.preferred_model = other.preferred_model or self.preferred_model
        merged.tools_enabled = other.tools_enabled or self.tools_enabled
        merged.tools_disabled = list(set(self.tools_disabled + other.tools_disabled))
        merged.personality_overrides = {**self.personality_overrides, **other.personality_overrides}
        merged.workspace_config = {**self.workspace_config, **other.workspace_config}
        merged.context_notes = "\n".join(filter(None, [self.context_notes, other.context_notes]))
        merged.source = other.source or self.source
        return merged


def load_hints_from_file(path: str) -> FridayHints:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return FridayHints(data, source=path)
    except Exception:
        return FridayHints(source=path)


def find_hints_for_path(target_path: str) -> FridayHints:
    """Walk up from target_path, collecting .fridayhints files (deepest wins)."""
    hints_chain = []
    current = Path(target_path).resolve()
    for _ in range(20):  # Max depth
        hints_file = current / HINTS_FILENAME
        if hints_file.exists():
            hints_chain.append(load_hints_from_file(str(hints_file)))
        if current.parent == current:
            break
        current = current.parent

    if not hints_chain:
        return FridayHints()

    # Merge from root to leaf (leaf overrides root)
    hints_chain.reverse()
    result = hints_chain[0]
    for h in hints_chain[1:]:
        result = result.merge(h)
    return result


def get_global_hints() -> FridayHints:
    """Load hints from ~/.friday/.fridayhints (global defaults)."""
    global_path = Path.home() / ".friday" / HINTS_FILENAME
    if global_path.exists():
        return load_hints_from_file(str(global_path))
    return FridayHints()


def get_workspace_hints(workspace_name: str) -> FridayHints:
    """Load hints for a specific workspace."""
    ws_path = Path.home() / ".friday" / "workspaces" / workspace_name / HINTS_FILENAME
    if ws_path.exists():
        return load_hints_from_file(str(ws_path))
    return FridayHints()
