"""Which features need which downloadable models, and what they say when one
is missing.

One table: feature -> the pinned artifacts it needs (services/voice_artifacts:
where each lives, how it is fetched, its size and licence). Three questions
are answered from it, and nothing here downloads anything:

* ``status()``: for the Settings screen and ``GET /api/voice/feature-models``,
  every feature, whether it is ready, and what is missing with its size and
  the one install target that fetches it ("Speech recognition needs whisper
  base.en (141 MB) - Download"). The download itself is the voice installer's
  consented, checksummed, resumable job (``feature:<id>`` targets).
* ``require(feature)``: called by every feature before it loads a model. When
  something is missing it raises ``FeatureModelMissing`` whose message names
  the feature, the missing model and where to install it, and writes the same
  line to friday.log. A failure is never silent and never a hidden download.
* ``whisper_model_dir(size)``: the local folder of a faster-whisper model, so
  callers load it by path with no network lookup at all.

Presence counts the pinned install AND a model an earlier version already put
where the loader reads (a faster-whisper snapshot under Friday's whisper cache
or the Hugging Face cache, Kokoro's files in the Hugging Face cache), so an
existing install is not asked to download again.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("friday.feature_models")

#: Where the owner installs a missing model.
SETTINGS_PATH = "Settings > Voice > Local voice models"

#: feature -> what it is called, what uses it, the artifacts it cannot run
#: without, and the ones that only improve it.
FEATURES: Dict[str, dict] = {
    "transcription": {
        "name": "Transcription",
        "label": "Speech recognition for media transcripts and the podcast listening check",
        "used_by": ["media transcripts and captions", "podcast audio verification",
                    "phone voicemail transcripts"],
        "artifacts": ["whisper-base.en"],
        "optional": [],
    },
    "dictation": {
        "name": "Dictation",
        "label": "Push-to-transcribe (Alt+T) and the local voice ear on the processor",
        "used_by": ["push-to-transcribe", "local voice (CPU ear)"],
        "artifacts": ["whisper-base"],
        # Used on a GPU when present ("auto" picks small there); base answers
        # until it is installed.
        "optional": ["whisper-small"],
    },
    "kokoro_voice": {
        "name": "Kokoro voice",
        "label": "Kokoro voice for podcasts and the Kokoro local voice",
        "used_by": ["podcast audio", "local voice with the Kokoro speaker"],
        "artifacts": ["kokoro", "misaki", "spacy-en-core-web-sm", "espeakng-loader",
                      "kokoro-82m"],
        "optional": [],
    },
}

#: The short name a message uses for each artifact.
SHORT = {
    "whisper-base.en": "whisper base.en",
    "whisper-base": "whisper base",
    "whisper-small": "whisper small",
    "kokoro": "the kokoro package",
    "misaki": "misaki[en]",
    "spacy-en-core-web-sm": "spaCy's en_core_web_sm model",
    "espeakng-loader": "espeakng-loader",
    "kokoro-82m": "the Kokoro-82M weights and voices",
}

#: faster-whisper size -> Hugging Face repo (faster_whisper.utils._MODELS for
#: the sizes Friday uses; other sizes follow the same naming).
WHISPER_REPOS = {
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
}

#: faster-whisper size -> the pinned artifact that installs it.
WHISPER_ARTIFACT = {"base.en": "whisper-base.en", "base": "whisper-base",
                    "small": "whisper-small"}

_LOCK = threading.Lock()
#: feature -> the last refusal (message, who asked, when), for status().
_REFUSALS: Dict[str, dict] = {}
#: (feature, who) -> when it was last logged at WARNING.
_LOGGED: Dict[tuple, float] = {}
_RELOG_S = 600.0


class FeatureModelMissing(RuntimeError):
    """A feature cannot run because a model it needs is not installed.

    ``str(e)`` is the whole plain-language reason; ``code`` matches the voice
    failure taxonomy's style so routes can return it as structured data."""

    code = "feature_model_missing"

    def __init__(self, feature: str, missing: List[str], message: str):
        super().__init__(message)
        self.feature = feature
        self.missing = list(missing)
        self.message = message
        self.offer = "Install it from %s." % SETTINGS_PATH


# ── the registry ────────────────────────────────────────────────────────────

def artifacts_for(feature: str, *, optional: bool = False) -> List[str]:
    f = FEATURES[feature]
    return list(f["artifacts"]) + (list(f.get("optional") or []) if optional else [])


def artifact_size_mb(aid: str) -> int:
    from agent_friday.services import voice_artifacts as va
    return int(va.ARTIFACTS[aid]["size_mb"])


def artifact_deps_mb(aid: str) -> int:
    """What pip adds for a package artifact that is not here yet (spaCy and
    its helpers for misaki[en], transformers for kokoro), approximate."""
    from agent_friday.services import voice_artifacts as va
    try:
        return int(va.deps_mb(aid))
    except Exception:
        return 0


def download_mb(aid: str) -> int:
    """The honest size of installing ``aid``: itself plus its dependencies."""
    return artifact_size_mb(aid) + artifact_deps_mb(aid)


def _installed_version(package: str) -> Optional[str]:
    try:
        from importlib import metadata
        return metadata.version(package)
    except Exception:
        return None


def _kokoro_files_present() -> bool:
    """Kokoro's config, weights and at least one voice in the Hugging Face
    cache, read the way kokoro_voice and podcast_render read them."""
    try:
        import os
        from huggingface_hub import try_to_load_from_cache
        from agent_friday.services.voice_artifacts import ARTIFACTS
        repo = ARTIFACTS["kokoro-82m"]["repo"]
        cfg = try_to_load_from_cache(repo, "config.json")
        pth = try_to_load_from_cache(repo, "kokoro-v1_0.pth")
        if not (isinstance(cfg, str) and os.path.isfile(cfg)
                and isinstance(pth, str) and os.path.isfile(pth)):
            return False
        return any((Path(cfg).parent / "voices").glob("*.pt"))
    except Exception:
        return False


def artifact_present(aid: str) -> bool:
    """Is this artifact usable here now? The pinned install, or (for model
    files) a copy an earlier version put where the loader reads."""
    from agent_friday.services import voice_artifacts as va
    from agent_friday.services import voice_installer as vi
    if vi.artifact_installed(aid):
        return True
    a = va.ARTIFACTS.get(aid) or {}
    if a.get("kind") == "pip":
        # An earlier version the runtime still works with (misaki 0.7.4 from
        # an earlier Kokoro install) is present, with an upgrade available
        # (``upgrades``), never "missing": the feature keeps working.
        return va.pip_state(aid) in ("pinned", "older", "other")
    if a.get("kind") != "hf_files":
        return False
    for size, wa in WHISPER_ARTIFACT.items():
        if wa == aid:
            return whisper_model_dir(size) is not None
    if aid == "kokoro-82m":
        return _kokoro_files_present()
    return False


def missing_artifacts(feature: str, *, optional: bool = False) -> List[str]:
    return [aid for aid in artifacts_for(feature, optional=optional)
            if not artifact_present(aid)]


def upgradable_artifacts(feature: str) -> List[str]:
    """Packages this feature has at an older, still-working version than the
    pin: the feature runs; Settings offers the upgrade."""
    from agent_friday.services import voice_artifacts as va
    out = []
    for aid in artifacts_for(feature):
        try:
            if va.pip_state(aid) == "older":
                out.append(aid)
        except Exception:
            continue
    return out


def to_install(feature: str) -> List[str]:
    """What the feature's one install target fetches: what is missing, then
    the upgrades."""
    miss = missing_artifacts(feature)
    return miss + [aid for aid in upgradable_artifacts(feature) if aid not in miss]


def _describe(aid: str) -> str:
    """``whisper base.en`` / ``misaki[en] 0.9.4 (0.7.4 is installed)``."""
    from agent_friday.services import voice_artifacts as va
    a = va.ARTIFACTS[aid]
    name = SHORT.get(aid, a["label"])
    if a["kind"] == "pip":
        name += " " + a["version"]
        have = _installed_version(a["package"])
        if have and have != a["version"]:
            name += " (%s is installed)" % have
    return name


def message(feature: str, missing: List[str]) -> str:
    """"Transcription unavailable: whisper base.en is not installed -
    Settings > Voice > Local voice models (141 MB)." """
    f = FEATURES[feature]
    names = [_describe(aid) for aid in missing]
    if len(names) == 1:
        what = names[0] + " is not installed"
    else:
        what = ", ".join(names[:-1]) + " and " + names[-1] + " are not installed"
    mb = sum(download_mb(aid) for aid in missing)
    about = "about " if any(artifact_deps_mb(aid) for aid in missing) else ""
    return ("%s unavailable: %s - %s, Download (%s%s MB). Nothing is downloaded "
            "without that click." % (f["name"], what, SETTINGS_PATH, about, mb))


def ready(feature: str) -> bool:
    return not missing_artifacts(feature)


def unavailable_reason(feature: str) -> str:
    """"" when the feature can run, else the message ``require`` would raise."""
    miss = missing_artifacts(feature)
    return message(feature, miss) if miss else ""


def require(feature: str, *, who: str = "") -> None:
    """Raise ``FeatureModelMissing`` (and log it) when ``feature`` cannot run.

    Logged at WARNING on the ``friday.feature_models`` logger, so it is in
    friday.log with the feature, the missing model and who asked; repeated
    refusals from the same caller are logged again after ten minutes."""
    miss = missing_artifacts(feature)
    if not miss:
        return
    msg = message(feature, miss)
    now = time.time()
    with _LOCK:
        _REFUSALS[feature] = {"message": msg, "who": who, "at": now, "missing": list(miss)}
        key = (feature, who)
        loud = now - _LOGGED.get(key, 0.0) >= _RELOG_S
        if loud:
            _LOGGED[key] = now
    (log.warning if loud else log.debug)(
        "feature model missing: %s (asked by %s; missing %s)",
        msg, who or "unknown", ", ".join(miss))
    raise FeatureModelMissing(feature, miss, msg)


#: What the first-run voice wizard offers in its one "Download" step
#: (voice_installer's "tier1-models" target): the speech recogniser local
#: voice and Alt+T need, and the one media transcripts and the podcast
#: listening check need, so a fresh install is not left refusing both.
FIRST_RUN_FEATURES = ("dictation", "transcription")

#: Piper's default voice, fetched by the engine itself after the models
#: (local_voice.PiperTTS, a "medium" voice: an .onnx of about 60 MB plus its
#: .json). Named in the same consent sentence, not fetched unannounced.
PIPER_VOICE_NOTE = "the Piper voice is fetched if it is not here yet (about 60 MB)"


def first_run_models() -> List[str]:
    """The pinned speech recognisers the first-run step installs: whatever
    of ``FIRST_RUN_FEATURES``' required models is missing, in order."""
    out: List[str] = []
    for fid in FIRST_RUN_FEATURES:
        for aid in missing_artifacts(fid):
            if aid not in out:
                out.append(aid)
    return out


def first_run_offer() -> dict:
    """What the wizard shows before its Download click: each model with its
    size and licence, the total, and one sentence saying all of it."""
    from agent_friday.services import voice_artifacts as va
    items = [{"id": aid, "label": va.ARTIFACTS[aid]["label"], "short": SHORT.get(aid, aid),
              "size_mb": download_mb(aid), "licence": va.ARTIFACTS[aid]["licence"],
              "for": [FEATURES[f]["name"] for f in FIRST_RUN_FEATURES
                      if aid in FEATURES[f]["artifacts"]]}
             for aid in first_run_models()]
    mb = sum(i["size_mb"] for i in items)
    parts = ["%s (%s MB, %s; for %s)" % (i["short"], i["size_mb"], i["licence"],
                                          " and ".join(i["for"]))
             for i in items]
    if items:
        text = ("Download %s MB? %s will be saved on this computer, each checked "
                "against a fixed checksum before it is used, then %s. Nothing "
                "downloads until you confirm." % (mb, " and ".join(parts), PIPER_VOICE_NOTE))
    else:
        text = ("The speech recognition models are installed; %s. Nothing "
                "downloads until you confirm." % PIPER_VOICE_NOTE)
    return {"target": "tier1-models", "items": items, "size_mb": mb,
            "consent_text": text, "where": SETTINGS_PATH}


def forget_refusal(feature: str) -> None:
    """After an install: the last refusal no longer describes the machine."""
    with _LOCK:
        _REFUSALS.pop(feature, None)
        for k in [k for k in _LOGGED if k[0] == feature]:
            _LOGGED.pop(k, None)


def status() -> dict:
    """Every feature, ready or not, with what is missing and how to get it."""
    from agent_friday.services import voice_artifacts as va

    def _row(aid):
        # ``size_mb`` is the artifact itself; ``deps_mb``/``deps_note`` what
        # pip adds for it; ``total_mb`` the two together (what is confirmed).
        return {"id": aid, "label": va.ARTIFACTS[aid]["label"], "short": _describe(aid),
                "size_mb": artifact_size_mb(aid), "deps_mb": artifact_deps_mb(aid),
                "deps_note": va.deps_note(aid), "total_mb": download_mb(aid),
                "kind": va.ARTIFACTS[aid]["kind"],
                "licence": va.ARTIFACTS[aid]["licence"]}

    out = []
    for fid, f in FEATURES.items():
        miss = missing_artifacts(fid)
        opt = [aid for aid in f.get("optional") or [] if not artifact_present(aid)]
        rows = [_row(aid) for aid in miss]
        ups = [_row(aid) for aid in upgradable_artifacts(fid) if aid not in miss]
        with _LOCK:
            last = dict(_REFUSALS.get(fid) or {}) or None
        out.append({"id": fid, "name": f["name"], "label": f["label"],
                    "used_by": list(f["used_by"]), "ready": not miss,
                    "missing": rows, "size_mb": sum(r["total_mb"] for r in rows),
                    # Older versions that still work: "upgrade available".
                    "upgrades": ups, "upgrade_mb": sum(r["total_mb"] for r in ups),
                    "optional_missing": opt,
                    "message": message(fid, miss) if miss else "",
                    "install_target": "feature:" + fid, "where": SETTINGS_PATH,
                    "last_refusal": last})
    return {"features": out, "where": SETTINGS_PATH}


# ── faster-whisper by path ──────────────────────────────────────────────────

def _whisper_roots() -> List[Path]:
    """Where a faster-whisper snapshot may be, in the order the loaders look:
    the sealed image's baked copy (OS mode), Friday's whisper cache, then the
    Hugging Face cache an earlier version's ``WhisperModel("base.en")`` used."""
    from agent_friday.services import local_voice as lv
    from agent_friday.services import voice_artifacts as va
    roots: List[Path] = []
    try:
        roots.append(Path(lv.WhisperASR("base")._download_root()))
    except Exception:
        pass
    for r in (Path(lv.WHISPER_DIR), va.hf_hub_cache()):
        if r not in roots:
            roots.append(r)
    return roots


def _complete(snap: Path) -> bool:
    return (snap / "model.bin").is_file() and (snap / "config.json").is_file()


def whisper_model_dir(size: str, first_root: Optional[Path] = None) -> Optional[Path]:
    """The folder holding faster-whisper ``size`` on this computer, or None.

    Reads the disk only; never imports faster_whisper or asks the hub. A
    folder path is returned as is when it holds a model. ``first_root`` is a
    cache the caller loads from (its own download root), searched first."""
    s = str(size or "").strip()
    if not s:
        return None
    if "/" in s or "\\" in s:
        # A folder chosen in settings, not a size name.
        p = Path(s)
        return p if p.is_dir() and _complete(p) else None
    repo = WHISPER_REPOS.get(s) or ("Systran/faster-whisper-" + s)
    folder = "models--" + repo.replace("/", "--")
    roots = _whisper_roots()
    if first_root is not None:
        roots = [Path(first_root)] + [r for r in roots if r != Path(first_root)]
    for root in roots:
        base = root / folder
        try:
            ref = base / "refs" / "main"
            if ref.is_file():
                snap = base / "snapshots" / ref.read_text(encoding="utf-8").strip()
                if _complete(snap):
                    return snap
            snaps = base / "snapshots"
            if snaps.is_dir():
                for snap in sorted(snaps.iterdir()):
                    if snap.is_dir() and _complete(snap):
                        return snap
        except OSError:
            continue
    return None


def require_whisper(size: str, feature: str, *, who: str = "",
                    first_root: Optional[Path] = None) -> Path:
    """The local folder of whisper ``size``, or ``FeatureModelMissing`` for
    ``feature`` naming that model (logged, never a download)."""
    d = whisper_model_dir(size, first_root)
    if d is not None:
        return d
    aid = WHISPER_ARTIFACT.get(str(size))
    if aid is None:
        msg = ("%s unavailable: whisper %s is not on this computer and is not "
               "a model Friday installs; choose another size in %s."
               % (FEATURES[feature]["name"], size, SETTINGS_PATH))
        log.warning("feature model missing: %s (asked by %s)", msg, who or "unknown")
        raise FeatureModelMissing(feature, [], msg)
    msg = message(feature, [aid])
    with _LOCK:
        _REFUSALS[feature] = {"message": msg, "who": who, "at": time.time(), "missing": [aid]}
    log.warning("feature model missing: %s (asked by %s)", msg, who or "unknown")
    raise FeatureModelMissing(feature, [aid], msg)
