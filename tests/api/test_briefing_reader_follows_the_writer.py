"""The News reader opens a briefing from the folder the writer saved it in.

briefing_files.write saves into <FRIDAY_DIR>/wiki/briefings. The reader (GET /api/briefing/<file>, and the list at
GET /api/briefings) looked in <home>/.friday/wiki/briefings instead, so whenever FRIDAY_DIR is not <home>/.friday
(a FRIDAY_HOME install, the scratch home the app specs use) Start My Day opened News on an empty reader: the file
it had just named was a 404. Synthetic briefing only; FRIDAY_DIR is moved to a temp folder for the test.
"""
from agent_friday import core
from agent_friday.services import briefing_files

DATE = "2001-01-02"                    # a date no real or test briefing has


def test_a_briefing_written_under_friday_dir_is_served_and_listed(client, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / "elsewhere")
    path = briefing_files.write(DATE, "# Briefing for the test\n- One item.\n")
    assert path.parent == tmp_path / "elsewhere" / "wiki" / "briefings"

    r = client.get("/api/briefing/%s.md" % DATE)
    assert r.status_code == 200, r.get_json()
    assert "One item." in r.get_json()["content"] and r.get_json()["is_html"] is False

    listed = [b for b in client.get("/api/briefings").get_json()["briefings"] if b["date"] == DATE]
    assert listed and listed[0].get("md") == "%s.md" % DATE


def test_a_name_outside_the_folder_still_finds_nothing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / "elsewhere")
    briefing_files.write(DATE, "# Briefing\n")
    assert client.get("/api/briefing/..%5C..%5Csecrets.md").status_code == 404
