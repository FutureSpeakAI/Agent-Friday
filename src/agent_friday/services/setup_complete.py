"""Finishing first-run setup: the one function both setup surfaces call.

``POST /api/setup/complete`` and the setup chat's finish (and its "Set up
later") both end here, so there is one definition of what completing setup
writes:

1. Provider API keys -> the credential store (encrypted) and the live process.
2. The vault passphrase -> vault_passphrase.store, never a file in the clear.
3. A whitelisted settings delta -> _save_settings, including the routing mode
   the user chose on the consent screen. The browser flow used to pass that
   choice and drop it here, so a web install always ran on the factory mode;
   it is now merged into the existing ``model_routing`` block, the same way
   the terminal wizard does it.
4. The distribution preset.
5. personality.json (the preferred holographic scene). Written only here:
   core._is_existing_install() treats personality.json as proof of a finished
   install, so writing it any earlier would end first-run setup early.
6. The ``.setup_complete`` marker.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

#: The modes the consent screen offers (onboarding_copy.ROUTING_CHOICES).
ROUTING_MODES = ("cloud_only", "local_only", "local_preferred")

_SETTINGS_KEYS = ('agent_name', 'orchestrator_model', 'subagent_model',
                  'creative_model', 'music_model', 'minor_mode',
                  'daily_creation_free_choice', 'voice_model', 'tts_voice',
                  'temperature', 'communication_style', 'response_length',
                  'distribution', 'demo_mode', 'capability_routing')


def store_vault_passphrase(passphrase: str) -> bool:
    """Arm at-rest vault encryption now. Never logged, never written in clear."""
    vault_pass = (passphrase or "").strip()
    if not vault_pass:
        return False
    import agent_friday.core as core
    from agent_friday.services import vault_passphrase as _vp
    _vp.store(vault_pass)
    os.environ["FRIDAY_VAULT_PASSPHRASE"] = vault_pass
    try:
        core.FRIDAY_VAULT_PASSPHRASE = vault_pass
    except Exception:
        pass
    _vp.reset_cache()
    return True


def routing_delta(mode: str) -> dict:
    """The settings delta that makes `mode` the routing mode, or {}.

    Merged into the existing model_routing block: _load_settings_raw replaces
    top-level keys wholesale, so writing a partial block would silently drop
    ollama_url, vault_local_only and the rest.
    """
    if mode not in ROUTING_MODES:
        return {}
    from agent_friday.core import _load_settings
    block = dict((_load_settings() or {}).get("model_routing") or {})
    block["mode"] = mode
    return {"model_routing": block}


def complete_setup(data: dict) -> dict:
    """Apply a completion payload and mark setup complete. Returns {status}."""
    from agent_friday.core import FRIDAY_DIR, _SETUP_MARKER, _save_settings
    from agent_friday.services import credential_store as cs
    data = data or {}

    # 1) Provider API keys -> encrypted store + live env.
    legacy_key_fields = {'anthropic_api_key': 'anthropic',
                         'gemini_api_key': 'google-gemini',
                         'openai_api_key': 'openai'}
    for field, pname in legacy_key_fields.items():
        val = (data.get(field) or '').strip()
        if val:
            cs.set_provider_key(pname, val)
            cs.hot_reload_provider_key(pname, val)
    providers_payload = data.get('providers') or {}
    for pname, pcfg in providers_payload.items():
        if isinstance(pcfg, dict):
            kv = (pcfg.get('api_key') or pcfg.get('key') or '').strip()
            if kv:
                cs.set_provider_key(pname, kv)
                cs.hot_reload_provider_key(pname, kv)

    # 2) Vault passphrase.
    store_vault_passphrase(data.get('vault_passphrase') or '')

    # 3) Settings delta (NO secrets), including the routing mode.
    delta = {k: data[k] for k in _SETTINGS_KEYS if k in data}
    if providers_payload:
        delta['providers'] = {
            n: {kk: vv for kk, vv in (c or {}).items() if kk not in ('api_key', 'key')}
            for n, c in providers_payload.items() if isinstance(c, dict)
        }
    delta.update(routing_delta(str(data.get('routing_mode') or '')))
    delta['setup_complete'] = True

    # 4) Distribution preset.
    if data.get('distribution'):
        try:
            from agent_friday.services import distributions
            delta.update(distributions.apply_distro(data['distribution']))
        except Exception:
            pass

    _save_settings(delta)

    # 5) personality.json -- only now.
    if 'preferred_scene_index' in data:
        pfile = FRIDAY_DIR / 'personality.json'
        pdata = {}
        if pfile.exists():
            try:
                pdata = json.loads(pfile.read_text('utf-8'))
            except Exception:
                pass
        try:
            pdata['preferred_scene_index'] = int(data['preferred_scene_index'])
        except (TypeError, ValueError):
            pdata['preferred_scene_index'] = 0
        try:
            pfile.write_text(json.dumps(pdata, indent=2), encoding='utf-8')
        except Exception:
            pass

    # 6) The marker.
    try:
        _SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_MARKER.write_text(datetime.now().isoformat(), encoding='utf-8')
    except Exception:
        pass
    return {"status": "ok"}
