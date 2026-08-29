#!/usr/bin/env python3
"""
Agent Friday — Interactive Setup Wizard
FutureSpeak.AI · Asimov's Mind

Usage:
  python setup_wizard.py           Full setup (all steps)
  python setup_wizard.py --quick   Minimal setup (name + API keys only)
  friday setup                     Via the CLI
  friday setup --quick
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.align import Align
    from rich.rule import Rule
    from rich import box
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "rich", "--quiet"], check=True)
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.align import Align
    from rich.rule import Rule
    from rich import box

console = Console()

# ── Paths ────────────────────────────────────────────────────────
HERE = Path(__file__).parent.resolve()
PROJ_ROOT = HERE.parent.parent  # src/agent_friday/ → repo root
FRIDAY_DIR = Path.home() / ".friday"
SETTINGS_FILE = FRIDAY_DIR / "settings.json"
CONFIG_YAML = FRIDAY_DIR / "config.yaml"
SETUP_MARKER = FRIDAY_DIR / ".setup_complete"

# ── Data ─────────────────────────────────────────────────────────

# NO hardcoded model lists (spec A2): provider model lineups resolve lazily
# via _anthropic_models() / _creative_engines() from the hosted discovery
# cache and the provider registry. The two-entry literals inside those
# functions are an EMERGENCY OFFLINE FALLBACK ONLY (two ids max, by design).

def _anthropic_models() -> list:
    """(id, label, desc) rows for the Claude lineup — hosted discovery cache
    (live /v1/models fetch) first, then the provider registry statics."""
    try:
        from agent_friday.services.model_discovery import cached_models
        rows = [(m["id"], m.get("label") or m["id"],
                 "Live from Anthropic /v1/models")
                for m in cached_models("anthropic")[0] if m.get("id")]
        if rows:
            return rows
    except Exception:
        pass
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        prov = get_provider_registry().get_provider("anthropic") or {}
        meta = prov.get("model_meta") or {}
        rows = [(mid, (meta.get(mid) or {}).get("label") or mid,
                 "From the provider registry")
                for mid in prov.get("models") or []]
        if rows:
            return rows
    except Exception:
        pass
    # offline-fallback-only (two ids max by design — spec A2)
    return [("claude-sonnet-5", "Claude Sonnet 5", "Frontier default (offline fallback)"),
            ("claude-opus-5", "Claude Opus 5", "Deep reasoning (offline fallback)")]


def _creative_engines() -> list:
    """(id, label, desc) rows for the creative-engine picker, from the same
    catalog the UI renders (registry `creative` role)."""
    try:
        from agent_friday.services.model_catalog import build_catalog
        rows = [(e["id"], e.get("label") or e["id"],
                 e.get("provider_label") or "")
                for e in build_catalog().get("roles", {}).get("creative") or []]
        if rows:
            return rows
    except Exception:
        pass
    # offline-fallback-only (two ids max by design — spec A2)
    return [("gemini-nano-banana-2", "Gemini Nano Banana 2", "Image generation (offline fallback)"),
            ("veo-3", "Google Veo", "Video generation (offline fallback)")]


PROVIDERS = [
    {
        "id": "anthropic",
        "name": "Anthropic",
        "desc": "Claude — best reasoning, cLaws certified",
        "tag": "RECOMMENDED",
        "key_hint": "sk-ant-...",
        "key_url": "console.anthropic.com",
        # Resolved lazily at pick time (dynamic catalog, spec A2).
        "models": _anthropic_models,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "desc": "GPT-4o and o-series models (coming in v5)",
        "tag": "COMING SOON",
        "key_hint": "sk-...",
        "key_url": "platform.openai.com",
        "models": [],
    },
    {
        "id": "ollama",
        "name": "Ollama (local)",
        "desc": "Llama 3, Mistral and other local models (coming in v5)",
        "tag": "COMING SOON",
        "key_hint": "(no key needed)",
        "key_url": "ollama.ai",
        "models": [],
    },
]


VOICE_PERSONAS = [
    ("Aoede",  "Warm, confident female  — calm and professional"),
    ("Puck",   "Energetic male          — bright and quick"),
    ("Charon", "Deep, authoritative     — gravitas and weight"),
    ("Kore",   "Clear, neutral female   — crisp and precise"),
    ("Leda",   "Soft, thoughtful female — gentle and reflective"),
]

EVOLUTION_STRUCTURES = [
    (0,  "CUBES",       "Genesis Lattice",        "Crystalline birth — the origin"),
    (1,  "ICOSAHEDRON", "Sacred Sphere",          "Perfect geometry — pure potential"),
    (2,  "NETWORK",     "Shannon Network",        "Signal and noise — communication"),
    (3,  "DOME",        "Geodesic Cathedral",     "Buckminster Fuller's dream"),
    (4,  "ASTROLABE",   "Lovelace Astrolabe",     "Ada Lovelace's celestial engine"),
    (5,  "TESSERACT",   "Von Neumann Tesseract",  "Four-dimensional thinking"),
    (6,  "QUANTUM",     "Dirac Probability",      "The quantum realm — wave collapse"),
    (7,  "MANDELBROT",  "Mandelbrot Set",         "Infinite complexity at every scale"),
    (8,  "MOBIUS",      "Turing Möbius",          "Alan Turing's infinite loop"),
    (9,  "GRID",        "Ocean of Light",         "The luminous grid — vaporwave"),
    (10, "CABLES",      "Fibonacci Nerve",        "Nature's golden spiral"),
    (11, "NONE",        "Transcendence",          "Beyond form — pure consciousness"),
    (12, "EDEN",        "Giga Earth (Rez)",       "Tribute to Rez — the beginning"),
]

CONNECTORS = [
    ("gmail",    "Gmail",          "Search inbox, draft emails"),
    ("calendar", "Google Calendar","Read upcoming events, schedule"),
    ("slack",    "Slack",          "Read channels, send messages (v5)"),
    ("notion",   "Notion",         "Read/write pages (v5)"),
]

ASCII_BANNER = r"""
    ╔═══════════════════════════════════════════════════╗
    ║                                                   ║
    ║     █████╗  ██████╗ ███████╗███╗   ██╗████████╗   ║
    ║    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝   ║
    ║    ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║      ║
    ║    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║      ║
    ║    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║      ║
    ║    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝      ║
    ║                                                   ║
    ║           F R I D A Y                             ║
    ║           by FutureSpeak.AI                       ║
    ║                                                   ║
    ╚═══════════════════════════════════════════════════╝
"""


# ── Config I/O ────────────────────────────────────────────────────

def _load_config() -> dict:
    if CONFIG_YAML.exists():
        try:
            import yaml
            with open(CONFIG_YAML, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _save_config(config: dict):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
        with open(CONFIG_YAML, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True,
                      sort_keys=False)
    except ImportError:
        pass
    SETTINGS_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


# -- Routing mode follows the provider you picked ------------------------

# Providers whose answer means "run it on this machine".
_LOCAL_PROVIDER_IDS = ("ollama", "local", "ollama-local")


def _default_routing() -> dict | None:
    """DEFAULT_SETTINGS' model_routing block, or None if unreadable.

    None is a real answer and callers must honour it. _save_config writes
    settings.json with a raw json.dumps and no merge, and
    core._load_settings_raw replaces top-level keys wholesale rather than
    deep-merging them -- so persisting a PARTIAL model_routing would delete
    ollama_url, vault_local_only, fallback_to_cloud and the rest. A silent
    reset wearing the shape of a fix. If the defaults cannot be read, write
    nothing.
    """
    try:
        from agent_friday.core import DEFAULT_SETTINGS
        block = (DEFAULT_SETTINGS or {}).get("model_routing")
        return dict(block) if block else None
    except Exception:
        return None


def _routing_block_for(provider_id: str, existing: dict,
                       previous_provider: str | None = None) -> dict | None:
    """The complete model_routing block this provider choice implies.

    The wizard asks "Choose your primary AI provider" and offers Ollama
    (local) as one of three answers. It wrote that answer to `provider` and
    never touched `model_routing.mode` -- and `mode` is what actually routes
    a turn. So every fresh install landed on the factory `cloud_only`
    whatever was answered, including by someone who had just chosen to run
    on their own machine.

    That stayed invisible while routes/chat.py silently rescued keyless
    turns onto Ollama. Scoping that rescue out of cloud_only (Janet chose
    cloud only on 2026-08-26 and was answered locally anyway) makes this
    gap load-bearing in the other direction: without this, someone who
    picked a local model and gave no cloud key would be told to add one.

    An answer already on disk is left alone unless the provider changed in
    THIS run. Setup is not the only place mode can be set -- Settings ->
    Intelligence writes it too -- and recomputing a value the user has
    already chosen, on every run, is the shape of the seat-binding defect
    of 2026-08-24. Changing the provider answer, though, IS an instruction.
    """
    base = _default_routing()
    if base is None:
        return None
    base.update(existing or {})

    provider_changed = (previous_provider is None
                        or previous_provider != provider_id)
    if (existing or {}).get("mode") and not provider_changed:
        return base

    base["mode"] = ("local_preferred"
                    if provider_id in _LOCAL_PROVIDER_IDS else "cloud_only")
    return base


# ── Key Validation ────────────────────────────────────────────────

def _validate_anthropic(key: str):
    """Returns (True/False/None, message)."""
    try:
        from anthropic import Anthropic
        c = Anthropic(api_key=key)
        c.models.list()
        return True, "Valid"
    except Exception as e:
        s = str(e).lower()
        if "auth" in s or "401" in s or "invalid" in s or "api_key" in s:
            return False, "Invalid key"
        return None, f"Could not connect ({type(e).__name__})"


def _validate_gemini(key: str):
    """Returns (True/False/None, message)."""
    try:
        from google import genai
        c = genai.Client(api_key=key)
        next(iter(c.models.list()), None)
        return True, "Valid"
    except Exception as e:
        s = str(e).lower()
        if "api key" in s or "401" in s or "403" in s or "invalid" in s:
            return False, "Invalid key"
        return None, f"Could not connect ({type(e).__name__})"


def _test_key(label: str, key: str, validator, required: bool = True) -> str:
    """Ask for a key, validate it immediately, loop until valid or skipped."""
    while True:
        key = Prompt.ask(
            f"  [cyan]{label}[/cyan]",
            password=True,
            default=key or "",
        )
        if not key:
            if not required or Confirm.ask(
                f"  [yellow]No key entered. Skip {label}?[/yellow]", default=not required
            ):
                return ""
            continue

        with console.status(f"  Validating {label}...", spinner="dots"):
            ok, msg = validator(key)

        if ok is True:
            console.print(f"  [green]✓ {msg}[/green]")
            return key
        elif ok is False:
            console.print(f"  [red]✗ {msg}[/red]")
            if Confirm.ask("  Try a different key?", default=True):
                key = ""
                continue
            return key  # user insists — keep it anyway
        else:
            console.print(f"  [yellow]? {msg}  (key saved anyway)[/yellow]")
            return key


# ── Step helpers ──────────────────────────────────────────────────

def _pause(msg: str = "  Press Enter to continue..."):
    """Let the user actually read a screen before the next _clear() wipes it."""
    try:
        Prompt.ask(msg, default="", show_default=False)
    except Exception:
        pass


def _clear():
    console.clear()


def _header(step: int, total: int, title: str):
    console.print()
    pct = int((step / total) * 100)
    filled = int((step / total) * 52)
    bar = "█" * filled + "░" * (52 - filled)
    console.print(f"  [dim]{step}/{total}[/dim]  [cyan]{bar}[/cyan]  [dim]{pct}%[/dim]")
    console.print()
    console.rule(f"[bold cyan]{title}[/bold cyan]")
    console.print()


# ════════════════════════════════════════════════════════════════════
#  STEP FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def _existing_user() -> bool:
    """
    Detect whether the wizard is being re-run by an existing user.

    Any of these signals counts:
      - ~/.friday/.setup_complete
      - settings.json or config.yaml has an API key
      - ANTHROPIC_API_KEY / GEMINI_API_KEY in env
      - start.bat or friday_startup.bat exists next to setup_wizard.py
    """
    if SETUP_MARKER.exists():
        return True
    cfg = _load_config()
    if cfg.get("anthropic_api_key") or cfg.get("gemini_api_key"):
        return True
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return True
    for n in ("start.bat", "friday_startup.bat", "friday_startup.vbs"):
        if (PROJ_ROOT / n).exists():
            return True
    return False


def step_welcome(quick: bool):
    _clear()
    console.print()
    console.print(ASCII_BANNER, style="bold cyan", highlight=False)
    console.print(Align.center(Text("A S I M O V ' S   M I N D", style="bold magenta")))
    console.print(Align.center(Text("Sovereign AI Infrastructure · FutureSpeak.AI", style="dim")))
    console.print()

    if quick:
        mode_note = "[dim]Quick mode — just API keys, skip cosmetics.[/dim]"
    else:
        mode_note = "[dim]Full setup — about 2 minutes.[/dim]"

    console.print(Panel(
        "[cyan]Welcome to Agent Friday — your personal sovereign AI.[/cyan]\n\n"
        "This wizard configures your agent on your machine.\n"
        "You'll need an [bold]Anthropic[/bold] API key to get started.\n\n"
        "[bold]Privacy posture[/bold]\n"
        "  • [bold]With Ollama (local):[/bold] Sensitive conversations are\n"
        "    processed entirely on your device — nothing leaves your machine.\n"
        "  • [bold]Without Ollama:[/bold] An egress gate redacts sensitive data\n"
        "    before sending to cloud providers. Your private information never\n"
        "    leaves your device, but redacted conversations may lose context.\n\n"
        "  You can install Ollama later in Settings to upgrade to full local\n"
        "  privacy. One-command install: [bold]winget install Ollama.Ollama[/bold]\n"
        "  (Windows) or [bold]brew install ollama[/bold] (macOS).\n\n"
        f"{mode_note}",
        title="[bold]FIRST RUN SETUP[/bold]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()
    Confirm.ask("  Ready to begin?", default=True)


def step_name(total: int, existing: str) -> str:
    _clear()
    _header(1, total, "NAME YOUR AGENT")
    console.print(
        "  What should your agent call itself?\n"
        "  [dim]This appears in the top bar and in the agent's self-references.[/dim]\n"
    )
    name = Prompt.ask("  [cyan]Agent name[/cyan]", default=existing or "AGENT FRIDAY")
    return name.strip().upper() or "AGENT FRIDAY"


def _ollama_available() -> bool:
    """Quick check for a running Ollama instance."""
    try:
        import requests as _r
        return _r.get("http://localhost:11434/api/tags", timeout=2).ok
    except Exception:
        return False


def _show_privacy_posture():
    """Display the current privacy posture based on Ollama availability."""
    if _ollama_available():
        console.print(Panel(
            "[bold green]Full local privacy[/bold green]\n"
            "Ollama detected — sensitive conversations stay entirely on your device.\n"
            "Nothing leaves your machine.",
            title="Privacy Posture", border_style="green", padding=(0, 2),
        ))
    else:
        console.print(Panel(
            "[bold yellow]Egress-gate privacy[/bold yellow]\n"
            "No Ollama detected. An egress gate redacts sensitive data before\n"
            "cloud calls — your private information never leaves your device, but\n"
            "redacted conversations may lose context.\n\n"
            "Install Ollama for full local privacy:\n"
            "  Windows: [bold]winget install Ollama.Ollama[/bold]\n"
            "  macOS:   [bold]brew install ollama[/bold]",
            title="Privacy Posture", border_style="yellow", padding=(0, 2),
        ))
    console.print()


def step_provider(total: int, existing_provider: str) -> str:
    _clear()
    _header(2, total, "LLM PROVIDER")
    _show_privacy_posture()
    console.print("  Choose your primary AI provider.\n")

    for i, p in enumerate(PROVIDERS):
        num = f"[bold cyan]{i + 1}[/bold cyan]"
        name = f"[bold white]{p['name']}[/bold white]"
        star = " [bold magenta]← RECOMMENDED[/bold magenta]" if p.get("tag") == "RECOMMENDED" else ""
        coming = " [dim](coming soon)[/dim]" if p.get("tag") == "COMING SOON" else ""
        console.print(f"  {num}.  {name}{star}{coming}")
        console.print(f"       [dim]{p['desc']}[/dim]")
        console.print()

    while True:
        choice = Prompt.ask("  [cyan]Provider (1–3)[/cyan]", default="1")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(PROVIDERS):
                p = PROVIDERS[idx]
                if p.get("tag") == "COMING SOON":
                    console.print(f"  [yellow]{p['name']} support is coming in v5.0. Defaulting to Anthropic.[/yellow]")
                    return "anthropic"
                return p["id"]
        except ValueError:
            pass
        console.print("  [red]Enter 1, 2, or 3.[/red]")


def step_model(total: int, provider_id: str, existing_model: str) -> str:
    _clear()
    _header(3, total, "ORCHESTRATOR MODEL")
    provider = next((p for p in PROVIDERS if p["id"] == provider_id), PROVIDERS[0])
    models = provider["models"]
    if callable(models):   # dynamic lineup (registry / discovery cache)
        models = models()
    if not models:
        console.print("  [yellow]No models available for this provider yet.[/yellow]")
        return existing_model or "claude-opus-5"

    console.print(f"  [dim]Provider: {provider['name']}[/dim]\n")
    for i, (mid, mname, mdesc) in enumerate(models):
        star = " [bold magenta]← RECOMMENDED[/bold magenta]" if i == 0 else ""
        console.print(f"  [bold cyan]{i + 1}[/bold cyan].  [bold white]{mname}[/bold white]{star}")
        console.print(f"       [dim]{mdesc}[/dim]")
        console.print()

    default_idx = next((str(i+1) for i, (mid,*_) in enumerate(models) if mid == existing_model), "1")
    while True:
        choice = Prompt.ask(f"  [cyan]Model (1–{len(models)})[/cyan]", default=default_idx)
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx][0]
        except ValueError:
            pass
        console.print(f"  [red]Enter 1–{len(models)}.[/red]")


def step_creative_engine(total: int, existing: str) -> str:
    _clear()
    _header(4, total, "CREATIVE ENGINE")
    console.print(
        "  The creative engine powers image generation, music synthesis,\n"
        "  code art, video, and TTS voice output.\n"
        "  [dim]Requires a Google Gemini API key (next step).[/dim]\n"
    )
    engines = _creative_engines()
    for i, (mid, mname, mdesc) in enumerate(engines):
        star = " [bold magenta]← RECOMMENDED[/bold magenta]" if i == 0 else ""
        console.print(f"  [bold cyan]{i + 1}[/bold cyan].  [bold white]{mname}[/bold white]{star}")
        console.print(f"       [dim]{mdesc}[/dim]")
        console.print()

    default_idx = next((str(i+1) for i,(mid,*_) in enumerate(engines) if mid == existing), "1")
    while True:
        choice = Prompt.ask(f"  [cyan]Engine (1–{len(engines)})[/cyan]", default=default_idx)
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(engines):
                return engines[idx][0]
        except ValueError:
            pass
        console.print(f"  [red]Enter 1–{len(engines)}.[/red]")


def step_brain(total: int, existing_anthro: str, existing_gemini: str) -> tuple[str, str]:
    """Decide local vs cloud from the hardware, and get a working key stored.

    Replaces the old "here are two key prompts" screen. The difference that
    matters: this asks the planner what THIS machine can actually be good at,
    and says so, instead of presenting the same choice to every user and
    letting the weak-hardware case discover the consequences later.

    Keys go through the encrypted credential store, never a launch script, and
    are validated with a real call before being stored.
    """
    from agent_friday import setup_brain as sb

    _clear()
    _header(5, total, "HOW FRIDAY THINKS")

    with console.status("  Checking what this machine can run...", spinner="dots"):
        a = sb.assess()

    console.print(f"  {a['reason']}\n")
    if a.get("brain_label"):
        console.print(f"  [dim]Local model for this machine: {a['brain_label']}[/dim]\n")
    console.print(Rule(style="dim"))
    console.print()

    anthro = existing_anthro or ""
    if a["capable"]:
        # Capable hardware gets a genuine choice. Assuming cloud here would be
        # as wrong as never asking — running entirely on your own machine is
        # the point of this project, not a fallback.
        console.print("  [bold]You have a real choice here.[/bold]\n")
        console.print("    [cyan]1[/cyan]  Local only — nothing leaves this machine")
        console.print("    [cyan]2[/cyan]  Add a Claude key — sharper answers, all tools")
        console.print("    [cyan]3[/cyan]  Both — local by default, Claude when it helps\n")
        choice = Prompt.ask("  Which?", choices=["1", "2", "3"], default="3")
        if choice == "1":
            console.print("\n  [green]Local only. You can add a key any time in "
                          "Settings -> Providers.[/green]\n")
            _pause()
            return "", existing_gemini or ""
    else:
        console.print("  [bold]Friday needs a Claude key to be useful on this "
                      "machine.[/bold]")
        console.print("  [dim]You can skip it, but see what stops working "
                      "below.[/dim]\n")

    console.print("  [dim]Get one at: console.anthropic.com/settings/keys[/dim]\n")
    anthro = _store_validated_key(
        "anthropic", "Anthropic key (sk-ant-...)", anthro,
        skippable_note=("Skipping means Friday uses this machine's local model. "
                        "On this machine that may mean no tool use."))

    console.print()
    console.print(Rule(style="dim"))
    console.print()
    console.print("  [bold]Google Gemini key[/bold]  [dim](optional)[/dim]")
    console.print("  [dim]This one is what powers voice and image generation.[/dim]")
    console.print("  [dim]Get one at: aistudio.google.com/app/apikey[/dim]\n")
    gemini = _store_validated_key(
        "google-gemini", "Gemini key (AIza...)", existing_gemini or "",
        skippable_note="Voice and image generation stay unavailable without it.")

    # Say what they have and have not, before they leave the screen.
    console.print()
    notice = sb.missing_capability_notice(bool(anthro), bool(gemini))
    if notice:
        console.print(Panel("\n".join(notice), border_style="yellow",
                            padding=(1, 4), title="What you have"))
    _pause()
    return anthro, gemini


def _store_validated_key(provider: str, label: str, existing: str,
                         skippable_note: str = "") -> str:
    """Prompt, VALIDATE with a real call, then store encrypted. Loops on error.

    A key that is wrong, expired or mistyped fails here with a clear message.
    Storing it unchecked would move that failure to the user's first sentence
    to Friday, which is the single worst place to discover it.
    """
    from agent_friday import setup_brain as sb

    if existing and sb.env_has(provider):
        console.print(f"  [green]A {provider} key is already stored.[/green]")
        if not Confirm.ask("  Replace it?", default=False):
            return existing

    while True:
        key = Prompt.ask(f"  [cyan]{label}[/cyan]", password=True, default="")
        if not key:
            if skippable_note:
                console.print(f"  [yellow]{skippable_note}[/yellow]")
            if Confirm.ask("  Continue without it?", default=True):
                return ""
            continue

        with console.status("  Checking the key works...", spinner="dots"):
            ok, msg = sb.validate_key(provider, key)

        if ok is False:
            console.print(f"  [red]x {msg}[/red]")
            if Confirm.ask("  Try again?", default=True):
                continue
            return ""
        if ok is None:
            console.print(f"  [yellow]? {msg}[/yellow]")
            if not Confirm.ask("  Store it anyway?", default=True):
                continue
        else:
            console.print(f"  [green]v {msg}[/green]")

        stored, smsg = sb.store_key(provider, key)
        if stored:
            console.print(f"  [green]v {smsg}[/green]")
            return key
        console.print(f"  [red]x {smsg}[/red]")
        if not Confirm.ask("  Try again?", default=True):
            return ""


def step_api_keys(total: int, existing_anthro: str, existing_gemini: str) -> tuple[str, str]:
    _clear()
    _header(5, total, "API KEYS")
    console.print(
        "  Keys are stored in [bold]~/.friday/config.yaml[/bold] — only on your machine.\n"
        "  They are never transmitted to any third party by Friday.\n"
    )
    console.print(Rule(style="dim"))
    console.print()

    # Anthropic
    console.print("  [bold]Anthropic API Key[/bold]  [dim](required for chat)[/dim]")
    console.print("  [dim]Get yours at: console.anthropic.com[/dim]\n")
    anthro = _test_key("Anthropic key (sk-ant-...)", existing_anthro, _validate_anthropic, required=True)

    console.print()
    console.print(Rule(style="dim"))
    console.print()

    # Gemini
    console.print("  [bold]Google Gemini API Key[/bold]  [dim](optional — enables voice, images, music)[/dim]")
    console.print("  [dim]Get yours at: aistudio.google.com/app/apikey[/dim]\n")
    gemini = _test_key("Gemini key (AIza...)", existing_gemini, _validate_gemini, required=False)

    return anthro, gemini


VAULT_DIR = FRIDAY_DIR / "vault"
VAULT_CONFIG = VAULT_DIR / ".vault_config.json"


def _vault_exists() -> bool:
    """Is there already an encrypted vault whose passphrase matters?

    The salt file is the thing that makes this irreversible: every byte in the
    vault was encrypted under Argon2id(passphrase, THIS salt). A different
    passphrase against the same salt derives a different key and the data is
    gone. So the salt existing is exactly the condition under which minting a
    new passphrase is destructive.
    """
    try:
        if VAULT_CONFIG.exists():
            return True
        if VAULT_DIR.is_dir() and any(VAULT_DIR.iterdir()):
            return True
    except Exception:
        pass
    return False


def _existing_vault_password() -> tuple:
    """(passphrase, where_it_came_from) for the vault, or ("", "").

    `_load_config()` can never supply this — `_persist` deliberately strips
    vault_password before writing settings (see its comment), so the `existing`
    argument threaded through every other wizard step arrives empty here on
    every single run. That is why this function exists: it goes and finds the
    passphrase in the three places it can actually be.

    Order matters. The environment is what the running product would see, and
    start.bat is what `_bootstrap_env_from_launch_scripts` loads it from at
    import, so those two agree by construction. The keyring is the copy made by
    `friday vault-setup`, which is the only home that survives the app folder
    being replaced.
    """
    for var in ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD"):
        v = os.environ.get(var, "").strip()
        if v:
            return v, f"the {var} environment variable"

    try:
        sb = PROJ_ROOT / "start.bat"
        if sb.exists():
            m = re.search(r"(?im)^\s*SET\s+FRIDAY_PASSWORD=(.*)$",
                          sb.read_text(encoding="utf-8", errors="ignore"))
            if m and m.group(1).strip():
                return m.group(1).strip(), "start.bat"
    except Exception:
        pass

    try:
        import keyring as _keyring
        v = _keyring.get_password("agent-friday", "vault-passphrase")
        if v:
            return v, "your operating system's keychain"
    except Exception:
        pass

    return "", ""


def _verify_vault_passphrase(pw: str):
    """True / False if it can be checked against real ciphertext, else None.

    None is not a failure — it means the vault has a salt but nothing encrypted
    yet to test against, so there is nothing to be wrong about.
    """
    try:
        from agent_friday.privacy import vault_crypto as vc
        salt = vc.load_salt(VAULT_CONFIG)
        # Production derives with the DEFAULT profile (no profile argument):
        # services/agent.py:4911, services/credential_store.py:97. Match it.
        key = vc.derive_key(pw, salt)
        for f in sorted(VAULT_DIR.rglob("*")):
            if not f.is_file() or f.name == ".vault_config.json":
                continue
            try:
                blob = f.read_bytes()
            except Exception:
                continue
            if not vc.is_encrypted(blob):
                continue
            try:
                vc.decrypt(blob, key)
                return True
            except Exception:
                return False
        return None
    except Exception:
        return None


def _vault_keep_existing(total: int, existing: str, source: str) -> str:
    """A vault exists and we still have its passphrase. Keep it. Say so."""
    _clear()
    _header(6, total, "VAULT ENCRYPTION")
    ok = _verify_vault_passphrase(existing)
    if ok is False:
        # We found *a* passphrase and it does not open the vault. Do not use it
        # and do not mint another — that would write a second wrong answer over
        # the first. This is the same conversation as a lost passphrase.
        console.print(Panel(
            "[bold yellow]The passphrase I found does not open your vault.[/bold yellow]\n\n"
            f"  I found one in {source}, but it does not decrypt the data in\n"
            "  [bold]~/.friday/vault[/bold]. That usually means the passphrase was\n"
            "  changed at some point and one of the two copies is stale.",
            title="Vault", border_style="yellow", padding=(0, 2)))
        console.print()
        return _vault_lost_passphrase(total, already_explained=True)

    detail = ("  I checked it against your encrypted data and it works."
              if ok else
              "  There is nothing encrypted yet to check it against, but it is\n"
              "  the passphrase this install is already configured with.")
    console.print(Panel(
        "[bold green]Your vault is already encrypted, and I have kept your\n"
        "existing passphrase.[/bold green]\n\n"
        f"  Found in: [bold]{source}[/bold]\n"
        f"{detail}\n\n"
        "  [dim]Nothing to do here. Your existing notes stay readable.[/dim]",
        title="Vault", border_style="green", padding=(0, 2)))
    console.print()
    console.print("  [dim]To change it deliberately, run [bold]friday vault-setup[/bold] — "
                  "changing it\n  here would make everything already saved unreadable.[/dim]\n")
    Prompt.ask("  [dim]Press Enter to continue[/dim]", default="")
    return existing


def _vault_lost_passphrase(total: int, already_explained: bool = False) -> str:
    """A vault exists and its passphrase is gone. Stop and explain.

    Stephen, 2026-08-29: "if it exists and the passphrase is not recoverable,
    that is a situation to stop and explain, not to paper over by generating a
    new one." Generating one here is precisely what destroys the data, because
    the ciphertext stays and only the key changes.
    """
    if not already_explained:
        _clear()
        _header(6, total, "VAULT ENCRYPTION")
        console.print(Panel(
            "[bold yellow]There is an encrypted vault here, but I cannot find its\n"
            "passphrase.[/bold yellow]\n\n"
            "  [bold]~/.friday/vault[/bold] holds data encrypted with a passphrase that\n"
            "  is not in this install's start.bat, its environment, or your\n"
            "  operating system's keychain.\n\n"
            "  Installers before 5.6.6 could delete the file that passphrase\n"
            "  lived in during an upgrade. If you upgraded recently, that is\n"
            "  almost certainly what happened.",
            title="Vault", border_style="yellow", padding=(0, 2)))
        console.print()

    console.print("  [bold]Three ways forward.[/bold]\n")
    console.print("    [bold]1.[/bold] Type the passphrase, if you know it or wrote it down.")
    console.print("       [dim]I will check it against your data before accepting it.[/dim]")
    console.print("    [bold]2.[/bold] Leave it unset for now and decide later.  [dim](default)[/dim]")
    console.print("       [dim]Friday runs. The vault stays locked and untouched.[/dim]")
    console.print("    [bold]3.[/bold] Start a new vault.  [bold red]This abandons the old data.[/bold red]")
    console.print("       [dim]Nothing is deleted, but it can never be read again.[/dim]\n")

    choice = Prompt.ask("  Which?", choices=["1", "2", "3"], default="2")

    if choice == "1":
        for _ in range(3):
            pw = Prompt.ask("  [cyan]Passphrase[/cyan]", password=True, default="")
            if not pw:
                break
            ok = _verify_vault_passphrase(pw)
            if ok is True:
                console.print("  [green]✓ That opens your vault. Keeping it.[/green]\n")
                return pw
            if ok is None:
                console.print("  [yellow]There is nothing encrypted yet to check it against — "
                              "accepting it.[/yellow]\n")
                return pw
            console.print("  [red]That does not open the vault. Try again, or press "
                          "Enter to go back.[/red]\n")
        console.print("  [yellow]Leaving the vault passphrase unset.[/yellow]\n")
        return ""

    if choice == "3":
        console.print()
        console.print("  [bold red]This makes everything currently in your vault permanently\n"
                      "  unreadable.[/bold red] The files stay on disk; the key to them does not.\n")
        typed = Prompt.ask('  Type [bold]abandon[/bold] to confirm, or press Enter to go back',
                           default="")
        if typed.strip().lower() != "abandon":
            console.print("  [green]Nothing changed.[/green]\n")
            return ""
        import secrets as _sec
        generated = _sec.token_urlsafe(24)
        console.print(f"\n  [bold green]New passphrase:[/bold green] [bold white]{generated}[/bold white]")
        console.print("  [dim]Written to start.bat. Save it somewhere safe.[/dim]\n")
        Prompt.ask("  [dim]Press Enter to continue[/dim]", default="")
        return generated

    console.print("  [yellow]Leaving the vault passphrase unset. Friday will run; the vault\n"
                  "  stays locked and nothing in it is touched.[/yellow]\n")
    console.print("  [dim]If you find the passphrase later, run [bold]friday vault-setup[/bold] "
                  "to store it.[/dim]\n")
    Prompt.ask("  [dim]Press Enter to continue[/dim]", default="")
    return ""


def step_vault_password(total: int, existing: str) -> str:
    """Ask for a vault encryption passphrase — default path, not optional.

    RE-RUN SAFE SINCE 5.6.6. This step used to open with "Generate a random
    passphrase for me?" defaulting to YES, and it never looked at whether a
    vault already existed. An existing user re-running setup — which the
    installer does on EVERY upgrade, at step 12 — could press Enter and mint a
    brand new passphrase over a vault encrypted under the old one. AES-256-GCM
    over an Argon2id key: wrong passphrase, no data, no recovery. It looked
    exactly like success.

    Every other step in this wizard already takes an `existing` and leaves a
    settled answer alone; `_routing_block_for` says why. This one now does too.
    """
    found, source = _existing_vault_password()
    if not existing:
        existing = found
    vault_here = _vault_exists()

    if vault_here and existing:
        return _vault_keep_existing(total, existing, source)
    if vault_here and not existing:
        return _vault_lost_passphrase(total)

    _clear()
    _header(6, total, "VAULT ENCRYPTION")
    console.print(Panel(
        "[bold cyan]Encrypt your vault at rest[/bold cyan]  [bold green]← RECOMMENDED[/bold green]\n\n"
        "  Friday stores financial, health, legal, and personal data in\n"
        "  [bold]~/.friday/vault[/bold].  A passphrase encrypts this data with\n"
        "  AES-256-GCM + Argon2id so it cannot be read even if your disk is\n"
        "  accessed by another user or process.\n\n"
        "  [bold]FRIDAY_PASSWORD[/bold] is set in start.bat — only you can read it.\n"
        "  You can also set it as an environment variable before launching.",
        title="Security", border_style="green", padding=(0, 2),
    ))
    console.print()

    auto_opt = Confirm.ask(
        "  [bold]Generate a random passphrase for me?[/bold]  [dim](saves it to start.bat)[/dim]",
        default=True,
    )
    if auto_opt:
        import secrets as _sec
        generated = _sec.token_urlsafe(24)
        console.print(f"\n  [bold green]Generated passphrase:[/bold green] [bold white]{generated}[/bold white]")
        console.print("  [dim]This is written to start.bat and never leaves your machine.[/dim]\n")
        Prompt.ask("  [dim]Press Enter to continue[/dim]", default="")
        return generated

    pw = Prompt.ask("  [cyan]Passphrase[/cyan]", password=True, default=existing or "")
    if not pw:
        console.print()
        confirmed = Confirm.ask(
            "  [bold red]⚠ Skip encryption?[/bold red]  Your vault data (finance, health, legal) "
            "will be stored in plaintext. Are you sure?",
            default=False,
        )
        if not confirmed:
            return step_vault_password(total, existing)  # re-ask
        console.print("  [yellow]Vault encryption disabled. You can enable it later by\n"
                      "  setting FRIDAY_PASSWORD in start.bat and restarting.[/yellow]\n")
        return ""
    pw2 = Prompt.ask("  [cyan]Confirm passphrase[/cyan]", password=True, default="")
    if pw != pw2:
        console.print("  [red]Passphrases do not match. Try again.[/red]\n")
        return step_vault_password(total, existing)
    console.print("  [green]✓ Vault encryption enabled.[/green]\n")
    return pw


def step_voice_engine(total: int, existing_engine: str) -> str:
    """Choose the voice ENGINE: local (default, private) vs cloud (Gemini Live).

    Local is recommended for everyone — it runs on-device (faster-whisper +
    Piper), works offline, and keeps audio private. Cloud is the opt-in for the
    most expressive delivery. Mirrors the ethos: local default, cloud opt-in."""
    _clear()
    _header(7, total, "VOICE ENGINE")
    # Hardware hint — local Tier-1 runs on any CPU; note GPU as a future premium.
    try:
        from agent_friday.routing.ollama_manager import get_manager
        hw = get_manager().detect_hardware()
        gpu = hw.get("gpu") or hw.get("has_gpu")
    except Exception:
        gpu = None
    console.print(
        "  How should Friday listen and speak?\n\n"
        "  [bold cyan]1[/bold cyan].  [bold white]Local[/bold white]  "
        "[dim](recommended — on-device, private, works offline; faster-whisper + Piper)[/dim]\n"
        "  [bold cyan]2[/bold cyan].  [bold white]Cloud[/bold white]  "
        "[dim](Gemini Live — most expressive; needs a Gemini key + network)[/dim]\n"
    )
    if gpu:
        console.print("  [dim]An NVIDIA GPU was detected — a premium local voice tier "
                      "(NeMo) can be added later in Settings.[/dim]\n")
    default_idx = "2" if str(existing_engine).lower() == "gemini" else "1"
    choice = Prompt.ask("  [cyan]Engine (1–2)[/cyan]", default=default_idx)
    return "gemini" if str(choice).strip() == "2" else "local"


def step_voice(total: int, existing_voice: str) -> str:
    _clear()
    _header(7, total, "VOICE PERSONA")
    console.print("  Choose the TTS voice Friday uses when speaking aloud "
                  "(applies to cloud Gemini Live).\n")

    for i, (vid, vdesc) in enumerate(VOICE_PERSONAS):
        star = "[bold cyan]●[/bold cyan]" if vid == existing_voice else " "
        console.print(f"  {star} [bold cyan]{i + 1}[/bold cyan].  [bold white]{vid}[/bold white]  [dim]{vdesc}[/dim]")
    console.print()

    default_idx = next((str(i+1) for i,(vid,*_) in enumerate(VOICE_PERSONAS) if vid == existing_voice), "1")
    while True:
        choice = Prompt.ask(f"  [cyan]Voice (1–{len(VOICE_PERSONAS)})[/cyan]", default=default_idx)
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(VOICE_PERSONAS):
                return VOICE_PERSONAS[idx][0]
        except ValueError:
            pass
        console.print(f"  [red]Enter 1–{len(VOICE_PERSONAS)}.[/red]")


def step_scene(total: int, existing_idx: int) -> int:
    _clear()
    _header(8, total, "HOLOGRAPHIC SCENE")
    console.print(
        "  Agent Friday renders a Three.js scene in the browser.\n"
        "  Scenes rotate automatically every 4 days — pin one to lock it in.\n"
    )

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("#", style="bold cyan", width=3)
    t.add_column("Name", style="bold white", width=26)
    t.add_column("Description", style="dim")
    for idx, _, name, desc in EVOLUTION_STRUCTURES:
        t.add_row(str(idx + 1), name, desc)
    console.print(t)
    console.print()

    while True:
        choice = Prompt.ask(
            "  [cyan]Scene number (1–13), or 0 to auto-rotate[/cyan]",
            default=str(existing_idx + 1) if existing_idx >= 0 else "0"
        )
        try:
            n = int(choice)
            if n == 0:
                return -1
            if 1 <= n <= 13:
                return n - 1
        except ValueError:
            pass
        console.print("  [red]Enter 0–13.[/red]")


def _connected_google_accounts() -> list:
    """Google accounts that are ACTUALLY signed in, from the real store.

    services/google_accounts.py behind credential_store is where a connected
    account lives. This step used to read its own `connectors` block out of
    config.yaml instead -- a key nothing in the tree consults -- so the dot it
    drew had no relationship to whether Gmail worked.

    Never raises: this is a status line in an installer, and a missing
    optional import must not take the last step of a fresh install with it.
    """
    try:
        from agent_friday.services.google_accounts import list_accounts
        return [a.get("email") or a.get("id") for a in (list_accounts() or [])]
    except Exception:
        return []


def step_connectors(total: int, existing: dict) -> dict:
    """Report what is connected. Do not claim what is not.

    This step used to offer "Enable Gmail?" and, on yes, write
    {"enabled": True} into config.yaml and print "(Full Gmail setup runs on
    first use via the UI)". It performed no OAuth, opened no browser and
    created no account. The key it wrote is read by nothing. On the next run
    it drew a green dot beside a service that had never been connected.

    Stephen, installing Friday on Janet's laptop 2026-08-26: "The connect
    services portion of the installer, once the gui comes up, should be
    interactive. I could not click to connect my accounts and would like to."
    He could not -- and the step implied he had.

    Actually clicking to connect is gated on Friday shipping its own Google
    OAuth client (docs/design/google-oauth-onboarding.md). Until that is
    decided a new user has no OAuth client at all, and the most useful thing
    this step can do is be accurate about where the flow lives, rather than
    send them toward a screen that will ask them for a JSON file.
    """
    _clear()
    _header(9, total, "CONNECT SERVICES  (optional)")

    signed_in = _connected_google_accounts()
    google_live = bool(signed_in)

    console.print(
        "  Friday can read your mail and calendar once a Google account is\n"
        "  connected. Connecting happens inside Friday, not here.\n"
    )

    connected = {}
    for cid, cname, cdesc in CONNECTORS:
        if "(v5)" in cdesc:
            console.print(f"  [dim]○  {cname}  — {cdesc}[/dim]")
            continue
        # The dot reflects the account store, not a preference file. An
        # unconnected service shows as unconnected however keen the answers.
        marker = "[green]●[/green]" if google_live else "○"
        console.print(f"  {marker}  [bold white]{cname}[/bold white]  [dim]{cdesc}[/dim]")
        connected[cid] = {"enabled": google_live}

    console.print()
    if google_live:
        console.print("  [green]Already connected:[/green] "
                      + ", ".join(str(e) for e in signed_in))
        console.print("  [dim]Manage these in Friday: "
                      "Settings → Connectors.[/dim]\n")
        return connected

    console.print(
        "  [dim]Nothing is connected yet, and this installer cannot connect it\n"
        "  for you — signing in needs a browser and a running Friday.[/dim]\n"
        "  When Friday opens: [bold]Settings → Connectors → + Add Account[/bold].\n"
    )
    console.print(
        "  [dim]Everything else works without it. Mail and calendar simply\n"
        "  stay quiet until you do.[/dim]\n"
    )

    # Nothing is asked, because there is nothing here that answering changes.
    # An "Enable Gmail?" prompt whose only effect is a boolean no reader
    # consults spends the person's attention and then misreports the result
    # back to them.
    return existing if existing else connected


def step_summary(config: dict, quick: bool) -> bool:
    _clear()
    console.print()
    console.rule("[bold cyan]CONFIRM CONFIGURATION[/bold cyan]")
    console.print()

    t = Table(box=box.ROUNDED, border_style="cyan", padding=(0, 2), show_header=False)
    t.add_column("Key", style="bold white", width=24)
    t.add_column("Value", style="cyan")

    t.add_row("Agent name", config["agent_name"])
    t.add_row("Provider", config.get("provider", "anthropic"))
    t.add_row("Orchestrator", config["orchestrator_model"])
    if not quick:
        t.add_row("Creative engine", config.get("creative_model", "gemini-nano-banana-2"))
        _ve = config.get("voice_engine", "local")
        t.add_row("Voice engine", "Local (on-device)" if _ve == "local" else "Cloud (Gemini Live)")
        t.add_row("Voice persona", config.get("tts_voice", "Aoede"))
        scene_idx = config.get("preferred_scene_index", -1)
        scene_name = (
            EVOLUTION_STRUCTURES[scene_idx][2] if 0 <= scene_idx < 13
            else "Auto-rotate (every 4 days)"
        )
        t.add_row("Holographic scene", scene_name)

    ak = config.get("anthropic_api_key", "")
    gk = config.get("gemini_api_key", "")
    vp = config.get("vault_password", "")
    t.add_row("Anthropic key",
              f"✓ SET  ({ak[:12]}...)" if ak else "[dim]not set[/dim]")
    t.add_row("Gemini key",
              f"✓ SET  ({gk[:12]}...)" if gk else "[dim]not set — voice/creative disabled[/dim]")
    t.add_row("Vault encryption",
              "[bold green]✓ AES-256-GCM enabled[/bold green]" if vp
              else "[bold yellow]⚠ DISABLED — vault stored plaintext[/bold yellow]")

    console.print(t)
    console.print()
    return Confirm.ask("  Save and launch?", default=True)


# ── Save config ───────────────────────────────────────────────────

def _persist(config: dict):
    """Write config.yaml + settings.json + setup marker + personality.json."""
    # Never write vault_password to settings files — it lives only in start.bat
    # as a FRIDAY_PASSWORD env var so it is not committed or version-controlled.
    safe_config = {k: v for k, v in config.items() if k != "vault_password"}
    _save_config(safe_config)

    # Mark setup done
    SETUP_MARKER.write_text(__import__("datetime").datetime.now().isoformat(), encoding="utf-8")

    # Persist scene preference
    idx = config.get("preferred_scene_index", -1)
    if idx >= 0:
        pfile = FRIDAY_DIR / "personality.json"
        pdata = {}
        if pfile.exists():
            try:
                pdata = json.loads(pfile.read_text("utf-8"))
            except Exception:
                pass
        pdata["preferred_scene_index"] = idx
        pfile.write_text(json.dumps(pdata, indent=2), encoding="utf-8")

    # Write start.bat
    _write_start_bat(config)


def _write_start_bat(config: dict):
    lines = ["@echo off", "title Agent Friday", ""]
    if config.get("anthropic_api_key"):
        lines.append(f'SET ANTHROPIC_API_KEY={config["anthropic_api_key"]}')  # pragma: allowlist secret
    if config.get("gemini_api_key"):
        lines.append(f'SET GEMINI_API_KEY={config["gemini_api_key"]}')  # pragma: allowlist secret
    if config.get("vault_password"):
        lines.append(f'SET FRIDAY_PASSWORD={config["vault_password"]}')  # pragma: allowlist secret
    lines += ["", f'cd /d "{PROJ_ROOT}"', "python server.py", "pause"]
    bat = PROJ_ROOT / "start.bat"
    bat.write_text("\r\n".join(lines), encoding="utf-8")


def _save_with_progress(config: dict):
    console.print()
    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}[/cyan]"),
                  console=console) as p:
        t = p.add_task("Creating ~/.friday/...", total=None)
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        time.sleep(0.2)
        p.update(t, description="Writing config.yaml + settings.json...")
        _persist(config)
        time.sleep(0.2)
        p.update(t, description="Done.")
        time.sleep(0.2)

    console.print()
    console.print(Panel(
        "[bold cyan]Setup complete![/bold cyan]\n\n"
        f"  Config: [dim]~/.friday/config.yaml[/dim]\n"
        f"  Launch: [bold]friday[/bold]  or  [bold]start.bat[/bold]\n\n"
        "[dim]Run [bold]friday status[/bold] to verify everything is working.[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(prog="setup_wizard.py")
    parser.add_argument("--quick", action="store_true",
                        help="Minimal setup: name + API keys only, skip cosmetics")
    parser.add_argument("--force", action="store_true",
                        help="Re-run setup even if a previous install is detected")
    args = parser.parse_args()
    quick = args.quick

    # Detect existing installs and bail out early unless --force
    if _existing_user() and not args.force:
        _clear()
        console.print()
        console.print(ASCII_BANNER, style="bold cyan", highlight=False)
        console.print(Align.center(Text(
            "Existing installation detected", style="bold cyan")))
        console.print()
        console.print(Panel(
            "[cyan]Agent Friday is already configured on this machine.[/cyan]\n\n"
            f"  Config: [dim]{CONFIG_YAML if CONFIG_YAML.exists() else SETTINGS_FILE}[/dim]\n"
            f"  Marker: [dim]{SETUP_MARKER}[/dim]\n\n"
            "  Launch:   [bold]friday[/bold]\n"
            "  Status:   [bold]friday status[/bold]\n"
            "  Re-config:[bold] friday config set KEY VALUE[/bold]\n\n"
            "[dim]Run [bold]setup_wizard.py --force[/bold] to redo setup from scratch.[/dim]",
            border_style="cyan", padding=(1, 4),
        ))
        console.print()
        sys.exit(0)

    # Load existing values for defaults
    existing = _load_config()

    total_steps = 6 if quick else 10

    # ── Welcome ──
    step_welcome(quick)

    config = dict(existing)  # start with existing so we don't wipe settings

    # Step 1: Name
    config["agent_name"] = step_name(total_steps, config.get("agent_name", "AGENT FRIDAY"))

    if not quick:
        # Step 2: Provider
        config["provider"] = step_provider(total_steps, config.get("provider", "anthropic"))

        # Step 3: Orchestrator model
        config["orchestrator_model"] = step_model(
            total_steps, config["provider"],
            config.get("orchestrator_model", "claude-opus-5")
        )

        # Step 4: Creative engine
        config["creative_model"] = step_creative_engine(
            total_steps, config.get("creative_model", "gemini-nano-banana-2")
        )
    else:
        config.setdefault("provider", "anthropic")
        config.setdefault("orchestrator_model", "claude-opus-5")
        config.setdefault("creative_model", "gemini-nano-banana-2")

    # Step 5 (always): API keys
    config["anthropic_api_key"], config["gemini_api_key"] = step_brain(
        total_steps,
        config.get("anthropic_api_key", ""),
        config.get("gemini_api_key", ""),
    )

    # Step 6 (always): Vault encryption — prominent, recommended, not buried.
    config["vault_password"] = step_vault_password(
        total_steps, config.get("vault_password", ""),
    )

    if not quick:
        # Step 7: Voice — engine (local default / cloud opt-in) + TTS persona.
        config["voice_engine"] = step_voice_engine(
            total_steps, config.get("voice_engine", "local"))
        config["tts_voice"] = step_voice(total_steps, config.get("tts_voice", "Aoede"))

        # Step 7: Scene
        config["preferred_scene_index"] = step_scene(
            total_steps, config.get("preferred_scene_index", -1)
        )

        # Step 8: Connectors
        config["connectors"] = step_connectors(
            total_steps, config.get("connectors", {})
        )
    else:
        config.setdefault("voice_engine", "local")
        config.setdefault("tts_voice", "Aoede")
        config.setdefault("preferred_scene_index", 0)
        config.setdefault("connectors", {})

    # The provider answer has to reach the thing that routes a turn.
    # `provider` alone never did -- see _routing_block_for.
    _routing = _routing_block_for(
        config.get("provider") or "anthropic",
        (existing.get("model_routing") or {}),
        previous_provider=existing.get("provider"),
    )
    if _routing is not None:
        config["model_routing"] = _routing

    # Defaults that server expects
    config.setdefault("subagent_model", "claude-sonnet-5")
    config.setdefault("voice_model", "gemini-2.5-flash-native-audio-latest")
    config.setdefault("temperature", 0.7)
    config.setdefault("response_length", "standard")
    config.setdefault("communication_style", "professional")
    config.setdefault("context_logging_enabled", True)
    config.setdefault("off_record", False)
    config["setup_complete"] = True

    # Summary + confirm
    if not step_summary(config, quick):
        console.print("\n  [yellow]Setup cancelled. Run again to start over.[/yellow]\n")
        sys.exit(0)

    # Save
    _save_with_progress(config)

    # Launch?
    console.print()
    if Confirm.ask("  Launch Agent Friday now?", default=True):
        _launch()
    else:
        console.print(
            "\n  [cyan]Run [bold]friday[/bold] or [bold]start.bat[/bold] to launch.[/cyan]\n"
        )


def _launch():
    server = PROJ_ROOT / "server.py"
    if not server.exists():
        console.print(f"  [red]server.py not found in {PROJ_ROOT}[/red]")
        return
    console.print()
    console.print(Panel(
        "[bold cyan]Starting Agent Friday...[/bold cyan]\n\n"
        "[dim]Open [bold]http://localhost:3000[/bold] in your browser.[/dim]\n"
        "[dim]Press Ctrl+C to stop.[/dim]",
        border_style="cyan", padding=(1, 4),
    ))
    console.print()
    cfg = _load_config()
    env = os.environ.copy()
    if cfg.get("anthropic_api_key") and not env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = cfg["anthropic_api_key"]
    if cfg.get("gemini_api_key") and not env.get("GEMINI_API_KEY"):
        env["GEMINI_API_KEY"] = cfg["gemini_api_key"]
    try:
        subprocess.run([sys.executable, str(server)], env=env, cwd=str(PROJ_ROOT))
    except KeyboardInterrupt:
        console.print("\n  [dim]Stopped.[/dim]\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n  [dim]Setup interrupted.[/dim]\n")
        sys.exit(0)
