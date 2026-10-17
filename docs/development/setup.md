# Developer setup on Windows

> **Status:** current · **Last verified against the code:** 2026-10-17 (main b605f2216)

This page takes you from a clean Windows machine to a running Friday built
from source, with its data in a scratch folder rather than your real one. The
pull request process and the required checks are in
[CONTRIBUTING.md](../../CONTRIBUTING.md); how the tests are organised is in
[Testing and gates](testing-and-gates.md).

## What you need

| Tool | Version | Why |
|---|---|---|
| Windows | 10 or 11 | The supported platform. The tray, computer control and the installer are Windows only. |
| Python | 3.12 recommended; 3.11 also tested | CI runs 3.11 and 3.12 (`.github/workflows/tests.yml`); the installer ships Python 3.12.10 (`packaging/windows/sources.json`). `pyproject.toml` accepts 3.10 or later, but 3.10 is not tested. Tick "Add python.exe to PATH" when you install it. |
| Git | any recent | The hooks and the installer build call it. |
| Node.js | 18 or later | Only for the Playwright app specs and the node UI tests (`package-lock.json` asks for Node 18 or later). CI uses the runner's default Node. |

Friday runs its own local models through `llama-server`, managed by the
residency arbiter, so Ollama is optional.

## Install from source

```powershell
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev,windows]"
git config core.hooksPath .githooks
```

- `[dev]` installs `pytest`, `pytest-xdist` and `ruff`. `pytest-xdist` is not
  optional: `pytest.ini` passes `-n auto`, and without the plugin every
  `pytest` run stops on an unknown argument.
- `[windows]` adds `pyautogui`, `pynput`, `pillow` and `pystray`; the tray
  tests import them.
- CI also installs `google`, `federation`, `podcast` and `compose`. To match
  CI exactly:

  ```powershell
  pip install -e ".[dev,google,federation,podcast,compose]"
  pip install -e ".[windows]"
  ```

- The other extras (`voice`, `voice-local-lite`, `creative`, `provenance`,
  `local`, `compression`, `pii`, `keyring`, `pdf`, `documents`, `all`) are in
  `pyproject.toml`. The GPU voice tier is not a pip extra; Settings installs it
  as its own step.
- Keep the virtual environment at `venv\` in the checkout. The pre-commit
  hook runs the import smoke test only when `venv\Scripts\python.exe` exists,
  and `playwright.app.config.ts` looks for that interpreter first.

**uv.** A `uv.lock` is committed so Dependabot can read it, but nothing
installs from it: a source install resolves `pyproject.toml`, and the Windows
installer installs from `packaging/windows/requirements/*.txt`
([dependency advisories](../security/dependency-advisories.md)). If you
prefer uv, `uv pip install -e ".[dev,windows]"` inside the activated venv
reads the same `pyproject.toml`.

**Git hooks.** `git config core.hooksPath .githooks` is required. The
pre-commit hook runs the import smoke test, the guard scripts and the secret
and personal-data scanner; the pre-push hook refuses a branch that shares no
history with `origin/main`. See [Repository guards](repository-guards.md).

### Node tooling for the app specs

```powershell
npm ci
npx playwright install chromium
```

`npm ci` installs the pinned `@playwright/test` from `package-lock.json`.
The `.test.cjs` files under `tests/ui/` are run by pytest wrappers that skip
when `node` is not on `PATH`.

## Where Friday keeps its data

Friday keeps all of its state in one folder: settings, the vault,
conversation memory, the wiki, credentials and logs (`friday.log`).

| What | Default | Override |
|---|---|---|
| Friday's state | `%USERPROFILE%\.friday` | `FRIDAY_HOME` |
| Local model runtime (model weights, `llama-server`, voice venvs) | `<state>\runtime` | `FRIDAY_RUNTIME_DIR`, or `runtime_dir` in settings |

`FRIDAY_HOME` **is** the state folder, not its parent: `FRIDAY_HOME=C:\x`
puts `settings.json` at `C:\x\settings.json` (`src/agent_friday/paths.py`,
`friday_home()`). Some paths are about your machine rather than Friday's
state and deliberately do not move with it: the Desktop, the sandbox root
that bounds which files Friday may read, and `~\Projects` (`user_home()`).

## Use a scratch home, not your real one

Importing the server seeds schedules and writes state files into the home it
resolves, and `scripts/check_imports.py` does the same. Unless you mean to
work on your real data, point `FRIDAY_HOME` at a scratch folder **under your
temp directory**:

```powershell
$env:FRIDAY_HOME = Join-Path $env:TEMP "friday-dev-home"
$env:FRIDAY_PORT = "3100"
python server.py
```

Why under temp: the vault passphrase's keychain slot is one per Windows
account, not one per Friday home. Friday treats a home as a scratch home, and
keeps it away from the keychain slot, only when the home is under the system
temp directory, when the process profile is redirected, or when
`FRIDAY_TESTING=1` (`services/vault_passphrase.py`, `_home_is_scratch()`).
A `FRIDAY_HOME` anywhere else counts as a real home, because an owner may
keep Friday's data anywhere, and storing a passphrase from it would
overwrite the entry your real Friday uses.

Use a port other than 3000 if a real Friday is running on this machine.
Never point a development server or a test at your real home.

The test suites isolate themselves: pytest gives every run its own temporary
home (`tests/conftest.py`), and the Playwright app config starts a scratch
server with a temporary `FRIDAY_HOME` (`playwright.app.config.ts`).

## Run Friday from source

```powershell
python server.py
```

The server listens on `http://localhost:3000`, or the next free port if that
one is busy, and prints the address it chose. `FRIDAY_PORT` pins a port.
`server.py` at the repository root is a shim that runs
`src/agent_friday/server.py` from the repository root, because the server
serves `index.html` and `static/` from there.

Other entry points, installed with the package:

| Command | What it does |
|---|---|
| `friday` | Starts the server and opens the browser. |
| `friday setup` | The terminal setup wizard. |
| `friday models` | What local models this machine can run, and installing them. |
| `friday doctor` (also `status`, `check`) | A health check of the installation. |
| `python friday_tray.py` | The system tray app, which starts the server for you. |

`friday --help` lists the rest (`config`, `skills`, `export`, `erase` and
others).

Cloud keys are entered in the running app (**Settings > Connections**) and
stored in the encrypted credential store. A key in an environment variable
such as `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` overrides the stored copy.

Out of the box the routing mode is `cloud_only` (`model_routing.mode` in
`core/__init__.py`); the setup chat's privacy answer rewrites it. A fresh
scratch home therefore needs a cloud key, or the "Only on this PC" answer and
a local model, before a chat turn can be answered.

## macOS and Linux

The same commands work with `source venv/bin/activate`. CI runs the unit,
API and security suites on Ubuntu as well as Windows, but the tray, computer
control and the installer are Windows only, and Windows is the supported
platform.
