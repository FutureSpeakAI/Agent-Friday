"""Unit tests for services/content_composer.py — the §5 composition engine.

Covers: grapheme-aware counting (emoji clusters, flags, combining marks, the
X URL=23 rule), thread splitting, per-platform hashtag caps with pinned brand
tags, the §5.4 conversion matrix shapes (timeline_engine profile mapping,
plan-only transforms), voice-card seeding from the DRAFT_MODE_PROMPTS
registers, the egress-gate fallback path, and adapt() persistence through the
content_pipeline store. All offline — the LLM rail is a monkeypatched stub.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import content_composer as cc  # noqa: E402
from agent_friday.services import content_pipeline as cpl  # noqa: E402

REWRITTEN = "[[composer-stub-rewrite]]"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """Hermetic composer: tmp voice cards + store, stubbed LLM/gate rails."""
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(cc, "_gate_prompt", lambda prompt, provider=None: prompt)
    monkeypatch.setattr(cc, "_friday_prompt", lambda: "SYSTEM-PROMPT-STUB")
    monkeypatch.setattr(cc, "_generate", lambda messages, system=None, **kw: REWRITTEN)
    monkeypatch.setattr(cc, "_pinned_tags", lambda platform: [])
    yield


def _echo_generate(monkeypatch):
    """Make the LLM stub echo the source body (deterministic length tests)."""
    def gen(messages, system=None, **kw):
        content = messages[0]["content"]
        return content.split("CONTENT:\n", 1)[-1]
    import agent_friday.services.content_composer as mod
    monkeypatch.setattr(mod, "_generate", gen)


# ─────────────────────────────────────────────────────────────────────────────
#  Grapheme-aware counting
# ─────────────────────────────────────────────────────────────────────────────

class TestCounting:
    def test_plain_ascii(self):
        assert cc.grapheme_count("hello world") == 11

    def test_zwj_family_is_one(self):
        assert cc.grapheme_count("\U0001F469‍\U0001F469‍\U0001F467‍\U0001F466") == 1

    def test_flag_pair_is_one(self):
        assert cc.grapheme_count("\U0001F1FA\U0001F1F8") == 1      # 🇺🇸
        assert cc.grapheme_count("\U0001F1FA\U0001F1F8\U0001F1EB\U0001F1F7") == 2

    def test_combining_mark_is_one(self):
        assert cc.grapheme_count("é") == 1                   # é

    def test_skin_tone_modifier_is_one(self):
        assert cc.grapheme_count("\U0001F44D\U0001F3FD") == 1      # 👍🏽

    def test_variation_selector_dropped(self):
        assert cc.grapheme_count("❤️") == 1              # ❤️

    def test_twitter_url_counts_23(self):
        url = "https://example.com/a/very/long/path/that/keeps/going/on"
        assert cc.count_chars(url, "twitter") == 23
        assert cc.count_chars(f"check this {url}", "twitter") == 11 + 23
        assert cc.count_chars(url, "bluesky") == cc.grapheme_count(url)

    def test_twitter_multiple_urls(self):
        text = "a https://x.io/aaaaaaaaaaaaaaaaaaaa b https://y.io/bbbbbbbbbbbbbbb"
        assert cc.count_chars(text, "twitter") == 1 + 1 + 1 + 1 + 1 + 46

    def test_trim_respects_clusters(self):
        text = "\U0001F1FA\U0001F1F8" * 10                          # 10 flags
        trimmed = cc._trim_to_count(text, 5, "bluesky")
        assert cc.count_chars(trimmed, "bluesky") <= 5
        assert trimmed.endswith("…")


# ─────────────────────────────────────────────────────────────────────────────
#  Thread splitting
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSplit:
    def test_short_body_single_segment(self):
        segs = cc.split_thread("One sharp idea.", 280)
        assert segs == ["One sharp idea."]

    def test_long_body_splits_within_limit(self):
        body = " ".join(f"Sentence number {i} adds detail to the story." for i in range(30))
        segs = cc.split_thread(body, 280, "twitter")
        assert len(segs) > 1
        for s in segs:
            assert cc.count_chars(s, "twitter") <= 280
        assert segs[0].endswith(f"(1/{len(segs)})")
        assert segs[-1].endswith(f"({len(segs)}/{len(segs)})")

    def test_order_preserved(self):
        body = "First point here. Second point here. " * 20
        segs = cc.split_thread(body, 100, "twitter")
        joined = " ".join(segs)
        assert joined.index("First point") < len(segs[0])

    def test_oversize_sentence_hard_wraps(self):
        body = "x" * 900
        segs = cc.split_thread(body, 280, "twitter")
        assert all(cc.count_chars(s, "twitter") <= 280 for s in segs)
        assert sum(len(s) for s in segs) >= 900   # nothing silently lost

    def test_emoji_body_counts_graphemes(self):
        body = ("\U0001F469‍\U0001F469‍\U0001F467‍\U0001F466 " * 200).strip()
        segs = cc.split_thread(body, 300, "bluesky")
        for s in segs:
            assert cc.count_chars(s, "bluesky") <= 300


# ─────────────────────────────────────────────────────────────────────────────
#  Bluesky 300 graphemes through preview
# ─────────────────────────────────────────────────────────────────────────────

class TestBlueskyLimit:
    def test_300_graphemes_pass(self, monkeypatch):
        _echo_generate(monkeypatch)
        body = "❤️" * 150 + "x" * 150                    # 300 graphemes
        assert cc.grapheme_count(body) == 300
        res = cc.preview(body, [], "bluesky", "post")
        assert res["ok"]
        assert res["char_limit"] == 300
        assert res["char_used"] <= 300

    def test_overflow_becomes_thread(self, monkeypatch):
        _echo_generate(monkeypatch)
        body = "A solid idea. " * 60                                # way over 300
        res = cc.preview(body, [], "bluesky", "post")
        assert res["ok"]
        assert res["segments"], "overflow on a thread platform should split"
        assert all(cc.count_chars(s, "bluesky") <= 300 for s in res["segments"])

    def test_overflow_non_thread_platform_trims(self, monkeypatch):
        _echo_generate(monkeypatch)
        body = "word " * 900                                        # > 2200 chars
        res = cc.preview(body, [], "instagram", "post")
        assert res["ok"]
        assert not res["segments"]
        assert res["char_used"] <= 2200
        assert any("trimmed" in w for w in res["warnings"])


# ─────────────────────────────────────────────────────────────────────────────
#  Hashtags (§5.3)
# ─────────────────────────────────────────────────────────────────────────────

BODY = ("Friday Desktop ships a new content pipeline today. The pipeline "
        "handles composition, scheduling and provenance for creators. "
        "#sovereignty matters for creators everywhere.")


class TestHashtags:
    def test_reddit_always_empty(self):
        assert cc.suggest_hashtags(BODY, "reddit") == []

    def test_linkedin_cap_3(self):
        tags = cc.suggest_hashtags(BODY, "linkedin", limit=8)
        assert 0 < len(tags) <= 3
        assert all(t.startswith("#") for t in tags)

    def test_twitter_cap_2(self):
        assert len(cc.suggest_hashtags(BODY, "twitter", limit=8)) <= 2

    def test_bluesky_mastodon_cap_3(self):
        assert len(cc.suggest_hashtags(BODY, "bluesky", limit=8)) <= 3
        assert len(cc.suggest_hashtags(BODY, "mastodon", limit=8)) <= 3

    def test_instagram_honors_explicit_limit(self):
        tags = cc.suggest_hashtags(BODY, "instagram", limit=5)
        assert len(tags) <= 5

    def test_pinned_brand_tags_come_first(self, monkeypatch):
        monkeypatch.setattr(cc, "_pinned_tags",
                            lambda platform: ["FridayAI", "friday ai"])
        tags = cc.suggest_hashtags(BODY, "linkedin")
        assert tags[0] == "#FridayAI"
        assert tags.count("#FridayAI") == 1          # dedupe, case-insensitive

    def test_existing_body_tags_extracted(self):
        tags = cc.suggest_hashtags(BODY, "instagram", limit=10)
        assert "#Sovereignty" in tags

    def test_never_raises(self):
        assert cc.suggest_hashtags(None, "nope-platform") == []


# ─────────────────────────────────────────────────────────────────────────────
#  Conversion matrix (§5.4) — plan objects only
# ─────────────────────────────────────────────────────────────────────────────

def _video_post():
    return cpl.new_post(title="My Film", body="A film about pipelines. It is good.",
                        assets=[cpl.new_asset_ref("clip.mp4", kind="video",
                                                  alt_text="a film")])


class TestConversionMatrix:
    @pytest.mark.parametrize("conversion,platform,fmt,profile", [
        ("reel", "instagram", "reel", "instagram-reel"),
        ("short", "youtube", "short", "mp4-vertical-9x16"),
        ("tiktok", "tiktok", "video_upload", "tiktok-vertical"),
    ])
    def test_video_profile_mapping(self, conversion, platform, fmt, profile):
        res = cc.convert_format(_video_post(), conversion)
        assert res["ok"], res
        t = res["targets"][0]
        assert t["platform"] == platform
        assert t["format"] == fmt
        plan = t["adapted_assets"]
        assert plan and plan[0]["profile"] == profile
        # Plans only — nothing executed at compose time (§5.4)
        assert plan[0]["out_path"] is None
        assert plan[0]["platform_media_id"] is None

    def test_clip_carries_slice_plan(self):
        res = cc.convert_format(_video_post(), "clip")
        assert res["ok"]
        entry = res["targets"][0]["adapted_assets"][0]
        assert entry["slice"] == {"start_s": None, "end_s": None}
        assert entry["profile"] == "mp4-vertical-9x16"

    def test_thread_conversion(self, monkeypatch):
        _echo_generate(monkeypatch)
        post = cpl.new_post(body="Idea one is strong. " * 40)
        res = cc.convert_format(post, "thread")          # alias → tweet_thread
        assert res["ok"]
        t = res["targets"][0]
        assert t["platform"] == "twitter" and t["format"] == "thread"
        assert len(t["segments"]) > 1
        assert all(cc.count_chars(s, "twitter") <= 280 for s in t["segments"])

    def test_carousel_shapes(self):
        post = cpl.new_post(assets=[
            cpl.new_asset_ref("a.png", kind="image", alt_text="one"),
            cpl.new_asset_ref("b.png", kind="image", alt_text="two")],
            body="Gallery drop.")
        res = cc.convert_format(post, "carousel")
        assert res["ok"]
        plan = res["targets"][0]["adapted_assets"]
        assert [e["slide"] for e in plan] == [1, 2]
        assert plan[0]["cover"] and not plan[1]["cover"]

    def test_missing_asset_kind_warns(self):
        post = cpl.new_post(body="text only")
        res = cc.convert_format(post, "reel")
        assert res["ok"]
        assert any("no video asset" in w for w in res["warnings"])
        assert res["targets"][0]["adapted_assets"] == []

    def test_newsletter_and_description_targets(self):
        post = cpl.new_post(title="Big Title", body="Long piece. More words here.")
        news = cc.convert_format(post, "newsletter")
        assert news["ok"] and news["targets"][0]["platform"] == "substack"
        desc = cc.convert_format(post, "youtube_description")
        assert desc["ok"]
        assert desc["targets"][0]["adapted_title"] == "Big Title"

    def test_unknown_conversion(self):
        res = cc.convert_format(cpl.new_post(body="x"), "hologram")
        assert not res["ok"]
        assert "tweet_thread" in res["conversions"]


# ─────────────────────────────────────────────────────────────────────────────
#  Voice cards (§5.6)
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceCards:
    def test_linkedin_seeds_from_draft_mode_prompts(self):
        res = cc.get_voice_card("linkedin")
        assert res["ok"] and res["seeded"]
        assert "LinkedIn ghostwriter" in res["text"]
        assert Path(res["path"]).exists()

    def test_twitter_seed_carries_280_register(self):
        res = cc.get_voice_card("twitter")
        assert res["ok"] and "280" in res["text"]

    def test_second_read_not_reseeded(self):
        cc.get_voice_card("bluesky")
        assert cc.get_voice_card("bluesky")["seeded"] is False

    def test_set_and_reread(self):
        assert cc.set_voice_card("mastodon", "My custom fediverse voice.")["ok"]
        assert cc.get_voice_card("mastodon")["text"] == "My custom fediverse voice."

    def test_edit_keeps_history(self):
        cc.get_voice_card("linkedin")                    # seed
        cc.set_voice_card("linkedin", "New voice.")
        hist = cc.VOICE_CARDS_DIR / "history" / "linkedin.md"
        assert hist.exists() and "LinkedIn ghostwriter" in hist.read_text(encoding="utf-8")

    def test_unknown_platform_rejected(self):
        assert not cc.get_voice_card("myspace")["ok"]
        assert not cc.set_voice_card("myspace", "x")["ok"]

    def test_card_clamped_to_2kb(self):
        cc.set_voice_card("tiktok", "y" * 9000)
        assert len(cc.get_voice_card("tiktok")["text"]) <= cc.VOICE_CARD_MAX_CHARS

    def test_folded_into_system_prompt(self):
        cc.set_voice_card("linkedin", "SIGNATURE-VOICE-MARKER")
        sys_prompt = cc._compose_system_prompt("linkedin")
        assert "SYSTEM-PROMPT-STUB" in sys_prompt
        assert "SIGNATURE-VOICE-MARKER" in sys_prompt
        assert "PLATFORM VOICE CARD (linkedin)" in sys_prompt


# ─────────────────────────────────────────────────────────────────────────────
#  adapt() — pipeline, gate fallback, persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestAdapt:
    def test_bare_dict_round_trip(self):
        post = cpl.new_post(title="T", body="Body of the post.",
                            platforms=["linkedin", "bluesky"])
        res = cc.adapt(post)
        assert res["ok"], res
        assert {t["platform"] for t in res["targets"]} == {"linkedin", "bluesky"}
        for t in res["targets"]:
            assert t["adapted_body"] == REWRITTEN
            assert t["char_limit"] > 0

    def test_confirmed_target_skipped(self):
        post = cpl.new_post(body="hello", platforms=["linkedin"])
        post["platforms"][0]["status"] = "CONFIRMED"
        res = cc.adapt(post)
        assert res["ok"]
        assert res["targets"] == []
        assert any("CONFIRMED" in w for w in res["warnings"])

    def test_composed_kept_unless_regenerate(self, monkeypatch):
        post = cpl.new_post(body="hello", platforms=["linkedin"])
        post["platforms"][0]["adapted_body"] = "ALREADY-DONE"
        res = cc.adapt(post)
        assert res["ok"] and res["targets"][0]["adapted_body"] == "ALREADY-DONE"
        res2 = cc.adapt(post, regenerate=True)
        assert res2["ok"] and res2["targets"][0]["adapted_body"] == REWRITTEN

    def test_gate_divergence_falls_back_deterministic(self, monkeypatch):
        called = {"llm": False}

        def boom(*a, **kw):
            called["llm"] = True
            return REWRITTEN
        monkeypatch.setattr(cc, "_generate", boom)
        monkeypatch.setattr(cc, "_gate_prompt",
                            lambda prompt, provider=None: "[REDACTED]")
        res = cc.adapt(cpl.new_post(body="my SSN is private",
                                    platforms=["linkedin"]))
        assert res["ok"]
        assert called["llm"] is False, "gated prompt must never reach the model"
        assert res["targets"][0]["adapted_body"] == "my SSN is private"
        assert any("withheld by egress gate" in w for w in res["warnings"])

    def test_llm_failure_falls_back(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("no model")
        monkeypatch.setattr(cc, "_generate", boom)
        res = cc.adapt(cpl.new_post(body="plain text", platforms=["twitter"]))
        assert res["ok"]
        assert res["targets"][0]["adapted_body"] == "plain text"
        assert any("deterministic adaptation" in w for w in res["warnings"])

    def test_federation_full_fidelity(self):
        body = "Every word matters here."
        res = cc.adapt(cpl.new_post(title="T", body=body,
                                    platforms=["federation"]))
        assert res["ok"]
        t = res["targets"][0]
        assert t["adapted_body"] == body          # never loses information
        assert t["format"] == "listing"
        assert t["char_limit"] == 0

    def test_nsfw_tag_maps_to_platform_flags(self):
        post = cpl.new_post(body="art", platforms=["mastodon", "reddit"],
                            tags=["nsfw"])
        res = cc.adapt(post)
        by_plat = {t["platform"]: t for t in res["targets"]}
        assert by_plat["mastodon"]["options"]["sensitive"] is True
        assert by_plat["mastodon"]["options"]["spoiler_text"]
        assert by_plat["reddit"]["options"]["nsfw"] is True

    def test_alt_text_nag(self):
        post = cpl.new_post(body="pic", platforms=["linkedin"],
                            assets=[cpl.new_asset_ref("img.png", kind="image")])
        res = cc.adapt(post)
        assert any("missing alt text" in w for w in res["warnings"])

    def test_persists_through_store(self):
        created = cpl.create_post(title="T", body="Store-backed body.",
                                  platforms=["linkedin"])
        assert created["ok"]
        post = created["post"]
        res = cc.adapt(post)
        assert res["ok"]
        stored = cpl.get_post(post["id"])["post"]
        assert stored["targets"][0]["adapted_body"] == REWRITTEN
        assert stored["targets"][0]["char_limit"] == 3000

    def test_new_platform_added_and_persisted(self):
        created = cpl.create_post(body="Body.", platforms=["linkedin"])
        post = created["post"]
        res = cc.adapt(post, platforms=["linkedin", "bluesky"])
        assert res["ok"]
        stored = cpl.get_post(post["id"])["post"]
        assert {t["platform"] for t in stored["targets"]} == {"linkedin", "bluesky"}

    def test_unknown_platform_warned(self):
        res = cc.adapt(cpl.new_post(body="x"), platforms=["friendster"])
        assert res["ok"]
        assert res["targets"] == []
        assert any("unknown platform" in w for w in res["warnings"])

    def test_no_platforms_errors(self):
        res = cc.adapt(cpl.new_post(body="x"))
        assert not res["ok"]


# ─────────────────────────────────────────────────────────────────────────────
#  preview() envelope
# ─────────────────────────────────────────────────────────────────────────────

class TestPreview:
    def test_preview_is_deterministic_never_fires_llm(self, monkeypatch):
        # D6 regression: a live preview refresh must not run a fresh
        # temperature-0.4 rewrite — the stored target payload is what ships.
        called = {"n": 0}

        def counting(*a, **kw):
            called["n"] += 1
            return REWRITTEN
        monkeypatch.setattr(cc, "_generate", counting)
        res = cc.preview("One sharp idea.", [], "twitter", "post")
        assert res["ok"]
        assert called["n"] == 0, "preview must never call the model"
        assert res["adapted_body"] == "One sharp idea."
        # identical input → identical output, every time
        assert cc.preview("One sharp idea.", [], "twitter", "post") == res

    def test_envelope_keys(self):
        res = cc.preview("A body.", [], "linkedin", "post")
        assert res["ok"]
        for k in ("adapted_body", "segments", "hashtags", "char_used",
                  "char_limit", "asset_plan", "warnings"):
            assert k in res

    def test_thread_char_used_is_max_segment(self, monkeypatch):
        _echo_generate(monkeypatch)
        res = cc.preview("Point one is fine. " * 40, [], "twitter", "thread")
        assert res["ok"] and res["segments"]
        assert res["char_used"] == max(cc.count_chars(s, "twitter")
                                       for s in res["segments"])

    def test_unknown_platform(self):
        assert not cc.preview("x", [], "geocities", "post")["ok"]

    def test_stateless_no_store_rows(self):
        cc.preview("A body.", [], "linkedin", "post")
        assert cpl.list_posts()["posts"] == []


# ─────────────────────────────────────────────────────────────────────────────
#  Compose → adapter.prepare round-trips — one length metric, reserved budgets
# ─────────────────────────────────────────────────────────────────────────────

class TestPublishTimeContracts:
    """What the composer trims must survive the adapters' own hard validation
    (regressions: grapheme-vs-codepoint drift, Mastodon spoiler_text budget,
    the appended hashtag block)."""

    @pytest.fixture(autouse=True)
    def _platforms_tmp(self, tmp_path, monkeypatch):
        from agent_friday.services.platforms import base as pbase
        monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
        monkeypatch.setattr(pbase, "BUDGET_PATH",
                            tmp_path / "platforms" / "rate_budget.json")
        yield

    def test_emoji_body_composed_for_x_passes_prepare(self):
        # Composer counts graphemes (family emoji = 1); the adapter must
        # agree or an exactly-at-limit tweet dies at publish time.
        from agent_friday.services.platforms.x_twitter import XTwitterAdapter, _x_len
        family = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"
        body = family * 280                        # 280 graphemes exactly
        assert cc.count_chars(body, "twitter") == 280
        assert _x_len(body) == 280                 # same metric, both sides
        fields, _w = cc._adapt_one(body, "", [], "twitter", "post",
                                   use_llm=False)
        prep = XTwitterAdapter().prepare({"id": "t1", **fields},
                                         {"id": "p1", "body": body})
        assert prep["ok"], prep

    def test_x_prepare_appends_hashtag_block(self):
        from agent_friday.services.platforms.x_twitter import XTwitterAdapter
        prep = XTwitterAdapter().prepare(
            {"id": "t1", "adapted_body": "hello world", "format": "post",
             "hashtags": ["#AI"]}, {"id": "p1"})
        assert prep["ok"], prep
        assert prep["prepared"]["body"] == "hello world\n\n#AI"
        # already-present tags are never duplicated
        prep2 = XTwitterAdapter().prepare(
            {"id": "t2", "adapted_body": "all about #AI", "format": "post",
             "hashtags": ["#AI"]}, {"id": "p1"})
        assert prep2["prepared"]["body"] == "all about #AI"

    def test_mastodon_nsfw_spoiler_budget_round_trip(self):
        # nsfw → spoiler_text counts against the 500 limit server-side; the
        # composer reserves it, so prepare must accept the composed payload.
        from agent_friday.services.platforms.mastodon import MastodonAdapter
        body = "a" * 495                            # within 17 of the limit
        fields, _w = cc._adapt_one(body, "", [], "mastodon", "post",
                                   tags=["nsfw"], use_llm=False)
        spoiler = fields["options"]["spoiler_text"]
        assert spoiler                              # the CW is mapped
        for s in (fields["segments"] or [fields["adapted_body"]]):
            assert cc.count_chars(s, "mastodon") + \
                cc.count_chars(spoiler, "mastodon") <= 500
        prep = MastodonAdapter().prepare(
            {"id": "t1", **fields}, {"id": "p1", "tags": ["nsfw"]})
        assert prep["ok"], prep
        assert prep["prepared"]["options"]["sensitive"] is True

    def test_bluesky_hashtag_block_fits_after_append(self, monkeypatch):
        from agent_friday.services.platforms.bluesky import (
            BlueskyAdapter, grapheme_len)
        monkeypatch.setattr(cc, "_pinned_tags", lambda platform: ["FridayAI"])
        body = "One sharp idea about pipelines. " * 12   # overflows 300
        fields, _w = cc._adapt_one(body, "", [], "bluesky", "post",
                                   use_llm=False)
        assert fields["hashtags"], "pinned brand tag should be suggested"
        prep = BlueskyAdapter().prepare({"id": "t1", **fields}, {"id": "p1"})
        assert prep["ok"], prep
        shipped = prep["prepared"]["segments"] or [prep["prepared"]["body"]]
        assert any("#FridayAI" in s for s in shipped)
        for s in shipped:
            assert grapheme_len(s) <= 300

    def test_linkedin_prepare_carries_hashtags_in_commentary(self):
        from agent_friday.services.platforms.linkedin import LinkedInAdapter
        prep = LinkedInAdapter().prepare(
            {"id": "t1", "adapted_body": "Shipping day.", "format": "post",
             "hashtags": ["#AI", "#Shipping"]}, {"id": "p1"})
        assert prep["ok"], prep
        assert prep["prepared"]["body"].endswith("#AI #Shipping")
