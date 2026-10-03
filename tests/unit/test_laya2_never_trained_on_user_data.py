"""The shipped tier-1 head is made of synthetic seeds and a pinned encoder.

Nothing a user typed can be in it, because there is nothing it could be in:
the heads are nearest-prototype over `services/laya2_seeds`, the manifest
names that one synthetic seed set with a content hash the build reproduces,
and the encoder is the published artifact, pinned by sha256. The encoder
runtime imports nothing that could reach the network.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re

from agent_friday.services import laya2_encoder, laya2_seeds

PUBLISHED_ENCODER_SHA256 = "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452"


def test_the_manifest_lists_only_the_synthetic_seed_set():
    m = laya2_seeds.manifest()
    assert m["trained"] is False
    assert m["head"] == "nearest-prototype"
    assert m["datasets"] == [{"id": laya2_seeds.SEED_SET_ID, "kind": "synthetic",
                              "content_hash": laya2_seeds.content_hash()}]
    assert laya2_seeds.SEED_SET_ID == "laya2-seeds-v1"


def test_the_content_hash_reproduces_from_the_seeds():
    canonical = json.dumps({"id": laya2_seeds.SEED_SET_ID,
                            "shapes": laya2_seeds.SHAPE_SEEDS,
                            "mutations": laya2_seeds.MUTATION_SEEDS},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert laya2_seeds.content_hash() == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert laya2_seeds.content_hash() == laya2_seeds.content_hash()
    assert laya2_seeds.manifest()["datasets"][0]["content_hash"] == laya2_seeds.content_hash()


def test_the_encoder_is_the_published_artifact():
    assert laya2_encoder.MODEL_SHA256 == PUBLISHED_ENCODER_SHA256
    m = laya2_seeds.manifest()["encoder"]
    assert m["sha256"] == PUBLISHED_ENCODER_SHA256
    assert m["model_id"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert m["licence"] == "Apache-2.0"


def test_every_class_has_seeds_and_none_names_anyone():
    assert set(laya2_seeds.SHAPE_SEEDS) == set(laya2_seeds.SHAPES)
    assert set(laya2_seeds.MUTATION_SEEDS) == set(laya2_seeds.MUTATIONS)
    phone_like = re.compile(r"\d{5,}")
    for seeds in (laya2_seeds.SHAPE_SEEDS, laya2_seeds.MUTATION_SEEDS):
        for label, utterances in seeds.items():
            assert len(utterances) >= 10, label
            for u in utterances:
                assert "@" not in u and "http" not in u.lower(), u
                assert not phone_like.search(u), u


def test_the_encoder_runtime_cannot_reach_the_network():
    src = inspect.getsource(laya2_encoder)
    for name in ("requests", "urllib", "huggingface_hub", "socket", "http.client"):
        assert not re.search(r"^\s*(import|from)\s+%s\b" % re.escape(name), src, re.M), name
    assert 'os.environ.setdefault("HF_HUB_OFFLINE", "1")' in src
    assert "disable_telemetry_events" in src


def test_a_wrong_hash_refuses_to_load(monkeypatch, tmp_path):
    d = tmp_path / "models" / laya2_encoder.ENCODER_DIRNAME
    d.mkdir(parents=True)
    (d / laya2_encoder.MODEL_NAME).write_bytes(b"not the model")
    (d / laya2_encoder.TOKENIZER_NAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(laya2_encoder, "artifacts_dir", lambda: d)
    laya2_encoder.reset()
    try:
        assert laya2_encoder.is_available() is True
        assert laya2_encoder.load() is False
        st = laya2_encoder.status()
        assert st["state"] == "missing" and "sha256" in st["detail"]
    finally:
        laya2_encoder.reset()
