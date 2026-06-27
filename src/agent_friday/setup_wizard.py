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
FRIDAY_DIR = Path.home() / ".friday"
SETTINGS_FILE = FRIDAY_DIR / "settings.json"
CONFIG_YAML = FRIDAY_DIR / "config.yaml"
SETUP_MARKER = FRIDAY_DIR / ".setup_complete"

# ── Data ─────────────────────────────────────────────────────────

PROVIDERS = [
    {
        "id": "anthropic",
        "name": "Anthropic",
        "desc": "Claude — best reasoning, cLaws certified",
        "tag": "RECOMMENDED",
        "key_hint": "sk-ant-...",
        "key_url": "console.anthropic.com",
        "models": [
            ("claude-opus-4-8",           "Claude Opus 4.8",    "Most capable — deep reasoning, complex multi-step"),
            ("claude-sonnet-4-6",         "Claude Sonnet 4.6",  "Fast and capable — great everyday driver"),
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5",   "Ultra-fast — quick responses, high volume"),
        ],
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

CREATIVE_ENGINES = [
    ("gemini-nano-banana-2",   "Gemini Nano Banana 2",   "Image generation — fast"),
    ("gemini-nano-banana-pro", "Gemini Nano Banana Pro", "Image generation — highest quality"),
    ("veo-3",                  "Google Veo",             "Video generation"),
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
        if (HERE / n).exists():
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
    if not models:
        console.print("  [yellow]No models available for this provider yet.[/yellow]")
        return existing_model or "claude-opus-4-8"

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
    for i, (mid, mname, mdesc) in enumerate(CREATIVE_ENGINES):
        star = " [bold magenta]← RECOMMENDED[/bold magenta]" if i == 0 else ""
        console.print(f"  [bold cyan]{i + 1}[/bold cyan].  [bold white]{mname}[/bold white]{star}")
        console.print(f"       [dim]{mdesc}[/dim]")
        console.print()

    default_idx = next((str(i+1) for i,(mid,*_) in enumerate(CREATIVE_ENGINES) if mid == existing), "1")
    while True:
        choice = Prompt.ask(f"  [cyan]Engine (1–{len(CREATIVE_ENGINES)})[/cyan]", default=default_idx)
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(CREATIVE_ENGINES):
                return CREATIVE_ENGINES[idx][0]
        except ValueError:
            pass
        console.print(f"  [red]Enter 1–{len(CREATIVE_ENGINES)}.[/red]")


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


def step_vault_password(total: int, existing: str) -> str:
    """Ask for a vault encryption passphrase — default path, not optional."""
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


def step_connectors(total: int, existing: dict) -> dict:
    _clear()
    _header(9, total, "CONNECT SERVICES  (optional)")
    console.print(
        "  Friday can connect to external services via connectors.\n"
        "  [dim]Available now: Gmail, Google Calendar. More coming in v5.[/dim]\n"
    )

    connected = {}
    for cid, cname, cdesc in CONNECTORS:
        is_connected = existing.get(cid, {}).get("enabled", False)
        coming = "(v5)" in cdesc
        if coming:
            console.print(f"  [dim]○  {cname}  — {cdesc}[/dim]")
            continue
        marker = "[green]●[/green]" if is_connected else "○"
        console.print(f"  {marker}  [bold white]{cname}[/bold white]  [dim]{cdesc}[/dim]")
        connected[cid] = {"enabled": is_connected}

    console.print()
    if not Confirm.ask("  Configure a connector now?", default=False):
        return existing

    for cid, cname, cdesc in CONNECTORS:
        if "(v5)" in cdesc:
            continue
        if Confirm.ask(f"  Enable {cname}?", default=False):
            connected[cid] = {"enabled": True}
            console.print(f"  [dim](Full {cname} setup runs on first use via the UI)[/dim]")

    return connected


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
    lines += ["", f'cd /d "{HERE}"', "python server.py", "pause"]
    bat = HERE / "start.bat"
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
            config.get("orchestrator_model", "claude-opus-4-8")
        )

        # Step 4: Creative engine
        config["creative_model"] = step_creative_engine(
            total_steps, config.get("creative_model", "gemini-nano-banana-2")
        )
    else:
        config.setdefault("provider", "anthropic")
        config.setdefault("orchestrator_model", "claude-opus-4-8")
        config.setdefault("creative_model", "gemini-nano-banana-2")

    # Step 5 (always): API keys
    config["anthropic_api_key"], config["gemini_api_key"] = step_api_keys(
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

    # Defaults that server expects
    config.setdefault("subagent_model", "claude-sonnet-4-6")
    config.setdefault("voice_model", "gemini-3.1-flash-live-preview")
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
    server = HERE / "server.py"
    if not server.exists():
        console.print(f"  [red]server.py not found in {HERE}[/red]")
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
        subprocess.run([sys.executable, str(server)], env=env, cwd=str(HERE))
    except KeyboardInterrupt:
        console.print("\n  [dim]Stopped.[/dim]\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n  [dim]Setup interrupted.[/dim]\n")
        sys.exit(0)
