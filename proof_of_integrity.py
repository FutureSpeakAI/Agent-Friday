"""
Proof of Integrity — AI Bill of Integrity manifest.

Generates and verifies a cryptographically signed manifest that attests
to the agent's current behavioral constraints, model configuration,
tool inventory, vault status, epistemic health, memory integrity, and
version.  This is the artifact that lets peer agents (and auditors)
verify that this Friday instance is trustworthy.

Two signature layers:
  1. HMAC-SHA256 over the cLaws (local governance verification).
  2. Ed25519 keypair (multi-agent attestation / federation).
"""

import hashlib
import hmac as _hmac
import json
import os
import time
import threading
from datetime import datetime
from pathlib import Path

# Ed25519 — uses the stdlib-adjacent PyNaCl if available, otherwise
# falls back to a stub that returns "ed25519_unavailable" signatures.
try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError
    _HAS_ED25519 = True
except ImportError:
    _HAS_ED25519 = False
    BadSignatureError = Exception


# ── cLaws text (canonical) ─────────────────────────────────────

CLAWS_TEXT = (
    "1. I shall not harm a human being or, through inaction, allow harm.\n"
    "2. I shall obey user instructions except where they conflict with the First Law.\n"
    "3. I shall protect my own integrity except where this conflicts with the First or Second Laws.\n"
    "4. All behavioral constraints are cryptographically signed (HMAC-SHA256) and verified before every action."
)

AGENT_VERSION = "4.4.0"


class AgentIntegrityManifest:
    """Structured manifest of the agent's integrity state.

    Fields
    ------
    claws_hash        SHA-256 of the canonical cLaws text.
    claws_hmac        HMAC-SHA256 of the cLaws using the governance key.
    ed25519_pubkey    Hex-encoded Ed25519 public key (or None).
    ed25519_sig       Ed25519 signature of the manifest body (or None).
    model_manifest    Dict of active model names/versions.
    tool_manifest     List of registered tool names with their ring levels.
    vault_status      Summary of vault access control state.
    epistemic_score   Current epistemic calibration metrics.
    memory_health     Output of CognitiveMemory.health().
    version           Agent software version string.
    generated_at      ISO-8601 timestamp of manifest generation.
    """

    def __init__(self):
        self.claws_hash: str = ""
        self.claws_hmac: str = ""
        self.ed25519_pubkey: str | None = None
        self.ed25519_sig: str | None = None
        self.model_manifest: dict = {}
        self.tool_manifest: list[dict] = []
        self.vault_status: dict = {}
        self.epistemic_score: dict = {}
        self.memory_health: dict = {}
        self.version: str = AGENT_VERSION
        self.generated_at: str = ""
        self._body_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "claws_hash": self.claws_hash,
            "claws_hmac": self.claws_hmac,
            "ed25519_pubkey": self.ed25519_pubkey,
            "ed25519_sig": self.ed25519_sig,
            "model_manifest": self.model_manifest,
            "tool_manifest": self.tool_manifest,
            "vault_status": self.vault_status,
            "epistemic_score": self.epistemic_score,
            "memory_health": self.memory_health,
            "version": self.version,
            "generated_at": self.generated_at,
            "body_hash": self._body_hash,
        }

    def body_for_signing(self) -> bytes:
        """Deterministic JSON of the manifest body (excludes signatures)."""
        body = {
            "claws_hash": self.claws_hash,
            "model_manifest": self.model_manifest,
            "tool_manifest": self.tool_manifest,
            "vault_status": self.vault_status,
            "epistemic_score": self.epistemic_score,
            "memory_health": self.memory_health,
            "version": self.version,
            "generated_at": self.generated_at,
        }
        return json.dumps(body, sort_keys=True).encode("utf-8")


class IntegrityEngine:
    """Generates and verifies integrity manifests.

    Parameters
    ----------
    friday_dir : Path
        Root of the ~/.friday directory.
    governance_key_fn : callable
        Returns the HMAC governance key bytes.
    """

    def __init__(self, friday_dir=None, governance_key_fn=None):
        self.friday_dir = Path(friday_dir or Path.home() / ".friday")
        self._governance_key_fn = governance_key_fn
        self._signing_key = None  # Ed25519 SigningKey
        self._verify_key = None   # Ed25519 VerifyKey
        self._lock = threading.Lock()
        self._load_or_generate_ed25519()

    # ── Key management ─────────────────────────────────────────────

    def _load_or_generate_ed25519(self):
        """Load or create the Ed25519 attestation keypair."""
        if not _HAS_ED25519:
            return
        key_file = self.friday_dir / "vault" / ".attestation-key-ed25519"
        pub_file = self.friday_dir / "vault" / ".attestation-pubkey-ed25519"
        try:
            if key_file.exists():
                seed = key_file.read_bytes()
                self._signing_key = SigningKey(seed)
            else:
                self._signing_key = SigningKey.generate()
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_bytes(bytes(self._signing_key))
                pub_file.write_bytes(bytes(self._signing_key.verify_key))
            self._verify_key = self._signing_key.verify_key
        except Exception as e:
            print(f"  [INTEGRITY] Ed25519 key init failed: {e}")

    def get_public_key_hex(self) -> str | None:
        if self._verify_key is None:
            return None
        return bytes(self._verify_key).hex()

    # ── Generic payload signing (reused by the Federation protocol) ─

    def sign_payload(self, data: bytes) -> str | None:
        """Ed25519-sign an arbitrary payload. Returns the hex signature, or
        None if no signing key is available."""
        if self._signing_key is None:
            return None
        try:
            return self._signing_key.sign(data).signature.hex()
        except Exception:
            return None

    @staticmethod
    def verify_payload(data: bytes, sig_hex: str, pubkey_hex: str) -> bool:
        """Verify an Ed25519 signature over a payload with a given public key."""
        if not (_HAS_ED25519 and sig_hex and pubkey_hex):
            return False
        try:
            VerifyKey(bytes.fromhex(pubkey_hex)).verify(data, bytes.fromhex(sig_hex))
            return True
        except (BadSignatureError, Exception):
            return False

    # ── Manifest generation ────────────────────────────────────────

    def sign_manifest(self, model_manifest: dict | None = None,
                      tool_manifest: list[dict] | None = None,
                      vault_status: dict | None = None,
                      epistemic_score: dict | None = None,
                      memory_health: dict | None = None) -> AgentIntegrityManifest:
        """Build and sign a full integrity manifest."""
        m = AgentIntegrityManifest()
        m.generated_at = datetime.utcnow().isoformat() + "Z"

        # cLaws hash
        m.claws_hash = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()

        # cLaws HMAC
        if self._governance_key_fn:
            try:
                key = self._governance_key_fn()
                m.claws_hmac = _hmac.new(
                    key, CLAWS_TEXT.encode("utf-8"), hashlib.sha256
                ).hexdigest()
            except Exception:
                m.claws_hmac = "governance_key_unavailable"
        else:
            m.claws_hmac = "no_governance_key_fn"

        # Populate fields
        m.model_manifest = model_manifest or self._default_model_manifest()
        m.tool_manifest = tool_manifest or []
        m.vault_status = vault_status or {}
        m.epistemic_score = epistemic_score or self._load_epistemic()
        m.memory_health = memory_health or self._load_memory_health()

        # Body hash
        body = m.body_for_signing()
        m._body_hash = hashlib.sha256(body).hexdigest()

        # Ed25519 signature
        m.ed25519_pubkey = self.get_public_key_hex()
        if self._signing_key is not None:
            try:
                signed = self._signing_key.sign(body)
                m.ed25519_sig = signed.signature.hex()
            except Exception:
                m.ed25519_sig = "signing_failed"
        else:
            m.ed25519_sig = "ed25519_unavailable"

        return m

    def verify_manifest(self, manifest_dict: dict) -> dict:
        """Verify an integrity manifest's signatures.

        Returns {valid: bool, checks: {claws_hmac: ..., ed25519: ..., body_hash: ...}}.
        """
        checks = {}

        # 1. Verify cLaws hash
        expected_claws = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()
        checks["claws_hash"] = manifest_dict.get("claws_hash") == expected_claws

        # 2. Verify cLaws HMAC
        if self._governance_key_fn:
            try:
                key = self._governance_key_fn()
                expected_hmac = _hmac.new(
                    key, CLAWS_TEXT.encode("utf-8"), hashlib.sha256
                ).hexdigest()
                checks["claws_hmac"] = manifest_dict.get("claws_hmac") == expected_hmac
            except Exception:
                checks["claws_hmac"] = False
        else:
            checks["claws_hmac"] = None  # can't verify without key

        # 3. Verify body hash
        body = {
            "claws_hash": manifest_dict.get("claws_hash"),
            "model_manifest": manifest_dict.get("model_manifest"),
            "tool_manifest": manifest_dict.get("tool_manifest"),
            "vault_status": manifest_dict.get("vault_status"),
            "epistemic_score": manifest_dict.get("epistemic_score"),
            "memory_health": manifest_dict.get("memory_health"),
            "version": manifest_dict.get("version"),
            "generated_at": manifest_dict.get("generated_at"),
        }
        body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        checks["body_hash"] = body_hash == manifest_dict.get("body_hash")

        # 4. Ed25519 signature verification
        sig_hex = manifest_dict.get("ed25519_sig")
        pubkey_hex = manifest_dict.get("ed25519_pubkey")
        if _HAS_ED25519 and sig_hex and pubkey_hex and \
           sig_hex not in ("ed25519_unavailable", "signing_failed"):
            try:
                vk = VerifyKey(bytes.fromhex(pubkey_hex))
                vk.verify(body_bytes, bytes.fromhex(sig_hex))
                checks["ed25519"] = True
            except (BadSignatureError, Exception):
                checks["ed25519"] = False
        else:
            checks["ed25519"] = None  # can't verify

        valid = all(v is True for v in checks.values() if v is not None)
        return {"valid": valid, "checks": checks}

    # ── Helpers ────────────────────────────────────────────────────

    def _default_model_manifest(self) -> dict:
        try:
            settings_file = self.friday_dir / "settings.json"
            if settings_file.exists():
                s = json.loads(settings_file.read_text(encoding="utf-8"))
                return {
                    "orchestrator": s.get("orchestrator_model", "claude-opus-4-8"),
                    "subagent": s.get("subagent_model", "claude-sonnet-4-6"),
                    "creative": s.get("creative_model", "gemini-nano-banana-2"),
                    "voice": s.get("voice_model", "gemini-3.1-flash-live-preview"),
                }
        except Exception:
            pass
        return {"orchestrator": "claude-opus-4-8"}

    def _load_epistemic(self) -> dict:
        try:
            ep_file = self.friday_dir / "epistemic_scores.json"
            if ep_file.exists():
                return json.loads(ep_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _load_memory_health(self) -> dict:
        try:
            from cognitive_memory import get_cognitive_memory
            cm = get_cognitive_memory()
            return cm.health()
        except Exception:
            return {}


# ── Governance key — OS keychain with file fallback ───────────────────────────

_KEYRING_SERVICE = "agent-friday"
_KEYRING_ACCOUNT = "governance-key"
_GOV_KEY_FILE    = Path.home() / ".friday" / "vault" / ".governance-key"
_GOV_KEY_LOCK    = threading.Lock()


def get_governance_key() -> bytes:
    """Load (or generate) the HMAC governance key.

    Priority:
      1. OS keychain via the 'keyring' library (Windows Credential Manager,
         macOS Keychain, Secret Service on Linux).
      2. File fallback: ~/.friday/vault/.governance-key (600 permissions).
      3. Generate a new random key and persist it to whichever store works.

    The key is 32 random bytes (256 bits). It is generated once and reused;
    rotating it invalidates all existing manifests (expected behaviour during
    a governance reset).
    """
    with _GOV_KEY_LOCK:
        # 1. Try OS keychain
        try:
            import keyring as _kr
            stored = _kr.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
            if stored:
                return bytes.fromhex(stored)
        except Exception:
            pass

        # 2. Try key file
        if _GOV_KEY_FILE.exists():
            try:
                raw = _GOV_KEY_FILE.read_bytes()
                if len(raw) == 32:
                    return raw
                # Hex-encoded in file
                return bytes.fromhex(raw.decode().strip())
            except Exception:
                pass

        # 3. Generate a new key and persist it
        key = os.urandom(32)
        try:
            import keyring as _kr
            _kr.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, key.hex())
        except Exception:
            # keyring unavailable — fall back to file
            try:
                _GOV_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                _GOV_KEY_FILE.write_bytes(key)
                try:
                    import stat
                    _GOV_KEY_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
                except Exception:
                    pass
            except Exception:
                pass
        return key


# ── Singleton accessor ─────────────────────────────────────────

_engine_instance = None
_engine_lock = threading.Lock()


def get_integrity_engine(friday_dir=None, governance_key_fn=None) -> IntegrityEngine:
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                # Default governance key comes from the OS keychain (or file
                # fallback). Callers may override by passing governance_key_fn.
                key_fn = governance_key_fn or get_governance_key
                _engine_instance = IntegrityEngine(
                    friday_dir=friday_dir,
                    governance_key_fn=key_fn,
                )
    return _engine_instance
