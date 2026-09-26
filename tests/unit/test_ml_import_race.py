"""The boot-time ML import race, reproduced, and what it may no longer cost.

Seen on the live server: Laya's import retry evicted "half-built" transformers
modules from sys.modules at the moment the privacy classifier was importing
them, the classifier's Layer 3 (the only layer that catches personal content
with no keyword in it) failed with KeyError 'transformers.utils.import_utils',
and that failure was cached for the life of the process. Cloud egress then
classified unsignalled text PUBLIC: fail open.

The race is reproduced here with a fake package that sits mid-import (named
under the "transformers" prefix, which Laya's purge matches) while the real
purge runs, against the classifier's real loader and Kokoro's real import
check. Fakes stand in for the heavy packages; the code under test is real.
"""
import importlib
import sys
import textwrap
import threading
import time

import pytest

from agent_friday.services import laya_backend
from agent_friday.services import sensitivity_classifier as sc

SLOW_PKG = "transformers_racefake"


def _write(root, rel, body):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")


@pytest.fixture
def fake_stack(tmp_path, monkeypatch):
    """A slow shared package, and fake sentence_transformers/kokoro on top."""
    _write(tmp_path, f"{SLOW_PKG}/__init__.py", """
        import sys
        import time
        time.sleep(0.8)          # a lazy namespace being built
        # transformers' _LazyModule looks itself up in sys.modules directly;
        # an evicted entry is the KeyError seen on the live server.
        _self = sys.modules[__name__]
        from . import sub
    """)
    _write(tmp_path, f"{SLOW_PKG}/sub.py", "READY = True\n")
    _write(tmp_path, "sentence_transformers/__init__.py", f"""
        import {SLOW_PKG}
        import numpy as _np
        class SentenceTransformer:
            def __init__(self, name):
                self.name = name
            def encode(self, texts, normalize_embeddings=True):
                v = _np.ones((len(texts), 4)) / 2.0
                return v
    """)
    _write(tmp_path, "kokoro/__init__.py", f"""
        import {SLOW_PKG}
        class KPipeline:
            pass
    """)
    saved = {k: sys.modules.pop(k) for k in list(sys.modules)
             if k.split(".")[0] in ("sentence_transformers", "kokoro", SLOW_PKG)}
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    from agent_friday.services import embedder_cache
    monkeypatch.setattr(embedder_cache, "ensure_available", lambda *a, **k: None)
    yield tmp_path
    for k in [k for k in list(sys.modules)
              if k.split(".")[0] in ("sentence_transformers", "kokoro", SLOW_PKG)]:
        sys.modules.pop(k, None)
    sys.modules.update(saved)


def _purge_during(target, delay=0.25):
    """Run `target` on a thread; Laya's retry purge fires while it imports."""
    out = {}

    def run():
        try:
            out["result"] = target()
        except BaseException as e:  # noqa: BLE001
            out["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(delay)
    laya_backend._purge_partial_imports()      # exactly what Laya's retry calls
    t.join(10)
    assert not t.is_alive(), "the import never finished"
    return out


def test_laya_purge_cannot_break_the_privacy_classifier(fake_stack, monkeypatch):
    monkeypatch.setattr(sc, "_EMBEDDER", sc._UNTRIED)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    out = _purge_during(sc._load_embedder)
    assert "error" not in out, out.get("error")
    assert out["result"] is not None, "Layer 3 failed to load: the purge raced the import"
    assert sys.modules[SLOW_PKG].sub.READY


def test_laya_purge_cannot_break_kokoro(fake_stack, monkeypatch):
    from agent_friday.services import kokoro_voice as kv
    monkeypatch.setattr(kv, "_import_check", None)
    out = _purge_during(lambda: kv.kokoro_import_status(refresh=True))
    assert "error" not in out, out.get("error")
    assert out["result"]["ok"], out["result"]


def test_a_failed_layer3_load_is_retried_not_cached_forever(monkeypatch):
    calls = {"n": 0}

    class Fake:
        def encode(self, texts, normalize_embeddings=True):
            import numpy as np
            return np.ones((len(texts), 4)) / 2.0

    def flaky(name):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ImportError("cannot import name 'AlbertModel' from 'transformers'")
        return Fake()

    import types as _t
    fake_mod = _t.ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = flaky
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)
    from agent_friday.services import embedder_cache
    monkeypatch.setattr(embedder_cache, "ensure_available", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_EMBEDDER", sc._UNTRIED)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_EMBEDDER_RETRY_S", 0.0, raising=False)
    assert sc._load_embedder() is None          # the boot-time blip
    assert sc._load_embedder() is not None      # tried again, and it works
    assert calls["n"] == 2


class _Unready:
    """Layer 3 expected (installed) but not loaded."""


def test_cloud_egress_fails_closed_while_layer3_is_expected_but_down(monkeypatch):
    from agent_friday.services import egress_gate as eg
    text = "she stays with me every other weekend and her school is near the park"
    monkeypatch.setattr(sc, "_load_embedder", lambda: None)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True, raising=False)
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    assert eg._classify_cloud(text) >= sc.Tier.PRIVATE
    # Routing decisions (not egress) are unchanged.
    assert sc.classify(text) == sc.Tier.PUBLIC


def test_a_layer_absent_by_design_is_reported_not_silently_assumed(monkeypatch):
    from agent_friday.services import egress_gate as eg
    from agent_friday.services import privacy_layers as pl
    monkeypatch.setattr(sc, "_load_embedder", lambda: None)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: False, raising=False)
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    assert eg._classify_cloud("she stays with me every other weekend") == sc.Tier.PUBLIC
    monkeypatch.setattr(pl, "_module_available", lambda m: False)
    emb = pl.probe_layers()["embedding"]
    assert emb["active"] is False and "NOT INSTALLED" in emb["reason"]


def test_health_reports_layer3_as_it_is_at_runtime(monkeypatch):
    from agent_friday.services import privacy_layers as pl
    monkeypatch.setattr(pl, "_module_available", lambda m: True)
    monkeypatch.setattr(sc, "_EMBEDDER", None)
    emb = pl.probe_layers()["embedding"]
    assert emb["active"] is False, "installed is not the same as running"
    assert "failed" in emb["reason"] or "retry" in emb["reason"]
    monkeypatch.setattr(sc, "_EMBEDDER", object())
    assert pl.probe_layers()["embedding"]["active"] is True


def test_boot_preloads_the_ml_stack_before_the_warm_ups():
    import inspect
    from agent_friday import server
    src = inspect.getsource(server)
    assert "ml_imports" in src and "preload" in src
