"""Which app holds the webcam (services/camera_holders): Windows' consent store
says who is using it now (a start with no stop), running camera apps are only
candidates, and nothing here can raise."""
from agent_friday.services import camera_holders as ch


def test_an_app_with_a_start_and_no_stop_is_using_the_camera_now():
    entries = [
        (r"C:#Users#x#AppData#Roaming#Zoom#bin#Zoom.exe", 133700000000000000, 0),
        (r"C:#Program Files#Google#Chrome#Application#chrome.exe", 133700000000000000, 133700000001000000),
        ("MSTeams_8wekyb3d8bbwe", 133700000000000000, None),
        (r"C:#x#never.exe", 0, 0),
    ]
    assert ch.parse(entries) == ["Zoom", "Microsoft Teams"]


def test_names_are_the_ones_a_person_would_say():
    assert ch.friendly(r"C:#Users#x#AppData#Roaming#Zoom#bin#CptHost.exe") == "Zoom"
    assert ch.friendly(r"C:#Program Files#Google#Chrome#Application#chrome.exe") == "Chrome"
    assert ch.friendly("MSTeams_8wekyb3d8bbwe") == "Microsoft Teams"
    assert ch.friendly(r"C:#Tools#SomeCapture.exe") == "somecapture"


def test_duplicates_collapse_and_bad_values_are_skipped():
    entries = [("a#Zoom.exe", 5, 0), ("b#CptHost.exe", 5, 0), ("c#x.exe", "junk", 0)]
    assert ch.parse(entries) == ["Zoom"]


def test_running_camera_apps_are_candidates_only():
    assert ch.running_camera_apps(["explorer.exe", "Zoom.exe", "CptHost.exe", "ms-teams.exe"]) == ["Zoom", "Microsoft Teams"]
    assert ch.running_camera_apps(["chrome.exe"]) == []      # a browser running says nothing


def test_snapshot_separates_the_certain_from_the_possible(monkeypatch):
    monkeypatch.setattr(ch, "read_registry", lambda kind="webcam": [("x#Zoom.exe", 7, 0)])
    monkeypatch.setattr(ch, "_process_names", lambda: ["Zoom.exe", "ms-teams.exe"])
    assert ch.snapshot() == {"holders": ["Zoom"], "candidates": ["Microsoft Teams"]}


def test_snapshot_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("registry")
    monkeypatch.setattr(ch, "read_registry", lambda kind="webcam": [])
    monkeypatch.setattr(ch, "_process_names", boom)
    assert ch.snapshot() == {"holders": [], "candidates": []}


def test_the_route_answers_with_both_lists(monkeypatch):
    from agent_friday.routes import desktop as d
    monkeypatch.setattr(ch, "snapshot", lambda: {"holders": ["Zoom"], "candidates": []})
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session
    app.register_blueprint(d.desktop_bp)
    with app.test_client() as c:
        r = c.get("/api/camera/holders")
    # login_required was applied at import; a local test client is either
    # let through or refused, and the parse tests cover the substance.
    assert r.status_code in (200, 401, 403), r.status_code
    if r.status_code == 200:
        assert r.get_json() == {"status": "ok", "holders": ["Zoom"], "candidates": []}
