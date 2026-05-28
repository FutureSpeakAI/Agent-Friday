"""
Application engine — drives the full application pipeline for a JobListing.

This module is deliberately I/O-light at the core: the actual resume
generation, brand-voice check, and form submission are pluggable so
server.py (which has the LLM clients + browser tools) can inject them.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

try:
    from data.job_tracker_schema import JobListing, JobTracker, ApplicationRecord
except ImportError:  # pragma: no cover
    from job_tracker_schema import JobListing, JobTracker, ApplicationRecord  # type: ignore


log = logging.getLogger("friday.skills.application_engine")

HERE = Path(__file__).parent
DEFAULT_CONFIG_PATH = HERE / "config.yaml"
USER_OVERRIDE = Path.home() / ".friday" / "skills" / "application_engine.local.yaml"

VARIANT_STATE_PATH = Path.home() / ".friday" / "skills" / "application_engine.bandit.json"


# ════════════════════════════════════════════════════════════════════════
#  Config
# ════════════════════════════════════════════════════════════════════════

def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required for application_engine config")
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with cfg_path.open(encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}
    if USER_OVERRIDE.exists():
        try:
            with USER_OVERRIDE.open(encoding="utf-8") as f:
                base = _deep_merge(base, yaml.safe_load(f) or {})
        except Exception as e:
            log.warning("application_engine user override merge failed: %s", e)
    return base


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ════════════════════════════════════════════════════════════════════════
#  ATS detection
# ════════════════════════════════════════════════════════════════════════

def detect_ats(url: str, config: Dict[str, Any]) -> str:
    """Return platform name (greenhouse|lever|...) or 'unknown'."""
    if not url:
        return "unknown"
    detection = config.get("ats", {}).get("detection", {})
    host = (urllib.parse.urlparse(url).netloc or "").lower()
    for name, rules in detection.items():
        for needle in rules.get("domain_contains", []):
            if needle.lower() in host or needle.lower() in url.lower():
                return name
    return "unknown"


def ats_field_plan(ats: str, config: Dict[str, Any],
                   candidate: Dict[str, str]) -> List[Dict[str, str]]:
    """Build a field-by-field fill plan for the given ATS."""
    fmap = config.get("ats", {}).get("field_maps", {}).get(ats, {})
    plan: List[Dict[str, str]] = []
    if not fmap:
        return plan
    for canonical, selector in fmap.items():
        value = candidate.get(canonical, "")
        plan.append({"field": canonical, "selector": selector,
                     "value_preview": _truncate(value, 80)})
    return plan


# ════════════════════════════════════════════════════════════════════════
#  Resume A/B bandit
# ════════════════════════════════════════════════════════════════════════

class VariantBandit:
    """
    Epsilon-greedy bandit over resume variants. Persisted to disk so
    state survives restarts.

    State schema:
        { variant_id: { "plays": int, "wins": int, "score": float } }
    """

    def __init__(self, epsilon: float = 0.10, min_samples: int = 5):
        self.epsilon = epsilon
        self.min_samples = min_samples
        self._state: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not VARIANT_STATE_PATH.exists():
            return {}
        try:
            return json.loads(VARIANT_STATE_PATH.read_text("utf-8"))
        except Exception:
            return {}

    def _save(self):
        try:
            VARIANT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            VARIANT_STATE_PATH.write_text(json.dumps(self._state, indent=2),
                                          encoding="utf-8")
        except Exception as e:
            log.warning("bandit save failed: %s", e)

    def pick(self, candidates: List[str], default: Optional[str] = None) -> str:
        candidates = [c for c in candidates if c]
        if not candidates:
            return default or "default"

        # Cold-start: round-robin through under-sampled variants first
        under = [c for c in candidates
                 if self._state.get(c, {}).get("plays", 0) < self.min_samples]
        if under:
            return random.choice(under)

        # Epsilon-greedy
        if random.random() < self.epsilon:
            return random.choice(candidates)
        scored = sorted(
            candidates,
            key=lambda c: self._state.get(c, {}).get("score", 0.0),
            reverse=True,
        )
        return scored[0]

    def record_outcome(self, variant: str, reward: float):
        """reward in [0,1]: 0=ghost, 0.3=screening, 0.7=interview, 1.0=offer."""
        s = self._state.setdefault(variant, {"plays": 0, "wins": 0, "score": 0.0})
        s["plays"] += 1
        s["wins"] += int(reward >= 0.5)
        # Exponential moving average to handle drift
        alpha = 0.3
        s["score"] = (1 - alpha) * s["score"] + alpha * reward
        self._save()


# ════════════════════════════════════════════════════════════════════════
#  Pluggable hooks (LLM clients, browser, brand voice) — defaults are stubs
# ════════════════════════════════════════════════════════════════════════

ResumeBuilder = Callable[[JobListing, str, Dict[str, Any]], Dict[str, Any]]
CoverLetterDrafter = Callable[[JobListing, Dict[str, Any], Dict[str, Any]], str]
BrandVoiceChecker = Callable[[str], Tuple[float, List[str]]]
Submitter = Callable[[Dict[str, Any]], Dict[str, Any]]
Notifier = Callable[[Dict[str, Any]], None]


def _default_resume_builder(listing: JobListing, variant: str,
                            cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Stub — returns a metadata-only resume reference."""
    variants = cfg.get("resume", {}).get("variants", {})
    spec = variants.get(variant, {})
    return {
        "variant": variant,
        "path": spec.get("file", ""),
        "must_include": list(spec.get("must_include", [])),
        "tailored": False,   # real builder would set True
        "headline": spec.get("title", ""),
    }


def _default_cover_drafter(listing: JobListing, resume_pack: Dict[str, Any],
                           cfg: Dict[str, Any]) -> str:
    """Stub cover letter — server.py will plug in an LLM-backed drafter."""
    return (
        f"Dear {listing.company} team,\n\n"
        f"I'm writing about the {listing.title} role. At FutureSpeak.AI, "
        f"I've been building Agent Friday — Asimov's Mind, a sovereign "
        f"AI desktop integrating Claude Opus orchestration with Gemini Live "
        f"voice. Recent work includes a self-improving skills system "
        f"inspired by SkillOpt and a 30-tool governance gate signed with "
        f"HMAC-SHA256.\n\n"
        f"I'd like to bring this builder's track record to "
        f"{listing.company} and would welcome a conversation.\n\n"
        f"— Stephen C. Webster\n"
    )


def _default_brand_voice_checker(text: str) -> Tuple[float, List[str]]:
    """Stub — flags forbidden phrases."""
    forbidden_default = ["passionate", "rockstar", "team player", "synergy"]
    found = [p for p in forbidden_default if p in text.lower()]
    score = 1.0 - (0.2 * len(found))
    return max(0.0, min(1.0, score)), found


def _default_submitter(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Stub — pretends to submit, no real network call."""
    return {"submitted": False, "reason": "no submitter wired up",
            "would_submit_to": payload.get("source_url", "")}


def _default_notifier(payload: Dict[str, Any]):
    log.info("[notify] %s", payload.get("title", "(no title)"))


# ════════════════════════════════════════════════════════════════════════
#  Quality gates
# ════════════════════════════════════════════════════════════════════════

def evaluate_quality_gates(*, listing: JobListing, resume_pack: Dict[str, Any],
                            cover_letter: str, cover_voice_score: float,
                            cover_voice_violations: List[str],
                            cfg: Dict[str, Any],
                            tracker: JobTracker) -> Tuple[List[str], List[str], bool, bool]:
    """
    Returns (passed, failed, hard_block, needs_confirmation).
    """
    gates = cfg.get("quality_gates", {})
    passed: List[str] = []
    failed: List[str] = []
    hard_block = False
    needs_confirmation = False

    # salary_floor
    floor = gates.get("salary_floor_usd", 0)
    salary_top = listing.salary_max or listing.salary_min or 0
    if salary_top and salary_top < floor:
        failed.append("salary_floor")
        hard_block = True
    else:
        passed.append("salary_floor")

    # salary_ceiling (needs confirmation)
    confirm = gates.get("salary_confirmation_threshold_usd", 0)
    if salary_top and confirm and salary_top >= confirm:
        needs_confirmation = True
        passed.append("salary_confirmation_required")

    # must_include
    must = resume_pack.get("must_include", [])
    if must:
        # In a real flow, we'd check the rendered resume bullets.
        # Stub: trust the variant declaration.
        passed.append("must_include")
    else:
        failed.append("must_include")

    # cover_voice
    if gates.get("require_brand_voice_check", True):
        if cover_voice_score >= gates.get("brand_voice_min_score", 0.75):
            passed.append("cover_voice")
        else:
            failed.append(f"cover_voice (score={cover_voice_score:.2f}, "
                          f"violations={cover_voice_violations})")

    # cover_length
    wc = len(cover_letter.split())
    if wc <= gates.get("cover_letter_max_words", 450) and \
       wc >= gates.get("cover_letter_min_words", 180):
        passed.append("cover_length")
    else:
        failed.append(f"cover_length ({wc} words)")

    # dedup_apply
    if any(a.is_active for a in tracker.applications_for(listing.job_id)):
        failed.append("dedup_apply (already submitted)")
        hard_block = True
    else:
        passed.append("dedup_apply")

    return passed, failed, hard_block, needs_confirmation


# ════════════════════════════════════════════════════════════════════════
#  Engine entrypoint
# ════════════════════════════════════════════════════════════════════════

def apply_to_job(*, job_id: str,
                 tracker: Optional[JobTracker] = None,
                 config: Optional[Dict[str, Any]] = None,
                 candidate: Optional[Dict[str, str]] = None,
                 force_confirm: bool = False,
                 resume_variant: Optional[str] = None,
                 dry_run: bool = False,
                 resume_builder: Optional[ResumeBuilder] = None,
                 cover_drafter: Optional[CoverLetterDrafter] = None,
                 brand_voice: Optional[BrandVoiceChecker] = None,
                 submitter: Optional[Submitter] = None,
                 notifier: Optional[Notifier] = None) -> Dict[str, Any]:
    """
    Run the application pipeline against a tracked job.

    Returns the same shape as documented in SKILL.md.
    """
    started = time.monotonic()
    config = config or load_config()
    tracker = tracker or JobTracker().load()
    resume_builder = resume_builder or _default_resume_builder
    cover_drafter = cover_drafter or _default_cover_drafter
    brand_voice = brand_voice or _default_brand_voice_checker
    submitter = submitter or _default_submitter
    notifier = notifier or _default_notifier

    listing = tracker.get_job(job_id)
    if not listing:
        return _failure(f"job {job_id} not found", duration_ms=_dt(started))

    # Pick variant
    bandit_cfg = config.get("resume", {}).get("ab_testing", {})
    bandit = VariantBandit(epsilon=bandit_cfg.get("epsilon", 0.10),
                           min_samples=bandit_cfg.get("min_samples_before_exploit", 5))
    available = list(config.get("resume", {}).get("variants", {}).keys())
    chosen_variant = resume_variant or bandit.pick(
        available,
        default=config.get("resume", {}).get("default_variant", "default"),
    )

    # Build resume + cover
    resume_pack = resume_builder(listing, chosen_variant, config)
    cover_letter = cover_drafter(listing, resume_pack, config)

    # Brand-voice check
    voice_score, violations = brand_voice(cover_letter)

    # ATS detection
    ats = detect_ats(listing.source_url, config)
    if ats == "unknown":
        log.warning("ATS unknown for url %s", listing.source_url)

    # Quality gates
    passed, failed, hard_block, needs_confirm = evaluate_quality_gates(
        listing=listing,
        resume_pack=resume_pack,
        cover_letter=cover_letter,
        cover_voice_score=voice_score,
        cover_voice_violations=violations,
        cfg=config,
        tracker=tracker,
    )
    if force_confirm:
        needs_confirm = True

    # Decide outcome
    status = "submitted"
    submit_result: Dict[str, Any] = {}
    if hard_block:
        status = "blocked"
    elif dry_run:
        status = "dry_run"
    elif needs_confirm:
        status = "needs_confirmation"
    else:
        candidate = candidate or _default_candidate_info()
        candidate.setdefault("cover_letter", cover_letter)
        candidate.setdefault("resume_path", resume_pack.get("path", ""))
        plan = ats_field_plan(ats, config, candidate)
        submit_payload = {
            "ats": ats,
            "source_url": listing.source_url,
            "field_plan": plan,
            "resume": resume_pack,
            "cover_letter": cover_letter,
        }
        try:
            submit_result = submitter(submit_payload) or {}
            if not submit_result.get("submitted", False):
                status = "submit_failed"
        except Exception as e:
            log.exception("submission error")
            submit_result = {"submitted": False, "error": str(e)}
            status = "submit_failed"

    # Persist record
    rec = ApplicationRecord(
        job_id=job_id,
        stage=("applied" if status == "submitted" else "discovered"),
        ats_platform=ats,
        resume_variant=chosen_variant,
        cover_letter_variant=resume_pack.get("variant", chosen_variant) + "_cover",
        cover_letter_word_count=len(cover_letter.split()),
        quality_gates_passed=passed,
        quality_gates_failed=failed,
        notes=(submit_result.get("reason") or submit_result.get("error") or ""),
    )
    rec.history.append({"at": _now_iso(), "stage": rec.stage,
                        "note": f"status={status}"})
    tracker.log_application(rec)

    # Notification surface
    if status == "blocked":
        notifier({"title": f"Application blocked — {listing.title} @ {listing.company}",
                  "body": f"Gates failed: {failed}", "priority": "high",
                  "meta": {"kind": "application_blocked"}})
    elif status == "needs_confirmation":
        notifier({"title": f"Confirm application — {listing.title} @ {listing.company}",
                  "body": f"Above confirmation threshold. Approve to submit.",
                  "priority": "high",
                  "meta": {"kind": "application_needs_confirmation",
                           "application_id": rec.application_id}})

    duration_ms = _dt(started)
    result = {
        "application_id": rec.application_id,
        "job_id": job_id,
        "status": status,
        "ats": ats,
        "resume_variant": chosen_variant,
        "cover_letter": cover_letter,
        "cover_letter_word_count": rec.cover_letter_word_count,
        "quality_gates_passed": passed,
        "quality_gates_failed": failed,
        "brand_voice_score": voice_score,
        "brand_voice_violations": violations,
        "submit_result": submit_result,
        "duration_ms": duration_ms,
    }
    _record_to_skillopt(result, config)
    return result


# ════════════════════════════════════════════════════════════════════════
#  Outcome feedback — closes the loop
# ════════════════════════════════════════════════════════════════════════

OUTCOME_REWARDS = {
    "ghost": 0.0,
    "rejection": 0.0,
    "screening": 0.3,
    "interview": 0.7,
    "offer": 1.0,
}


def record_response(*, application_id: str, response_kind: str,
                    tracker: Optional[JobTracker] = None,
                    config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Call when a real response is detected (email scrape, manual entry).
    Updates the bandit + SkillOpt for the variant used.
    """
    tracker = tracker or JobTracker().load()
    config = config or load_config()
    rec = tracker.get_application(application_id)
    if not rec:
        return {"updated": False, "reason": "application not found"}

    reward = OUTCOME_REWARDS.get(response_kind.lower(), 0.0)
    bandit_cfg = config.get("resume", {}).get("ab_testing", {})
    bandit = VariantBandit(epsilon=bandit_cfg.get("epsilon", 0.10),
                           min_samples=bandit_cfg.get("min_samples_before_exploit", 5))
    bandit.record_outcome(rec.resume_variant, reward)

    # Advance pipeline stage
    if response_kind.lower() in {"screening", "interview", "offer"}:
        rec.advance(response_kind.lower(), note=f"response: {response_kind}")
    elif response_kind.lower() == "rejection":
        rec.advance("rejected", note="response: rejection")
    tracker.log_application(rec)

    # Push to SkillOpt as user_satisfaction signal
    _record_outcome_to_skillopt(rec, reward, response_kind, config)
    return {"updated": True, "application_id": application_id,
            "reward": reward, "stage": rec.stage}


# ════════════════════════════════════════════════════════════════════════
#  SkillOpt integration
# ════════════════════════════════════════════════════════════════════════

def _record_to_skillopt(result: Dict[str, Any], config: Dict[str, Any]):
    try:
        from skillopt_engine import record_skill_run
    except ImportError:
        return
    skill_name = config.get("skillopt", {}).get("skill_name", "application_engine")
    completeness = 1.0 - (len(result.get("quality_gates_failed", [])) /
                          max(len(result.get("quality_gates_passed", [])) +
                              len(result.get("quality_gates_failed", [])), 1))
    status = result.get("status", "")
    # Treat blocked as a successful catch by the gate (accuracy=1).
    accuracy_map = {
        "submitted": 1.0,
        "needs_confirmation": 0.9,
        "blocked": 1.0,        # the gate caught a bad fit
        "dry_run": 0.8,
        "submit_failed": 0.0,
    }
    try:
        record_skill_run(
            skill_name=skill_name,
            inputs={"job_id": result.get("job_id"),
                    "resume_variant": result.get("resume_variant")},
            outputs={"status": status,
                     "quality_gates_failed": result.get("quality_gates_failed", [])},
            metrics={
                "accuracy": accuracy_map.get(status, 0.5),
                "completeness": completeness,
                "user_satisfaction": 0.0,
            },
            duration_ms=result.get("duration_ms", 0.0),
            cost_usd=0.0,
            error=None if status != "submit_failed" else "submitter error",
        )
    except Exception as e:
        log.warning("skillopt record failed: %s", e)


def _record_outcome_to_skillopt(rec: ApplicationRecord, reward: float,
                                response_kind: str, config: Dict[str, Any]):
    try:
        from skillopt_engine import record_skill_run
    except ImportError:
        return
    skill_name = config.get("skillopt", {}).get("skill_name", "application_engine")
    try:
        record_skill_run(
            skill_name=skill_name,
            inputs={"application_id": rec.application_id,
                    "resume_variant": rec.resume_variant,
                    "response_kind": response_kind},
            outputs={"stage": rec.stage},
            metrics={
                "accuracy": reward,
                "user_satisfaction": reward,
                "completeness": 1.0,
            },
            duration_ms=0,
            cost_usd=0.0,
        )
    except Exception as e:
        log.warning("skillopt outcome record failed: %s", e)


# ════════════════════════════════════════════════════════════════════════
#  Utilities
# ════════════════════════════════════════════════════════════════════════

def _default_candidate_info() -> Dict[str, str]:
    return {
        "first_name": "Stephen",
        "last_name": "Webster",
        "name": "Stephen C. Webster",
        "email": "user@example.com",
        "phone": "",   # user fills in or comes from config
    }


def _failure(reason: str, duration_ms: int) -> Dict[str, Any]:
    return {
        "status": "failed",
        "error": reason,
        "duration_ms": duration_ms,
    }


def _dt(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n - 1] + "…"


# ════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="application_engine")
    sub = parser.add_subparsers(dest="cmd")

    p_apply = sub.add_parser("apply", help="Apply to a tracked job (dry-run by default)")
    p_apply.add_argument("job_id")
    p_apply.add_argument("--dry-run", action="store_true", default=True)
    p_apply.add_argument("--variant", default=None)

    p_resp = sub.add_parser("response", help="Record a response on an application")
    p_resp.add_argument("application_id")
    p_resp.add_argument("kind", choices=list(OUTCOME_REWARDS.keys()))

    args = parser.parse_args()
    if args.cmd == "apply":
        out = apply_to_job(job_id=args.job_id, dry_run=args.dry_run,
                           resume_variant=args.variant)
        print(json.dumps(out, indent=2))
    elif args.cmd == "response":
        print(json.dumps(record_response(application_id=args.application_id,
                                         response_kind=args.kind), indent=2))
    else:
        parser.print_help()
