#!/usr/bin/env python3
"""
friday — Agent Friday CLI
FutureSpeak.AI · Asimov's Mind

Usage:
  friday                    Start Agent Friday (server + browser)
  friday setup [--quick]    Run the setup wizard
  friday model              Change LLM model
  friday tools              Browse tool ring configuration
  friday config set K V     Set a config value
  friday config get [K]     Show config (all or one key)
  friday status             System health check (alias: doctor)
  friday doctor             System health check (alias: status)
  friday update             Update to latest version
  friday skills [--delete]  Browse and manage skills
"""
import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# ── Rich UI ──────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.text import Text
    from rich import box
    from rich.rule import Rule
    from rich.columns import Columns
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "rich", "--quiet"], check=True)
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.text import Text
    from rich import box
    from rich.rule import Rule
    from rich.columns import Columns

console = Console()

# ── Paths ────────────────────────────────────────────────────────
HERE = Path(__file__).parent.resolve()
FRIDAY_DIR = Path.home() / ".friday"
SETTINGS_FILE = FRIDAY_DIR / "settings.json"
CONFIG_YAML = FRIDAY_DIR / "config.yaml"
SETUP_MARKER = FRIDAY_DIR / ".setup_complete"
SKILLS_DIR = FRIDAY_DIR / "skills"

SERVER_PORT = int(os.environ.get("FRIDAY_PORT", "5000"))
SERVER_URL = f"http://localhost:{SERVER_PORT}"

# ── Config I/O ────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load from config.yaml if available, else settings.json."""
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
    """Write config to both config.yaml and settings.json."""
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    # YAML (primary, human-editable)
    try:
        import yaml
        with open(CONFIG_YAML, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True,
                      sort_keys=False)
    except ImportError:
        pass
    # JSON (for server.py backward compat)
    SETTINGS_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


# ── Port / Server helpers ─────────────────────────────────────────

def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) != 0


def _server_ready(url: str, timeout: int = 30) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ═══════════════════════════════════════════════════════════════════
#  CMD: start (default — no args)
# ═══════════════════════════════════════════════════════════════════

def _is_existing_user() -> bool:
    """
    Heuristic detection — was Friday set up on this machine before?

    Any one of these signals is enough:
      - ~/.friday/.setup_complete marker
      - ~/.friday/settings.json or config.yaml exists with an API key
      - ANTHROPIC_API_KEY or GEMINI_API_KEY in environment
      - start.bat / friday_startup.bat / friday_startup.vbs in project dir
    """
    if SETUP_MARKER.exists():
        return True
    cfg = _load_config()
    if cfg.get("anthropic_api_key") or cfg.get("gemini_api_key"):
        # Backfill the marker so we don't have to re-detect on every run
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            SETUP_MARKER.write_text("backfilled-on-detect", encoding="utf-8")
        except Exception:
            pass
        return True
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return True
    for batch_name in ("start.bat", "friday_startup.bat", "friday_startup.vbs"):
        if (HERE / batch_name).exists():
            return True
    return False


def cmd_start():
    """Launch the server and open the browser."""
    if not _is_existing_user():
        console.print()
        console.print(Panel(
            "[cyan]Agent Friday isn't set up yet.[/cyan]\n\n"
            "Run [bold]friday setup[/bold] to configure your agent,\n"
            "or [bold]friday setup --quick[/bold] for minimal setup (API keys only).",
            border_style="yellow", padding=(1, 4)
        ))
        console.print()
        if Confirm.ask("  Run setup now?", default=True):
            cmd_setup()
        return

    if not _port_free(SERVER_PORT):
        console.print(f"\n  [cyan]Server already running at {SERVER_URL}[/cyan]")
        webbrowser.open(SERVER_URL)
        return

    server_script = HERE / "server.py"
    if not server_script.exists():
        console.print(f"  [red]server.py not found in {HERE}[/red]")
        sys.exit(1)

    console.print()
    console.print(Panel(
        f"[bold cyan]Starting Agent Friday...[/bold cyan]\n\n"
        f"[dim]Server:  {SERVER_URL}[/dim]\n"
        f"[dim]Config:  {FRIDAY_DIR}[/dim]\n\n"
        f"[dim]Press Ctrl+C to stop.[/dim]",
        border_style="cyan", padding=(1, 4)
    ))
    console.print()

    env = os.environ.copy()
    cfg = _load_config()
    if cfg.get("anthropic_api_key") and not env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = cfg["anthropic_api_key"]
    if cfg.get("gemini_api_key") and not env.get("GEMINI_API_KEY"):
        env["GEMINI_API_KEY"] = cfg["gemini_api_key"]

    proc = subprocess.Popen([sys.executable, str(server_script)], env=env, cwd=str(HERE))

    console.print("  [dim]Waiting for server...[/dim]")
    if _server_ready(SERVER_URL, timeout=20):
        console.print(f"  [green]✓ Ready[/green]  Opening {SERVER_URL}")
        webbrowser.open(SERVER_URL)
    else:
        console.print(f"  [yellow]Server took too long to respond — open {SERVER_URL} manually.[/yellow]")

    try:
        proc.wait()
    except KeyboardInterrupt:
        console.print("\n  [dim]Shutting down...[/dim]")
        proc.terminate()


# ═══════════════════════════════════════════════════════════════════
#  CMD: setup
# ═══════════════════════════════════════════════════════════════════

def cmd_setup(quick: bool = False):
    """Run the interactive setup wizard."""
    wizard_script = HERE / "setup_wizard.py"
    if not wizard_script.exists():
        console.print("  [red]setup_wizard.py not found.[/red]")
        sys.exit(1)
    args = [sys.executable, str(wizard_script)]
    if quick:
        args.append("--quick")
    subprocess.run(args)


# ═══════════════════════════════════════════════════════════════════
#  CMD: model
# ═══════════════════════════════════════════════════════════════════

ORCHESTRATOR_MODELS = [
    ("claude-opus-4-7",           "Claude Opus 4.7",    "Most capable — deep reasoning, complex tasks"),
    ("claude-sonnet-4-6",         "Claude Sonnet 4.6",  "Fast and capable — great everyday driver"),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5",   "Ultra-fast — quick responses, high volume"),
]
CREATIVE_MODELS = [
    ("gemini-2.5-flash",   "Gemini 2.5 Flash",  "Fast creative engine — images, music, video"),
    ("gemini-2.0-flash",   "Gemini 2.0 Flash",  "Previous gen — stable and reliable"),
    ("gemini-1.5-pro",     "Gemini 1.5 Pro",    "Large context window — long creative tasks"),
]


def _pick_model(models: list, current: str, label: str) -> str:
    console.print(f"\n  [dim]{label}[/dim]\n")
    for i, (mid, mname, mdesc) in enumerate(models):
        star = "[bold cyan]●[/bold cyan]" if mid == current else " "
        num  = f"[bold]{i+1}[/bold]"
        console.print(f"  {star} {num}.  [bold white]{mname}[/bold white]  [dim]{mdesc}[/dim]")
    console.print()
    cur_idx = next((i+1 for i,(mid,*_) in enumerate(models) if mid == current), 1)
    choice = Prompt.ask(f"  Choose (1–{len(models)})", default=str(cur_idx))
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(models):
            return models[idx][0]
    except ValueError:
        pass
    return current


def cmd_model():
    """Interactively change the active LLM models."""
    cfg = _load_config()

    console.print()
    console.rule("[bold cyan]MODEL SELECTION[/bold cyan]")

    console.print("\n  [bold]Orchestrator[/bold] (primary reasoning + tool use)")
    new_orch = _pick_model(ORCHESTRATOR_MODELS, cfg.get("orchestrator_model", "claude-opus-4-7"), "")

    console.print("\n  [bold]Subagent[/bold] (background tasks)")
    new_sub = _pick_model(ORCHESTRATOR_MODELS, cfg.get("subagent_model", "claude-sonnet-4-6"), "")

    console.print("\n  [bold]Creative engine[/bold] (images, music, voice)")
    new_creative = _pick_model(CREATIVE_MODELS, cfg.get("creative_model", "gemini-2.5-flash"), "")

    console.print()
    t = Table(box=box.ROUNDED, border_style="cyan", padding=(0, 2), show_header=False)
    t.add_column("Key", style="dim")
    t.add_column("Value", style="cyan")
    t.add_row("orchestrator_model", new_orch)
    t.add_row("subagent_model", new_sub)
    t.add_row("creative_model", new_creative)
    console.print(t)
    console.print()

    if Confirm.ask("  Save changes?", default=True):
        cfg["orchestrator_model"] = new_orch
        cfg["subagent_model"] = new_sub
        cfg["creative_model"] = new_creative
        _save_config(cfg)
        console.print("  [green]✓ Model config saved.[/green]")
        console.print("  [dim]Restart the server to apply changes.[/dim]")


# ═══════════════════════════════════════════════════════════════════
#  CMD: tools
# ═══════════════════════════════════════════════════════════════════

TOOL_RINGS = {
    0: [
        ("read_file",          "Read any local file (up to 500KB)"),
        ("read_wiki",          "Read a personal wiki file"),
        ("search_wiki",        "Full-text wiki search"),
        ("query_trust_graph",  "Look up a person in trust graph"),
        ("query_calendar",     "Check upcoming calendar events"),
        ("get_career_pipeline","Read job search status"),
        ("get_briefing",       "Fetch latest daily briefing"),
    ],
    1: [
        ("write_file",          "Write or append to any file"),
        ("write_clipboard",     "Copy text to clipboard"),
        ("propose_wiki_update", "Queue a wiki edit for approval"),
        ("correct_wiki",        "Global find-replace across wiki"),
        ("learn_skill",         "Create/manage skill YAML workflows"),
    ],
    2: [
        ("search_web",    "DuckDuckGo search"),
        ("browse_web",    "Fetch full page content"),
        ("search_email",  "Search Gmail (if connector set up)"),
        ("draft_email",   "Draft email (if connector set up)"),
        ("open_url",      "Launch URL in browser"),
        ("spawn_task",    "Start a background agent task"),
        ("run_command",   "Run a PowerShell command"),
        ("install_package","Install a pip/npm package"),
    ],
    3: [
        ("move_mouse",  "Move cursor to coordinates"),
        ("click",       "Mouse click"),
        ("type_text",   "Keyboard injection"),
        ("press_key",   "Press key or chord"),
        ("screenshot",  "Capture screen"),
        ("scroll",      "Mouse wheel scroll"),
    ],
}

RING_LABELS = {
    0: ("READ-ONLY", "cyan",    "Always allowed — no side effects"),
    1: ("LOCAL WRITE","magenta","Always allowed — affects ~/.friday/ only"),
    2: ("NETWORK",   "yellow",  "Requires authenticated session"),
    3: ("OS CONTROL","red",     "Requires CC_ENABLED toggle in UI"),
}


def cmd_tools():
    """Show tool ring configuration and toggle Ring 3 (OS Control)."""
    cfg = _load_config()
    cc_enabled = cfg.get("cc_enabled", False)

    console.print()
    console.rule("[bold cyan]TOOL RING CONFIGURATION[/bold cyan]")
    console.print()

    for ring, tools in TOOL_RINGS.items():
        rlabel, rcolor, rdesc = RING_LABELS[ring]
        status = ""
        if ring == 3:
            status = f"  [{'green' if cc_enabled else 'red'}]{'ENABLED' if cc_enabled else 'DISABLED'}[/{'green' if cc_enabled else 'red'}]"

        console.print(f"  [bold {rcolor}]RING {ring} — {rlabel}[/bold {rcolor}]{status}")
        console.print(f"  [dim]{rdesc}[/dim]")
        for name, desc in tools:
            console.print(f"    [dim]•[/dim] [white]{name}[/white]  [dim]{desc}[/dim]")
        console.print()

    console.print(Rule(style="dim"))
    console.print()
    console.print("  [dim]Ring 0–2 are always active. Ring 3 (OS Control) is togglable.[/dim]")
    console.print()

    new_cc = Confirm.ask(
        f"  {'Disable' if cc_enabled else 'Enable'} OS Control (Ring 3)?",
        default=False
    )
    if new_cc:
        cfg["cc_enabled"] = not cc_enabled
        _save_config(cfg)
        state = "enabled" if not cc_enabled else "disabled"
        console.print(f"  [green]✓ Ring 3 OS Control {state}.[/green]")
        console.print("  [dim]Restart the server to apply.[/dim]")


# ═══════════════════════════════════════════════════════════════════
#  CMD: config
# ═══════════════════════════════════════════════════════════════════

def cmd_config(args):
    """Manage configuration key/value pairs."""
    cfg = _load_config()

    if args.config_cmd == "set":
        if not args.key or args.value is None:
            console.print("  [red]Usage: friday config set KEY VALUE[/red]")
            sys.exit(1)
        key, raw_val = args.key, " ".join(args.value)
        # Try to coerce type
        val = raw_val
        if raw_val.lower() in ("true", "yes"):
            val = True
        elif raw_val.lower() in ("false", "no"):
            val = False
        else:
            try:
                val = float(raw_val) if "." in raw_val else int(raw_val)
            except ValueError:
                pass
        cfg[key] = val
        _save_config(cfg)
        console.print(f"  [green]✓[/green]  [cyan]{key}[/cyan] = [white]{val!r}[/white]")

    elif args.config_cmd in ("get", "list", None):
        key = getattr(args, "key", None)
        # Mask API keys
        def _mask(k, v):
            if "api_key" in k and isinstance(v, str) and len(v) > 12:
                return v[:12] + "..." + "*" * 8
            return v

        t = Table(box=box.SIMPLE, padding=(0, 2), show_header=True,
                  header_style="bold cyan")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")

        items = {key: cfg[key]}.items() if key and key in cfg else cfg.items()
        for k, v in sorted(items):
            t.add_row(k, str(_mask(k, v)))
        console.print()
        console.print(t)
        console.print(f"\n  [dim]Config file: {CONFIG_YAML if CONFIG_YAML.exists() else SETTINGS_FILE}[/dim]\n")
    else:
        console.print(f"  [red]Unknown config command: {args.config_cmd}[/red]")
        console.print("  Usage: friday config [set|get|list]")


# ═══════════════════════════════════════════════════════════════════
#  CMD: status / doctor
# ═══════════════════════════════════════════════════════════════════

REQUIRED_PACKAGES = [
    "flask", "anthropic", "rich", "requests", "bs4",
]
OPTIONAL_PACKAGES = [
    ("google.genai",  "google-genai",  "Voice, images, music"),
    ("pyautogui",     "pyautogui",     "Ring 3 OS control"),
    ("colorama",      "colorama",      "Windows color support"),
]


def _check(label: str, ok: bool, detail: str = ""):
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    suffix = f"  [dim]{detail}[/dim]" if detail else ""
    console.print(f"  {icon}  {label}{suffix}")


def _try_import(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def cmd_status():
    """System health check — like hermes doctor."""
    cfg = _load_config()

    console.print()
    console.rule("[bold cyan]FRIDAY DOCTOR[/bold cyan]")
    console.print()

    # Python
    pv = sys.version_info
    py_ok = pv >= (3, 10)
    _check(f"Python {pv.major}.{pv.minor}.{pv.micro}", py_ok,
           "" if py_ok else "Python 3.10+ required")

    # Required packages
    console.print()
    console.print("  [bold]Required packages[/bold]")
    for pkg in REQUIRED_PACKAGES:
        mod = pkg if pkg != "bs4" else "bs4"
        _check(pkg, _try_import(mod))

    # Optional packages
    console.print()
    console.print("  [bold]Optional packages[/bold]")
    for mod, pkg, note in OPTIONAL_PACKAGES:
        ok = _try_import(mod)
        _check(f"{pkg}  [dim]{note}[/dim]", ok,
               f"pip install {pkg}" if not ok else "")

    # Config
    console.print()
    console.print("  [bold]Configuration[/bold]")
    _check("~/.friday/ exists", FRIDAY_DIR.exists())
    _check("Setup complete", SETUP_MARKER.exists(),
           "run: friday setup" if not SETUP_MARKER.exists() else "")

    anthro_key = cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    gemini_key  = cfg.get("gemini_api_key")   or os.environ.get("GEMINI_API_KEY", "")
    _check("Anthropic API key set", bool(anthro_key),
           "run: friday setup" if not anthro_key else "")
    _check("Gemini API key set (optional)", bool(gemini_key),
           "voice + creative disabled" if not gemini_key else "")

    # Validate keys if set
    if anthro_key:
        console.print()
        console.print("  [bold]API key validation[/bold]")
        valid, msg = _validate_anthropic_key(anthro_key)
        if valid is True:
            _check("Anthropic key valid", True)
        elif valid is False:
            _check("Anthropic key valid", False, msg)
        else:
            console.print(f"  [yellow]?[/yellow]  Anthropic key  [dim]{msg}[/dim]")

    if gemini_key:
        valid, msg = _validate_gemini_key(gemini_key)
        if valid is True:
            _check("Gemini key valid", True)
        elif valid is False:
            _check("Gemini key valid", False, msg)
        else:
            console.print(f"  [yellow]?[/yellow]  Gemini key  [dim]{msg}[/dim]")

    # Port
    console.print()
    console.print("  [bold]Network[/bold]")
    port_free = _port_free(SERVER_PORT)
    _check(f"Port {SERVER_PORT} available", port_free,
           "server may already be running" if not port_free else "")

    # Disk
    import shutil as _shutil
    total, used, free = _shutil.disk_usage(Path.home())
    gb_free = free / (1024 ** 3)
    _check(f"Disk space  ({gb_free:.1f} GB free)", gb_free > 1.0,
           "low disk space" if gb_free <= 1.0 else "")

    # server.py present
    console.print()
    console.print("  [bold]Installation[/bold]")
    _check("server.py found", (HERE / "server.py").exists())
    _check("build_ui.py found", (HERE / "build_ui.py").exists())
    _check("index.html built", (HERE / "index.html").exists(),
           "run: python build_ui.py" if not (HERE / "index.html").exists() else "")
    _check("ui_parts/ present", (HERE / "ui_parts").is_dir())

    console.print()


def _validate_anthropic_key(key: str):
    """Returns (True/False/None, message)."""
    try:
        from anthropic import Anthropic, AuthenticationError
        c = Anthropic(api_key=key)
        c.models.list()
        return True, "OK"
    except Exception as e:
        name = type(e).__name__
        if "auth" in name.lower() or "authentication" in str(e).lower() or "401" in str(e):
            return False, "Invalid key"
        return None, f"Network error ({name})"


def _validate_gemini_key(key: str):
    """Returns (True/False/None, message)."""
    try:
        from google import genai
        c = genai.Client(api_key=key)
        next(iter(c.models.list()), None)
        return True, "OK"
    except Exception as e:
        s = str(e).lower()
        if "api key" in s or "401" in s or "403" in s or "invalid" in s:
            return False, "Invalid key"
        return None, f"Network error ({type(e).__name__})"


# ═══════════════════════════════════════════════════════════════════
#  CMD: update
# ═══════════════════════════════════════════════════════════════════

def cmd_update():
    """Pull latest changes and reinstall dependencies."""
    console.print()
    console.rule("[bold cyan]UPDATE AGENT FRIDAY[/bold cyan]")
    console.print()

    # Check git
    if not (HERE / ".git").exists():
        console.print("  [yellow]Not a git repository — manual update required.[/yellow]")
        console.print(f"  Download latest from https://github.com/FutureSpeakAI/friday-desktop")
        return

    # git pull
    console.print("  [dim]Pulling latest changes...[/dim]")
    result = subprocess.run(["git", "pull", "origin", "main"],
                            capture_output=True, text=True, cwd=str(HERE))
    if result.returncode == 0:
        console.print(f"  [green]✓[/green]  {result.stdout.strip()}")
    else:
        console.print(f"  [red]git pull failed:[/red] {result.stderr.strip()}")
        return

    # pip install
    console.print("  [dim]Updating dependencies...[/dim]")
    pip_args = [sys.executable, "-m", "pip", "install", "--quiet",
                "flask", "anthropic", "google-genai", "rich", "colorama",
                "pyautogui", "beautifulsoup4", "requests", "pyyaml"]
    req = HERE / "requirements.txt"
    if req.exists():
        pip_args = [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(req)]
    subprocess.run(pip_args, check=False)
    console.print("  [green]✓[/green]  Dependencies up to date")

    # Rebuild UI
    build = HERE / "build_ui.py"
    if build.exists():
        console.print("  [dim]Rebuilding UI...[/dim]")
        subprocess.run([sys.executable, str(build)], capture_output=True, cwd=str(HERE))
        console.print("  [green]✓[/green]  index.html rebuilt")

    console.print()
    console.print("  [bold cyan]Update complete.[/bold cyan]  Run [bold]friday[/bold] to start.\n")


# ═══════════════════════════════════════════════════════════════════
#  CMD: skills
# ═══════════════════════════════════════════════════════════════════

def cmd_skills(delete_name: str = None):
    """Browse and manage skill YAML workflows."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills = sorted(SKILLS_DIR.glob("*.yaml")) + sorted(SKILLS_DIR.glob("*.yml"))

    console.print()
    console.rule("[bold cyan]SKILLS[/bold cyan]")
    console.print(f"  [dim]~/.friday/skills/  ({len(skills)} skill{'s' if len(skills) != 1 else ''})[/dim]\n")

    if not skills:
        console.print("  [dim]No skills yet. Ask Friday to 'learn a skill' for a recurring task.[/dim]\n")
        return

    try:
        import yaml as _yaml
        _yaml_ok = True
    except ImportError:
        _yaml_ok = False

    for i, f in enumerate(skills):
        name = f.stem
        desc = ""
        if _yaml_ok:
            try:
                with open(f, encoding="utf-8") as fh:
                    data = _yaml.safe_load(fh) or {}
                desc = data.get("description", "")
            except Exception:
                pass
        console.print(f"  [bold cyan]{i+1}.[/bold cyan]  [bold white]{name}[/bold white]")
        if desc:
            console.print(f"       [dim]{desc}[/dim]")

    console.print()

    if delete_name:
        target = SKILLS_DIR / f"{delete_name}.yaml"
        if not target.exists():
            target = SKILLS_DIR / f"{delete_name}.yml"
        if target.exists():
            if Confirm.ask(f"  Delete skill [bold]{delete_name}[/bold]?", default=False):
                target.unlink()
                console.print(f"  [green]✓[/green]  Skill '{delete_name}' deleted.")
        else:
            console.print(f"  [red]Skill '{delete_name}' not found.[/red]")
        return

    # Interactive: pick a skill to view
    if skills:
        choice = Prompt.ask(
            f"  View skill (1–{len(skills)}, or Enter to exit)",
            default=""
        )
        if choice:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(skills):
                    console.print()
                    console.print(skills[idx].read_text(encoding="utf-8"))
            except (ValueError, IndexError):
                pass
    console.print()


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="friday",
        description="Agent Friday — Asimov's Mind CLI  (FutureSpeak.AI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  (none)          Start Agent Friday (launch server + open browser)
  setup           Run the interactive setup wizard
  model           Choose or change your LLM models
  tools           Browse and configure tool rings
  config          Manage configuration key-value pairs
  status          System health check (aliases: doctor, check)
  update          Pull latest changes and rebuild
  skills          Browse and manage skill YAML workflows

examples:
  friday
  friday setup --quick
  friday config set temperature 0.9
  friday config get orchestrator_model
  friday skills --delete my-old-skill
  friday status
""",
    )
    sub = p.add_subparsers(dest="command")

    # setup
    p_setup = sub.add_parser("setup", help="Run the setup wizard")
    p_setup.add_argument("--quick", action="store_true",
                         help="Minimal setup — just API keys, skip cosmetics")

    # model
    sub.add_parser("model", help="Change LLM model")

    # tools
    sub.add_parser("tools", help="Browse and configure tool rings")

    # config
    p_cfg = sub.add_parser("config", help="Manage configuration")
    p_cfg.add_argument("config_cmd", nargs="?", choices=["set", "get", "list"],
                       default="list")
    p_cfg.add_argument("key", nargs="?")
    p_cfg.add_argument("value", nargs="*")

    # status / doctor / check
    for alias in ("status", "doctor", "check"):
        sub.add_parser(alias, help="System health check")

    # update
    sub.add_parser("update", help="Update to latest version")

    # skills
    p_skills = sub.add_parser("skills", help="Browse and manage skills")
    p_skills.add_argument("--delete", metavar="NAME", help="Delete a skill by name")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    cmd = args.command

    if cmd is None:
        cmd_start()
    elif cmd == "setup":
        cmd_setup(quick=getattr(args, "quick", False))
    elif cmd == "model":
        cmd_model()
    elif cmd == "tools":
        cmd_tools()
    elif cmd == "config":
        cmd_config(args)
    elif cmd in ("status", "doctor", "check"):
        cmd_status()
    elif cmd == "update":
        cmd_update()
    elif cmd == "skills":
        cmd_skills(delete_name=getattr(args, "delete", None))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
