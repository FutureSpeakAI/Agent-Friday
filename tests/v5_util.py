"""Shared helpers for the Fable 5 v5 comprehensive suite.

Used by tests/security, tests/integration, tests/edge_cases, tests/regression
and the new tests/unit/*_v5.py files. Every helper repoints a service module's
SQLite storage into a per-test tmp_path so tests are hermetic and order-free.
"""
from __future__ import annotations


# ── Classifier helpers ────────────────────────────────────────────────────────

def fast_classify(text, default=1, **kwargs):
    """The real layered classifier with only the fast, deterministic layers
    (regex + keywords) enabled. Presidio/embeddings are skipped so tests never
    pay a model download or a multi-second lazy load."""
    from agent_friday.services.sensitivity_classifier import classify
    kwargs.setdefault("use_presidio", False)
    kwargs.setdefault("use_embeddings", False)
    return classify(text, default=default, **kwargs)


def patch_fast_gate(monkeypatch):
    """Swap the egress gate's classifier for the fast deterministic layers."""
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(
        eg, "_classify_impl",
        lambda text, default=1: fast_classify(text, default=default))
    return eg


# ── SQLite repointing ─────────────────────────────────────────────────────────

def repoint_economy(monkeypatch, tmp_path):
    from agent_friday.services import economy as m
    monkeypatch.setattr(m, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "economy.db")
    m._ensure_schema()
    return m


def repoint_defederation(monkeypatch, tmp_path):
    from agent_friday.services import defederation as m
    monkeypatch.setattr(m, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "defederation.db")
    m._ensure_schema()
    return m


def repoint_content_policies(monkeypatch, tmp_path):
    from agent_friday.services import content_policies as m
    monkeypatch.setattr(m, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "content_policies.db")
    m._ensure_schema()
    return m


def repoint_learning(monkeypatch, tmp_path):
    from agent_friday.services import learning_loop as m
    monkeypatch.setattr(m, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "learning.db")
    return m


def repoint_user_model(monkeypatch, tmp_path):
    from agent_friday.services import user_model as m
    monkeypatch.set