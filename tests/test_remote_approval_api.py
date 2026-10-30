"""The /remote-approval routes (server/remote_approval_api.py), Settings →
Notifications & Remote Approval: the session header on every route that
changes something, nothing secret in what they return, remote approval only
after pairing and a PIN, and the whole Settings set-up against the real relay
code (meta-relay/test/serve.mjs, Meta faked) when Node is installed.
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from remote_approval_fake import SECRET as DEVICE_SECRET  # noqa: E402
from remote_approval_fake import FakeRelay, finished_job  # noqa: E402
from test_remote_approval_e2e import ENROLL_KEY, Phone, _node_ok, relay_url  # noqa: E402, F401

from core.state import StateDB  # noqa: E402
from remote_approval import notifier, service, states, store  # noqa: E402
from remote_approval import settings as ra_settings  # noqa: E402
from server import remote_approval_api  # noqa: E402

SESSION = "test-session-secret-0123456789"
HEADERS = {"X-Clips-Kitty-Session": SESSION}


@pytest.fixture
def events(monkeypatch):
    from server.events import broadcaster

    seen: list[dict] = []
    monkeypatch.setattr(broadcaster, "publish", seen.append)
    return seen


@pytest.fixture
def client(tmp_path, events):
    app = fastapi.FastAPI()
    db_path = tmp_path / "state.db"
    StateDB(db_path).close()
    remote_approval_api.install(app, db=lambda: StateDB(db_path), data_dir=tmp_path, session_secret=SESSION)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db = lambda: StateDB(db_path)
    c.dir = tmp_path
    return c


CHANGING = [
    ("PATCH", "/remote-approval/settings", {"whatsapp_enabled": True}),
    ("POST", "/remote-approval/register", {"enroll_key": "x"}),
    ("POST", "/remote-approval/refresh", None),
    ("POST", "/remote-approval/pair/start", None),
    ("POST", "/remote-approval/pair/confirm", {"confirm": True}),
    ("POST", "/remote-approval/pin", {"pin": "4826"}),
    ("POST", "/remote-approval/test", None),
    ("POST", "/remote-approval/disconnect", None),
    ("POST", "/remote-approval/accounts/connect", {"platform": "facebook"}),
    ("POST", "/remote-approval/accounts/disconnect", {"platform": "facebook"}),
    ("POST", "/remote-approval/templates", {"create": True}),
    ("POST", "/remote-approval/reviews/1/cancel", None),
    ("POST", "/remote-approval/reviews/1/retry", None),
    ("POST", "/remote-approval/reviews/1/ask-again", None),
    ("POST", "/remote-approval/reviews/1/resend", None),
]


@pytest.mark.parametrize("method, path, body", CHANGING)
def test_every_changing_route_needs_the_session_header(client, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 403
    r = client.request(method, path, json=body, headers={"X-Clips-Kitty-Session": "wrong"})
    assert r.status_code == 403


def test_reads_need_no_header_and_hold_no_secret(client):
    from core import secrets

    secrets.save(client.dir, ra_settings.DEVICE_SECRET, {"device_id": "dev_0123456789abcdef",
                                                         "device_secret": DEVICE_SECRET})
    d = client.db()
    ra_settings.save(d, {"pairing": {"state": "confirmed", "tail": "4567"}}, internal=True)
    d.close()
    for path in ("/remote-approval/status", "/remote-approval/reviews", "/remote-approval/audit"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert DEVICE_SECRET not in r.text
    status = client.get("/remote-approval/status").json()
    assert status["settings"]["registered"] is True
    assert status["settings"]["pairing"] == {"state": "confirmed", "tail": "4567"}
    assert status["settings"]["remote_approvals"] is False  # off by default


def test_remote_approval_is_refused_until_paired_with_a_pin(client):
    r = client.patch("/remote-approval/settings", json={"whatsapp_enabled": True, "remote_approvals": True},
                     headers=HEADERS)
    assert r.status_code == 409 and "Pair" in r.json()["detail"]
    status = client.get("/remote-approval/status").json()
    assert status["settings"]["whatsapp_enabled"] is True  # the rest of the change was kept
    assert status["settings"]["remote_approvals"] is False


def test_bad_settings_are_refused(client):
    r = client.patch("/remote-approval/settings", json={"relay_url": "http://relay.example.com"}, headers=HEADERS)
    assert r.status_code == 400
    r = client.patch("/remote-approval/settings",
                     json={"preferred_destinations": ["instagram", "tiktok"], "approval_hours": 2},
                     headers=HEADERS)
    assert r.json()["settings"]["preferred_destinations"] == ["instagram"]


def test_the_gaming_opt_in_is_saved_from_settings(client):
    assert client.get("/remote-approval/status").json()["settings"]["basketball_only"] is True
    r = client.patch("/remote-approval/settings", json={"basketball_only": False}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["settings"]["basketball_only"] is False
    assert client.get("/remote-approval/status").json()["settings"]["basketball_only"] is False
    r = client.patch("/remote-approval/settings", json={"basketball_only": True}, headers=HEADERS)
    assert r.json()["settings"]["basketball_only"] is True


def test_routes_that_need_the_relay_say_so_before_it_is_set_up(client):
    r = client.post("/remote-approval/pair/start", headers=HEADERS)
    assert r.status_code == 409 and "relay" in r.json()["detail"]
    r = client.post("/remote-approval/test", headers=HEADERS)
    assert r.status_code == 409


def test_offers_can_be_cancelled_resent_and_asked_again_from_the_app(client, monkeypatch, events):
    from core import secrets

    secrets.save(client.dir, ra_settings.DEVICE_SECRET, {"device_id": "dev_0123456789abcdef",
                                                         "device_secret": DEVICE_SECRET})
    relay = FakeRelay()
    monkeypatch.setattr(service, "client", lambda db, data_dir: relay)
    d = client.db()
    ra_settings.save(d, {"whatsapp_enabled": True, "relay_url": "https://relay.test"})
    job = finished_job(d, client.dir)
    review_id = notifier.on_job_finished(d, job, "done")
    service.flush_outbox(d, client.dir, relay)
    d.close()

    listed = client.get("/remote-approval/reviews").json()["reviews"]
    assert listed[0]["state"] == states.NOTIFICATION_SENT and listed[0]["can_cancel"]
    assert "path" not in json.dumps(listed)
    one = client.get(f"/remote-approval/reviews/{review_id}").json()
    assert one["audit"] and one["messages"][0]["status"] == "sent"

    r = client.post(f"/remote-approval/reviews/{review_id}/resend", headers=HEADERS)
    assert r.status_code == 409  # it was delivered: nothing to send again

    r = client.post(f"/remote-approval/reviews/{review_id}/cancel", headers=HEADERS)
    assert r.status_code == 200 and r.json()["review"]["state"] == states.CANCELLED
    assert ("cancel", store_approval(client, review_id)) in relay.calls

    r = client.post(f"/remote-approval/reviews/{review_id}/ask-again", headers=HEADERS)
    assert r.status_code == 200
    new = r.json()["review_id"]
    assert client.get(f"/remote-approval/reviews/{new}").json()["state"] == states.NOTIFICATION_PENDING
    assert any(e.get("type") == "remote_approval" for e in events)


def store_approval(client, review_id):
    d = client.db()
    try:
        return store.get_review(d, review_id)["approval_id"]
    finally:
        d.close()


@pytest.mark.skipif(not _node_ok(), reason="needs Node 22.5+ for the relay")
def test_settings_set_up_against_the_real_relay(client, relay_url):  # noqa: F811
    phone = Phone(relay_url)
    r = client.post("/remote-approval/register", json={"relay_url": relay_url, "enroll_key": "wrong-key"},
                    headers=HEADERS)
    assert r.status_code in (403, 502)
    assert client.get("/remote-approval/status").json()["settings"]["registered"] is False

    r = client.post("/remote-approval/register", json={"relay_url": relay_url, "enroll_key": ENROLL_KEY},
                    headers=HEADERS)
    assert r.status_code == 200 and r.json()["settings"]["registered"] is True

    code = client.post("/remote-approval/pair/start", headers=HEADERS).json()
    assert code["text"] == f"LINK {code['code']}"
    phone.send(code["text"])
    status = client.post("/remote-approval/refresh", headers=HEADERS).json()
    assert status["settings"]["pairing"]["state"] == "awaiting_confirm"
    tail = status["settings"]["pairing"]["tail"]
    assert len(tail) == 4 and phone.state()["phone"].endswith(tail)

    status = client.post("/remote-approval/pair/confirm", json={"confirm": True}, headers=HEADERS).json()
    assert status["settings"]["pairing"]["state"] == "confirmed"
    assert "notifications from Clips Kitty are on" in json.dumps(phone.last_message())

    assert client.post("/remote-approval/pin", json={"pin": "1234"}, headers=HEADERS).status_code == 400
    status = client.post("/remote-approval/pin", json={"pin": "4826"}, headers=HEADERS).json()
    assert status["settings"]["pin_set"] is True

    url = client.post("/remote-approval/accounts/connect", json={"platform": "facebook"}, headers=HEADERS).json()["url"]
    assert url.startswith("https://") and "client_id" in url
    assert phone.oauth(url)["ok"]
    status = client.post("/remote-approval/refresh", headers=HEADERS).json()
    assert status["settings"]["accounts"]["instagram"]["connected"] is True
    assert status["settings"]["accounts"]["facebook"]["name"] == "Hoops Daily"

    r = client.patch("/remote-approval/settings", json={"whatsapp_enabled": True, "remote_approvals": True,
                                                        "preferred_destinations": ["instagram"]}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["relay_error"] == ""
    assert r.json()["settings"]["remote_approvals"] is True

    assert client.post("/remote-approval/test", headers=HEADERS).json() == {"queued": True}
    d = client.db()
    try:
        service.tick(d, client.dir, service.TickState())
    finally:
        d.close()
    assert "test" in json.dumps(phone.last_message()).lower()

    status = client.post("/remote-approval/disconnect", headers=HEADERS).json()
    assert status["settings"]["registered"] is False
    assert status["settings"]["whatsapp_enabled"] is False and status["settings"]["remote_approvals"] is False
