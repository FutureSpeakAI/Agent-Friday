"""
ofw_monitor — daily Our Family Wizard monitoring skill.

Public API:
    scan(...)            run one monitoring cycle
    load_config(...)     load merged config
    classify_sentiment   local lexicon-based sentiment
    redact               privacy filter
    HMACChainArchive     tamper-evident archive

Session abstraction is pluggable — see `Session` for the interface
that server.py implements with the Claude-in-Chrome MCP.
"""
from .monitor import (
    scan,
    load_config,
    classify_sentiment,
    redact,
    Session,
    LocalSession,
    HMACChainArchive,
    OFWMessage,
    OFWCalendarEvent,
    OFWExpense,
    ScanResult,
)

__all__ = [
    "scan",
    "load_config",
    "classify_sentiment",
    "redact",
    "Session",
    "LocalSession",
    "HMACChainArchive",
    "OFWMessage",
    "OFWCalendarEvent",
    "OFWExpense",
    "ScanResult",
]
