"""Concurrent partial tracking saves retain both successful changes."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_friday import core


def test_simultaneous_tracking_saves_preserve_both_dials(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(core, "SETTINGS_FILE", path)
    core._invalidate_settings_cache()
    core._save_settings({"tracking": {"parallax_strength": .7, "hand_gain": 3.4}})
    first_read, release_first, second_ready = threading.Event(), threading.Event(), threading.Event()
    hand_captured = threading.Event()
    read_text = Path.read_text

    class ObservedWriterLock:
        def __init__(self):
            self.lock = threading.RLock()

        def __enter__(self):
            if threading.current_thread().name == "hand-dial":
                second_ready.set()
            self.lock.acquire()

        def __exit__(self, *_args):
            self.lock.release()

    def held_read(current_path, *args, **kwargs):
        result = read_text(current_path, *args, **kwargs)
        if current_path == path and threading.current_thread().name == "head-dial":
            first_read.set()
            assert release_first.wait(5), "The test must release the held settings read"
        if current_path == path and threading.current_thread().name == "hand-dial":
            hand_captured.set()
            second_ready.set()
        return result

    def save_head():
        threading.current_thread().name = "head-dial"
        core._save_settings({"tracking": {"parallax_strength": .3}})

    def save_hand():
        threading.current_thread().name = "hand-dial"
        core._save_settings({"tracking": {"hand_gain": 4.2}})

    monkeypatch.setattr(Path, "read_text", held_read)
    monkeypatch.setattr(core, "_SETTINGS_WRITE_LOCK", ObservedWriterLock(), raising=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save_head)
        try:
            assert first_read.wait(5)
            second = pool.submit(save_hand)
            assert second_ready.wait(5)
            if hand_captured.is_set():
                # An unlocked writer commits its old snapshot before the held
                # first writer resumes. A locked writer cannot read it yet.
                second.result(timeout=5)
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    saved = json.loads(path.read_text(encoding="utf-8"))["tracking"]
    assert saved == {"parallax_strength": .3, "hand_gain": 4.2}
    assert core._load_settings_raw()["tracking"] == saved
    core._invalidate_settings_cache()
