"""
application_engine — full-cycle job application skill for Agent Friday.

Public API:
    apply_to_job(...)        run the pipeline against a tracked job
    record_response(...)     close the loop on an outcome
    detect_ats(...)          ATS platform detection
    load_config(...)         load merged config
    VariantBandit            resume A/B selector
"""
from .engine import (
    apply_to_job,
    record_response,
    detect_ats,
    load_config,
    evaluate_quality_gates,
    VariantBandit,
    OUTCOME_REWARDS,
)

__all__ = [
    "apply_to_job",
    "record_response",
    "detect_ats",
    "load_config",
    "evaluate_quality_gates",
    "VariantBandit",
    "OUTCOME_REWARDS",
]
