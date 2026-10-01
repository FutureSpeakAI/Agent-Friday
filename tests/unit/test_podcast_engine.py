"""Podcasts: written by the local model, every line accountable, spoken and
checked on this computer, private material kept private.

docs/design/active/local-podcasts.md is the design these tests hold to.
"""
from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_render as render


#: A fictional number (the 555 exchange), used to prove it is redacted.
FAKE_PHONE = "555-867-5309"  # pragma: allowlist secret


# ── helpers ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _podcast_home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    yield tmp_path


def _fake_writer(script_for_chapter=None, outline=None):
    """A stand-in for the LOCAL model call, recording every prompt."""
    calls = []

    def llm(system, user, *, max_tokens=3000):
        calls.append({"system": system, "user": user})
        if "PROBLEMS FOUND" in user:
            # A revision pass: this stand-in cannot improve the script.
            body = user.split("sign-off are added around it):\n", 1)[1].split("\n\nPROBLEMS FOUND", 1)[0]
            return {"lines": json.loads(body)}, "bonsai2:27b"
        if '"chapters"' in user and "Plan an episode" in user:
            return (outline or {"title": "Test episode", "chapters": [
                {"title": "Open", "sources": ["S1"]},
                {"title": "Middle", "sources": ["S1", "S2"]},
                {"title": "Close", "sources": ["S2"]}]}, "bonsai2:27b")
        n = sum(1 for c in calls if "Chapter " in c["user"])
        # A different passage per chapter: a real writer does not repeat a
        # chapter, and the stitch drops a line that repeats an earlier one.
        lines = (script_for_chapter(n) if script_for_chapter else {
            1: [{"speaker": "a", "text": "Good evening, this is the show.", "cites": []},
                {"speaker": "b", "text": "The council voted 7 to 2 on Tuesday.", "cites": ["S1"]},
                {"speaker": "a", "text": "And the budget rose by 12 percent.", "cites": ["S2"]}],
            2: [{"speaker": "a", "text": "Here is what happened next.", "cites": []},
                {"speaker": "b", "text": "Councillors argued for an hour before the 7 to 2 result.", "cites": ["S1"]},
                {"speaker": "a", "text": "Roads take most of the 12 percent rise.", "cites": ["S2"]}],
        }.get(n, [{"speaker": "a", "text": "That is the shape of the week.", "cites": []},
                  {"speaker": "b", "text": "Watch the final vote on the 12 percent increase.", "cites": ["S2"]},
                  {"speaker": "a", "text": "We will be back.", "cites": []}]))
        return {"lines": lines}, "bonsai2:27b"
    llm.calls = calls
    return llm


def _fake_speaker(monkeypatch):
    """Speech as a short tone per word, so timings are real sample counts."""
    import numpy as np
    spoken = []

    class S:
        def speak(self, text, voice):
            spoken.append((voice, text))
            n = int(0.05 * render.RATE * max(1, len(text.split())))
            t = np.arange(n) / render.RATE
            return (0.1 * np.sin(2 * math.pi * 220 * t)).astype("float32")
    monkeypatch.setattr(render, "speaker", lambda: S())
    monkeypatch.setattr(render, "encode_mp3", lambda *a, **k: False)
    return spoken


def _text_ep(**kw):
    return pe.create([{"kind": "text", "text": "The council voted 7 to 2. The budget rose 12 percent.",
                       "title": "Council notes"},
                      {"kind": "text", "text": "Budget up 12 percent.", "title": "Budget"}], **kw)


# ── validation ──────────────────────────────────────────────────────────────

def test_a_line_citing_a_source_that_does_not_exist_is_cut():
    kept, cut = pe.clean_lines([{"speaker": "a", "text": "Claim.", "cites": ["S9"]}], {"S1"})
    assert kept == [] and "do not exist" in cut[0]["reason"]


def test_a_number_without_a_source_is_cut_and_recorded():
    kept, cut = pe.clean_lines([{"speaker": "b", "text": "Turnout was 64 percent.", "cites": []},
                                {"speaker": "a", "text": "Welcome back.", "cites": []}], {"S1"})
    assert [k["text"] for k in kept] == ["Welcome back."]
    assert cut[0]["reason"] == "states a number without a source"


def test_markdown_host_names_and_stage_directions_are_not_spoken():
    kept, _ = pe.clean_lines([{"speaker": "A", "text": "Friday: **Big** news [laughs] (pause) today.",
                               "cites": ["S1"]}], {"S1"})
    assert kept[0]["text"] == "Big news today."
    assert kept[0]["speaker"] == "a"


def test_long_lines_are_split_at_sentences_for_captions():
    text = " ".join(["This sentence is here to be long enough."] * 20)
    kept, _ = pe.clean_lines([{"speaker": "a", "text": text, "cites": ["S1"]}], {"S1"})
    assert len(kept) > 1 and all(len(k["text"]) <= pe.MAX_LINE_CHARS for k in kept)


# ── writing is local-only ───────────────────────────────────────────────────

def test_no_local_model_means_no_script_and_no_cloud_call(monkeypatch):
    from agent_friday.services import local_call
    monkeypatch.setattr(local_call, "call_json",
                        lambda *a, **k: pytest.fail("no model may be called"))
    with pytest.raises(render.RenderError) as exc:
        pe._llm_json("s", "u")
    assert exc.value.code == "no_local_model"
    assert "not sent to the cloud" in str(exc.value)


def test_the_writer_calls_the_local_seat_inside_the_local_only_guard(monkeypatch):
    from agent_friday.services import local_call, local_only_guard, scheduler
    seen = {}

    def call_json(system, user, model, **kw):
        seen["model"] = model
        seen["guarded"] = local_only_guard.is_active()
        return {"ok": True}
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: "bonsai2:27b")
    monkeypatch.setattr(local_call, "call_json", call_json)
    out, model = pe._llm_json("s", "u")
    assert out == {"ok": True} and model == "bonsai2:27b"
    assert seen == {"model": "bonsai2:27b", "guarded": True}


def test_news_episodes_carry_the_evidence_rules():
    from agent_friday.services.voice_persona import VOICE_ANCHOR_RULES
    ep = {"show": "The Front Page", "hosts": pe.DEFAULTS["hosts"],
          "attached": {"routine": "front_page", "run_id": "2026-09-29-morning"}}
    assert VOICE_ANCHOR_RULES in pe._system_prompt(ep)
    other = dict(ep, attached=None)
    assert VOICE_ANCHOR_RULES not in pe._system_prompt(other)


def test_the_script_is_planned_then_written_chapter_by_chapter(monkeypatch):
    llm = _fake_writer()
    monkeypatch.setattr(pe, "_llm_json", llm)
    docs = [{"sid": "S1", "title": "Council", "text": "7 to 2", "url": "", "kind": "text"},
            {"sid": "S2", "title": "Budget", "text": "12 percent", "url": "", "kind": "text"}]
    ep = {"id": "x", "length": "short", "show": "Show", "hosts": pe.DEFAULTS["hosts"]}
    script = pe.write_script(ep, docs)
    assert [c["title"] for c in script["chapters"]] == ["Open", "Middle", "Close"]
    assert {ln["chapter"] for ln in script["lines"]} == {0, 1, 2}
    # Every chapter after the first sees the conversation so far.
    chapter_prompts = [c["user"] for c in llm.calls if "Chapter " in c["user"]]
    assert "(nothing yet)" in chapter_prompts[0]
    assert "The council voted 7 to 2" in chapter_prompts[1]
    # Chapter 3 is only given the sources its outline named.
    assert "[S1]" not in chapter_prompts[2] and "[S2]" in chapter_prompts[2]


# ── producing: written, spoken, checked, signed ─────────────────────────────

def test_an_episode_is_produced_with_timed_lines_captions_and_a_check(monkeypatch, _podcast_home):
    monkeypatch.setattr(pe, "_llm_json", _fake_writer())
    spoken = _fake_speaker(monkeypatch)
    heard = {}
    real = render.listen_back

    def listen_back(wav, script_text, transcribe=None):
        heard["script"] = script_text
        return real(wav, script_text, transcribe=lambda p: script_text)
    monkeypatch.setattr(render, "listen_back", listen_back)
    signed = []
    from agent_friday.services import provenance
    monkeypatch.setattr(provenance, "write", lambda path, **kw: signed.append((Path(path), kw)) or
                        {"artifact": {"content_hash": "h"}, "signature": "sig"})

    ep = _text_ep(length="short")
    assert ep["status"] == "queued" and ep["privacy"] == "private"
    done = pe.produce(ep["id"])
    assert done["status"] == "ready", done.get("error")
    d = _podcast_home / "podcasts" / ep["id"]
    assert (d / "audio.wav").is_file() and done["audio"] == "audio.wav"
    starts = [ln["start"] for ln in done["lines"]]
    assert starts == sorted(starts) and all(ln["end"] > ln["start"] for ln in done["lines"])
    assert [c["title"] for c in done["chapters"]] == ["Open", "Middle", "Close"]
    assert done["chapters"][1]["start"] > done["chapters"][0]["start"]
    vtt = (d / "captions.vtt").read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT") and "<v Friday>" in vtt and "<v Emma>" in vtt
    assert done["check"]["ok"] is True and done["check"]["wer"] == 0.0
    # Both hosts, each in their own installed voice.
    assert {v for v, _t in spoken} == {"af_heart", "bf_emma"}
    # Signed provenance names what made it and where, and hashes private sources.
    path, kw = signed[0]
    assert path.name == "audio.wav"
    tools = [t["tool"] for t in kw["tool_chain"]]
    assert tools == ["podcast_engine.write_script", "kokoro", "faster-whisper"]
    assert all("sha256" in s and "title" not in s for s in kw["sources"])
    assert done["provenance"]["signed"] is True


def test_a_failed_check_is_labelled_not_hidden(monkeypatch):
    monkeypatch.setattr(pe, "_llm_json", _fake_writer())
    _fake_speaker(monkeypatch)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back",
                        lambda wav, s, transcribe=None: real(
                            wav, s, transcribe=lambda p: "something else entirely"))
    done = pe.produce(_text_ep(length="short")["id"])
    assert done["status"] == "ready"
    assert done["check"]["ok"] is False and done["check"]["wer"] > render.WER_OK


def test_no_local_model_waits_and_retries_then_fails_with_the_reason(monkeypatch):
    ep = _text_ep()
    done = pe.produce(ep["id"])
    assert done["status"] == "waiting" and done["tries"] == 1
    assert done["retry_after"] > done["updated_at"] and "No local model" in done["waiting_reason"]
    # The worker skips it until the retry time.
    assert pe.pending()[0]["id"] == ep["id"]
    for _ in range(pe.MAX_TRIES - 1):
        done = pe.produce(ep["id"])
    assert done["status"] == "failed" and done["error"]["code"] == "no_local_model"
    assert done["tries"] == pe.MAX_TRIES


def test_running_short_of_memory_while_speaking_waits_and_retries(monkeypatch):
    monkeypatch.setattr(pe, "_llm_json", _fake_writer())

    class Hungry:
        def speak(self, text, voice):
            raise MemoryError()
    monkeypatch.setattr(render, "speaker", lambda: Hungry())
    done = pe.produce(_text_ep()["id"])
    assert done["status"] == "waiting" and "short of memory" in done["waiting_reason"]
    # The script it already wrote is kept; the retry starts at speaking.
    assert done["lines"]


def test_a_script_with_nothing_accountable_is_not_spoken(monkeypatch):
    monkeypatch.setattr(pe, "_llm_json", _fake_writer(lambda n: [
        {"speaker": "a", "text": "Sales hit 900 units.", "cites": []}]))
    spoken = _fake_speaker(monkeypatch)
    done = pe.produce(_text_ep()["id"])
    assert done["status"] == "failed" and done["error"]["code"] == "script_empty"
    assert spoken == []


def test_a_cancelled_episode_stops(monkeypatch):
    monkeypatch.setattr(pe, "_llm_json", _fake_writer())
    spoken = _fake_speaker(monkeypatch)
    ep = _text_ep()
    pe.cancel(ep["id"])
    assert pe.produce(ep["id"])["status"] == "cancelled"
    assert spoken == []


def test_the_worker_resumes_a_stage_after_a_restart(monkeypatch):
    ep = _text_ep()
    pe._update(ep["id"], status="speaking")
    pe._recover()
    assert pe.load(ep["id"])["status"] == "queued"


# ── privacy ─────────────────────────────────────────────────────────────────

def test_private_material_makes_a_private_episode_that_refuses_cloud_voices():
    with pytest.raises(pe.PodcastRefused) as exc:
        pe.create([{"kind": "text", "text": "my notes"}], voice_engine="cloud")
    assert "private" in exc.value.user_message


def test_public_sources_make_a_public_episode_but_cloud_voice_stays_opt_in():
    ep = pe.create([{"kind": "url", "url": "https://example.org/a"}])
    assert ep["privacy"] == "public" and ep["voice_engine"] == "local"
    with pytest.raises(pe.PodcastRefused) as exc:
        pe.create([{"kind": "url", "url": "https://example.org/a"}], voice_engine="cloud")
    assert "Cloud voices are off" in exc.value.user_message


def test_local_only_mode_refuses_a_cloud_voice(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {
        "model_routing": {"mode": "local_only"}, "podcasts": {"cloud_voice": True}})
    with pytest.raises(pe.PodcastRefused) as exc:
        pe.create([{"kind": "url", "url": "https://example.org/a"}], voice_engine="cloud")
    assert "Local-only" in exc.value.user_message


def test_the_briefing_episode_is_private_because_it_carries_mail():
    ep = pe.create([{"kind": "news_run", "routine": "briefing", "run_id": "2026-09-29"}],
                   origin="routine")
    assert ep["privacy"] == "private"
    ep = pe.create([{"kind": "news_run", "routine": "front_page", "run_id": "2026-09-29-morning"}],
                   origin="routine")
    assert ep["privacy"] == "public"


def test_off_the_record_writes_no_episode(monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    before = list((pe.root()).glob("*"))
    with pytest.raises(pe.PodcastRefused) as exc:
        pe.create([{"kind": "text", "text": "x"}])
    assert "off the record" in exc.value.user_message
    assert list(pe.root().glob("*")) == before
    # A routine episode is not about the conversation.
    assert pe.create([{"kind": "text", "text": "x"}], origin="routine")["status"] == "queued"


def test_the_notice_for_a_private_episode_carries_no_personal_details(monkeypatch):
    pushed = []
    from agent_friday import notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    ep = {"id": "20260929T100000-abcdef", "title": "Call Ada at " + FAKE_PHONE,
          "privacy": "private", "show": "Friday Podcast", "duration_s": 300}
    pe._announce(ep)
    assert FAKE_PHONE not in pushed[0]["title"]
    assert "private, made on this PC" in pushed[0]["body"]


def test_episodes_notify_and_never_autoplay(monkeypatch):
    pushed = []
    from agent_friday import notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    pe._announce({"id": "20260929T100000-abcdef", "title": "T", "privacy": "public",
                  "show": "S", "duration_s": 60})
    assert pushed and pushed[0]["target"]["view"] == "podcasts"
    assert not any("play" in json.dumps(a).lower() and a.get("type") == "podcast"
                   for a in pushed[0].get("actions") or [])


# ── the render gate ─────────────────────────────────────────────────────────

def test_a_request_waits_only_for_stand_down_and_the_gpu_lease(monkeypatch):
    from agent_friday.services import residency_arbiter, scheduler, stand_down
    monkeypatch.setattr(scheduler, "idle_work_blocked_reason",
                        lambda *a, **k: pytest.fail("a request must not wait for idle"))
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: False)
    monkeypatch.setattr(residency_arbiter, "exclusive_lease", lambda: None)
    assert pe._gate_reason({"priority": "now"}) == ""
    monkeypatch.setattr(residency_arbiter, "exclusive_lease", lambda: {"role": "image job"})
    assert "image job" in pe._gate_reason({"priority": "now"})


def test_routine_and_long_episodes_use_the_scheduler_idle_gate(monkeypatch):
    from agent_friday.services import scheduler
    seen = []
    monkeypatch.setattr(scheduler, "idle_work_blocked_reason",
                        lambda rec=None, spec=None, now=None: seen.append(spec) or "")
    pe._gate_reason({"priority": "routine", "length": "short"})
    pe._gate_reason({"priority": "routine", "length": "long"})
    assert seen[0] == {"from_hour": 0, "to_hour": 24, "idle_after_s": 60}
    assert seen[1] is None          # the owner's own idle window


# ── speech: local files only ────────────────────────────────────────────────

def test_the_renderer_never_downloads(monkeypatch):
    """No hf_hub_download / snapshot_download anywhere in the render path."""
    src = Path(render.__file__).read_text(encoding="utf-8")
    assert "hf_hub_download(" not in src and "snapshot_download(" not in src
    assert "try_to_load_from_cache" in src and "local_files_only=True" in src


def test_a_voice_that_is_not_installed_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(render, "_cached", lambda repo, fn: None)
    with pytest.raises(render.RenderError) as exc:
        render.CpuKokoro().speak("hello", "am_michael")
    assert exc.value.code == "voice_not_installed" and "am_michael" in str(exc.value)


def test_kokoro_is_built_from_cached_paths_on_the_cpu(monkeypatch, tmp_path):
    built = {}

    class KModel:
        def __init__(self, config=None, model=None):
            built.update(config=config, model=model)

        def to(self, dev):
            built["device"] = dev
            return self

        def eval(self):
            return self

    class KPipeline:
        def __init__(self, lang_code, model):
            built.setdefault("langs", []).append(lang_code)
            self.g2p = types.SimpleNamespace(fallback=object())

        def __call__(self, text, voice):
            import numpy as np
            built.setdefault("voices", []).append(voice)
            yield text, "ph", np.zeros(240, dtype="float32")

    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KModel=KModel, KPipeline=KPipeline))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(set_num_threads=lambda n: None))
    from agent_friday.services import kokoro_voice
    monkeypatch.setattr(kokoro_voice, "ensure_espeak_fallback", lambda: {"wired": True})
    files = {}
    for fn in ("config.json", "kokoro-v1_0.pth", "voices/af_heart.pt", "voices/bf_emma.pt"):
        p = tmp_path / fn
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        files[fn] = str(p)
    monkeypatch.setattr(render, "_cached", lambda repo, fn: files.get(fn))
    k = render.CpuKokoro()
    assert len(k.speak("Hello there.", "af_heart")) == 240
    k.speak("Hello there.", "bf_emma")
    assert built["config"] == files["config.json"] and built["model"] == files["kokoro-v1_0.pth"]
    assert built["device"] == "cpu"
    assert built["langs"] == ["a", "b"]          # American and British g2p
    assert built["voices"] == [files["voices/af_heart.pt"], files["voices/bf_emma.pt"]]


def test_the_listening_check_reads_numbers_as_spoken():
    assert render.word_error_rate("In 2019 turnout was 41.6%.",
                                  "in twenty nineteen turnout was forty-one point six percent") == 0.0
    assert render.word_error_rate("Seven to two.", "seven to two") == 0.0
    assert render.word_error_rate("a b c d", "a x c") == 0.5


def test_captions_are_valid_webvtt():
    vtt = render.captions_vtt([{"speaker": "a", "text": "Hi --> there", "start": 0.0, "end": 1.25},
                               {"speaker": "b", "text": "Yes.", "start": 3661.5, "end": 3662.0}],
                              {"a": "Friday", "b": "Emma"})
    assert "00:00:00.000 --> 00:00:01.250" in vtt and "01:01:01.500" in vtt
    assert "Hi → there" in vtt


def test_underscores_are_spoken_as_spaces_not_deleted():
    kept, _ = pe.clean_lines([{"speaker": "a", "text": "The days_to_close column is _really_ long.",
                               "cites": ["S1"]}], {"S1"})
    assert kept[0]["text"] == "The days to close column is really long."


def test_the_hosts_are_asked_to_alternate_and_not_to_narrate_the_structure(monkeypatch):
    llm = _fake_writer()
    monkeypatch.setattr(pe, "_llm_json", llm)
    docs = [{"sid": "S1", "title": "A", "text": "x", "url": "", "kind": "text"},
            {"sid": "S2", "title": "B", "text": "y", "url": "", "kind": "text"}]
    pe.write_script({"id": "x", "length": "short", "show": "Show", "hosts": pe.DEFAULTS["hosts"]}, docs)
    chapter = [c["user"] for c in llm.calls if "Chapter " in c["user"]][0]
    assert "Alternate between the two hosts" in chapter
    assert "Never mention chapters, sections or these instructions" in chapter


def test_a_momentarily_locked_episode_file_is_saved_on_retry(monkeypatch):
    """Windows refuses a rename onto a file another process has open for a moment."""
    ep = _text_ep()
    real = Path.replace
    refusals = {"n": 0}

    def flaky(self, target):
        if refusals["n"] < 3:
            refusals["n"] += 1
            raise PermissionError(5, "Access is denied")
        return real(self, target)
    monkeypatch.setattr(Path, "replace", flaky)
    assert pe._update(ep["id"], title="Saved anyway")["title"] == "Saved anyway"
    assert refusals["n"] == 3 and pe.load(ep["id"])["title"] == "Saved anyway"


def test_no_render_worker_runs_in_the_test_process():
    """Under FRIDAY_TESTING the worker never starts, even when an episode is
    created, and the routes module does not start it at registration."""
    import agent_friday.routes.podcasts as routes
    pe.create([{"kind": "text", "text": "x"}])
    assert pe._WORKER is None
    assert "start_worker" not in Path(routes.__file__).read_text(encoding="utf-8")


def test_the_wav_master_is_removed_once_the_mp3_is_checked_and_signed(monkeypatch, _podcast_home):
    monkeypatch.setattr(pe, "_llm_json", _fake_writer())
    _fake_speaker(monkeypatch)

    def encode(wav, mp3, title="", **tags):
        mp3.write_bytes(b"ID3fake")
        return True
    monkeypatch.setattr(render, "encode_mp3", encode)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back", lambda wav, s, transcribe=None: real(wav, s, transcribe=lambda p: s))
    done = pe.produce(_text_ep()["id"])
    d = _podcast_home / "podcasts" / done["id"]
    assert done["status"] == "ready" and done["audio"] == "audio.mp3"
    assert (d / "audio.mp3").is_file() and not (d / "audio.wav").exists()


# ── audio identity ──────────────────────────────────────────────────────────

def test_the_intro_and_outro_share_one_short_motif():
    import numpy as np
    intro, outro = render.sting("intro"), render.sting("outro")
    for s in (intro, outro):
        assert 0.5 < len(s) / render.RATE < 2.0            # under two seconds
        assert 0.02 < float(np.max(np.abs(s))) <= 0.3      # audible, never loud
    assert render.motif("outro") == list(reversed(render.motif("intro")))


def test_a_sound_in_the_brand_slot_replaces_the_generated_motif(tmp_path, monkeypatch):
    import numpy as np
    slot = tmp_path / "podcast_intro.wav"
    render.write_wav((np.ones(4800, dtype="<i2") * 1000).tobytes(), slot)
    monkeypatch.setattr(render, "AUDIO_SLOT_DIR", tmp_path)
    got = render.sting("intro")
    assert len(got) == 4800 and abs(float(got[0]) - 1000 / 32767.0) < 1e-3


def test_the_stings_frame_the_episode_and_the_lines_are_timed_after_them(monkeypatch):
    import numpy as np

    class S:
        def speak(self, text, voice):
            return np.full(2400, 0.1, dtype="float32")
    pcm, timings = render.render_lines(
        [{"speaker": "a", "text": "x", "chapter": 0}], {"a": "af_heart"}, speak=S().speak,
        intro=np.zeros(12000, dtype="float32"), outro=np.zeros(6000, dtype="float32"))
    assert timings[0]["start"] == round((12000 + render.GAP_STING_S * render.RATE) / render.RATE, 3)
    assert len(pcm) / 2 >= 12000 + 2400 + 6000


def test_every_episode_opens_and_signs_off_the_same_way(monkeypatch):
    monkeypatch.setattr(pe, "_llm_json", _fake_writer())
    _fake_speaker(monkeypatch)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back", lambda wav, s, transcribe=None: real(wav, s, transcribe=lambda p: s))
    done = pe.produce(_text_ep(show="The Front Page")["id"])
    first, second, last = done["lines"][0], done["lines"][1], done["lines"][-1]
    assert first["signature"] and first["speaker"] == "a"
    # The show is credited to the product in plain words; she is herself.
    assert first["text"] == "This is The Front Page from Agent Friday. I'm Friday."
    assert second["signature"] and second["text"] == "And I'm Emma."
    assert last["signature"] and last["text"].startswith("That's The Front Page from Agent Friday.")
    assert last["text"].endswith("I'm Friday.")
    # The writer is told not to greet or sign off itself.
    assert first["start"] > 0.5                              # after the intro sting


def test_friday_sounds_like_friday_not_a_generic_two_host_show():
    prompt = pe._system_prompt({"show": "S", "hosts": pe.DEFAULTS["hosts"]})
    for rule in ("Answer first", "what she did not check", "\"deep dive\"", "Speak to the listener as \"you\""):
        assert rule in prompt


def test_her_habits_are_character_not_a_refrain():
    """A real run said "I did not check" nine times: the brief asks for it once,
    where it matters, and for varied wording."""
    prompt = pe._system_prompt({"show": "S", "hosts": pe.DEFAULTS["hosts"]})
    assert "once, where it matters" in prompt and "not as a refrain" in prompt


def test_an_outline_heading_never_reaches_speech_and_the_sentence_after_it_does():
    kept, _cut = pe.clean_lines([{"speaker": "a", "cites": ["S1"], "text":
                                  "2. Top News (relevant to you). The pledge is thin on terms."}], {"S1"})
    assert kept[0]["text"] == "The pledge is thin on terms."
    kept, _cut = pe.clean_lines([{"speaker": "a", "cites": ["S1"], "text":
                                  "It rose 3.5 percent. 4. Proactive Insight: lead with it."}], {"S1"})
    assert kept[0]["text"] == "It rose 3.5 percent. lead with it."


def test_lines_with_their_own_sources_are_not_merged_so_each_chip_stays_by_its_sentence():
    ls = [{"speaker": "a", "chapter": 0, "text": "Story one.", "cites": ["S1"]},
          {"speaker": "a", "chapter": 0, "text": "Why it matters.", "cites": []},
          {"speaker": "a", "chapter": 0, "text": "Story two.", "cites": ["S2"]}]
    out = pe.merge_turns(ls)
    assert [o["text"] for o in out] == ["Story one. Why it matters.", "Story two."]
    assert [o["cites"] for o in out] == [["S1"], ["S2"]]
