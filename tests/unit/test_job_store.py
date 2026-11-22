"""The job store: one record per posting, with a status, so a panel can show what is open and recently seen and
keep what is gone in an archive instead of showing it forever.

Rules held here (synthetic listings only):
* a posting has one stable key, however many times and from however many sources it is seen;
* a posting is open until its closing date passes or it has not been seen for STALE_DAYS; then it moves to the
  archive with the date it closed, and is never deleted;
* "recent" is open (or applied), recently seen postings, newest sighting first, never ordered by score;
* a posting seen again after it expired is open again; one that was closed on purpose stays closed;
* the file is written atomically, and a damaged file is an error, never an empty store.
"""
import json
import os
from datetime import datetime, timedelta

import pytest

from agent_friday.services import job_store as js

NOW = datetime(2026, 11, 20, 9, 0)


def days(n):
    return NOW + timedelta(days=n)


def listing(title="Staff Engineer", company="Acme Robotics", **kw):
    base = {"title": title, "company": company, "location": "Remote", "source": "feed-a", "external_id": None,
            "url": "https://jobs.example.test/%s" % title.lower().replace(" ", "-"), "score": 0.5}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    return tmp_path


# -- the keys -------------------------------------------------------------------------------

def test_the_same_posting_has_the_same_key_however_it_is_spelled():
    a = js.make_key(listing("Staff Engineer", "Acme Robotics", source="feed-a"))
    b = js.make_key(listing("  staff   engineer ", "ACME robotics", source="feed-b", url="https://other.example.test/9"))
    assert a == b


def test_a_source_id_beats_the_words_and_a_url_is_the_last_resort():
    one = js.make_key(listing(source="feed-a", external_id="X1", title="One"))
    two = js.make_key(listing(source="feed-a", external_id="X1", title="Renamed after a repost"))
    assert one == two
    bare = {"url": "https://jobs.example.test/only-a-url"}
    assert js.make_key(bare) == js.make_key(dict(bare))
    with pytest.raises(ValueError):
        js.make_key({})


# -- recent shows what is open and recently seen -----------------------------------------------

def test_an_open_recently_seen_job_shows_in_recent_and_not_in_the_archive():
    js.upsert(listing(), NOW)
    assert [r["title"] for r in js.list_recent(NOW)] == ["Staff Engineer"]
    assert js.list_archive(NOW) == []


def test_recent_is_newest_sighting_first_not_highest_score_first():
    js.upsert(listing("Old High Scorer", score=0.99), days(-10))
    js.upsert(listing("New Low Scorer", score=0.10), days(-1))
    assert [r["title"] for r in js.list_recent(NOW)] == ["New Low Scorer", "Old High Scorer"]


def test_duplicates_are_one_record_and_seeing_it_again_refreshes_when_it_was_seen():
    js.upsert(listing(), days(-9))
    js.upsert(listing(source="feed-b", url="https://elsewhere.example.test/1"), days(-2))
    rows = js.list_recent(NOW)
    assert len(rows) == 1
    assert rows[0]["first_seen"] == days(-9).isoformat() and rows[0]["last_seen"] == days(-2).isoformat()


# -- dead listings leave recent and go to the archive with their date ----------------------------

def test_two_duplicates_of_a_closed_job_show_zero_times_in_recent_and_once_in_the_archive():
    closes = days(-120)
    js.upsert(listing(closes_at=closes.isoformat()), days(-150))
    js.upsert(listing(source="feed-b", url="https://elsewhere.example.test/2", closes_at=closes.isoformat()), days(-149))
    assert js.list_recent(NOW) == []
    archive = js.list_archive(NOW)
    assert len(archive) == 1 and archive[0]["status"] == "closed"
    assert archive[0]["closed_at"] == closes.isoformat() and archive[0]["close_reason"]


def test_a_posting_not_seen_for_the_stale_window_expires_into_the_archive_on_the_day_it_went_stale():
    js.upsert(listing(), days(-(js.STALE_DAYS + 20)))
    assert js.list_recent(NOW) == []
    row = js.list_archive(NOW)[0]
    assert row["status"] == "expired"
    assert row["closed_at"] == (days(-(js.STALE_DAYS + 20)) + timedelta(days=js.STALE_DAYS)).isoformat()


def test_a_posting_seen_just_inside_the_window_is_still_recent():
    js.upsert(listing(), days(-(js.STALE_DAYS - 1)))
    assert len(js.list_recent(NOW)) == 1 and js.list_archive(NOW) == []


def test_a_closing_date_in_the_future_keeps_it_open_and_the_day_it_passes_it_closes():
    js.upsert(listing(closes_at=days(3).isoformat()), NOW)
    assert len(js.list_recent(NOW)) == 1
    later = days(4)
    assert js.list_recent(later) == [] and js.list_archive(later)[0]["closed_at"] == days(3).isoformat()


def test_a_posting_that_arrives_already_past_its_closing_date_goes_straight_to_the_archive():
    js.upsert(listing(closes_at=days(-5).isoformat()), NOW)
    assert js.list_recent(NOW) == [] and len(js.list_archive(NOW)) == 1


def test_the_archive_is_newest_closing_first_and_nothing_is_deleted(_home):
    js.upsert(listing("Older Close", closes_at=days(-90).isoformat()), days(-100))
    js.upsert(listing("Newer Close", closes_at=days(-10).isoformat()), days(-30))
    assert [r["title"] for r in js.list_archive(NOW)] == ["Newer Close", "Older Close"]
    assert len(json.loads((_home / "job_store.json").read_text(encoding="utf-8"))["jobs"]) == 2


# -- what happens when a posting comes back ------------------------------------------------------

def test_a_posting_that_expired_and_is_seen_again_is_open_again():
    js.upsert(listing(), days(-100))
    assert js.list_recent(NOW) == []
    js.upsert(listing(), NOW)
    assert len(js.list_recent(NOW)) == 1 and js.list_archive(NOW) == []


def test_a_posting_the_owner_closed_stays_closed_when_a_feed_still_lists_it():
    key = js.upsert(listing(), days(-2))["key"]
    js.close(key, days(-1), "not for me")
    js.upsert(listing(), NOW)
    assert js.list_recent(NOW) == [] and js.list_archive(NOW)[0]["close_reason"] == "not for me"


def test_a_posting_past_its_closing_date_does_not_reopen_just_because_it_was_seen_again():
    js.upsert(listing(closes_at=days(-3).isoformat()), days(-10))
    js.upsert(listing(closes_at=days(-3).isoformat()), NOW)
    assert js.list_recent(NOW) == []


# -- applied ----------------------------------------------------------------------------------------

def test_an_applied_posting_stays_in_recent_while_it_is_seen_and_goes_to_the_archive_when_it_is_not():
    key = js.upsert(listing(), days(-2))["key"]
    js.mark_applied(key, days(-1))
    assert js.list_recent(NOW)[0]["status"] == "applied"
    later = days(js.STALE_DAYS + 40)
    assert js.list_recent(later) == []
    assert js.list_archive(later)[0]["status"] == "applied"


# -- the file ------------------------------------------------------------------------------------------

def test_a_damaged_file_is_an_error_and_is_never_replaced(_home):
    js.upsert(listing(), NOW)
    (_home / "job_store.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(js.StoreUnreadable):
        js.list_recent(NOW)
    with pytest.raises(js.StoreUnreadable):
        js.upsert(listing("Another"), NOW)
    assert (_home / "job_store.json").read_text(encoding="utf-8") == "{not json"


def test_a_failed_write_leaves_the_old_file_whole(_home, monkeypatch):
    js.upsert(listing(), NOW)
    before = (_home / "job_store.json").read_text(encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        js.upsert(listing("Another"), NOW)
    monkeypatch.undo()
    assert (_home / "job_store.json").read_text(encoding="utf-8") == before


def test_an_empty_store_has_nothing_recent_and_nothing_archived():
    assert js.list_recent(NOW) == [] and js.list_archive(NOW) == []


def test_the_store_lives_under_the_friday_home_it_is_given(_home):
    js.upsert(listing(), NOW)
    assert (_home / "job_store.json").is_file()


# -- the old tracker's listings come in once and are judged by the same rules ------------------------------

def test_the_old_trackers_listings_are_imported_once_and_the_dead_ones_land_in_the_archive(monkeypatch):
    rows = [{"title": "Closed Back In July", "company": "Acme Robotics", "location": "Remote", "source": "feed-a",
             "external_id": "A1", "source_url": "https://jobs.example.test/a1", "discovered_at": "2026-07-02T09:00:00",
             "relevance_score": 0.95},
            {"title": "Also Closed In July", "company": "Beta Labs", "location": "Remote", "source": "feed-a",
             "external_id": "B1", "source_url": "https://jobs.example.test/b1", "discovered_at": "2026-07-03T09:00:00",
             "relevance_score": 0.91},
            {"title": "Posted This Week", "company": "Gamma Works", "location": "Remote", "source": "feed-a",
             "external_id": "C1", "source_url": "https://jobs.example.test/c1", "discovered_at": days(-2).isoformat(),
             "relevance_score": 0.40}]
    monkeypatch.setattr(js, "_tracker_rows", lambda: rows)
    assert js.import_tracker(NOW) == 3 and js.import_tracker(NOW) == 0          # once
    assert [r["title"] for r in js.list_recent(NOW)] == ["Posted This Week"]
    assert sorted(r["title"] for r in js.list_archive(NOW)) == ["Also Closed In July", "Closed Back In July"]
