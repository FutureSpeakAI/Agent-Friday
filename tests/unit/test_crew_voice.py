"""A shared speaking floor is governed by rendered playback, not model completion."""
from types import SimpleNamespace

import pytest

from agent_friday.services import crew_voice as cv
from agent_friday.services import voice_live_channel as channels


def profile():
    return {"id": "researcher", "revision": 1, "status": "active", "name": "Researcher",
            "caption": {"label": "Researcher"},
            "voice": {"provider": "elevenlabs", "model": "eleven_flash_v2_5", "voice_id": "voiceA"}}


class Room:
    def __init__(self, synthesize=None):
        self.profile = profile()
        self.snapshot = {"members": [self.profile], "project_id": "project-a"}
        self.frames, self.jobs, self.spoken, self.receipts = [], [], [], []
        self.now = 100.0
        self.floor = cv.CrewVoiceSession(
            lambda f: self.frames.append(f) or True, "conversation-a",
            room=lambda cid: self.snapshot, synthesize=synthesize or self.synth,
            spoken=self.spoken.append, receipt=self.receipts.append,
            clock=lambda: self.now, session_id="call-a",
            launch=lambda fn, item: self.jobs.append((fn, item)))
        self.floor.start()

    @staticmethod
    def synth(p, text, task):
        return SimpleNamespace(audio=b"test-audio", mime="audio/mpeg", provider="elevenlabs",
                               model="eleven_flash_v2_5", voice_id=p["voice"]["voice_id"])

    def produce(self):
        fn, item = self.jobs.pop(0)
        fn(item)

    def starts(self):
        return [f for f in self.frames if f["type"] == "crew_speech_start"]

    def ack(self, start, status, samples=0):
        return self.floor.acknowledge({
            "session_id": start["session_id"], "epoch": start["epoch"],
            "utterance_id": start["utterance_id"], "status": status,
            "played_samples": samples, "total_samples": samples})


def test_host_generation_end_does_not_give_specialist_the_audible_floor():
    r = Room()
    assert r.floor.host_audio(b"\x00\x01" * 240)
    host = r.starts()[0]
    assert r.floor.deliver(r.profile, "A checked result.", task_id="task-a")
    assert not r.jobs
    assert r.floor.host_end()
    r.floor.tick()
    assert not r.jobs, "server generation end is not playback completion"
    assert not r.ack(host, "finished", 239), "incomplete PCM cannot acknowledge its end"
    assert r.ack(host, "finished", 240)
    assert len(r.jobs) == 1
    r.produce()
    assert [f["speaker_id"] for f in r.starts()] == ["friday", "researcher"]


def test_underrun_or_premature_finished_ack_cannot_release_streaming_host():
    r = Room()
    r.floor.host_audio(b"\x00\x01" * 240)
    host = r.starts()[0]
    assert r.ack(host, "started", 0)
    assert not r.ack(host, "finished", 240)
    assert r.floor.busy
    assert not r.ack({**host, "session_id": "other-call"}, "progress", 10)
    assert not r.ack(host, "progress", 241)


def test_native_response_and_tools_are_suppressed_during_specialist_ownership():
    r = Room()
    assert r.floor.deliver(r.profile, "My result.")
    assert not r.floor.host_allowed()
    assert not r.floor.host_begin()
    assert not r.floor.host_audio(b"\0\0" * 240)
    assert not r.floor.host_end()
    r.produce()
    assert [f["speaker_id"] for f in r.starts()] == ["researcher"]


def test_barge_discards_inflight_synthesis_and_waiting_reports():
    r = Room()

    def interrupted_synth(p, text, task):
        r.floor.interrupt()
        return r.synth(p, text, task)

    r.floor._synthesize = interrupted_synth
    r.floor.deliver(r.profile, "First.", task_id="task-a")
    r.floor.deliver(r.profile, "Second.", task_id="task-b")
    r.produce()
    assert not r.starts()
    assert not r.jobs
    assert r.floor.epoch == 1
    assert r.frames[-1]["type"] == "crew_interrupted"


def test_barged_native_chunks_keep_their_old_generation_until_boundary():
    r = Room()
    r.floor.host_audio(b"\0\0" * 240)
    r.floor.interrupt()
    assert not r.floor.host_audio(b"\0\0" * 240)
    assert not r.floor.host_begin(), "late output must not borrow the new epoch"
    assert not r.floor.host_end()
    assert r.floor.host_audio(b"\0\0" * 240)
    assert [f["epoch"] for f in r.starts()] == [0, 1]


def test_queued_native_tool_cannot_borrow_new_epoch_after_old_turn_boundary():
    r = Room()
    old_epoch = r.floor.epoch
    assert r.floor.host_begin(expected_epoch=old_epoch)
    r.floor.interrupt()
    r.floor.host_end()
    assert not r.floor.host_begin(expected_epoch=old_epoch)
    assert r.floor.host_begin(expected_epoch=r.floor.epoch)


def test_failed_browser_playback_suppresses_remaining_native_generation():
    r = Room()
    r.floor.host_audio(b"\0\0" * 240)
    assert r.ack(r.starts()[0], "failed", 0)
    assert not r.floor.host_audio(b"\0\0" * 240)
    assert not r.floor.host_end()
    assert r.floor.host_begin()


def test_profile_change_during_synthesis_prevents_old_voice_leaving():
    r = Room()

    def change_profile(p, text, task):
        r.profile["revision"] = 2
        return r.synth(p, text, task)

    r.floor._synthesize = change_profile
    r.floor.deliver(r.profile, "Old request.")
    r.produce()
    assert not r.starts()
    assert any(f["type"] == "crew_speech_error" for f in r.frames)
    assert not r.floor.busy


def test_removed_membership_refuses_delivery_and_no_network_job_is_created():
    r = Room()
    r.snapshot["members"] = []
    assert not r.floor.deliver(r.profile, "Not a room member.")
    assert not r.jobs


def test_project_change_during_synthesis_prevents_audio_crossing_room_scope():
    r = Room()

    def changed_room(p, text, task):
        r.snapshot["project_id"] = "project-b"
        return r.synth(p, text, task)

    r.floor._synthesize = changed_room
    r.floor.deliver(r.profile, "Project A result.")
    r.produce()
    assert not r.starts()
    assert not r.floor.busy


def test_pending_report_is_discarded_when_room_project_changes():
    r = Room()
    r.floor.note_user()
    r.floor.deliver(r.profile, "Project A result.")
    r.snapshot["project_id"] = "project-b"
    r.now += 1
    r.floor.tick()
    assert not r.jobs


def test_synthesis_failure_releases_floor_and_reports_attributed_failure():
    def broken(*args):
        raise RuntimeError("private implementation detail")

    r = Room(broken)
    r.floor.deliver(r.profile, "Result.", task_id="task-a")
    r.produce()
    failure = next(f for f in r.frames if f["type"] == "crew_speech_error")
    assert failure["speaker_id"] == "researcher" and failure["task_id"] == "task-a"
    assert "private implementation" not in failure["message"]
    assert not r.floor.busy
    assert r.floor.host_begin()


def test_playback_timeout_releases_floor_and_late_ack_cannot_affect_new_voice():
    r = Room()
    r.floor.deliver(r.profile, "Result.")
    r.produce()
    old = r.starts()[0]
    r.now += cv.ACK_TIMEOUT + 1
    r.floor.tick()
    assert not r.floor.busy
    assert r.floor.host_audio(b"\0\0" * 240)
    assert not r.ack(old, "finished", 240)
    assert r.floor.busy


def test_only_finished_specialist_playback_enters_host_context():
    r = Room()
    r.floor.deliver(r.profile, "Report.", task_id="task-a")
    r.produce()
    start = r.starts()[0]
    assert not r.spoken
    assert r.ack(start, "started", 0)
    assert r.ack(start, "progress", 120)
    assert not r.ack(start, "progress", 119)
    assert not r.spoken
    assert r.ack(start, "finished", 240)
    assert r.spoken[0]["text"] == "Report."
    assert r.receipts[0]["shared_with"] == {"researcher": 1}
    assert r.receipts[0]["project_id"] == "project-a"


def test_encoded_finish_requires_decoded_sample_count_and_off_record_is_latched():
    r = Room()
    private = [True]
    r.floor._off_record = lambda: private[0]
    r.floor.deliver(r.profile, "Report.")
    r.produce()
    private[0] = False
    start = r.starts()[0]
    receipt = {"session_id": start["session_id"], "epoch": start["epoch"],
               "utterance_id": start["utterance_id"], "status": "finished", "played_samples": 240}
    assert not r.floor.acknowledge(receipt)
    assert not r.floor.acknowledge({**receipt, "total_samples": 241})
    assert r.floor.acknowledge({**receipt, "total_samples": 240})
    assert r.receipts[0]["off_record"] is True
    assert r.receipts[0]["provider"] == "elevenlabs"


def test_host_caption_deltas_are_bounded_to_the_advertised_text_limit():
    r = Room()
    r.floor.host_text("x" * (cv.MAX_TEXT + 1))
    r.floor.host_text("overflow")
    assert sum(len(f["text"]) for f in r.frames if f["type"] == "crew_speech_text") == cv.MAX_TEXT


def test_speech_limits_report_written_only_outcomes_instead_of_silence():
    r = Room()
    assert not r.floor.deliver(r.profile, "x" * (cv.MAX_TEXT + 1), task_id="long-reply")
    assert r.frames[-1]["code"] == "crew_text_limit"
    assert r.frames[-1]["task_id"] == "long-reply"
    r.floor.note_user()
    for _ in range(cv.MAX_PENDING):
        assert r.floor.deliver(r.profile, "Queued reply")
    assert not r.floor.deliver(r.profile, "Overflow")
    assert r.frames[-1]["code"] == "crew_queue_full"
    assert not r.jobs


def test_user_activity_holds_new_speech_and_retarget_invalidates_prepared_output():
    r = Room()
    r.floor.note_user()
    r.floor.deliver(r.profile, "Wait for the user.")
    assert not r.jobs
    r.now += 1
    r.floor.tick()
    assert len(r.jobs) == 1
    r.floor.retarget("conversation-b")
    r.produce()
    assert not r.starts()
    assert r.frames[-1]["conversation_id"] == "conversation-b"


@pytest.mark.parametrize("encoding,data", [("text/plain", b"no"), ("audio/mpeg", b""),
                                          ("audio/mpeg", b"x" * (cv.MAX_AUDIO_BYTES + 1))],
                         ids=["unsupported-format", "empty-audio", "oversized-audio"])
def test_invalid_audio_is_not_forwarded(encoding, data):
    r = Room(lambda p, text, task: SimpleNamespace(mime=encoding, audio=data))
    r.floor.deliver(r.profile, "Report.")
    r.produce()
    assert not any(f["type"] == "crew_audio" for f in r.frames)
    assert not r.floor.busy


def test_explicit_cloud_binding_uses_exact_model_and_voice_without_mutating_settings(monkeypatch):
    from agent_friday.services import cloud_voice
    settings = {"elevenlabs_model": "eleven_multilingual_v2", "elevenlabs_voice_id": "defaultVoice"}
    sent = []
    monkeypatch.setattr(cloud_voice, "ga_established", lambda p: True)
    monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "synthetic")
    monkeypatch.setattr(cloud_voice, "key_problem", lambda p, key: None)
    monkeypatch.setattr(cloud_voice, "gate_synthesis_input", lambda t, p: t)
    monkeypatch.setattr(cloud_voice, "_meter", lambda *a: 0)
    monkeypatch.setattr(cloud_voice, "_synth_elevenlabs",
                        lambda t, k, m, v: sent.append((m, v)) or (b"audio", "audio/mpeg"))
    result = cloud_voice.synthesize("Hi.", provider="elevenlabs", settings=settings,
                                    model="eleven_flash_v2_5", voice_id="agentVoice")
    assert sent == [("eleven_flash_v2_5", "agentVoice")]
    assert result.voice_id == "agentVoice"
    assert settings["elevenlabs_voice_id"] == "defaultVoice"
    with pytest.raises(cloud_voice.CloudVoiceUnavailable):
        cloud_voice.synthesize("Hi.", provider="elevenlabs", settings=settings,
                               model="nonexistent", voice_id="agentVoice")
    with pytest.raises(cloud_voice.CloudVoiceUnavailable):
        cloud_voice.synthesize("Hi.", provider="elevenlabs", settings=settings,
                               model="eleven_flash_v2_5", voice_id="../another-endpoint")
    assert len(sent) == 1


def test_structured_delivery_does_not_fall_back_to_friday_or_another_conversation():
    plain, structured = [], []
    callback = lambda p, t, **kw: structured.append((p, t, kw)) or True
    channels.register("crew-test-a", lambda *args: plain.append(args))
    channels.register_crew("crew-test-a", callback)
    try:
        assert channels.deliver_crew("crew-test-a", profile(), "Report.", task_id="task-a")
        assert structured[0][2] == {"task_id": "task-a"}
        assert not channels.deliver_crew("crew-test-b", profile(), "Report.")
        assert not plain
        channels.unregister_crew("crew-test-a", lambda *a: None)
        assert channels.deliver_crew("crew-test-a", profile(), "Still registered.")
        channels.unregister_crew("crew-test-a", callback)
        assert not channels.deliver_crew("crew-test-a", profile(), "Written only.")
        assert channels.deliver("crew-test-a", "Ordinary report.")
        assert plain == [("Ordinary report.", "result")]
    finally:
        channels.unregister("crew-test-a")
        channels.unregister_crew("crew-test-a")
