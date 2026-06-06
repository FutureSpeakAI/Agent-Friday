# Agent Friday v4.4.0 — The trust-and-portability release

Hardens authentication, adds a third (OpenAI-compatible) provider with a full
agentic tool loop, gates every tool call behind a sandbox policy, ships a
portable SKILL.md skill registry, and closes the loop on skill learning so real
chat usage feeds the optimizer.

## ✨ Added
- **OpenAI-compatible provider** — A third cloud provider alongside Anthropic and Ollama. Opt-in via `model_routing.cloud_provider = "openai"` + `openai_base_url` (defaults to OpenRouter), `openai_model`, `openai_api_key` (or env `OPENAI_API_KEY` / `OPENROUTER_API_KEY`). Unlocks OpenRouter's hundreds of models and any `/v1` endpoint, with a **full agentic tool loop** at parity with the Anthropic path. Vault / sensitive requests never route here.
- **Portable skill registry** (`skill_registry.py`) — A portable **SKILL.md folder** format (YAML frontmatter + markdown body, agentskills.io-compatible). Import/export across folder, zip, legacy-YAML, and OpenClaw formats. New routes `GET /api/skills`, `POST /api/skills/import`, `GET /api/skills/<name>/export`, `GET /api/skillopt/state`. Matched skills are injected into the system prompt each turn — learned skills take effect without a restart.
- **Closed-loop learning** (`skill_capture.py`) — Captures turn trajectories to CognitiveMemory + JSONL, feeds real chat usage into the SkillOpt optimizer, and runs a nightly `skillopt-nightly` auto-research job. Connects the previously-dormant SkillOpt machinery to live usage.

## 🔒 Security
- **Auth hardening** — Session secret is now a persisted random value (`~/.friday/secret_key`, mode `0600`) instead of a hardcoded default. Constant-time credential checks (`hmac.compare_digest`). Per-IP login throttle (8 / 5 min). New toggles: `FRIDAY_TRUST_LOOPBACK` (set `0` to require login even on localhost), `FRIDAY_WS_TOKEN` (token for the `/ws/live` voice socket), `FRIDAY_COOKIE_SECURE`. SameSite=Lax + HttpOnly cookies.
- **Tool-execution sandbox** — Every agent tool call passes a policy gate. `FRIDAY_SANDBOX_MODE` = `off` / `confine` (default) / `strict`; `FRIDAY_SANDBOX_ROOT` (default: home). `confine` confines `write_file` to the root and keeps a destructive-command blocklist for `run_command`; `strict` additionally allowlists commands.

## 🐛 Fixed
- **Command injection** in the vibe-code launcher — `cwd` is now validated under the sandbox root and the task string is sanitized before shell launch.
- **Startup banner crash** — `python server.py` to a piped/redirected stdout no longer crashes on box-drawing characters under Windows cp1252.

## 📦 Install
- **Windows binary:** download `AgentFriday.exe` below, run it, open <http://localhost:3000>. (First launch creates `~/.friday` and `~/wiki`. Set `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` in `~/.friday/settings.json` or the in-app setup wizard.)
- **From source:** `install.ps1` (Windows) / `install.sh` (macOS/Linux) — clones, builds a venv, assembles the UI, and creates a launcher.

> The bundled `.exe` excludes the heavy optional ML stack (torch / sentence-transformers / Headroom); semantic context-pruning and Headroom compression fall back to no-ops in the binary. Run from source to enable them.

**Full changelog:** see `CHANGELOG.md`.
