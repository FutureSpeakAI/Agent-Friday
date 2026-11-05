"""Channel sharing with a paired TV: the PC stores a TV's channels and hands them back.

A channel is data. The promises guarded here: only a TV the owner allowed (a screen-only card)
may share; a bundle is stored only if its seal matches; only defined fields survive; the
sender is the device, not the file; nothing is ever run; and nothing outside the store can be
named by an id.
"""
import hashlib
import json

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import channels, pairing
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

HTML = "<html><body>space jumper</body></html>"


def bundle(html=HTML, **over):
    b = {"format": "friday-channel", "v": 1, "title": "Space Jumper", "emoji": "\U0001F680",
         "description": "A game", "html": html, "network": ["api.example.org"],
         "voice": {"phrases": ["jump", "pause"]}, "sha256": hashlib.sha256(html.encode()).hexdigest(),
         "exportedAt": 5, "from": "the TV"}
    b.update(over)
    return b


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(channels, "_dir", lambda: tmp_path / "tv_channels")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    pairing._reset_for_tests()
    yield
    pairing._reset_for_tests()
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


@pytest.fixture
def tv():
    t = FakeTv()
    device_pairing.add_device(t.spki, name="Living room TV")
    return t


@pytest.fixture
def allowed(tv):
    device_pairing.set_right(tv.id, "tv_channels", True)
    return tv


def post(client, tv, doc, raw=None):
    tok = open_session(client, tv)
    body = raw if raw is not None else json.dumps(doc)
    return client.post("/tv/v1/channels", data=body, headers=dict(auth(tok), **{"Content-Type": "application/json"}))


def cards():
    return [c for c in ap.list_approvals(status="pending")
            if c.get("kind") == pairing.RIGHT_KIND and c["payload"]["right"] == "tv_channels"]


# ── the right ───────────────────────────────────────────────────────────────

def test_channels_need_a_session(client):
    assert client.get("/tv/v1/channels").status_code == 401
    assert client.post("/tv/v1/channels", json=bundle()).status_code == 401


def test_a_tv_without_the_right_is_refused_and_the_owner_is_asked_once(client, tv):
    tok = open_session(client, tv)
    r = client.get("/tv/v1/channels", headers=auth(tok))
    assert r.status_code == 403 and r.get_json()["needs_right"] == "tv_channels"
    assert len(cards()) == 1
    client.get("/tv/v1/channels", headers=auth(tok))
    assert post(client, tv, bundle()).status_code == 403
    assert len(cards()) == 1, "asking again within the hour must not pile up cards"
    assert channels.listing() == [], "nothing was stored for a TV without the right"


def test_the_card_says_the_pc_never_runs_a_channel():
    title, text = pairing.RIGHT_CARDS["tv_channels"]
    assert "never runs" in text and "website access" in text
    assert "tv_channels" in device_pairing.RIGHTS


def test_a_new_right_needs_a_press_not_a_session(client, tv):
    with pytest.raises(ValueError):
        device_pairing.set_right(tv.id, "channels_for_free", True)
    assert not device_pairing.has_right(device_pairing.get_device(tv.id), "tv_channels")


def test_offer_right_has_a_cooldown_and_only_for_known_rights(tv):
    assert pairing.offer_right(tv.id, "tv_channels")
    assert pairing.offer_right(tv.id, "tv_channels") is None
    assert pairing.offer_right(tv.id, "owner") is None


# ── storing and handing back ─────────────────────────────────────────────────

def test_a_good_bundle_is_stored_listed_and_handed_back(client, allowed):
    r = post(client, allowed, bundle())
    assert r.status_code == 200 and r.get_json()["new"] is True
    cid = r.get_json()["id"]
    tok = open_session(client, allowed)
    rows = client.get("/tv/v1/channels", headers=auth(tok)).get_json()["channels"]
    assert rows == [{"id": cid, "title": "Space Jumper", "sha256": hashlib.sha256(HTML.encode()).hexdigest(), "from": "Living room TV"}]
    got = client.get("/tv/v1/channels/" + cid, headers=auth(tok)).get_json()
    assert got["format"] == "friday-channel" and got["html"] == HTML and got["network"] == ["api.example.org"]
    assert got["voice"] == {"phrases": ["jump", "pause"]}


def test_the_same_page_twice_is_stored_once(client, allowed):
    a = post(client, allowed, bundle()).get_json()
    b = post(client, allowed, bundle(title="Renamed")).get_json()
    assert a["id"] == b["id"] and b["new"] is False
    assert len(channels.listing()) == 1


def test_the_sender_is_the_device_not_the_file(client, allowed):
    post(client, allowed, bundle(**{"from": "Mom's laptop (trust me)"}))
    assert channels.listing()[0]["from"] == "Living room TV"


def test_only_defined_fields_survive_no_grants_no_ids_no_extras(client, allowed):
    cid = post(client, allowed, bundle(granted=["evil.example.org"], id="hijack", extra={"owlBuild": 1}, key="secret")).get_json()["id"]
    got = channels.fetch(cid)
    assert set(got) == {"format", "v", "title", "emoji", "description", "html", "network", "voice", "sha256", "from", "exportedAt"}
    stored = (channels._dir() / (cid + ".json")).read_text(encoding="utf-8")
    assert "evil.example.org" not in stored and "hijack" not in stored and "secret" not in stored


# ── what is refused ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc,status", [
    (bundle(html="<html>changed</html>", sha256=hashlib.sha256(HTML.encode()).hexdigest()), 400),   # seal no longer matches
    (bundle(sha256="0" * 64), 400),
    (bundle(format="other"), 400),
    (bundle(v=2), 400),
    (bundle(title="   "), 400),
    (bundle(network=["*.example.org"]), 400),
    (bundle(network=["192.168.1.5"]), 400),
    (bundle(network=["localhost"]), 400),
    (bundle(network=["printer.local"]), 400),
    (bundle(network=["h%d.example.org" % i for i in range(9)]), 400),
    (bundle(network="api.example.org"), 400),
])
def test_bad_bundles_are_refused_and_nothing_is_kept(client, allowed, doc, status):
    assert post(client, allowed, doc).status_code == status
    assert channels.listing() == []


def test_html_over_the_limit_and_bodies_over_the_limit_are_413(client, allowed):
    big = "x" * (channels.MAX_HTML + 1)
    assert post(client, allowed, bundle(html=big, sha256=hashlib.sha256(big.encode()).hexdigest())).status_code == 413
    assert post(client, allowed, None, raw="x" * (channels.MAX_BUNDLE_BYTES + 5)).status_code == 413
    assert channels.listing() == []


def test_not_json_is_a_400(client, allowed):
    assert post(client, allowed, None, raw="not json").status_code == 400
    assert post(client, allowed, None, raw="[1,2]").status_code == 400


def test_the_store_is_capped(client, allowed, monkeypatch):
    monkeypatch.setattr(channels, "MAX_CHANNELS", 2)
    for i in range(2):
        assert post(client, allowed, bundle(html="<p>%d</p>" % i)).status_code == 200
    assert post(client, allowed, bundle(html="<p>third</p>")).status_code == 409
    assert post(client, allowed, bundle(html="<p>0</p>")).status_code == 200, "a page already held is not new"


def test_an_id_can_only_name_a_stored_channel(client, allowed):
    tok = open_session(client, allowed)
    for bad in ("..%2f..%2fsecurity%2fdevices", "ABCDEF0123456789", "short", "0" * 17, "%00"):
        assert client.get("/tv/v1/channels/" + bad, headers=auth(tok)).status_code == 404
    assert channels.fetch("../x") is None and channels.fetch(None) is None
    # a real, valid bundle sitting one directory above the store must not be reachable by a crafted id
    outside = channels._dir().parent / "outside.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(json.dumps(dict(bundle(), **{"from": "x", "received_at": 1})), encoding="utf-8")
    assert channels.fetch("../outside") is None
    assert not channels.remove("../outside") and outside.exists()


def test_a_hand_edited_file_is_not_served(client, allowed):
    cid = post(client, allowed, bundle()).get_json()["id"]
    p = channels._dir() / (cid + ".json")
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["html"] = "<script>steal()</script>"
    p.write_text(json.dumps(doc), encoding="utf-8")
    tok = open_session(client, allowed)
    assert client.get("/tv/v1/channels/" + cid, headers=auth(tok)).status_code == 404


def test_the_owner_can_remove_a_channel_locally(client, allowed):
    cid = post(client, allowed, bundle()).get_json()["id"]
    assert channels.remove(cid) and channels.listing() == []
    assert not channels.remove("../x")


# ── receipts, and the format the TV writes ─────────────────────────────────────

def test_receipts_name_the_device_and_channel_never_the_page(client, allowed, monkeypatch):
    from agent_friday.services import action_ledger
    rows = []
    monkeypatch.setattr(action_ledger, "append", lambda kind, body, *a, **k: rows.append((kind, body)))
    cid = post(client, allowed, bundle()).get_json()["id"]
    tok = open_session(client, allowed)
    client.get("/tv/v1/channels/" + cid, headers=auth(tok))
    kinds = [k for k, _ in rows]
    assert "tv_channel_received" in kinds and "tv_channel_sent" in kinds
    text = json.dumps(rows)
    assert "space jumper" not in text and cid in text and "device:" in text


def test_the_tvs_own_export_shape_is_accepted():
    """Exactly the keys friday-tv's ChannelBundle.export writes (sharing/ChannelBundle.java)."""
    tv_bundle = {"format": "friday-channel", "v": 1, "title": "Weather board", "emoji": "\U0001F326", "description": "Local weather",
                 "html": "<html><body>hi</body></html>", "network": ["api.weather.example.org"], "voice": {},
                 "sha256": hashlib.sha256(b"<html><body>hi</body></html>").hexdigest(), "exportedAt": 5, "from": "the TV"}
    b = channels.check(json.dumps(tv_bundle))
    assert b["network"] == ["api.weather.example.org"] and b["voice"] == {"phrases": []}


def test_voice_phrases_are_kept_short_and_plain():
    b = channels.check(bundle(voice={"phrases": ["Jump!", "x" * 40, "one two three four", "<script>", "jump"]}))
    assert b["voice"]["phrases"] == ["jump", "script"]
