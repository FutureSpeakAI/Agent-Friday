"""
job_scanner — autonomous LinkedIn discovery skill for Agent Friday.

Public API:
    scan(...)                  one-shot scan cycle
    load_config(...)           load merged config
    build_linkedin_url(...)    URL constructor
    score_listing(...)         scoring function
    in_active_hours(...)       schedule guard
"""
from .scanner import (
    scan,
    load_config,
    build_linkedin_url,
    score_listing,
    dedup_key,
    in_active_hours,
    KeywordRotator,
    normalize_raw,
)

__all__ = [
    "scan",
    "load_config",
    "build_linkedin_url",
    "score_listing",
    "dedup_key",
    "in_active_hours",
    "KeywordRotator",
    "normalize_raw",
]
