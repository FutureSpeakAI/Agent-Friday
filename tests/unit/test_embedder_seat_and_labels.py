"""The orphaned embedder seat, and the labels that over-promised.

`embeddinggemma:300m` was planned RESIDENT on a 12 GB card for a consumer that
does not exist: role_consumers classifies the embedding seat as DISPLAYS --
"Read, never obeyed" -- because EMBED_MODEL is a module constant pinned to
all-MiniLM-L6-v2 running in-process on CPU. The live server reported the seat
`calls: 0, last_used: null` while the arbiter reserved room for it.
"""

from agent_friday.services import residency_policy as rp


def test_embedder_is_not_planned_resident():
    """Nothing consumes this seat; it must not hold a card."""
    assert rp.ROLE_RESIDENCY["embedder"] == rp.ON_DEMAND, (
        "the embedder seat has no SELECTS consumer -- planning it resident "
        "reserves VRAM for nobody")


def test_embedder_is_still_assignable():
    """On-demand, not deleted: wiring a real consumer stays a one-line change."""
    assert "embedder" in rp.ROLE_RESIDENCY
    assert "embedder" in rp.CPU_CAPABLE_ROLES


def test_roles_that_are_actually_consumed_stay_resident():
    """The fix must not demote the conversational path."""
    for role in ("orchestrator", "sidekick", "interactive_brain"):
        assert rp.ROLE_RESIDENCY[role] == rp.RESIDENT


def test_embedder_residency_has_one_source_of_truth():
    """The CPU-demotion path used to hardcode 'resident' beside the table."""
    import inspect
    src = inspect.getsource(rp)
    assert 'DEFAULT_NUM_CTX["embedder"], "resident", 0' not in src, (
        "hardcoded residency beside ROLE_RESIDENCY is a second source of truth")


# ── label honesty ───────────────────────────────────────────────────────────

def test_mode_help_does_not_claim_to_cover_embeddings():
    from agent_friday.routes import intelligence

    note = intelligence._MODE_SCOPE_NOTE.lower()
    assert "embedding" in note
    assert "locally" in note or "local" in note
    assert "never leave" in note or "never leaves" in note


def test_cloud_only_label_says_what_it_does_not_govern():
    """'Cloud only' silently not covering embeddings is a promise problem."""
    import inspect
    from agent_friday.routes import intelligence

    src = inspect.getsource(intelligence)
    i = src.index('"id": "cloud_only"')
    blurb = src[i:i + 500].lower()
    assert "embedding" in blurb, (
        "the cloud_only help text must say embeddings stay local")
