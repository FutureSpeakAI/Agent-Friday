"""The job store: one record per posting, with a status, so a panel can show what is open and recently seen and
keep what is gone in an archive instead of showing it forever.

Rules held here (synthetic listings only):
* a posting is one record however many times, from however many feeds and under however many ids or URL
  spellings it is seen;
* a posting is open until its closing date passes (a date-only closing date at the end of that day) or it has not
  been seen for STALE_DAYS; then it moves to the archive with the date it closed, and is never deleted;
* "recent" is applied and open, recently seen postings, newest sighting first, never ordered by score;
* a posting seen again after it expired is open again; one past its closing date stays closed;
* "applied" comes from the tracker's active applications, and such a posting is never swept or archived;
* the file is written atomically, once per batch of sightings, and a damaged file is an error, never an empty store.
"""
import itertools
import json
import os
from datetime import datetime, timedelta

import pytest

from agent_friday.seed.data import job_tracker_schema as schema
from agent_friday.services import job_store as js

NOW = datetime(2026, 11, 20, 9, 0)


def days(n):
    return NOW + timedelta(days=n)


def listing(title="Staff Engineer", company="Acme Robotics", **kw):
    base = {"title": title, "company": company, "location": "Remote", "source": "feed-a", "external_id": None,
            "url": "https://jobs.example.test/%s" % title.lower().replace(" ", "-"), "score": 0.5}
    base.update(kw)
    return base


def stored(home):
    return json.loads((home / "job_store.json").read_text(encoding="utf-8"))["jobs"]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(schema, "DEFAULT_TRACKER_PATH", tmp_path / "job_tracker.json")
    return tmp_path


def tracked_job(title="Staff Engineer", stage=None, discovered=None):
    """A job in the tracker (and, with a stage, an application for it); returns the tracker's job id."""
    tracker = schema.JobTracker().load()
    job = schema.JobListing(title=title, company="Acme Robotics", location="Remote", source="feed-a",
                            external_id="T-" + title.replace(" ", ""), discovered_at=discovered or NOW.isoformat())
    tracker.add_job(job)
    if stage:
        tracker.log_application(schema.ApplicationRecord(job_id=job.job_id, stage=stage))
    return job.job_id


# -- one record per posting --------------------------------------------------------------------

def test_the_same_posting_is_one_record_however_it_is_spelled(_home):
    js.upsert(listing("Staff Engineer", "Acme Robotics", source="feed-a"), days(-2))
    js.upsert(listing("  staff   engineer ", "ACME robotics", source="feed-b", url="https://other.example.test/9"), NOW)
    assert len(stored(_home)) == 1


def test_two_feeds_with_their_own_ids_are_one_record(_home):
    js.upsert(listing(source="feed-a", external_id="A-1"), days(-2))
    js.upsert(listing(source="feed-b", external_id="B-77", url="https://b.example.test/77"), NOW)
    assert len(stored(_home)) == 1
    assert js.list_recent(NOW)[0]["first_seen"] == days(-2).isoformat()


def test_the_same_feed_seen_with_and_without_its_id_is_one_record(_home):
    js.upsert(listing(external_id="A-1"), days(-2))
    js.upsert(listing(external_id=None), days(-1))
    js.upsert(listing(external_id="A-1"), NOW)
    assert len(stored(_home)) == 1


def test_a_url_that_differs_only_by_tracking_parameters_or_fragment_is_one_record(_home):
    base = "https://Jobs.Example.test/view/42"
    for url in (base, base + "?utm_source=mail&utm_campaign=x", base + "/?fbclid=abc#apply", base + "?gclid=1&ref=feed"):
        js.upsert({"url": url}, NOW)
    assert len(stored(_home)) == 1
    js.upsert({"url": base + "?id=43"}, NOW)
    assert len(stored(_home)) == 2, "a query parameter that is not tracking names another posting"


def test_a_feed_id_holds_a_posting_together_when_it_is_renamed_after_a_repost(_home):
    js.upsert(listing("One", external_id="X1"), days(-1))
    js.upsert(listing("Renamed after a repost", external_id="X1", url="https://jobs.example.test/x1"), NOW)
    assert len(stored(_home)) == 1
    assert js.list_recent(NOW)[0]["title"] == "Renamed after a repost"


def test_different_postings_stay_different_records(_home):
    js.upsert(listing("Staff Engineer", external_id="A-1"), NOW)
    js.upsert(listing("Data Engineer", external_id="A-2"), NOW)
    js.upsert(listing("Staff Engineer", company="Beta Labs", external_id="A-3"), NOW)
    assert len(stored(_home)) == 3


def test_every_small_combination_of_feed_id_and_url_spelling_of_one_posting_is_one_record(_home):
    sources, ids = ("feed-a", "feed-b"), (None, "", "A-1", "B-2")
    urls = ("https://jobs.example.test/42", "https://jobs.example.test/42?utm_medium=rss",
            "https://jobs.example.test/42#top", None)
    titles = ("Staff Engineer", "STAFF engineer")
    for when, (source, external, url, title) in enumerate(itertools.product(sources, ids, urls, titles)):
        js.upsert(listing(title, source=source, external_id=external, url=url), days(-when / 100))
    assert len(stored(_home)) == 1
    assert len(js.list_recent(NOW)) == 1


def test_a_listing_that_names_nothing_to_key_on_is_refused():
    with pytest.raises(ValueError):
        js.make_keys({})
    with pytest.raises(ValueError):
        js.upsert({"location": "Remote"}, NOW)


# -- recent shows what is open and recently seen -----------------------------------------------

def test_an_open_recently_seen_job_shows_in_recent_and_not_in_the_archive():
    js.upsert(listing(remote=True), NOW)
    rows = js.list_recent(NOW)
    assert [r["title"] for r in rows] == ["Staff Engineer"] and rows[0]["remote"] is True
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
    js.upsert(listing(closes_at=closes.isoformat(), external_id="A-1"), days(-150))
    js.upsert(listing(source="feed-b", external_id="B-9", url="https://elsewhere.example.test/2",
                      closes_at=closes.isoformat()), days(-149))
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


def test_a_date_only_closing_date_stays_open_through_that_whole_day():
    js.upsert(listing(closes_at=NOW.date().isoformat()), NOW)
    end_of_day = datetime.combine(NOW.date(), datetime.max.time()).replace(microsecond=0)
    assert len(js.list_recent(end_of_day - timedelta(seconds=1))) == 1
    next_morning = end_of_day + timedelta(seconds=1)
    assert js.list_recent(next_morning) == []
    assert js.list_archive(next_morning)[0]["closed_at"].startswith(NOW.date().isoformat())


def test_a_posting_that_arrives_already_past_its_closing_date_goes_straight_to_the_archive():
    js.upsert(listing(closes_at=days(-5).isoformat()), NOW)
    assert js.list_recent(NOW) == [] and len(js.list_archive(NOW)) == 1


def test_the_archive_is_newest_closing_first_and_nothing_is_deleted(_home):
    js.upsert(listing("Older Close", closes_at=days(-90).isoformat()), days(-100))
    js.upsert(listing("Newer Close", closes_at=days(-10).isoformat()), days(-30))
    assert [r["title"] for r in js.list_archive(NOW)] == ["Newer Close", "Older Close"]
    assert len(stored(_home)) == 2


# -- what happens when a posting comes back ------------------------------------------------------

def test_a_posting_that_expired_and_is_seen_again_is_open_again():
    js.upsert(listing(), days(-100))
    assert js.list_recent(NOW) == []
    js.upsert(listing(), NOW)
    assert len(js.list_recent(NOW)) == 1 and js.list_archive(NOW) == []


def test_a_posting_past_its_closing_date_does_not_reopen_just_because_it_was_seen_again():
    js.upsert(listing(closes_at=days(-3).isoformat()), days(-10))
    js.upsert(listing(closes_at=days(-3).isoformat()), NOW)
    assert js.list_recent(NOW) == []


# -- applied comes from the tracker's applications --------------------------------------------------

def test_a_posting_with_an_active_application_is_applied_in_recent_and_never_expires():
    job_id = tracked_job(stage="interview")
    js.upsert(listing(job_id=job_id), days(-(js.STALE_DAYS + 40)))
    rows = js.list_recent(NOW)
    assert [(r["title"], r["status"]) for r in rows] == [("Staff Engineer", "applied")]
    assert js.list_archive(NOW) == []


def test_a_posting_past_its_closing_date_with_an_active_application_stays_in_recent():
    job_id = tracked_job(stage="applied")
    js.upsert(listing(job_id=job_id, closes_at=days(-5).isoformat()), days(-10))
    assert js.list_recent(NOW)[0]["status"] == "applied" and js.list_archive(NOW) == []


def test_when_the_application_ends_the_posting_is_judged_by_the_usual_rules_again():
    job_id = tracked_job(stage="interview")
    js.upsert(listing(job_id=job_id), days(-(js.STALE_DAYS + 40)))
    assert js.list_recent(NOW)[0]["status"] == "applied"
    tracker = schema.JobTracker().load()
    tracker.advance_application(tracker.applications_for(job_id)[0].application_id, "rejected")
    assert js.list_recent(NOW) == [] and js.list_archive(NOW)[0]["status"] == "expired"


def test_an_application_on_the_other_feeds_copy_of_a_posting_still_marks_it_applied():
    other = tracked_job("Staff Engineer", stage="screening")
    js.upsert(listing(job_id="job_from_feed_a"), days(-1))
    js.upsert(listing(job_id=other, source="feed-b", external_id="B-1"), NOW)
    js.upsert(listing(job_id="job_from_feed_a"), NOW)
    assert js.list_recent(NOW)[0]["status"] == "applied"


def test_a_posting_without_an_application_is_open_not_applied():
    job_id = tracked_job()
    js.upsert(listing(job_id=job_id), NOW)
    assert js.list_recent(NOW)[0]["status"] == "open"


@pytest.mark.parametrize("stage", ["discovered", "triaged"])
def test_an_application_friday_only_prepared_leaves_the_posting_open(stage):
    job_id = tracked_job(stage=stage)
    js.upsert(listing(job_id=job_id), NOW)
    assert js.list_recent(NOW)[0]["status"] == "open"


# -- the file ------------------------------------------------------------------------------------------

def test_a_batch_of_sightings_is_one_read_and_one_write(monkeypatch):
    writes = []
    real_save = js._save
    monkeypatch.setattr(js, "_save", lambda data: (writes.append(1), real_save(data)))
    rows = js.upsert_many([listing("Job %d" % n, company="Company %d" % n) for n in range(25)] + [{}], NOW)
    assert len(rows) == 25 and len(writes) == 1
    assert len(js.list_recent(NOW)) == 25


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


def test_the_import_never_archives_a_tracker_job_with_an_active_application():
    tracked_job("Applied Last Month", stage="interview", discovered=days(-45).isoformat())
    tracked_job("Forgotten Last Month", discovered=days(-45).isoformat())
    assert js.import_tracker(NOW) == 2
    assert [(r["title"], r["status"]) for r in js.list_recent(NOW)] == [("Applied Last Month", "applied")]
    assert [(r["title"], r["status"]) for r in js.list_archive(NOW)] == [("Forgotten Last Month", "expired")]
