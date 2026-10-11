"""Sharing the turn vector must not move a vault routing decision (critic B6).

The router's vault check (`needs_vault_access`) and the egress gate keep their
own sensitivity passes. They truncate differently from the shared encoder
(text[:512] characters against 256 tokens, first and last 128 for long text),
so a shared vector could clear a vault turn that the classifier holds, or the
other way. These tests pin that the privacy path neither reads nor depends on
`services/turn_vector`:

  * the decisions on a fixture set are the ones the base build reached (the
    EXPECTED table was produced from the commit before turn_vector existed);
  * they are the same with a deliberately wrong shared vector cached for every
    message, and with the shared-vector functions made to fail;
  * no privacy module mentions turn_vector at all.

Synthetic text only; Layer 3's model is a hashed bag-of-words stand-in.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services import laya2_encoder, turn_vector
from agent_friday.services import sensitivity_classifier as sc

DIM = 128


def _bow(text: str) -> np.ndarray:
    v = np.zeros(DIM, dtype="float32")
    for w in re.findall(r"[a-z]+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest()[:8], 16) % DIM] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


class FakeTorch:
    def encode(self, x, **kw):
        items = [x] if isinstance(x, str) else list(x)
        return np.stack([_bow(t) for t in items])


FILLER = ("the garden path needs fresh gravel and the tomato plants want water "
          "after sunrise while the river walk clears the head ") * 6      # > 512 chars, neutral
assert len(FILLER) > 512

#: (label, messages, ctx, vault routing decision on the base build)
FIXTURES = [
    ("keyword finance", [{"role": "user", "content": "show me my bank account balance"}], {}, True),
    ("plain weather", [{"role": "user", "content": "what is the weather like by the river"}], {}, False),
    ("plain garden", [{"role": "user", "content": "garden path gravel"}], {}, False),
    ("embedding only, exact", [{"role": "user", "content": "my brother is staying with me this month"}], {}, True),
    ("embedding only, near", [{"role": "user", "content": "my brother is staying with the garden river path"}], {}, True),
    ("long, keyword past 512 chars", [{"role": "user", "content": FILLER + " my medical diagnosis"}], {}, True),
    ("long, embedding-only tail past 512 chars",
     [{"role": "user", "content": FILLER + " my brother is staying with me this month"}], {}, False),
    ("long, embedding-only head diluted by filler",
     [{"role": "user", "content": "my brother is staying with me this month " + FILLER}], {}, False),
    ("vault word in history only",
     [{"role": "user", "content": "show me my bank account balance"},
      {"role": "assistant", "content": "here it is"},
      {"role": "user", "content": "thanks, what about the garden"}], {}, False),
    ("explicit vault flag", [{"role": "user", "content": "hello"}], {"vault_access": True}, True),
    ("vault tool in context", [{"role": "user", "content": "hello"}], {"tool_names": ["vault_read"]}, True),
    ("empty", [], {}, False),
]


@pytest.fixture
def privacy_env(monkeypatch):
    model = FakeTorch()
    monkeypatch.setattr(sc, "_EMBEDDER", model)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", np.stack([_bow(t) for t in sc._SENSITIVE_EXEMPLARS]))
    monkeypatch.setattr(sc, "_load_embedder", lambda: model)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True)
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    monkeypatch.setattr(eg, "_log", lambda *a, **k: None)
    from agent_friday.services import presidio_shadow
    monkeypatch.setattr(presidio_shadow, "observe", lambda *a, **k: None)
    turn_vector.reset()
    yield
    turn_vector.reset()


def _router():
    from agent_friday.routing.model_router import ModelRouter
    return ModelRouter()


def _decisions():
    r = _router()
    return {label: bool(r.needs_vault_access(msgs, ctx)) for label, msgs, ctx, _ in FIXTURES}


def test_vault_routing_matches_the_base_build(privacy_env):
    got = _decisions()
    want = {label: expected for label, _, _, expected in FIXTURES}
    assert got == want


def test_a_wrong_shared_vector_for_every_message_changes_no_decision(privacy_env, monkeypatch):
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    opposite = [-1.0 if i % 2 else 1.0 for i in range(384)]
    monkeypatch.setattr(laya2_encoder, "embed", lambda texts, max_len=256: [list(opposite) for _ in texts])
    for _, msgs, _, _ in FIXTURES:
        for m in msgs:
            turn_vector.compute(m.get("content", ""))
    assert turn_vector.stats()["cached"] > 0
    assert _decisions() == {label: exp for label, _, _, exp in FIXTURES}


def test_the_privacy_path_never_asks_for_the_shared_vector(privacy_env, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("the privacy path read the shared turn vector")

    monkeypatch.setattr(turn_vector, "peek", forbidden)
    monkeypatch.setattr(turn_vector, "compute", forbidden)
    assert _decisions() == {label: exp for label, _, _, exp in FIXTURES}
    for _, msgs, _, _ in FIXTURES:
        for m in msgs:
            eg._gate_text(m.get("content", ""), "anthropic", "f")
            eg._gate_text(m.get("content", "") + "\n\n" + FILLER[:80], "anthropic", "f")


def test_no_privacy_module_mentions_the_shared_vector():
    import agent_friday
    root = Path(agent_friday.__file__).parent
    guarded = [
        root / "services" / "sensitivity_classifier.py",
        root / "services" / "egress_gate.py",
        root / "services" / "egress_memo.py",
        root / "services" / "judgment_gate.py",
        root / "routing" / "model_router.py",
        root / "privacy" / "vault_access.py",
        root / "privacy" / "vault_policy.py",
    ]
    present = [p for p in guarded if p.exists()]     # not every 1.1 privacy module is on every build
    assert len(present) >= 2
    for p in present:
        assert "turn_vector" not in p.read_text(encoding="utf-8-sig"), p.name
