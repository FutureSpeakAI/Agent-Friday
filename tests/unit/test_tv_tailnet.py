"""Off-LAN reach over Tailscale for already-paired devices (Stephen approved it, 2026-11-14).

The promises guarded here:

* the second listener binds only this PC's Tailscale address: never a wildcard, the LAN address or a public one;
* pairing stays LAN-only: the tailnet door refuses every /tv/v1/pair/* path, and refuses a peer that is not on
  the tailnet; discovery is not offered there;
* what does cross it is the same device-session door: no session, no answer; a device that is not paired cannot open
  one; a paired phone is served as the owner's Friday and the receipt says ``via: tailnet`` (``lan`` on the LAN);
* the interfaces are chosen by adapter, so PIA taking the default route changes neither the LAN address nor the
  Tailscale one;
* a device that holds a session is told the tailnet address and name to fail over to; before one, nothing.
"""
import base64

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, owner_turn, tailnet
from tests.ledger_support import rows
from tests.unit.test_owner_phone_turn import OwnerChat
from tests.unit.test_tv_gateway import FakeTv, auth

TAILNET_PEER = {"REMOTE_ADDR": "100.101.102.103"}
FORM = lambda *adapters: (lambda: list(adapters))                      # noqa: E731


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    monkeypatch.setattr(tailnet, "_cli", lambda *a, **k: None)
    tailnet.stop()
    device_pairing.open_pairing_window(900)          # the owner has opened pairing at the PC
    yield
    tailnet.stop()
    device_pairing._reset_for_tests()


@pytest.fixture
def lan():
    return gateway.create_app().test_client()


@pytest.fixture
def tn():
    return gateway.create_app(tailnet=True).test_client()


@pytest.fixture
def owner_chat(monkeypatch):
    fake = OwnerChat()
    monkeypatch.setattr(owner_turn, "_post", fake)
    return fake


def phone():
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="Friday on Pixel 10", kind="phone")
    household.bind_device(dev.id, "stephen")
    return dev


def session(client, dev, **env):
    nonce = client.post("/tv/v1/session/challenge", environ_overrides=env).get_json()["nonce"]
    return client.post("/tv/v1/session/open", environ_overrides=env,
                       json={"device_id": dev.id, "nonce": nonce, "sig": dev.sign(nonce)})


def ask(client, token, text="Who am I?", **env):
    return client.post("/tv/v1/messages", environ_overrides=env, headers=auth(token),
                       json={"messages": [{"role": "user", "content": text}]})


# ── which address it will bind ───────────────────────────────────────────────

@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "192.168.1.106", "127.0.0.1", "8.8.8.8", "100.63.255.255",
                                  "100.128.0.1", "my-pc.tailnet.ts.net"])
def test_the_tailnet_listener_binds_only_a_tailscale_address(host):
    with pytest.raises(ValueError):
        tailnet.serve(host, 47810)
    assert tailnet.info() is None


@pytest.mark.parametrize("addr,ok", [("100.64.0.1", True), ("100.101.102.103", True), ("100.127.255.254", True),
                                     ("fd7a:115c:a1e0::1", True), ("192.168.1.106", False), ("10.2.0.5", False),
                                     ("100.128.0.1", False), ("", False), ("nonsense", False)])
def test_what_counts_as_a_tailnet_address(addr, ok):
    assert tailnet.is_tailnet_address(addr) is ok


def test_the_address_comes_from_the_tailscale_adapter(monkeypatch):
    monkeypatch.setattr(tailnet, "_nics", FORM(("Ethernet", True, ["192.168.1.106"]),
                                               ("Tailscale", True, ["100.101.102.103"])))
    assert tailnet.address() == "100.101.102.103"


@pytest.mark.parametrize("adapters", [
    [("Tailscale", False, ["100.101.102.103"])],                      # adapter down
    [("Tailscale", True, ["192.168.7.7"])],                           # something else wearing the name
    [("Wi-Fi", True, ["100.101.102.103"])],                           # a tailnet-looking address on another adapter
    []])
def test_no_connected_tailscale_adapter_means_no_tailnet_address(monkeypatch, adapters):
    monkeypatch.setattr(tailnet, "_nics", FORM(*adapters))
    assert tailnet.address() is None


def test_the_cli_is_the_second_source(monkeypatch):
    monkeypatch.setattr(tailnet, "_nics", FORM())
    monkeypatch.setattr(tailnet, "_cli", lambda *a, **k: "100.88.1.2\n")
    assert tailnet.address() == "100.88.1.2"
    monkeypatch.setattr(tailnet, "_cli", lambda *a, **k: "192.168.1.9\n")      # never a non-tailnet answer
    assert tailnet.address() is None


# ── PIA ──────────────────────────────────────────────────────────────────────

PC_WITH_PIA_ON = [("Ethernet", False, ["169.254.215.70"]),
                  ("PIA OpenVPN WinTUN Adapter", True, ["10.0.0.2"]),         # the tunnel the default route now uses
                  ("vEthernet (WSL (Hyper-V firewall))", True, ["172.26.240.1"]),
                  ("vEthernet (Default Switch)", True, ["172.20.208.1"]),
                  ("Bluetooth Network Connection", True, ["169.254.169.49"]),
                  ("Loopback Pseudo-Interface 1", True, ["127.0.0.1"]),
                  ("A9000_NETGEAR", True, ["192.168.1.106"]),
                  ("Tailscale", True, ["100.101.102.103"])]


def test_with_pia_on_the_lan_address_is_still_the_home_network_and_not_the_tunnel(monkeypatch):
    monkeypatch.setattr(tailnet, "_nics", FORM(*PC_WITH_PIA_ON))
    assert tailnet.lan_address() == "192.168.1.106"
    assert gateway.lan_address() == "192.168.1.106"


def test_with_pia_on_the_tailscale_address_is_still_found(monkeypatch):
    monkeypatch.setattr(tailnet, "_nics", FORM(*PC_WITH_PIA_ON))
    assert tailnet.address() == "100.101.102.103"


def test_two_home_adapters_still_give_one_private_address(monkeypatch):
    monkeypatch.setattr(tailnet, "_nics", FORM(("Ethernet", True, ["10.0.5.20"]), ("Wi-Fi", True, ["192.168.1.106"])))
    assert tailnet.lan_address() in ("10.0.5.20", "192.168.1.106")


def test_no_home_network_means_no_lan_address(monkeypatch):
    monkeypatch.setattr(tailnet, "_nics", FORM(("PIA OpenVPN WinTUN Adapter", True, ["10.0.0.2"]),
                                               ("Tailscale", True, ["100.101.102.103"])))
    assert tailnet.lan_address() is None


# ── the tailnet door ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,method", [("/tv/v1/pair/start", "post"), ("/tv/v1/pair/reveal", "post"),
                                         ("/tv/v1/pair/status", "get")])
def test_pairing_is_never_possible_over_the_tailnet(tn, path, method):
    kw = {"json": {"device_spki": base64.b64encode(FakeTv().spki).decode(), "name": "New", "kind": "phone"}} \
        if method == "post" else {}
    r = getattr(tn, method)(path, environ_overrides=TAILNET_PEER, **kw)
    assert r.status_code == 403 and r.get_json()["code"] == "pairing_lan_only"
    assert device_pairing.list_devices() == []


def test_the_same_pairing_path_still_works_on_the_lan(lan):
    dev = FakeTv()
    r = lan.post("/tv/v1/pair/start", json={"device_spki": base64.b64encode(dev.spki).decode(), "name": "Pixel",
                                            "kind": "phone"})
    assert r.status_code == 200 and r.get_json()["pairing_id"]


@pytest.mark.parametrize("peer", ["192.168.1.50", "203.0.113.9", "127.0.0.1", "100.128.0.9"])
def test_a_peer_that_is_not_on_the_tailnet_is_refused_before_anything(tn, peer):
    dev = phone()
    r = tn.post("/tv/v1/session/challenge", environ_overrides={"REMOTE_ADDR": peer})
    assert r.status_code == 403 and r.get_json()["code"] == "not_tailnet"
    r = tn.post("/tv/v1/session/open", environ_overrides={"REMOTE_ADDR": peer},
                json={"device_id": dev.id, "nonce": "x", "sig": "AA=="})
    assert r.status_code == 403 and r.get_json()["code"] == "not_tailnet"


def test_a_tailnet_peer_with_no_session_gets_nothing(tn):
    for path in ("/tv/v1/whoami", "/tv/v1/models", "/tv/v1/stt"):
        r = tn.get(path, environ_overrides=TAILNET_PEER)
        assert r.status_code == 401 and r.get_json()["code"] == "session_required"
    assert ask(tn, "forged-token", **TAILNET_PEER).status_code == 401


def test_the_main_apps_credentials_are_refused_on_the_tailnet_too(tn):
    r = tn.get("/tv/v1/whoami", environ_overrides=TAILNET_PEER, headers={"X-Friday-Token": "x"})
    assert r.status_code == 401


def test_a_device_that_was_never_paired_cannot_open_a_session_over_the_tailnet(tn):
    r = session(tn, FakeTv(), **TAILNET_PEER)
    assert r.status_code == 401 and r.get_json()["code"] == "not_paired_or_bad_challenge"


def test_a_removed_device_loses_its_tailnet_session_at_once(tn):
    dev = phone()
    token = session(tn, dev, **TAILNET_PEER).get_json()["session"]
    assert tn.get("/tv/v1/whoami", environ_overrides=TAILNET_PEER, headers=auth(token)).status_code == 200
    device_pairing.remove_device(dev.id)
    assert tn.get("/tv/v1/whoami", environ_overrides=TAILNET_PEER, headers=auth(token)).status_code == 401


def test_a_session_from_the_lan_works_on_the_tailnet_door_and_the_reverse(lan, tn):
    dev = phone()
    t1 = session(lan, dev).get_json()["session"]
    assert tn.get("/tv/v1/whoami", environ_overrides=TAILNET_PEER, headers=auth(t1)).status_code == 200
    t2 = session(tn, dev, **TAILNET_PEER).get_json()["session"]
    assert lan.get("/tv/v1/whoami", headers=auth(t2)).status_code == 200


def test_member_and_rights_are_checked_the_same_way_over_the_tailnet(tn):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="Living-room TV", kind="tv")
    household.bind_device(dev.id, "family")
    token = session(tn, dev, **TAILNET_PEER).get_json()["session"]
    r = tn.get("/tv/v1/channels", environ_overrides=TAILNET_PEER, headers=auth(token))
    assert r.status_code == 403 and r.get_json()["needs_right"] == "tv_channels"      # the right is still needed
    models = tn.get("/tv/v1/models", environ_overrides=TAILNET_PEER, headers=auth(token)).get_json()
    assert models["scope"] == "household"                                              # a TV still cannot pick


# ── the proof, in tests: a phone away from home is served, with a receipt ────

def test_a_paired_phone_on_the_tailnet_is_served_as_the_owner_with_a_tailnet_receipt(tn, owner_chat):
    dev = phone()
    token = session(tn, dev, **TAILNET_PEER).get_json()["session"]
    r = ask(tn, token, "Who is Griff Jay?", **TAILNET_PEER)
    assert r.status_code == 200 and "Griff Jay is " in r.get_data(as_text=True)
    assert owner_chat.calls[0]["text"] == "Who is Griff Jay?"
    sent = [x for x in rows("device_turn") if x.get("device_id") == dev.id or x.get("device") == dev.id] or rows("device_turn")
    assert sent[-1]["via"] == "tailnet" and sent[-1]["watching"] == "owner"


def test_the_same_phone_on_the_lan_is_receipted_as_lan(lan, owner_chat):
    dev = phone()
    token = session(lan, dev).get_json()["session"]
    assert ask(lan, token).status_code == 200
    assert rows("device_turn")[-1]["via"] == "lan"


def test_whoami_says_which_door_the_device_came_through(lan, tn):
    dev = phone()
    t = session(lan, dev).get_json()["session"]
    assert lan.get("/tv/v1/whoami", headers=auth(t)).get_json()["via"] == "lan"
    assert tn.get("/tv/v1/whoami", environ_overrides=TAILNET_PEER, headers=auth(t)).get_json()["via"] == "tailnet"


# ── telling the device where to fail over ────────────────────────────────────

def test_before_a_session_nothing_about_the_tailnet_is_shown(lan, monkeypatch):
    monkeypatch.setitem(tailnet._state, "server", object())
    monkeypatch.setitem(tailnet._state, "address", "100.101.102.103")
    for path in ("/tv/v1/hello", "/tv/v1/session/challenge"):
        r = lan.open(path, method="POST" if "challenge" in path else "GET")
        assert "100." not in r.get_data(as_text=True) and "tailnet" not in r.get_data(as_text=True).lower()


def test_a_session_carries_the_tailnet_address_and_name_to_fail_over_to(lan, monkeypatch):
    monkeypatch.setitem(tailnet._state, "server", object())
    monkeypatch.setitem(tailnet._state, "address", "100.101.102.103")
    monkeypatch.setitem(tailnet._state, "port", 47810)
    monkeypatch.setitem(tailnet._state, "name", "desk-pc.tail1234.ts.net")
    got = session(lan, phone()).get_json()
    assert got["tailnet"] == {"address": "100.101.102.103", "name": "desk-pc.tail1234.ts.net", "port": 47810}


def test_with_no_tailnet_the_session_says_null_so_the_device_does_not_guess(lan):
    assert session(lan, phone()).get_json()["tailnet"] is None


def test_the_tailnet_door_is_reported_open_only_while_it_serves(monkeypatch):
    class Srv:
        port = 47810

        def serve_forever(self):
            pass

        def shutdown(self):
            pass
    monkeypatch.setattr("agent_friday.tv_gateway.app.make_bounded_server", lambda *a, **k: Srv())
    monkeypatch.setattr("agent_friday.tv_gateway.tls.server_context", lambda: None)
    got = tailnet.serve("100.101.102.103", 47810)
    assert got["address"] == "100.101.102.103" and got["port"] == 47810 and tailnet.info() == got
    tailnet.stop()
    assert tailnet.info() is None


def test_ensure_follows_tailscale_coming_and_going(monkeypatch):
    opened = []
    monkeypatch.setattr(tailnet, "serve", lambda addr, port: opened.append(addr) or
                        tailnet._state.update(server=object(), address=addr, port=port, name=None) or tailnet.info())
    monkeypatch.setattr(tailnet, "address", lambda: None)
    assert tailnet.ensure(47810) is None and opened == []                      # Tailscale not signed in yet
    monkeypatch.setattr(tailnet, "address", lambda: "100.101.102.103")
    assert tailnet.ensure(47810)["address"] == "100.101.102.103" and opened == ["100.101.102.103"]
    tailnet.ensure(47810)
    assert opened == ["100.101.102.103"]                                       # unchanged: not reopened
    monkeypatch.setattr(tailnet, "address", lambda: None)                      # signed out or disconnected
    assert tailnet.ensure(47810) is None and tailnet.info() is None


def test_ensure_never_raises_into_the_gateways_boot(monkeypatch):
    monkeypatch.setattr(tailnet, "address", lambda: "100.101.102.103")
    monkeypatch.setattr(tailnet, "serve", lambda *a: (_ for _ in ()).throw(OSError("address in use")))
    assert tailnet.ensure(47810) is None


def test_discovery_is_only_ever_built_for_the_lan_address():
    """boot() builds the UDP responder from the LAN address; the tailnet module has no responder at all."""
    import inspect
    assert "Responder(host," in inspect.getsource(gateway.boot) and not hasattr(tailnet, "Responder")
