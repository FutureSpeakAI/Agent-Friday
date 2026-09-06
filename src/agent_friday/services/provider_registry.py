"""
Agent Friday — Declarative Provider Registry
Inspired by patterns in Goose (Apache-2.0). All code is original.

JSON-based provider registration enabling zero-code provider addition.

v2 (model-agnostic provider layer): descriptors are normalized through
routing/provider_descriptors.normalize_descriptor on load (v1 JSONs keep
working — `type` aliases to `adapter`, classification is inferred), validated
on add, and the built-in set grows to sixteen providers: the six originals
below plus OpenRouter (first-class), HuggingFace, Groq, Together, Fireworks,
Mistral, DeepSeek, xAI, Perplexity, and Cohere from
routing/provider_descriptors.BUILTIN_EXTRA_PROVIDERS. YAML descriptors
(*.yaml/*.yml) are accepted alongside JSON in ~/.friday/providers/.
"""
import json, logging, os
from pathlib import Path

from agent_friday.paths import friday_home
from agent_friday.routing.provider_descriptors import (
    BUILTIN_EXTRA_PROVIDERS,
    normalize_descriptor,
    provider_env_keys,
    validate_descriptor,
)

_log = logging.getLogger("friday.provider_registry")

PROVIDERS_DIR = friday_home() / "providers"
PROVIDERS_DIR.mkdir(parents=True, exist_ok=True)

PROVIDER_SCHEMA_KEYS = {"name", "type", "adapter", "base_url", "auth", "models",
                        "capabilities", "cost_per_1k", "enabled",
                        "label", "roles", "model_meta", "classification",
                        "discovery", "pricing", "budget", "features",
                        "network", "priority", "schema_version",
                        "model_format", "extra_headers"}

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
        # The CURRENT Claude family, 2026-08-17. Superseded ids (opus-4-6/4-7/4-8,
        # sonnet-4-5/4-6) are gone rather than kept "just in case": a stale
        # hardcoded model id is the same defect class as the picker's
        # provider-ordered cap — nobody maintains it, and it quietly becomes
        # what the product actually uses. `start.bat` was pinning
        # ANTHROPIC_MODEL=claude-sonnet-4-6, overriding the configured
        # sonnet-5 on every launch.
        "models": ["claude-sonnet-5", "claude-opus-5", "claude-fable-5",
                   "claude-haiku-4-5-20251001"],
        "capabilities": ["tools", "vision"],
        "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
        # Blended (midpoint of in/out) per 1K, kept equal to cost_meter.PRICING
        # by tests/unit/test_cost_meter.py. This is both the rate the picker
        # displays and cost_meter.price_for's fallback, so a stale number here
        # is visible in two places at once.
        "cost_per_1k": {
            "claude-fable-5": 0.030,
            "claude-opus-5": 0.015,
            "claude-sonnet-5": 0.009,
            "claude-haiku-4-5": 0.003,
            "claude-haiku-4-5-20251001": 0.003,
        },
        "model_meta": {
            "claude-sonnet-5": {"label": "Claude Sonnet 5", "short": "Sonnet 5",
                                "modalities": ["text", "vision", "tools"]},
            "claude-opus-5": {"label": "Claude Opus 5", "short": "Opus 5",
                              "modalities": ["text", "vision", "tools"]},
            # Fable is the one to reach for on writing and spec work.
            "claude-fable-5": {"label": "Claude Fable 5", "short": "Fable 5",
                               "modalities": ["text", "vision", "tools"]},
            "claude-haiku-4-5-20251001": {"label": "Claude Haiku 4.5",
                                          "short": "Haiku 4.5",
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
        # On-device image generation (Z-Image Turbo FP8 via ComfyUI). Until
        # this entry existed the creative role could only ever resolve to a
        # cloud Gemini model, so a working local image stack was invisible to
        # the picker. Decision D8 gated it on a residency scheduler, because
        # Z-Image's ~14.5 GB of weights and the language seats cannot share a
        # 12 GB card — generation runs under the Arbiter's exclusive image
        # lease (services/local_image.py).
        #
        # Availability is EARNED: model_catalog marks it unavailable unless the
        # weights are actually on this machine, so the picker never offers a
        # model that cannot run here.
        "name": "local-comfyui",
        "label": "Local (ComfyUI)",
        "type": "comfyui",
        "base_url": "http://127.0.0.1:8188",
        "auth": {"type": "none"},
        "classification": "local",
        # Two on-device image models, and the picker should offer BOTH. They
        # are not interchangeable: Z-Image is a turbo model that trades some
        # fidelity for eight-step speed, SD 3.5 Medium runs thirty steps and
        # costs several times as much per picture. `note` carries that trade
        # so the choice is informed at the point it is made.
        # Two image models were here for a long time; five more (three video,
        # two more image) joined 2026-09-04, each declaring the files that
        # prove it is really here (services/local_image.py, local_video.py).
        # FLUX.1 dev is deliberately NOT in this list — its licence forbids
        # commercial use of the model itself, so it is registered per-machine
        # via services/local_creative_overrides.py instead of shipping here.
        "models": ["z-image-turbo-fp8", "sd3.5-medium-fp8",
                   "sdxl-base-1.0", "qwen-image-q3ks",
                   "wan2.2-ti2v-5b", "wan2.2-14b-a14b-gguf", "cogvideox-2b"],
        "capabilities": ["image", "video"],
        "roles": [ROLE_CREATIVE],
        "cost_per_1k": {},
        "model_meta": {
            "z-image-turbo-fp8": {
                "label": "Z-Image Turbo FP8 (local image)",
                "short": "Z-Image",
                "roles": [ROLE_CREATIVE],
                "modalities": ["image"],
                "note": "8 steps — fastest local image",
                "default": True,
            },
            "sd3.5-medium-fp8": {
                "label": "Stable Diffusion 3.5 Medium (local image)",
                "short": "SD 3.5 Medium",
                "roles": [ROLE_CREATIVE],
                "modalities": ["image"],
                "note": "30 steps — slower, stronger prompt adherence",
                # Surfaced rather than buried: this model is free to use
                # commercially only below $1M annual revenue, and redistributing
                # it obliges a "Powered by Stability AI" notice. Z-Image carries
                # no such condition, so the difference belongs in the picker.
                "licence": "Stability AI Community License",
                "licence_note": "free commercial use below $1M annual revenue; "
                                "attribution required if redistributed",
            },
            "sdxl-base-1.0": {
                "label": "Stable Diffusion XL Base 1.0 (local image)",
                "short": "SDXL Base",
                "roles": [ROLE_CREATIVE],
                "modalities": ["image"],
                "note": "best pick for consistent styles or recurring "
                        "characters via LoRA — measured ~55s per 1024x1024 "
                        "image on a 4070 12GB, ~8GB VRAM peak",
                "licence": "CreativeML Open RAIL++-M",
                "licence_note": "no commercial-use restriction; large "
                                "LoRA/ControlNet ecosystem",
            },
            "qwen-image-q3ks": {
                "label": "Qwen-Image Q3_K_S (local image)",
                "short": "Qwen-Image",
                "roles": [ROLE_CREATIVE],
                "modalities": ["image"],
                "note": "renders legible text in images — quantized for 12GB. "
                        "Measured: bold high-contrast text (a chalkboard "
                        "sign) came out fully legible on a real test, but "
                        "this is one data point, not a guarantee across all "
                        "prompts. ~4.25 min per 1024x1024 image, ~10.7GB "
                        "VRAM peak on a 4070 12GB — close to the ceiling",
                "licence": "Apache 2.0",
            },
            "wan2.2-ti2v-5b": {
                "label": "Wan 2.2 TI2V 5B (local video)",
                "short": "Wan 2.2 5B",
                "roles": [ROLE_CREATIVE],
                "modalities": ["video"],
                "note": "NOT currently reliable on 12GB cards — sampling "
                        "completes cleanly (~9 min for 20 steps at 832x480), "
                        "but VAE decode hung for 15+ minutes with no result "
                        "in two separate tests (both an 81-frame and a "
                        "29-frame clip), so this is length-independent, not "
                        "just slow for long clips. Prefer Wan 2.2 14B GGUF "
                        "until this is root-caused.",
                "licence": "Apache 2.0",
                "licence_note": "no commercial restriction",
            },
            "wan2.2-14b-a14b-gguf": {
                "label": "Wan 2.2 A14B GGUF (local video)",
                "short": "Wan 2.2 14B",
                "roles": [ROLE_CREATIVE],
                "modalities": ["video"],
                "note": "default video model — the only one of the two Wan "
                        "tiers confirmed reliable on this hardware. Measured "
                        "~8.1 min for a short (~1.8s) clip at 832x480, "
                        "~9.2-9.7GB VRAM peak on a 4070 12GB. Two-expert "
                        "model, so expect longer clips to take "
                        "proportionally longer — full 5s-clip timing not "
                        "yet measured.",
                "licence": "Apache 2.0",
                "default": True,
            },
            "cogvideox-2b": {
                "label": "CogVideoX 2B (local video)",
                "short": "CogVideoX 2B",
                "roles": [ROLE_CREATIVE],
                "modalities": ["video"],
                "note": "6 seconds, fixed 480x720, 8fps — reliable but the "
                        "most limited of the three video options. Measured "
                        "~2.4 min for a short (~1.6s) clip, ~5GB VRAM peak "
                        "on a 4070 12GB — the lightest of the three.",
                "licence": "Apache 2.0",
            },
        },
        "enabled": True,
    },
    {
        # Higgsfield — cloud generation across image, video, 3D and audio.
        #
        # `models` is DELIBERATELY EMPTY and must stay that way. Higgsfield's
        # catalogue is ~120 models that change without notice, so the list is
        # enumerated at runtime by services/higgsfield_catalog into the shared
        # discovery cache and REPLACES these statics — the same hosted-native
        # path Anthropic uses (model_catalog._model_entries_for). A hardcoded
        # lineup here is how the prior spec came to name `soul/standard` and
        # `dop/standard`, neither of which exists on the live account: the list
        # was stale before it was built.
        #
        # No models cached yet → the provider still renders, dimmed, with an
        # honest hint. It never invents a model it has not seen.
        "name": "higgsfield",
        "label": "Higgsfield",
        "type": "higgsfield",
        "base_url": "https://mcp.higgsfield.ai/mcp",
        # Auth is MCP OAuth 2.1 (PKCE) — tokens live encrypted in
        # ~/.friday/mcp_oauth/higgsfield.oauth.enc, never an env var. `none`
        # here means "no API key to add in Settings", NOT "no credential
        # required"; availability is probed against the live connector in
        # is_provider_available() below.
        "auth": {"type": "none"},
        # Cloud by construction. `higgsfield` is deliberately absent from
        # routing.provider_descriptors.LOCAL_CAPABLE_ADAPTERS so the egress
        # gate can never classify a Higgsfield call as local.
        "classification": "cloud",
        "models": [],
        # Enumerated over the MCP connector, not an HTTP /models endpoint.
        # Declaring it here (rather than special-casing the name inside the
        # sweep) is what lets model_discovery.refresh_all_stale populate this
        # catalogue on boot: without it the provider had no discovery mode,
        # the sweep skipped it, and the picker stayed empty until someone
        # POSTed /api/models/refresh by hand.
        "discovery": {"mode": "mcp", "module": "higgsfield_catalog"},
        # Declared from what was measured on the live account, and no more:
        # image/video/3D generation plus audio (one music model, the rest
        # speech). 3D IS present — it appears via the MCP catalogue even
        # though the Higgsfield CLI's `model list` has no 3D type at all.
        "capabilities": ["image", "video", "3d", "music", "speech"],
        "roles": [ROLE_CREATIVE],
        "cost_per_1k": {},
        "model_meta": {},
        "enabled": True,
    },
    {
        # kie.ai — pay-per-use creative model marketplace (image/video/audio),
        # 30-50% cheaper than official vendor APIs because it resells API
        # access rather than hosting compute. Stephen, 2026-09-04: wants this
        # alongside Higgsfield because it is pay-per-use, not subscription,
        # and it carries MiniMax/Hailuo — a model he cannot run locally.
        #
        # UNLIKE every other provider in this file, kie.ai has no enumerable
        # catalog API: model discovery is a WEB PAGE (kie.ai/market), not a
        # JSON endpoint. Higgsfield's `models_explore` MCP tool has no
        # equivalent here, so `models` below is a HAND-CURATED, hand-VERIFIED
        # list (each id confirmed against its docs.kie.ai page 2026-09-04) —
        # not a hosted-native catalog that refreshes itself. Adding a model
        # kie.ai actually ships means confirming the exact "model" string on
        # its docs page and adding it here; guessing the string from a URL
        # slug is unsafe — measured 2026-09-04, the Flux-2 docs page lives at
        # /market/flux2/... but its real model id is "flux-2/..." (hyphen the
        # URL doesn't have). A wrong string fails silently at generation time
        # with a vendor 400, which is worse than the model being merely
        # unlisted.
        #
        # Two more asymmetries with Higgsfield, both left as follow-up work
        # rather than silently mis-wired:
        #   * Veo 3.1 and Suno each have their OWN dedicated kie.ai endpoint
        #     (/api/v1/veo/generate, /api/v1/generate) with their own request/
        #     response shape, separate from the unified Market API
        #     (/api/v1/jobs/createTask + /jobs/recordInfo) every model below
        #     dispatches through. Neither is wired up — kie_generate.py only
        #     speaks the unified Market API — so they are NOT in `models`.
        #   * kie.ai's async model is submit -> POLL (or webhook). This
        #     integration polls only (services/kie_generate.py); a webhook
        #     needs a publicly reachable callback URL, which is an exposure
        #     decision for Stephen, not this integration.
        "name": "kie",
        "label": "kie.ai",
        "type": "kie",
        "base_url": "https://api.kie.ai/api/v1",
        "auth": {"type": "env_var", "key": "KIE_API_KEY"},
        "classification": "cloud",
        "models": [
            # Image.
            "nano-banana-pro", "flux-2/pro-text-to-image",
            "flux-2/pro-image-to-image", "gpt-image-2-text-to-image",
            "qwen/image-to-image", "bytedance/seedream-v4-edit",
            # Video.
            "kling-3.0/video", "hailuo/02-text-to-video-standard",
            # Audio utilities (ElevenLabs) — catalogued, not a creative-role
            # pick: dialogue/isolation/sound-effect tools, not a one-shot
            # "speak this text" TTS, so roles:[] below (same treatment
            # higgsfield_catalog gives non-core audio).
            "elevenlabs/text-to-dialogue-v3", "elevenlabs/audio-isolation",
            "elevenlabs/sound-effect-v2",
        ],
        "capabilities": ["image", "video", "audio"],
        "roles": [ROLE_CREATIVE],
        # kie.ai bills in CREDITS per task (reported as `creditsConsumed` on
        # the completed task), never per-token — cost_per_1k stays empty and
        # kie_generate.generate() feeds cost_meter.record() a direct cost_usd
        # figure instead (see kie_generate.CREDIT_USD_ESTIMATE for the caveat
        # on that conversion rate).
        "cost_per_1k": {},
        "model_meta": {
            "nano-banana-pro": {"label": "Nano Banana Pro (kie.ai)",
                                 "short": "Nano BPro", "roles": [ROLE_CREATIVE],
                                 "modalities": ["image"]},
            "flux-2/pro-text-to-image": {"label": "Flux-2 Pro (kie.ai)",
                                          "short": "Flux-2 Pro",
                                          "roles": [ROLE_CREATIVE],
                                          "modalities": ["image"]},
            "flux-2/pro-image-to-image": {
                "label": "Flux-2 Pro — edit (kie.ai)", "short": "Flux-2 Edit",
                "roles": [ROLE_CREATIVE], "modalities": ["image"],
                "note": "image-to-image only — needs a source image"},
            "gpt-image-2-text-to-image": {"label": "GPT Image 2 (kie.ai)",
                                           "short": "GPT Image 2",
                                           "roles": [ROLE_CREATIVE],
                                           "modalities": ["image"]},
            "qwen/image-to-image": {
                "label": "Qwen — edit (kie.ai)", "short": "Qwen Edit",
                "roles": [ROLE_CREATIVE], "modalities": ["image"],
                "note": "image-to-image only — needs a source image"},
            "bytedance/seedream-v4-edit": {
                "label": "Seedream v4 — edit (kie.ai)", "short": "Seedream",
                "roles": [ROLE_CREATIVE], "modalities": ["image"],
                "note": "image-to-image only — needs a source image"},
            "kling-3.0/video": {"label": "Kling 3.0 (kie.ai)",
                                 "short": "Kling 3.0", "roles": [ROLE_CREATIVE],
                                 "modalities": ["video"]},
            "hailuo/02-text-to-video-standard": {
                "label": "Hailuo 02 Standard — MiniMax (kie.ai)",
                "short": "Hailuo 02", "roles": [ROLE_CREATIVE],
                "modalities": ["video"],
                "note": "MiniMax H3 is on kie.ai but its exact model id was "
                        "not confirmed as of 2026-09-04 — this is Hailuo 02 "
                        "Standard, the sibling model actually verified"},
            "elevenlabs/text-to-dialogue-v3": {
                "label": "ElevenLabs Dialogue v3 (kie.ai)", "short": "11L Dialogue",
                "roles": [], "modalities": ["audio", "speech"]},
            "elevenlabs/audio-isolation": {
                "label": "ElevenLabs Audio Isolation (kie.ai)", "short": "11L Isolate",
                "roles": [], "modalities": ["audio"]},
            "elevenlabs/sound-effect-v2": {
                "label": "ElevenLabs Sound Effect v2 (kie.ai)", "short": "11L SFX",
                "roles": [], "modalities": ["audio"]},
        },
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
        #   * TEXT      — Gemini 3.5 Flash / 3.1 Pro / 3.1 Flash-Lite plus the
        #                 2.5 generation (2.5 Pro/Flash sunset 2026-10-16). NOT
        #                 creative/generative. (Gemini 3.5 Pro is not yet in the
        #                 public API as of 2026-07.)
        #   * CREATIVE  — image generation (Nano Banana Pro / Nano Banana 2) and
        #                 video generation (Google Veo + Gemini Omni Flash).
        "models": [
            "gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
            "gemini-2.5-pro",
            "gemini-nano-banana-2", "gemini-nano-banana-pro", "veo-3",
            "gemini-omni-flash",
            "lyria-clip", "lyria-pro",
            "gemini-2.5-flash-native-audio-latest",
            "gemini-2.5-flash-native-audio-preview-09-2025",
            "gemini-3.1-flash-live-preview",
            "gemini-2.5-flash-native-audio-preview-12-2025",
            "gemini-2.5-flash",
        ],
        "capabilities": ["tools", "vision", "audio", "live", "image", "video", "music"],
        # Mixed-role provider — every model overrides via model_meta below.
        "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
        # Blended (in+out)/2 per-1K display rates; the real split in/out
        # pricing lives in services/cost_meter.PRICING.
        "cost_per_1k": {"gemini-3.1-flash-live-preview": 0.01,
                        "gemini-3.5-flash": 0.00525,
                        "gemini-3.1-pro-preview": 0.007,
                        "gemini-3.1-flash-lite": 0.000875,
                        "gemini-omni-flash": 0.0095},
        "model_meta": {
            # Voice — Gemini 2.5 Flash is the Gemini Live voice model.
            "gemini-2.5-flash": {"label": "Gemini 2.5 Flash", "short": "Flash",
                                  "roles": [ROLE_VOICE],
                                  "modalities": ["audio", "live"]},
            # Text / reasoning — Gemini's frontier text model, but roles:[] until
            # a Gemini text/agentic dispatch exists in routing/model_router.py
            # (_apply_cloud_provider only retags anthropic/openai/local, so a
            # picked gemini-2.5-pro would silently fall back to another model —
            # never offer what can't dispatch). Creations resolve Pro separately.
            "gemini-2.5-pro": {"label": "Gemini 2.5 Pro", "short": "Pro",
                                "roles": [],
                                "modalities": ["text", "vision", "tools"]},
            # Gemini 3.x text/reasoning — the July-2026 lineup. Same roles:[]
            # deal as 2.5 Pro above: visible in the catalog/Model Browser, but
            # never offered for orchestrator/subagent until a google text
            # dispatch lands in routing/model_router.py. 3.5 Flash is the
            # frontier agentic model (1M ctx); 3.1 Pro is the deep-reasoning
            # preview; 3.1 Flash-Lite is the cheap high-volume tier.
            "gemini-3.5-flash": {"label": "Gemini 3.5 Flash", "short": "3.5 Flash",
                                  "roles": [],
                                  "modalities": ["text", "vision", "tools"]},
            "gemini-3.1-pro-preview": {"label": "Gemini 3.1 Pro (preview)",
                                        "short": "3.1 Pro", "roles": [],
                                        "modalities": ["text", "vision", "tools"]},
            "gemini-3.1-flash-lite": {"label": "Gemini 3.1 Flash-Lite",
                                       "short": "3.1 Lite", "roles": [],
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
            # Gemini Omni Flash — any-to-any video generation/editing (I/O
            # 2026). Friendly id: creative_engine resolves it to
            # gemini-omni-flash-preview and dispatches via the Interactions
            # API (NOT Veo’s long-running-operation path).
            "gemini-omni-flash": {"label": "Gemini Omni Flash (video)",
                                   "short": "Omni", "roles": [ROLE_CREATIVE],
                                   "modalities": ["video"]},
            # Music generation (Lyria 3). lyria-clip = clips ≤30s, lyria-pro = full songs.
            # These friendly IDs are resolved to real API strings by music_engine.resolve_music_model().
            # roles:[] — picked in the Studio Music panel via `music_model`, never
            # via the creative_model picker (Lyria as the image model breaks gen).
            "lyria-clip": {"label": "Lyria 3 Clip (music · ≤30s)", "short": "Lyria Clip",
                            "roles": [], "modalities": ["audio", "music"]},
            "lyria-pro": {"label": "Lyria 3 Pro (music · full)", "short": "Lyria Pro",
                           "roles": [], "modalities": ["audio", "music"]},
            # Voice-only live models — only ever offered for the Voice role.
            # All four verified via a real bidiGenerateContent connect 2026-07-06.
            # The -latest alias is the default: it tracks Google's current
            # native-audio model and survives preview retirements.
            "gemini-2.5-flash-native-audio-latest": {
                "label": "Gemini 2.5 Flash Native Audio (latest)", "short": "2.5 Audio ✦",
                "roles": [ROLE_VOICE], "modalities": ["audio", "live"]},
            "gemini-2.5-flash-native-audio-preview-09-2025": {
                "label": "Gemini 2.5 Flash Audio Preview (09-2025)", "short": "2.5 Audio 09",
                "roles": [ROLE_VOICE], "modalities": ["audio", "live"]},
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
    # (`.[voice-local-gpu]` + a torch-CUDA wheel). See docs/user-guide/local-voice-gpu-tier.md.
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

# One-click provider templates for the Add Provider UI. Built from the same
# v2 descriptors that ship as built-ins (disabled copies), plus the generic
# "custom" endpoint recipe — covers vLLM, LM Studio, TGI, Azure OpenAI, or any
# /v1 server the user points it at.
PROVIDER_TEMPLATES = {
    p["name"]: {**normalize_descriptor(p), "enabled": False}
    for p in BUILTIN_EXTRA_PROVIDERS
}
PROVIDER_TEMPLATES["custom"] = normalize_descriptor({
    "name": "custom",
    "label": "Custom endpoint",
    "type": "openai-compatible",
    "base_url": "https://your-endpoint.example/v1",
    "auth": {"type": "env_var", "key": "CUSTOM_API_KEY"},
    "models": [],
    "capabilities": ["tools"],
    "cost_per_1k": {},
    "enabled": False,
})


class ProviderRegistry:
    def __init__(self):
        self._providers = {}
        self._origins = {}      # name -> "builtin" | "file" | "ui"
        self._load_errors = []  # [{file, error}] — surfaced in /api/health/full
        self._load_defaults()
        self._load_custom()

    def _load_defaults(self):
        for p in DEFAULT_PROVIDERS + BUILTIN_EXTRA_PROVIDERS:
            norm = normalize_descriptor(p)
            self._providers[norm["name"]] = norm
            self._origins[norm["name"]] = "builtin"

    def _load_custom(self):
        """Load user descriptors from ~/.friday/providers/ (*.json + *.yaml).

        Bad files are SKIPPED with a logged, surfaced error — never a silent
        `pass` — so a typo'd descriptor shows up in /api/health/full instead
        of vanishing.
        """
        self._load_errors = []
        files = sorted(list(PROVIDERS_DIR.glob("*.json"))
                       + list(PROVIDERS_DIR.glob("*.yaml"))
                       + list(PROVIDERS_DIR.glob("*.yml")))
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    if f.suffix in (".yaml", ".yml"):
                        import yaml
                        data = yaml.safe_load(fh)
                    else:
                        data = json.load(fh)
                if not isinstance(data, dict) or "name" not in data:
                    raise ValueError("descriptor must be an object with a 'name'")
                ok, errors, _warnings = validate_descriptor(data)
                if not ok:
                    raise ValueError("; ".join(errors))
                norm = normalize_descriptor(data)
                self._providers[norm["name"]] = norm
                self._origins[norm["name"]] = "file"
            except Exception as e:
                _log.warning("skipping provider descriptor %s: %s", f.name, e)
                self._load_errors.append({"file": f.name, "error": str(e)[:300]})

    def load_errors(self):
        """Descriptor files that failed to load this session (name + reason)."""
        return list(self._load_errors)

    def provider_origin(self, name: str) -> str:
        """'builtin' | 'file' | 'ui' | 'unknown' — where a descriptor came from."""
        return self._origins.get(name, "unknown")

    def list_providers(self):
        return list(self._providers.values())

    def get_provider(self, name: str):
        return self._providers.get(name)

    def add_provider(self, data: dict, validate: bool = True) -> str:
        """Validate, normalize, register, and persist a descriptor.

        Raises ValueError with a joined error message on an invalid descriptor
        so callers (routes) can 400 with actionable detail.
        """
        if validate:
            ok, errors, _warnings = validate_descriptor(data)
            if not ok:
                raise ValueError("; ".join(errors))
        norm = normalize_descriptor(data)
        name = norm.get("name", "custom")
        self._providers[name] = norm
        self._origins[name] = "ui" if self._origins.get(name) != "file" else "file"
        path = PROVIDERS_DIR / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(norm, f, indent=2)
        return str(path)

    def update_provider(self, name: str, patch: dict) -> dict:
        """Merge a partial update into an existing descriptor and persist.

        Returns the updated descriptor; raises KeyError when the provider does
        not exist and ValueError when the merged result fails validation.
        """
        current = self._providers.get(name)
        if current is None:
            raise KeyError(name)
        merged = {**current, **(patch or {}), "name": name}
        ok, errors, _warnings = validate_descriptor(merged)
        if not ok:
            raise ValueError("; ".join(errors))
        norm = normalize_descriptor(merged)
        self._providers[name] = norm
        path = PROVIDERS_DIR / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(norm, f, indent=2)
        if self._origins.get(name) == "builtin":
            self._origins[name] = "ui"
        return norm

    def remove_provider(self, name: str) -> bool:
        """Remove a provider. A user-added provider disappears; a customized
        BUILT-IN reverts to its shipped default (the override file is deleted
        and the built-in descriptor reloads)."""
        if name not in self._providers:
            return False
        for suffix in (".json", ".yaml", ".yml"):
            path = PROVIDERS_DIR / f"{name}{suffix}"
            if path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass
        builtin = next((p for p in DEFAULT_PROVIDERS + BUILTIN_EXTRA_PROVIDERS
                        if p.get("name") == name), None)
        if builtin is not None:
            self._providers[name] = normalize_descriptor(builtin)
            self._origins[name] = "builtin"
        else:
            del self._providers[name]
            self._origins.pop(name, None)
        return True

    def is_builtin(self, name: str) -> bool:
        return any(p.get("name") == name
                   for p in DEFAULT_PROVIDERS + BUILTIN_EXTRA_PROVIDERS)

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
                from agent_friday.services.local_voice import deps_installed
                return deps_installed()
            except Exception:
                return False
        # Local voice (Tier-2, NeMo GPU): "available" only when the full GPU
        # stack can actually run (torch + NeMo + CUDA GPU + enough VRAM). Without
        # it the provider shows as an unavailable upgrade, never blocking Tier-1.
        if p.get("type") == "nemo-local":
            try:
                from agent_friday.services.nemo_voice import gpu_tier_ready
                return gpu_tier_ready()
            except Exception:
                return False
        # Higgsfield authenticates through the MCP connector's OAuth, not an
        # env var, so "available" means the connector is actually reachable
        # and authorized right now. Asserting availability from the descriptor
        # alone is exactly how a config file comes to claim a capability the
        # system cannot deliver.
        if p.get("type") == "higgsfield":
            try:
                from agent_friday.services import agent as _agent
                mgr = getattr(_agent, "_MCP_MANAGER", None)
                if mgr is None:
                    return False
                sp = (getattr(mgr, "servers", {}) or {}).get("higgsfield")
                return sp is not None and bool(getattr(sp, "tools", None))
            except Exception:
                return False
        auth = p.get("auth", {})
        if auth.get("type") == "env_var":
            # Primary env var + any aliases (HF_TOKEN vs HUGGINGFACE_API_KEY).
            env_keys = provider_env_keys(p)
            if any(os.environ.get(k) for k in env_keys):
                return True
            # Also count a key stored encrypted via the credential store — covers
            # the window after a wizard/Settings save but before bootstrap_provider_env
            # has run (or in a process that never ran it).
            try:
                from agent_friday.services.credential_store import provider_key_status
                if provider_key_status(name) == "connected":
                    return True
            except Exception:
                pass
            # Importing the credential store bootstraps launch-script keys into
            # the environment as a side effect — re-check before reporting
            # unavailable, or the FIRST catalog build after boot dims every
            # cloud model even though the keys exist.
            return any(os.environ.get(k) for k in env_keys)
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
