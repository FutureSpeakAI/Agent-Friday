"""Tests read models from the machine's one Hugging Face cache, offline, and never download one.

The test home replaces USERPROFILE/HOME, which used to move the Hugging Face
cache into an empty throwaway folder: the first test needing embeddings then
pulled the whole model repository (every format, about a gigabyte) into it, once
per pytest process. The suite now points HF_HOME at the real shared cache,
offline, and a test that reaches for a Hugging Face host fails.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest


def test_the_cache_is_the_shared_one_not_the_test_home():
    hf_home = os.environ.get("HF_HOME", "")
    assert hf_home, "HF_HOME is not set for the suite"
    test_home = os.environ["USERPROFILE"]
    assert not Path(hf_home).resolve().is_relative_to(Path(test_home).resolve()), \
        "the Hugging Face cache sits inside the throwaway test home"


def test_the_suite_runs_hugging_face_offline():
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"


def test_reaching_for_a_hugging_face_host_fails_the_test():
    with pytest.raises(Exception) as caught:
        socket.getaddrinfo("huggingface.co", 443)
    assert "network model download" in str(caught.value).lower()


def test_other_hosts_still_resolve_as_before():
    # The guard is aimed at Hugging Face only; loopback keeps working.
    assert socket.getaddrinfo("127.0.0.1", 80)
