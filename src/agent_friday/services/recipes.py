"""
Agent Friday — Recipe/Workflow System
Inspired by patterns in Goose (Apache-2.0). All code is original.

YAML-based portable workflows with parameters, templates, and step sequencing.
"""
import yaml, os, re, time, threading, uuid
from pathlib import Path
from datetime import datetime

RECIPES_DIR = Path.home() / ".friday" / "recipes"
RECIPES_DIR.mkdir(parents=True, exist_ok=True)

# --- Schema ---
RECIPE_SCHEMA_KEYS = {"name", "description", "author", "version", "variables", "steps", "triggers"}

# $variable references inside step params/prompts ("$company AI strategy").
_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _interpolate(value, variables, unset_fmt="<unset:{}>"):
    """Substitute every $var token inside a string param with its value."""
    if not isinstance(value, str):
        return value
    return _VAR_RE.sub(
        lambda m: str(variables[m.group(1)]) if m.group(1) in variables
        else unset_fmt.format(m.group(1)),
        value,
    )

class RecipeValidationError(Exception):
    pass

class Recipe:
    def __init__(self, data: dict, path: str = None):
        self.name = data.get("name", "unnamed")
        self.description = data.get("description", "")
        self.author = data.get("author", "unknown")
        self.version = data.get("version", "1.0")
        self.variables = data.get("variables", {})
        self.steps = data.get("steps", [])
        self.triggers = data.get("triggers", ["manual"])
        self.path = path
        self.raw = data

    def validate(self, available_tools: list = None):
        errors = []
        if not self.name:
            errors.append("Recipe must have a name")
        if not self.steps:
            errors.append("Recipe must have at least one step")
        for i, step in enumerate(self.steps):
            if "tool" not in step and "prompt" not in step:
                errors.append(f"Step {i+1} must have either 'tool' or 'prompt'")
            if "tool" in step and available_tools and step["tool"] not in available_tools:
                errors.append(f"Step {i+1} references unknown tool: {step['tool']}")
        # Check variables referenced in steps are declared (chain results exempt)
        for i, step in enumerate(self.steps):
            params = step.get("params", {})
            for k, v in params.items():
                if not isinstance(v, str):
                    continue
                for ref in _VAR_RE.findall(v):
                    if ref not in self.variables and not ref.startswith("result_"):
                        errors.append(f"Step {i+1} param '{k}' references undefined variable '{ref}'")
        if errors:
            raise RecipeValidationError("; ".join(errors))
        return True

    def dry_run(self, variables: dict = None):
        """Simulate execution without side effects."""
        merged = {**{k: v.get("default", "") for k, v in self.variables.items()}, **(variables or {})}
        plan = []
        for i, step in enumerate(self.steps):
            resolved_params = {k: _interpolate(v, merged)
                               for k, v in step.get("params", {}).items()}
            plan.append({
                "step": i + 1,
                "action": step.get("tool") or "generate",
                "params": resolved_params,
                "description": step.get("description", ""),
            })
        return plan


def load_recipe(path: str) -> Recipe:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Recipe(data, path=path)


def list_recipes() -> list:
    recipes = []
    for f in RECIPES_DIR.glob("*.yaml"):
        try:
            r = load_recipe(str(f))
            recipes.append({"name": r.name, "description": r.description, "author": r.author,
                           "version": r.version, "path": str(f), "triggers": r.triggers})
        except Exception as e:
            recipes.append({"name": f.stem, "error": str(e), "path": str(f)})
    return recipes


def save_recipe(data: dict) -> str:
    name = data.get("name", "unnamed").replace(" ", "-").lower()
    path = RECIPES_DIR / f"{name}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return str(path)


def run_recipe(recipe: Recipe, variables: dict = None, tool_executor=None):
    """Execute a recipe. tool_executor(tool_name, params) -> result"""
    merged_vars = {**{k: v.get("default", "") for k, v in recipe.variables.items()}, **(variables or {})}
    results = []
    context = {"variables": merged_vars, "results": results}

    for i, step in enumerate(recipe.steps):
        resolved_params = {}
        for k, v in step.get("params", {}).items():
            if isinstance(v, str) and v.startswith("$result_"):
                # Exact prior-step reference keeps the raw result object.
                idx = int(v[len("$result_"):]) - 1
                resolved_params[k] = results[idx] if 0 <= idx < len(results) else ""
            else:
                resolved_params[k] = _interpolate(v, merged_vars, unset_fmt="")

        if "tool" in step and tool_executor:
            result = tool_executor(step["tool"], resolved_params)
        elif "prompt" in step and tool_executor:
            prompt_text = _interpolate(step["prompt"], merged_vars, unset_fmt="")
            result = tool_executor("_generate_text", {"prompt": prompt_text})
        else:
            result = f"[dry] Step {i+1}: {step.get('tool', 'generate')}"

        results.append(result)

    return results


# --- Built-in recipes (created on first run) ---
BUILTIN_RECIPES = [
    {
        "name": "morning-briefing",
        "description": "Start your day with calendar, email highlights, and top news",
        "author": "FutureSpeak.AI",
        "version": "1.0",
        "variables": {},
        "steps": [
            {"tool": "query_calendar", "params": {}, "description": "Check today's schedule"},
            {"tool": "search_email", "params": {"query": "is:unread is:important"}, "description": "Find urgent emails"},
            {"tool": "search_news", "params": {"query": "top stories"}, "description": "Get news highlights"},
        ],
        "triggers": ["manual", "scheduled:07:00"]
    },
    {
        "name": "research-company",
        "description": "Research a company and draft outreach",
        "author": "FutureSpeak.AI",
        "version": "1.0",
        "variables": {"company": {"description": "Company name to research", "required": True}},
        "steps": [
            {"tool": "search_web", "params": {"query": "$company AI strategy leadership"}, "description": "Web research"},
            {"prompt": "Summarize what you found about $company and identify the key decision makers.", "description": "Analyze findings"},
            {"prompt": "Draft a brief, personalized outreach email to the head of AI at $company.", "description": "Draft outreach"},
        ],
        "triggers": ["manual"]
    },
    {
        "name": "weekly-review",
        "description": "Generate a weekly productivity and progress review",
        "author": "FutureSpeak.AI",
        "version": "1.0",
        "variables": {},
        "steps": [
            {"tool": "query_calendar", "params": {"range": "week"}, "description": "This week's meetings"},
            {"tool": "search_email", "params": {"query": "newer_than:7d is:sent"}, "description": "Emails sent this week"},
            {"prompt": "Based on the calendar and sent emails, write a brief weekly review: what was accomplished, what's pending, and what to focus on next week.", "description": "Generate review"},
        ],
        "triggers": ["manual", "scheduled:fri:17:00"]
    },
]


def ensure_builtin_recipes():
    for recipe_data in BUILTIN_RECIPES:
        path = RECIPES_DIR / f"{recipe_data['name']}.yaml"
        if not path.exists():
            save_recipe(recipe_data)


# Auto-create built-ins on import
ensure_builtin_recipes()
