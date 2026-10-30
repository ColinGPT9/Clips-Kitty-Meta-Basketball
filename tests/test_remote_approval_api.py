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
from remote_approval_fake import FakeRelay, finished_job, sign_command  # noqa: E402
from test_remote_approval_e2e import ENROLL_KEY, Phone, _node_ok, relay_url  # noqa: E402, F401

from core.state import StateDB  # noqa: E402
from remote_approval import commands, notifier, service, states, store  # noqa: E402
from remote_approval import settings as ra_settings  # noqa: E402
from remote_approval.relay import RelayError  # noqa: E402
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
    ("POST", "/remote-approval/muse/token", None),
    ("POST", "/remote-approval/muse/revoke", None),
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


def test_asking_again_says_so_when_nothing_is_left_to_offer(client):
    d = client.db()
    ra_settings.save(d, {"whatsapp_enabled": True, "relay_url": "https://relay.test"})
    job = finished_job(d, client.dir)
    review_id = notifier.on_job_finished(d, job, "done")
    d.close()
    r = client.post(f"/remote-approval/reviews/{review_id}/ask-again", headers=HEADERS)
    assert r.status_code == 409 and r.json()["detail"] == "Only an expired or cancelled offer can be sent again."

    d = client.db()
    for clip in d.clips_for_video(job["video_id"]):  # every clip posted everywhere since
        for platform in ra_settings.PLATFORMS:
            d.record_clip_publish(int(clip["id"]), platform, {"provider": "woopsocial", "state": "published"})
    store.move(d, review_id, states.EXPIRED, actor="test")
    d.close()
    r = client.post(f"/remote-approval/reviews/{review_id}/ask-again", headers=HEADERS)
    assert r.status_code == 409
    assert r.json()["detail"] == ("None of this video's clips can be offered again: they were removed or made "
                                  "again since, or are posted everywhere already.")


def test_a_retry_says_so_when_what_failed_went_up_from_somewhere_else(client):
    d = client.db()
    ra_settings.save(d, {"whatsapp_enabled": True, "relay_url": "https://relay.test", "remote_approvals": True})
    ra_settings.save(d, {"pairing": {"state": "confirmed"}, "pin_set": True, "accounts": FakeRelay().accounts},
                     internal=True)
    review_id = notifier.on_job_finished(d, finished_job(d, client.dir), "done")
    service.flush_outbox(d, client.dir, FakeRelay())
    review = store.get_review(d, review_id)
    clip = review["clips"][0]["clip_id"]
    out = commands.handle(d, sign_command({
        "id": "cmd_" + "e" * 24, "kind": "approve", "approval_id": review["approval_id"], "review_ref": review_id,
        "fingerprint": review["fingerprint"], "clips": [clip], "platforms": ["instagram"],
        "decided_at": store.stamp(),
    }), secret=DEVICE_SECRET)
    assert out.outcome == "accepted"
    d.record_clip_publish(clip, "instagram", {"provider": "meta", "state": "failed", "error": "Refused.",
                                              "request_id": review["approval_id"]})
    d.finish_publish_job(commands.jobs_for_review(d, review_id)[0]["id"], "failed")
    service.settle_publishing(d, ra_settings.load(d))
    assert store.get_review(d, review_id)["state"] == states.FAILED
    d.record_clip_publish(clip, "instagram", {"provider": "woopsocial", "state": "published"})  # posted since
    d.close()
    r = client.post(f"/remote-approval/reviews/{review_id}/retry", headers=HEADERS)
    assert r.status_code == 409
    assert r.json()["detail"] == ("What failed is already posted, on its way or uploading now, so there is "
                                  "nothing to retry.")


def _registered(client, monkeypatch):
    """This PC registered with a fake relay, nothing else set up."""
    from core import secrets

    secrets.save(client.dir, ra_settings.DEVICE_SECRET, {"device_id": "dev_0123456789abcdef",
                                                         "device_secret": DEVICE_SECRET})
    relay = FakeRelay()
    monkeypatch.setattr(service, "client", lambda db, data_dir: relay)
    d = client.db()
    ra_settings.save(d, {"relay_url": "https://relay.test"})
    d.close()
    return relay


def test_a_muse_key_is_shown_once_and_kept_nowhere_on_this_pc(client, monkeypatch, events, capsys):
    relay = _registered(client, monkeypatch)
    r = client.post("/remote-approval/muse/token", headers=HEADERS)
    assert r.status_code == 200
    key = r.json()["key"]
    assert key == relay.keys[0] and r.json()["settings"]["muse"]["enabled"] is True
    assert any(c[0] == "preferences" and c[1]["whatsapp_enabled"] is False for c in relay.calls)
    # Not in any answer after this one, the database, a file, an event or the log.
    for path in ("/remote-approval/status", "/remote-approval/reviews", "/remote-approval/audit"):
        assert key not in client.get(path).text, path
    for f in client.dir.rglob("*"):
        if f.is_file():
            assert key.encode() not in f.read_bytes(), f.name
    assert key not in json.dumps(events)
    assert key not in capsys.readouterr().out

    status = client.patch("/remote-approval/settings", json={"remote_approvals": True}, headers=HEADERS)
    assert status.status_code == 409 and "PIN" in status.json()["detail"]  # Muse stands in for WhatsApp, not the PIN
    d = client.db()
    ra_settings.save(d, {"pin_set": True}, internal=True)
    d.close()
    status = client.patch("/remote-approval/settings", json={"remote_approvals": True}, headers=HEADERS)
    assert status.status_code == 200 and status.json()["settings"]["remote_approvals"] is True

    r = client.post("/remote-approval/muse/revoke", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["settings"]["muse"]["enabled"] is False
    assert r.json()["settings"]["remote_approvals"] is False  # no WhatsApp either: nothing reaches the phone


def test_a_relay_from_before_muse_says_to_deploy_it_again(client, monkeypatch):
    relay = _registered(client, monkeypatch)
    relay.fail["muse_token"] = RelayError("not_found", "Unknown relay route.", status=404)
    r = client.post("/remote-approval/muse/token", headers=HEADERS)
    assert r.status_code == 409
    assert r.json()["detail"] == "Your relay is older than this version of Clips Kitty. Deploy it again to connect Muse."


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
