"""
Agent Friday — Custom Distributions
Inspired by patterns in Goose (Apache-2.0). All code is original.

Layered config profiles for different user personas (journalist, developer, etc.).
"""
import yaml, os, sys
from pathlib import Path

DISTROS_DIR = Path.home() / ".friday" / "distros"
DISTROS_DIR.mkdir(parents=True, exist_ok=True)

BUILTIN_DISTROS = {
    "default": {
        "name": "default",
        "description": "Standard Agent Friday — all workspaces, balanced for general use",
        "default_workspaces": ["home", "news", "messages", "calendar", "career", "code", "wiki", "contacts", "sites", "settings", "studio"],
        "default_providers": ["anthropic", "google-gemini", "ollama-local"],
        "default_recipes": ["morning-briefing"],
        "system_prompt_overrides": {},
        "show_all_workspaces": False,
        "dock_layout": "standard",
    },
    "journalist": {
        "name": "journalist",
        "description": "News-heavy configuration with source trust, editorial tools, and research focus",
        "default_workspaces": ["home", "news", "messages", "calendar", "wiki", "contacts", "settings", "studio", "content"],
        "default_providers": ["anthropic", "google-gemini", "ollama-local"],
        "default_recipes": ["morning-briefing", "weekly-review"],
        "system_prompt_overrides": {
            "base_personality": "You are Friday, an AI research assistant built for investigative journalists. Prioritize source verification, fact-checking, and editorial rigor. Always cite sources. Be skeptical of claims without evidence."
        },
        "news_feeds_preset": "investigative",
        "show_all_workspaces": False,
        "dock_layout": "journalism",
    },
    "developer": {
        "name": "developer",
        "description": "Code-heavy configuration with GitHub integration, CI/CD awareness, and dev tools",
        "default_workspaces": ["home", "code", "news", "messages", "calendar", "wiki", "settings", "studio"],
        "default_providers": ["anthropic", "ollama-local"],
        "default_recipes": [],
        "system_prompt_overrides": {
            "base_personality": "You are Friday, an AI pair programmer and development assistant. Focus on code quality, testing, architecture decisions, and developer productivity. Be precise with technical details."
        },
        "show_all_workspaces": False,
        "dock_layout": "developer",
    },
    "researcher": {
        "name": "researcher",
        "description": "Deep-research configuration — long-form synthesis, citations, wiki and source trust",
        "default_workspaces": ["home", "wiki", "news", "code", "messages", "calendar", "contacts", "settings", "studio"],
        "default_providers": ["anthropic", "google-gemini", "ollama-local"],
        "default_recipes": ["morning-briefing"],
        "system_prompt_overrides": {
            "base_personality": "You are Friday, a research analyst. Prioritize depth, accuracy, and citations. Synthesize across sources, distinguish primary from secondary evidence, and flag uncertainty explicitly. Prefer structured, well-organized long-form answers."
        },
        "show_all_workspaces": False,
        "dock_layout": "research",
    },
    "executive": {
        "name": "executive",
        "description": "Executive configuration — briefings, calendar, finance, concise decision support",
        "default_workspaces": ["home", "calendar", "messages", "news", "finance", "contacts", "settings", "studio"],
        "default_providers": ["anthropic", "google-gemini"],
        "default_recipes": ["morning-briefing", "weekly-review"],
        "system_prompt_overrides": {
            "base_personality": "You are Friday, an executive chief of staff. Lead with the bottom line, then the why. Be concise and decisive, surface risks and tradeoffs, and protect the user's time and attention."
        },
        "show_all_workspaces": False,
        "dock_layout": "executive",
    },
}


class Distribution:
    def __init__(self, data: dict):
        self.name = data.get("name", "default")
        self.description = data.get("description", "")
        self.default_workspaces = data.get("default_workspaces", [])
        self.default_providers = data.get("default_providers", [])
        self.default_recipes = data.get("default_recipes", [])
        self.system_prompt_overrides = data.get("system_prompt_overrides", {})
        self.show_all_workspaces = data.get("show_all_workspaces", False)
        self.dock_layout = data.get("dock_layout", "standard")
        self.raw = data


def load_distro(name: str) -> Distribution:
    # Check custom distros first
    custom_path = DISTROS_DIR / f"{name}.yaml"
    if custom_path.exists():
        with open(custom_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return Distribution(data)
    # Fall back to built-in
    if name in BUILTIN_DISTROS:
        return Distribution(BUILTIN_DISTROS[name])
    raise ValueError(f"Unknown distribution: {name}")


def list_distros() -> list:
    # Built-ins win; custom YAMLs that aren't shadowing a built-in are appended.
    # (ensure_builtin_distros writes the built-ins to disk too, so dedup by name.)
    seen = set()
    distros = []
    for name, data in BUILTIN_DISTROS.items():
        distros.append({"name": name, "description": data["description"], "builtin": True})
        seen.add(name)
    for f in DISTROS_DIR.glob("*.yaml"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            nm = data.get("name", f.stem)
            if nm in seen:
                continue
            seen.add(nm)
            distros.append({"name": nm, "description": data.get("description", ""), "builtin": False})
        except Exception:
            pass
    return distros


def save_distro(data: dict) -> str:
    name = data.get("name", "custom").replace(" ", "-").lower()
    path = DISTROS_DIR / f"{name}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return str(path)


def apply_distro(name: str) -> dict:
    """Return the settings delta that activates a distribution, persisting its
    persona personality override as a side effect.

    Pure preset application — there is NO separate code path: the delta only sets
    standard settings keys the rest of the app already honors (distribution,
    show_all_workspaces, dock_layout). The caller writes it via core._save_settings
    so the change flows through the single settings source of truth. Raises
    ValueError for an unknown distribution.
    """
    distro = load_distro(name)
    delta = {
        "distribution": distro.name,
        "show_all_workspaces": bool(distro.show_all_workspaces),
        "dock_layout": distro.dock_layout or "standard",
    }
    persona = (distro.system_prompt_overrides or {}).get("base_personality")
    if persona:
        try:
            import agent_friday.core as core
            core._save_agent_personality(persona)
        except Exception:
            pass
    return delta


def get_active_distro() -> str:
    """Check command line args for --distro flag."""
    for i, arg in enumerate(sys.argv):
        if arg == "--distro" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return os.environ.get("FRIDAY_DISTRO", "default")


def ensure_builtin_distros():
    for name, data in BUILTIN_DISTROS.items():
        path = DISTROS_DIR / f"{name}.yaml"
        if not path.exists():
            save_distro(data)

ensure_builtin_distros()
