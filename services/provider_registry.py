"""
Agent Friday — Declarative Provider Registry
Inspired by patterns in Goose (Apache-2.0). All code is original.

JSON-based provider registration enabling zero-code provider addition.
"""
import json, os
from pathlib import Path

PROVIDERS_DIR = Path.home() / ".friday" / "providers"
PROVIDERS_DIR.mkdir(parents=True, exist_ok=True)

PROVIDER_SCHEMA_KEYS = {"name", "type", "base_url", "auth", "models",
                        "capabilities", "cost_per_1k", "enabled",
                        "label", "roles", "model_meta"}

# UI selector roles a model can be offered for. A provider declares which roles
# its models default to; individual models can override via `model_meta`.
#   orchestrator — main agent brain  (needs text + tool-calling)
#   subagent     — background tasks / drafts (text + tools)
#   creative     — image / vision generation
#   voice        — live audio (speech-to-speech)
ROLE_ORCHESTRATOR = "orchestrator"
ROLE_SUBAGENT = "subagent"
ROLE_CREATIVE = "creative"
ROLE_VOICE = "voice"
ALL_ROLES = (ROLE_ORCHESTRATOR, ROLE_SUBAGENT, ROLE_CREATIVE, ROLE_VOICE)

# Default providers (shipped with Friday). `model_meta` is the single source of
# truth for how each model is presented and which selectors it appears in — the
# UI renders entirely from this (via /api/models), nothing is hardcoded there.
# Adding a provider (drop a JSON in ~/.friday/providers/) or a model id makes it
# show up automatically; unknown models fall back to inferred metadata.
DEFAULT_PROVIDERS = [
    {
        "name": "anthropic",
        "label": "Anthropic (Claude)",
        "type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "auth": {"type": "env_var", "key": "ANTHROPIC_API_KEY"},
        "models": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "capabilities": ["tools", "vision"],
        "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
        "cost_per_1k": {"claude-opus-4-8": 0.075, "claude-sonnet-4-6": 0.045,
                        "claude-haiku-4-5-20251001": 0.001},
        "model_meta": {
            "claude-opus-4-8": {"label": "Claude Opus 4.8", "short": "Opus 4.8",
                                 "modalities": ["text", "vision", "tools"]},
            "claude-sonnet-4-6": {"label": "Claude Sonnet 4.6", "short": "Sonnet 4.6",
                                   "modalities": ["text", "vision", "tools"]},
            "claude-haiku-4-5-20251001": {"label": "Claude Haiku 4.5", "short": "Haiku 4.5",
                                           "modalities": ["text", "vision", "tools"]},
        },
        "enabled": True,
    },
    {
        "name": "openai",
        "label": "OpenAI",
        "type": "openai-compatible",
        "base_url": "https://api.openai.com/v1",
        "auth": {"type": "env_var", "key": "OPENAI_API_KEY"},
        "models": ["gpt-4o", "gpt-4o-mini", "o3"],
        "capabilities": ["tools", "vision"],
        "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
        "cost_per_1k": {"gpt-4o": 0.0375, "gpt-4o-mini": 0.00225},
        "model_meta": {
            "gpt-4o": {"label": "GPT-4o", "short": "GPT-4o",
                       "modalities": ["text", "vision", "tools"]},
            "gpt-4o-mini": {"label": "GPT-4o mini", "short": "4o-mini",
                            "modalities": ["text", "vision", "tools"]},
            "o3": {"label": "OpenAI o3", "short": "o3",
                   "modalities": ["text", "tools"]},
        },
        "enabled": True,
    },
    {
        "name": "ollama-local",
        "label": "Local (Ollama)",
        "type": "ollama",
        "base_url": "http://localhost:11434",
        "auth": {"type": "none"},
        # Static fallbacks; installed models are merged in live from the Ollama
        # daemon by the catalog builder so this list need not be maintained.
        "models": ["gemma4:latest", "gemma4:12b", "llama3.1:8b"],
        "capabilities": ["tools", "vision"],
        "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
        "cost_per_1k": {},
        "model_meta": {},
        "enabled": True,
    },
    {
        "name": "google-gemini",
        "label": "Google (Gemini)",
        "type": "google",
        "base_url": "https://generativelanguage.googleapis.com",
        "auth": {"type": "env_var", "key": "GEMINI_API_KEY"},
        # Gemini spans THREE roles, and each model declares its own in model_meta
        # so the picker never mixes them up:
        #   * VOICE     — Gemini 2.5 Flash (Gemini Live voice) + the live-audio
        #                 preview variants. NOT a text/creative model.
        #   * TEXT      — Gemini 2.5 Pro (frontier reasoning/text) serves the
        #                 orchestrator/subagent roles. NOT creative/generative.
        #   * CREATIVE  — image generation (Nano Banana Pro / Nano Banana 2) and
        #                 video generation (Google Veo).
        "models": [
            "gemini-2.5-flash", "gemini-2.5-pro",
            "gemini-nano-banana-pro", "gemini-nano-banana-2", "veo-3",
            "gemini-3.1-flash-live-preview",
            "gemini-2.5-flash-native-audio-preview-12-2025",
        ],
        "capabilities": ["tools", "vision", "audio", "live", "image", "video"],
        # Mixed-role provider — every model overrides via model_meta below.
        "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
        "cost_per_1k": {"gemini-3.1-flash-live-preview": 0.01},
        "model_meta": {
            # Voice — Gemini 2.5 Flash is the Gemini Live voice model.
            "gemini-2.5-flash": {"label": "Gemini 2.5 Flash", "short": "Flash",
                                  "roles": [ROLE_VOICE],
                                  "modalities": ["audio", "live"]},
            # Text / reasoning — frontier model for the agent roles.
            "gemini-2.5-pro": {"label": "Gemini 2.5 Pro", "short": "Pro",
                                "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
                                "modalities": ["text", "vision", "tools"]},
            # Image generation.
            "gemini-nano-banana-pro": {"label": "Gemini Nano Banana Pro (image)",
                                        "short": "Nano BPro", "roles": [ROLE_CREATIVE],
                                        "modalities": ["image"]},
            "gemini-nano-banana-2": {"label": "Gemini Nano Banana 2 (image)",
                                      "short": "Nano B2", "roles": [ROLE_CREATIVE],
                                      "modalities": ["image"]},
            # Video generation.
            "veo-3": {"label": "Google Veo (video)", "short": "Veo",
                       "roles": [ROLE_CREATIVE], "modalities": ["video"]},
            # Voice-only live models — only ever offered for the Voice role.
            "gemini-3.1-flash-live-preview": {
                "label": "Gemini 3.1 Flash Live Preview", "short": "3.1 Live",
                "roles": [ROLE_VOICE], "modalities": ["audio", "live"]},
            "gemini-2.5-flash-native-audio-preview-12-2025": {
                "label": "Gemini 2.5 Flash Audio Preview", "short": "2.5 Audio",
                "roles": [ROLE_VOICE], "modalities": ["audio", "live"]},
        },
        "enabled": True,
    },
    # ── Local voice (Tier-1, CPU) — the DEFAULT voice engine ──────────────────
    # faster-whisper ASR + Piper TTS, no torch/CUDA. Fulfills the asr + tts
    # capabilities. auth:none → registry reports it "available"; real readiness
    # (deps installed + models downloaded) is reported by services.provider_health
    # and services.local_voice.health(). Cloud Gemini Live stays the opt-in.
    {
        "name": "local-voice-lite",
        "label": "Local Voice (CPU)",
        "type": "local-voice",
        "base_url": "",
        "auth": {"type": "none"},
        "models": ["whisper-small", "piper-en_US-amy-medium"],
        "capabilities": ["asr", "tts"],
        "roles": [ROLE_VOICE],
        "cost_per_1k": {},
        "model_meta": {
            "whisper-small": {"label": "Whisper Small (ASR · local)",
                               "short": "Whisper S", "roles": [ROLE_VOICE],
                               "modalities": ["audio"]},
            "piper-en_US-amy-medium": {"label": "Piper Amy (TTS · local)",
                                        "short": "Piper Amy", "roles": [ROLE_VOICE],
                                        "modalities": ["audio"]},
        },
        "enabled": True,
    },
    # ── Local voice (Tier-2, GPU premium) — NeMo. Opt-in install only. ─────────
    # Registered + ENABLED so the UI surfaces it as a discoverable upgrade, but
    # its *availability* is gated by services.nemo_voice.gpu_tier_ready() (torch +
    # NeMo installed AND a CUDA GPU with enough VRAM). Without that stack it shows
    # as an unavailable upgrade with an install hint — it never gates Tier-1.
    # The heavy torch/CUDA + NeMo deps are a separate opt-in install step
    # (`.[voice-local-gpu]` + a torch-CUDA wheel). See VOICE_INTEGRATION_SPEC §13.
    {
        "name": "nvidia-nemo",
        "label": "NVIDIA NeMo (GPU · premium)",
        "type": "nemo-local",
        "base_url": "",
        "auth": {"type": "none"},
        "models": ["nemotron-3.5-asr-streaming-0.6b", "nemo-fastpitch-hifigan"],
        "capabilities": ["asr", "tts"],
        "roles": [ROLE_VOICE],
        "cost_per_1k": {},
        "model_meta": {
            "nemotron-3.5-asr-streaming-0.6b": {
                "label": "Nemotron 3.5 Streaming ASR (GPU)", "short": "Nemotron",
                "roles": [ROLE_VOICE], "modalities": ["audio"]},
            "nemo-fastpitch-hifigan": {
                "label": "NeMo FastPitch + HiFi-GAN (TTS · GPU)", "short": "FastPitch",
                "roles": [ROLE_VOICE], "modalities": ["audio"]},
        },
        "enabled": True,
    },
]

# Commonly requested providers users can add via JSON drop
PROVIDER_TEMPLATES = {
    "openrouter": {
        "name": "openrouter",
        "type": "openai-compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "auth": {"type": "env_var", "key": "OPENROUTER_API_KEY"},
        "models": [],
        "capabilities": ["tools"],
        "cost_per_1k": {},
        "enabled": False
    },
    "together": {
        "name": "together",
        "type": "openai-compatible",
        "base_url": "https://api.together.xyz/v1",
        "auth": {"type": "env_var", "key": "TOGETHER_API_KEY"},
        "models": [],
        "capabilities": ["tools"],
        "cost_per_1k": {},
        "enabled": False
    },
    "groq": {
        "name": "groq",
        "type": "openai-compatible",
        "base_url": "https://api.groq.com/openai/v1",
        "auth": {"type": "env_var", "key": "GROQ_API_KEY"},
        "models": [],
        "capabilities": ["tools"],
        "cost_per_1k": {},
        "enabled": False
    },
    "fireworks": {
        "name": "fireworks",
        "label": "Fireworks AI",
        "type": "openai-compatible",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "auth": {"type": "env_var", "key": "FIREWORKS_API_KEY"},
        "models": [],
        "capabilities": ["tools"],
        "cost_per_1k": {},
        "enabled": False
    },
    # Generic OpenAI-compatible endpoint — user supplies base_url + key. Covers
    # vLLM, LM Studio, Mistral, DeepSeek, Azure OpenAI, or any /v1 server.
    "custom": {
        "name": "custom",
        "label": "Custom endpoint",
        "type": "openai-compatible",
        "base_url": "https://your-endpoint.example/v1",
        "auth": {"type": "env_var", "key": "CUSTOM_API_KEY"},
        "models": [],
        "capabilities": ["tools"],
        "cost_per_1k": {},
        "enabled": False
    },
}


class ProviderRegistry:
    def __init__(self):
        self._providers = {}
        self._load_defaults()
        self._load_custom()

    def _load_defaults(self):
        for p in DEFAULT_PROVIDERS:
            self._providers[p["name"]] = p

    def _load_custom(self):
        for f in PROVIDERS_DIR.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if "name" in data:
                    self._providers[data["name"]] = data
            except Exception:
                pass

    def list_providers(self):
        return list(self._providers.values())

    def get_provider(self, name: str):
        return self._providers.get(name)

    def add_provider(self, data: dict) -> str:
        name = data.get("name", "custom")
        self._providers[name] = data
        path = PROVIDERS_DIR / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return str(path)

    def remove_provider(self, name: str) -> bool:
        if name in self._providers:
            del self._providers[name]
            path = PROVIDERS_DIR / f"{name}.json"
            if path.exists():
                path.unlink()
            return True
        return False

    def get_enabled_providers(self):
        return [p for p in self._providers.values() if p.get("enabled", True)]

    def get_models_for_provider(self, name: str):
        p = self._providers.get(name)
        return p.get("models", []) if p else []

    def is_provider_available(self, name: str) -> bool:
        p = self._providers.get(name)
        if not p or not p.get("enabled", True):
            return False
        # Local voice (Tier-1): "available" = the CPU deps are importable. Real
        # model-download readiness is reported separately by provider_health.
        if p.get("type") == "local-voice":
            try:
                from services.local_voice import deps_installed
                return deps_installed()
            except Exception:
                return False
        # Local voice (Tier-2, NeMo GPU): "available" only when the full GPU
        # stack can actually run (torch + NeMo + CUDA GPU + enough VRAM). Without
        # it the provider shows as an unavailable upgrade, never blocking Tier-1.
        if p.get("type") == "nemo-local":
            try:
                from services.nemo_voice import gpu_tier_ready
                return gpu_tier_ready()
            except Exception:
                return False
        auth = p.get("auth", {})
        if auth.get("type") == "env_var":
            if os.environ.get(auth.get("key", "")):
                return True
            # Also count a key stored encrypted via the credential store — covers
            # the window after a wizard/Settings save but before bootstrap_provider_env
            # has run (or in a process that never ran it).
            try:
                from services.credential_store import provider_key_status
                return provider_key_status(name) == "connected"
            except Exception:
                return False
        return True

    def get_templates(self):
        return PROVIDER_TEMPLATES


# Singleton
_registry = None
def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
